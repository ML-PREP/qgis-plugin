"""The EIL tab must fit common screen sizes without horizontal scrolling.

Long raster paths used to widen the left column to ~1300 px and the log
header to ~680 px, so every column scrolled sideways even on a 1920 px
screen. Needs QGIS's PyQt bindings (skipped otherwise).
"""

import importlib
import os
import sys
import types

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("qgis.PyQt.QtWidgets", reason="needs QGIS's PyQt bindings")

from qgis.PyQt import QtWidgets  # noqa: E402

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

eil_form = importlib.import_module("mlprep_plugin.eil_mainwindow")
columns_module = importlib.import_module("mlprep_plugin.common.responsive_columns")
dashboard_module = importlib.import_module("mlprep_plugin.eil.dashboard")

LONG_PATH = (
    "/Users/someone/Documents/ml-prep/EIL_INPUTS/Cotabato/"
    "{}_cotabato_province_30m_utm51n.tif"
)
PARAMETERS = [
    "DEM", "BulkDensity", "Clay", "Sand", "Silt", "Slope", "PGA", "PRC",
    "SoilThickness", "SoilType", "ContributingFactor", "NDVI",
]
# Window sizes the plugin opens maximized into, minus the OS chrome.
SCREEN_SIZES = [(1024, 640), (1366, 700), (1920, 1000)]
COLUMNS = ("Left", "Middle", "Right")


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def form(qt_app):
    """The EIL form as the plugin hosts it, filled with worst-case content."""
    host = QtWidgets.QMainWindow()
    ui = eil_form.Ui_MainWindow()
    ui.setupUi(host)

    # What RoiSelector inserts at runtime.
    box = QtWidgets.QGroupBox("Clip Results to Region of Interest")
    grid = QtWidgets.QGridLayout(box)
    for row, text in enumerate(["Search Location:", "Select Level:", "Select Name:"]):
        grid.addWidget(QtWidgets.QLabel(text), row, 0)
        grid.addWidget(QtWidgets.QComboBox() if row else QtWidgets.QLineEdit(), row, 1)
    ui.verticalLayout.insertWidget(2, box)

    for name in PARAMETERS:
        getattr(ui, f"comboBox_{name}").addItem(LONG_PATH.format(name.lower()))
    ui.plainTextEdit_Logs.append_message("[WARNING] a warning")
    ui.plainTextEdit_Logs.append_message("[ERROR] an error")

    tabs = QtWidgets.QTabWidget()
    tabs.addTab(ui.centralwidget, "EIL Detection")
    yield ui, tabs
    tabs.deleteLater()
    host.deleteLater()


def show_at(qt_app, tabs, size):
    tabs.resize(*size)
    tabs.show()
    qt_app.processEvents()


class TestNoHorizontalScroll:
    @pytest.mark.parametrize("size", SCREEN_SIZES)
    def test_no_column_scrolls_sideways(self, qt_app, form, size):
        # Arrange
        ui, tabs = form

        # Act
        show_at(qt_app, tabs, size)

        # Assert
        scrolling = [
            name for name in COLUMNS
            if getattr(ui, f"scrollArea{name}").horizontalScrollBar().isVisible()
        ]
        assert scrolling == []

    def test_long_paths_do_not_set_the_input_column_width(self, form):
        # Arrange
        ui, _ = form

        # Act
        width = ui.scrollAreaWidgetContentsLeft.minimumSizeHint().width()

        # Assert -- fits beside the results on a 1024 px screen.
        assert width < 520

    def test_log_header_fits_a_narrow_column(self, form):
        # Arrange
        ui, _ = form

        # Act / Assert
        assert ui.plainTextEdit_Logs.minimumSizeHint().width() < 360


class TestReflow:
    def test_three_columns_on_a_wide_screen(self, qt_app, form):
        # Arrange
        ui, tabs = form

        # Act
        show_at(qt_app, tabs, (1920, 1000))

        # Assert
        assert ui.columns.mode == columns_module.MODE_WIDE

    def test_results_stack_over_the_log_on_a_narrow_screen(self, qt_app, form):
        # Arrange
        ui, tabs = form

        # Act
        show_at(qt_app, tabs, (1024, 640))

        # Assert
        assert ui.columns.mode == columns_module.MODE_STACKED
        middle = ui.scrollAreaMiddle.geometry()
        right = ui.scrollAreaRight.geometry()
        assert middle.left() == right.left()
        assert middle.bottom() < right.top()

    def test_switches_back_when_the_window_widens(self, qt_app, form):
        # Arrange
        ui, tabs = form
        show_at(qt_app, tabs, (1024, 640))

        # Act
        show_at(qt_app, tabs, (1920, 1000))

        # Assert -- every panel is visible again, side by side.
        assert ui.columns.mode == columns_module.MODE_WIDE
        lefts = [getattr(ui, f"scrollArea{n}").mapTo(tabs, getattr(ui, f"scrollArea{n}").rect().topLeft()).x()
                 for n in COLUMNS]
        assert lefts == sorted(lefts) and len(set(lefts)) == 3


class TestPathChoices:
    def test_full_path_is_available_as_a_tooltip(self, form):
        # Arrange
        ui, _ = form
        box = ui.comboBox_Slope
        path = LONG_PATH.format("other")

        # Act
        box.addItem(path)
        box.setCurrentIndex(box.count() - 1)

        # Assert
        assert box.toolTip() == path


def tallies(names):
    return {
        name: {"High": 5000 - 400 * i, "Moderate": 900, "Low": 22000}
        for i, name in enumerate(names)
    }


LONG_NAMES = [f"Municipality of Very Long Name Number {i}" for i in range(10)]


@pytest.fixture
def dashboard(form):
    ui, tabs = form
    return ui, tabs, dashboard_module.HazardDashboard(ui, lambda *_: None, None)


class TestResultsPanels:
    def test_empty_state_until_results_arrive(self, dashboard):
        # Arrange
        ui, _, _ = dashboard

        # Act / Assert
        assert ui.tableWidget_Hazard.isHidden()
        assert not ui.label_HazardEmpty.isHidden()

    def test_table_is_exactly_as_tall_as_its_rows(self, qt_app, dashboard):
        # Arrange
        ui, tabs, board = dashboard
        show_at(qt_app, tabs, (1366, 700))

        # Act
        board.show_tallies(tallies(["Makilala"]))
        qt_app.processEvents()

        # Assert
        table = ui.tableWidget_Hazard
        assert not table.isHidden() and ui.label_HazardEmpty.isHidden()
        assert table.verticalScrollBar().maximum() == 0
        assert table.height() < 4 * table.verticalHeader().defaultSectionSize()

    def test_counts_show_thousands_separators(self, dashboard):
        # Arrange
        ui, _, board = dashboard

        # Act
        board.show_tallies(tallies(["Makilala"]))

        # Assert
        assert ui.tableWidget_Hazard.item(0, 3).text() == "22,000"

    def test_one_municipality_draws_one_row_not_a_full_panel(self, dashboard):
        # Arrange
        _, _, board = dashboard

        # Act
        board.show_tallies(tallies(["Makilala"]))
        one = board._canvas.height()
        board.show_tallies(tallies(LONG_NAMES))
        ten = board._canvas.height()

        # Assert
        assert one == dashboard_module.chart_height(1)
        assert ten == dashboard_module.chart_height(10)
        assert one < ten / 2

    @pytest.mark.parametrize("size", SCREEN_SIZES)
    def test_long_names_do_not_widen_the_results(self, qt_app, dashboard, size):
        # Arrange
        ui, tabs, board = dashboard

        # Act
        board.show_tallies(tallies(LONG_NAMES))
        show_at(qt_app, tabs, size)

        # Assert
        assert not ui.scrollAreaMiddle.horizontalScrollBar().isVisible()
        assert ui.scrollAreaWidgetContentsMiddle.minimumSizeHint().width() < 420


class TestCompactCount:
    @pytest.mark.parametrize(
        "value, text",
        [(0, "0"), (950, "950"), (40000, "40k"), (42500, "42.5k"), (1_200_000, "1.2M")],
    )
    def test_formats_axis_counts(self, value, text):
        assert dashboard_module.compact_count(value) == text
