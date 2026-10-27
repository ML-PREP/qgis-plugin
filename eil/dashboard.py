"""The EIL results dashboard: top municipalities table and hazard bar chart."""

import os
import textwrap

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
# A thin dark outline keeps the yellow Low class visible on white.
BAR_EDGE = "#00000040"
LEGEND_LABELS = {HIGH: "High", MODERATE: "Moderate", LOW: "Low"}

# Names longer than this wrap onto a second line beside their bar.
TICK_LABEL_CHARS = 16

# Horizontal bars keep names readable in a narrow column. The chart grows a
# fixed amount per municipality, so one municipality is one bar-thick row
# rather than a block stretched across the whole panel.
BAR_ROW_HEIGHT = 34
CHART_CHROME_HEIGHT = 96  # legend, value axis and margins
EMPTY_CHART_HEIGHT = 140
TEXT_COLOR = "#1f2328"
MUTED_COLOR = "#6b7280"
GRID_COLOR = "#e5e7eb"
COUNT_COLUMNS = {1: HIGH, 2: MODERATE, 3: LOW}


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


def compact_count(value, _position=None):
    """Axis label for a count: 950, 40k, 1.2M."""
    for divisor, suffix in ((1_000_000, "M"), (1_000, "k")):
        if abs(value) >= divisor:
            return f"{value / divisor:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{value:.0f}"


def chart_height(bar_count):
    """Canvas height in pixels for `bar_count` horizontal bars."""
    if not bar_count:
        return EMPTY_CHART_HEIGHT
    return CHART_CHROME_HEIGHT + BAR_ROW_HEIGHT * bar_count


def _new_figure(Figure):
    # Constrained layout re-fits labels and legend on every resize; the
    # keyword changed in matplotlib 3.6.
    try:
        return Figure(figsize=(5, 4), dpi=100, layout="constrained")
    except TypeError:
        return Figure(figsize=(5, 4), dpi=100, constrained_layout=True)


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
        self._figure = _new_figure(Figure)
        self._figure.patch.set_facecolor("#ffffff")
        self._canvas = FigureCanvas(self._figure)
        # Width follows the column; the figure must not ask for its own.
        self._canvas.setMinimumWidth(0)
        self._canvas.setSizePolicy(
            QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed
        )
        self._ui.layout_Middle2.addWidget(self._canvas)
        self._draw_empty_chart()

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

            self._show(
                most_hazardous(
                    tally_by_municipality(
                        _municipality_values(hazard_layer, roi_layer, self._boundary["col"])
                    )
                )
            )

        except Exception as e:
            self._log(f"\n[Table Error] Failed to generate table:\n{str(e)}")

    @property
    def municipality_boundary(self):
        """{"path", "col"} of the municipality layer, or None when it is missing.

        A grid model tallies its cells against it in the venv (millions of
        cells are too many to walk here) and hands back show_tallies' input.
        """
        if not self._boundary or not os.path.exists(self._boundary["path"]):
            return None
        return self._boundary

    def show_tallies(self, tallies):
        """Fill table and chart from {municipality: {class: count}}."""
        self._log("\n--> Calculating Top 5 Hazardous Municipalities...")
        if not tallies:
            self._log("[Warning] No hazard counts by municipality to show.")
            return
        try:
            self._show(most_hazardous(tallies))
        except Exception as e:
            self._log(f"\n[Table Error] Failed to generate table:\n{str(e)}")

    def _show(self, ranked):
        self._fill_table(ranked)
        self._log("--> Table successfully updated!")

        if self._figure is None:
            self._log("--> Chart skipped (matplotlib not installed).")
            return
        self._draw_chart(ranked)
        self._log("--> Dashboard successfully updated!")

    def _fill_table(self, ranked):
        table = self._ui.tableWidget_Hazard
        table.setRowCount(len(ranked))
        for row, (name, counts) in enumerate(ranked):
            name_item = QtWidgets.QTableWidgetItem(name)
            name_item.setToolTip(name)
            table.setItem(row, 0, name_item)
            for column, hazard in COUNT_COLUMNS.items():
                item = QtWidgets.QTableWidgetItem(f"{counts[hazard]:,}")
                item.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
                if hazard == HIGH:  # the ranking key
                    font = table.font()
                    font.setBold(True)
                    item.setFont(font)
                table.setItem(row, column, item)

        self._ui.label_HazardEmpty.setVisible(not ranked)
        table.setVisible(bool(ranked))
        _fit_height_to_rows(table)

    def _draw_empty_chart(self):
        self._canvas.setFixedHeight(chart_height(0))
        self._figure.clear()
        self._figure.text(
            0.5, 0.5, "No results yet", ha="center", va="center",
            color=MUTED_COLOR, fontsize=10, style="italic",
        )
        self._canvas.draw_idle()

    def _draw_chart(self, ranked):
        names = [textwrap.fill(name, TICK_LABEL_CHARS) for name, _ in ranked]
        positions = list(range(len(ranked)))

        self._canvas.setFixedHeight(chart_height(len(ranked)))
        self._figure.clear()
        ax = self._figure.add_subplot(111)

        # Stacked High -> Moderate -> Low.
        lefts = [0] * len(ranked)
        for hazard in (HIGH, MODERATE, LOW):
            widths = [counts[hazard] for _, counts in ranked]
            ax.barh(
                positions,
                widths,
                BAR_WIDTH,
                left=lefts,
                color=BAR_COLORS[hazard],
                edgecolor=BAR_EDGE,
                linewidth=0.6,
                label=LEGEND_LABELS[hazard],
                zorder=2,
            )
            lefts = [left + width for left, width in zip(lefts, widths)]

        # Rank order, most hazardous on top.
        ax.set_ylim(len(ranked) - 0.5, -0.5)
        ax.set_yticks(positions)
        ax.set_yticklabels(names, fontsize=9, color=TEXT_COLOR)
        ax.set_xlabel("Number of Landslides", fontsize=9, color=MUTED_COLOR)
        ax.xaxis.set_major_formatter(_count_formatter())
        ax.xaxis.set_major_locator(_count_locator())
        ax.tick_params(axis="x", labelsize=9, colors=MUTED_COLOR, length=0)
        ax.tick_params(axis="y", length=0, pad=6)
        ax.grid(axis="x", color=GRID_COLOR, linewidth=0.8, zorder=0)
        for side in ("top", "right", "bottom"):
            ax.spines[side].set_visible(False)
        ax.spines["left"].set_color("#9ca3af")

        # Above the plot, so it never covers a bar.
        ax.legend(
            loc="lower left",
            bbox_to_anchor=(0, 1.0),
            ncol=3,
            frameon=False,
            fontsize=9,
            handlelength=1.0,
            handleheight=1.0,
            columnspacing=1.2,
            borderaxespad=0.4,
        )

        self._canvas.draw_idle()


def _count_formatter():
    from matplotlib.ticker import FuncFormatter

    return FuncFormatter(compact_count)


def _count_locator():
    # Few ticks: a narrow column has room for about four value labels.
    from matplotlib.ticker import MaxNLocator

    return MaxNLocator(nbins=4, integer=True)


def _fit_height_to_rows(table):
    """Make `table` exactly tall enough for its rows, so it never scrolls."""
    rows = table.verticalHeader().length()
    header = table.horizontalHeader().sizeHint().height()
    table.setFixedHeight(rows + header + 2 * table.frameWidth())
