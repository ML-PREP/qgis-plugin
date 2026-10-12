"""The EIL results dashboard: top municipalities table and hazard bar chart."""

import os

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt
from qgis.core import (
    QgsCoordinateTransform,
    QgsProject,
    QgsSpatialIndex,
    QgsVectorLayer,
)

from .hazard_stats import HIGH, LOW, MODERATE, most_hazardous, tally_by_municipality

SUSCEPTIBILITY_FIELD = "Hazard_Susceptibility"
BAR_WIDTH = 0.6
BAR_COLORS = {HIGH: "#ff0000", MODERATE: "#7030a0", LOW: "#ffff00"}


def _load_chart_backend():
    """(Figure, FigureCanvas) classes, or None when matplotlib is missing."""
    try:
        import matplotlib

        # Try the Qt-version-agnostic backend before the Qt5-only one.
        try:
            matplotlib.use("qtagg")
            from matplotlib.backends.backend_qtagg import (
                FigureCanvasQTAgg as FigureCanvas,
            )
        except (ImportError, ValueError):
            matplotlib.use("Qt5Agg")
            from matplotlib.backends.backend_qt5agg import (
                FigureCanvasQTAgg as FigureCanvas,
            )
        from matplotlib.figure import Figure
    except ImportError:
        return None
    return Figure, FigureCanvas


def _municipality_values(hazard_layer, roi_layer, roi_col):
    """(municipality, susceptibility) for each slope unit, located by centroid."""
    transform = None
    if hazard_layer.crs() != roi_layer.crs():
        transform = QgsCoordinateTransform(
            hazard_layer.crs(),
            roi_layer.crs(),
            QgsProject.instance().transformContext(),
        )

    names = {}
    index = QgsSpatialIndex()
    for feat in roi_layer.getFeatures():
        names[feat.id()] = str(feat[roi_col])
        index.addFeature(feat)

    field = hazard_layer.fields().indexOf(SUSCEPTIBILITY_FIELD)

    for feat in hazard_layer.getFeatures():
        geom = feat.geometry()
        if geom.isNull():
            continue
        centroid = geom.centroid()
        if transform:
            centroid.transform(transform)

        candidates = index.intersects(centroid.boundingBox())
        if not candidates:
            continue
        # Precise containment first; a centroid on a shared edge falls back
        # to the first bounding-box hit.
        name = next(
            (
                names[roi_id]
                for roi_id in candidates
                if roi_layer.getFeature(roi_id).geometry().contains(centroid)
            ),
            names[candidates[0]],
        )
        yield name, feat.attributes()[field]


class HazardDashboard:
    def __init__(self, ui, log, municipality_boundary):
        self._ui = ui
        self._log = log
        self._boundary = municipality_boundary
        self._figure = None
        self._canvas = None
        self._build_canvas()

    def _build_canvas(self):
        """Create the chart, or a placeholder if matplotlib is absent.

        matplotlib is not bundled with every QGIS build, and importing it at
        module scope stopped the plugin loading. The chart is optional: without
        it every other feature still works.
        """
        backend = _load_chart_backend()
        if backend is None:
            placeholder = QtWidgets.QLabel(
                "Chart unavailable: matplotlib is not installed in this "
                "QGIS.\nEverything else works; see the Dependencies tab."
            )
            placeholder.setWordWrap(True)
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setStyleSheet("color: #666666; font-style: italic;")
            self._ui.layout_Middle2.addWidget(placeholder)
            return

        Figure, FigureCanvas = backend
        self._figure = Figure(figsize=(5, 4), dpi=100)
        self._figure.patch.set_facecolor("#ffffff")
        self._canvas = FigureCanvas(self._figure)
        self._ui.layout_Middle2.addWidget(self._canvas)

    def update(self, hazard_gpkg):
        """Rank municipalities by High-hazard slope units; fill table and chart."""
        try:
            self._log("\n--> Calculating Top 5 Hazardous Municipalities...")
            QtWidgets.QApplication.processEvents()

            if not self._boundary or not os.path.exists(self._boundary["path"]):
                self._log("[Warning] Municipality ROI shapefile not found.")
                return

            hazard_layer = QgsVectorLayer(hazard_gpkg, "hazard_temp", "ogr")
            roi_layer = QgsVectorLayer(self._boundary["path"], "roi_temp", "ogr")
            if not hazard_layer.isValid() or not roi_layer.isValid():
                self._log("[Error] Could not load layers for table generation.")
                return

            ranked = most_hazardous(
                tally_by_municipality(
                    _municipality_values(hazard_layer, roi_layer, self._boundary["col"])
                )
            )
            self._fill_table(ranked)
            self._log("--> Table successfully updated!")

            if self._figure is None:
                self._log("--> Chart skipped (matplotlib not installed).")
                return
            self._draw_chart(ranked)
            self._log("--> Dashboard successfully updated!")

        except Exception as e:
            self._log(f"\n[Table Error] Failed to generate table:\n{str(e)}")

    def _fill_table(self, ranked):
        table = self._ui.tableWidget_Hazard
        table.setRowCount(len(ranked))
        for row, (name, counts) in enumerate(ranked):
            cells = (name, str(counts[HIGH]), str(counts[MODERATE]), str(counts[LOW]))
            for column, text in enumerate(cells):
                table.setItem(row, column, QtWidgets.QTableWidgetItem(text))

    def _draw_chart(self, ranked):
        names = [name for name, _ in ranked]
        positions = list(range(len(ranked)))

        self._figure.clear()
        ax = self._figure.add_subplot(111)

        # Stacked High -> Moderate -> Low.
        bottoms = [0] * len(ranked)
        bars = []
        for hazard in (HIGH, MODERATE, LOW):
            heights = [counts[hazard] for _, counts in ranked]
            bars.append(
                ax.bar(
                    positions,
                    heights,
                    BAR_WIDTH,
                    bottom=bottoms,
                    color=BAR_COLORS[hazard],
                )
            )
            bottoms = [b + h for b, h in zip(bottoms, heights)]

        ax.set_ylabel("Number of Landslides", fontsize=10)
        ax.set_title("Top 10 Hazard Distribution", fontsize=12, fontweight="bold")
        ax.set_xticks(positions)
        ax.set_xticklabels(names, rotation=45, ha="right", fontsize=9)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.legend(
            tuple(bar[0] for bar in bars),
            ("High Hazard", "Moderate Hazard", "Low Hazard"),
            loc="upper right",
            frameon=False,
            fontsize=9,
        )

        self._figure.tight_layout()
        self._canvas.draw()
