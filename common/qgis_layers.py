"""Adding pipeline outputs to the current QGIS project."""

from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer


def add_raster_layer(path, name):
    """Add a raster to the project; False if QGIS cannot open it."""
    layer = QgsRasterLayer(path, name)
    if not layer.isValid():
        return False
    QgsProject.instance().addMapLayer(layer)
    return True


def add_vector_layer(path, name):
    """Add an OGR vector to the project; False if QGIS cannot open it."""
    layer = QgsVectorLayer(path, name, "ogr")
    if not layer.isValid():
        return False
    QgsProject.instance().addMapLayer(layer)
    return True
