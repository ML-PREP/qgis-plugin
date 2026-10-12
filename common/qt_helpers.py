"""Small widget helpers shared by the Landslide and EIL tabs."""

import os

from qgis.PyQt import QtCore, QtWidgets
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QPixmap
from qgis.core import (
    QgsMapRendererParallelJob,
    QgsMapSettings,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

FALLBACK_PREVIEW_SIZE = QtCore.QSize(200, 200)


def disable_wheel_scroll(combobox):
    """Stop a combobox changing value while the user scrolls the panel."""
    combobox.wheelEvent = lambda event: event.ignore()


def add_file_choice(combobox, path, icon=None):
    """Add `path` (shown by basename) to a combobox and select it."""
    if icon is None:
        combobox.addItem(os.path.basename(path), path)
    else:
        combobox.addItem(icon, os.path.basename(path), path)
    combobox.setCurrentIndex(combobox.count() - 1)


def layer_source_path(item_data):
    """File path behind a combobox entry: a browsed file or a project layer id."""
    if not item_data:
        return None
    if os.path.isabs(str(item_data)):
        return str(item_data)
    layer = QgsProject.instance().mapLayer(item_data)
    if layer and layer.source():
        return layer.source().split("|")[0]
    return None


def project_raster_layers():
    return [
        layer
        for layer in QgsProject.instance().mapLayers().values()
        if isinstance(layer, QgsRasterLayer)
    ]


def render_preview(file_path, graphics_view, is_raster=False):
    """Render a layer to an image and show it fitted in `graphics_view`."""
    if is_raster:
        layer = QgsRasterLayer(file_path, "preview")
    else:
        layer = QgsVectorLayer(file_path, "preview", "ogr")

    if not layer.isValid():
        return

    settings = QgsMapSettings()
    settings.setLayers([layer])
    settings.setBackgroundColor(QColor(255, 255, 255, 0))
    settings.setExtent(layer.extent())

    view_size = graphics_view.size()
    if view_size.width() <= 0:
        view_size = FALLBACK_PREVIEW_SIZE
    settings.setOutputSize(view_size)

    render_job = QgsMapRendererParallelJob(settings)
    render_job.start()
    render_job.waitForFinished()

    scene = QtWidgets.QGraphicsScene()
    item = scene.addPixmap(QPixmap.fromImage(render_job.renderedImage()))
    graphics_view.setScene(scene)
    graphics_view.fitInView(item, Qt.KeepAspectRatio)
