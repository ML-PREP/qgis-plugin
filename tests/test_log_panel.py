"""Tests for the EIL log console's classification and filtering."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

log_panel = pytest.importorskip(
    "MLPREP_Plugin_v4.log_panel",
    reason="needs QGIS's PyQt bindings",
)

from MLPREP_Plugin_v4.log_panel import (  # noqa: E402
    LEVEL_DETAIL, LEVEL_ERROR, LEVEL_INFO, LEVEL_SECTION, LEVEL_STEP,
    LEVEL_SUCCESS, LEVEL_WARNING, LogPanel, classify,
)

from qgis.PyQt import QtWidgets  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


@pytest.fixture
def panel(qt_app):
    widget = LogPanel()
    yield widget
    widget.deleteLater()


# --- classify ---------------------------------------------------------------

@pytest.mark.parametrize("message,expected", [
    ("[ERROR] No DEM selected.", LEVEL_ERROR),
    ("[CRITICAL ERROR] boom", LEVEL_ERROR),
    ("❌ AI Prediction Failed", LEVEL_ERROR),
    ("[Warning] Municipality ROI shapefile not found.", LEVEL_WARNING),
    ("[Notice] No cells found", LEVEL_WARNING),
    ("✅ Depositional Routing Complete!", LEVEL_SUCCESS),
    ("--- STARTING FULL EIL PIPELINE ---", LEVEL_SECTION),
    ("--> Erasing tile seams on Slope Units...", LEVEL_STEP),
    ("   > GO: tiling dem", LEVEL_DETAIL),
    ("[Input DEM]: srtm.tif", LEVEL_INFO),
    ("", LEVEL_INFO),
])
def test_classify_maps_pipeline_lines_to_levels(message, expected):
    assert classify(message) == expected


def test_explicit_tag_outranks_keyword_sniffing():
    # Arrange: an author-tagged warning that also contains the word "failed".
    message = "[Warning] seam cleanup failed, using raw geometry"

    # Act
    level = classify(message)

    # Assert
    assert level == LEVEL_WARNING


# --- append_message ---------------------------------------------------------

def test_append_message_splits_newlines_into_records(panel):
    # Arrange / Act
    panel.append_message("first\nsecond\nthird")

    # Assert
    assert len(panel._records) == 3


def test_continuation_lines_inherit_error_level(panel):
    # Arrange / Act
    panel.append_message("[Error] prediction failed\n  File 'x.py', line 3\n  ValueError")

    # Assert
    assert [record.level for record in panel._records] == [LEVEL_ERROR] * 3


def test_explicit_level_overrides_inference(panel):
    # Arrange / Act
    panel.append_message("[Output] chart generation skipped.", LEVEL_INFO)

    # Assert
    assert panel._records[0].level == LEVEL_INFO


def test_appendplaintext_shim_records_message(panel):
    # Existing call sites and worker signals use the QPlainTextEdit name.
    panel.appendPlainText("--> Table successfully updated!")

    assert panel._records[0].level == LEVEL_SUCCESS


def test_records_are_capped(panel):
    # Arrange
    limit = log_panel.MAX_LOG_RECORDS

    # Act
    for index in range(limit + 25):
        panel.append_message("line {0}".format(index))

    # Assert
    assert len(panel._records) == limit
    assert panel._records[-1].text == "line {0}".format(limit + 24)


# --- filtering and export ---------------------------------------------------

def _select_filter(panel, rank):
    index = panel.comboBox_Filter.findData(rank)
    assert index != -1
    panel.comboBox_Filter.setCurrentIndex(index)


def test_verbose_filter_hides_detail_lines(panel):
    # Arrange
    panel.append_message("   > GO: tiling dem")
    panel.append_message("--> Building slope units")

    # Act
    _select_filter(panel, 1)

    # Assert
    visible = [record.text.strip() for record in panel._visible_records()]
    assert visible == ["--> Building slope units"]


def test_warnings_filter_keeps_only_warnings_and_errors(panel):
    # Arrange
    panel.append_message("--> Building slope units")
    panel.append_message("[Warning] ROI shapefile not found.")
    panel.append_message("[ERROR] No DEM selected.")

    # Act
    _select_filter(panel, 3)

    # Assert
    levels = {record.level for record in panel._visible_records()}
    assert levels == {LEVEL_WARNING, LEVEL_ERROR}


def test_to_text_timestamps_visible_records(panel):
    # Arrange
    panel.append_message("[ERROR] No DEM selected.")

    # Act
    text = panel.to_text()

    # Assert
    assert text.endswith("[ERROR] No DEM selected.")
    assert text.startswith("[")


def test_clear_resets_records_and_counts(panel):
    # Arrange
    panel.append_message("[ERROR] No DEM selected.")

    # Act
    panel.clear()

    # Assert
    assert panel._records == []
    assert panel._counts[LEVEL_ERROR] == 0
    assert panel.label_counts.text() == ""


def test_counts_label_reports_warnings_and_errors(panel):
    # Arrange / Act
    panel.append_message("[Warning] ROI shapefile not found.")
    panel.append_message("[ERROR] No DEM selected.")

    # Assert
    label = panel.label_counts.text()
    assert "1 warning" in label
    assert "1 error" in label


def test_traceback_block_counts_as_one_error(panel):
    # Arrange / Act
    panel.append_message("[Error] prediction failed\n  File 'x.py', line 3\n  ValueError")

    # Assert
    assert panel._counts[LEVEL_ERROR] == 1
