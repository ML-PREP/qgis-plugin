# -*- coding: utf-8 -*-
"""Structured, filterable log console for the EIL Detection tab.

The pipeline writes progress from several worker threads as plain strings
("--> step", "[ERROR] ...", "   > detail"). This panel keeps every line as a
structured record (timestamp, level, text) so it can be colour-coded,
filtered, copied and saved, while still exposing the plain
``appendPlainText``/``clear`` surface the existing call sites and Qt signal
connections rely on.
"""

import html
import os
from datetime import datetime

# qgis.PyQt maps to whichever Qt binding this QGIS was built against.
from qgis.PyQt import QtCore, QtGui, QtWidgets

from .common.flow_layout import FlowLayout


# --- Levels ------------------------------------------------------------------
# Rank drives the severity filter: a record shows when rank >= selected rank.
LEVEL_DETAIL = "detail"
LEVEL_INFO = "info"
LEVEL_STEP = "step"
LEVEL_SECTION = "section"
LEVEL_SUCCESS = "success"
LEVEL_WARNING = "warning"
LEVEL_ERROR = "error"

LEVEL_RANK = {
    LEVEL_DETAIL: 0,
    LEVEL_INFO: 1,
    LEVEL_STEP: 2,
    LEVEL_SECTION: 2,
    LEVEL_SUCCESS: 2,
    LEVEL_WARNING: 3,
    LEVEL_ERROR: 4,
}

LEVEL_COLOR = {
    LEVEL_DETAIL: "#6a737d",
    LEVEL_INFO: "#24292f",
    LEVEL_STEP: "#1f6feb",
    LEVEL_SECTION: "#0b5ed7",
    LEVEL_SUCCESS: "#1a7f37",
    LEVEL_WARNING: "#9a6700",
    LEVEL_ERROR: "#cf222e",
}

BOLD_LEVELS = frozenset({LEVEL_SECTION, LEVEL_ERROR})

FILTER_CHOICES = (
    ("All messages", 0),
    ("Hide verbose details", 1),
    ("Warnings & errors", 3),
)

# An explicit tag always wins over keyword sniffing: "[Warning] ... failed"
# is a warning the author already classified, not an error.
TAG_LEVELS = (
    ("[critical error]", LEVEL_ERROR),
    ("[error]", LEVEL_ERROR),
    ("[warning]", LEVEL_WARNING),
    ("[warn]", LEVEL_WARNING),
    ("[notice]", LEVEL_WARNING),
    ("[skipped]", LEVEL_WARNING),
)

# Fallback keyword sniffing for lines that carry no tag.
ERROR_MARKERS = ("❌", "traceback", "failed", "worker error", "exception")
SUCCESS_MARKERS = ("✅", "🎉", "complete", "successfully", "success!")
WARNING_MARKERS = ("⚠", "skipped", "skipping", "missing", "not found")

TIMESTAMP_FORMAT = "%H:%M:%S"
MAX_LOG_RECORDS = 5000
DEFAULT_LOG_BASENAME = "eil_pipeline_log"


def classify(message):
    """Infer a level from a raw pipeline line.

    Kept as a free function so worker output, which arrives as bare strings
    over a Qt signal, is classified the same way as direct UI calls.
    """
    stripped = message.strip()
    if not stripped:
        return LEVEL_INFO

    lowered = stripped.lower()
    for tag, level in TAG_LEVELS:
        if tag in lowered:
            return level

    if any(marker in lowered for marker in ERROR_MARKERS):
        return LEVEL_ERROR
    if any(marker in lowered for marker in SUCCESS_MARKERS):
        return LEVEL_SUCCESS
    if any(marker in lowered for marker in WARNING_MARKERS):
        return LEVEL_WARNING
    if stripped.startswith("---") or stripped.startswith("==="):
        return LEVEL_SECTION
    if stripped.startswith("-->"):
        return LEVEL_STEP
    if message.startswith("   ") or stripped.startswith(">"):
        return LEVEL_DETAIL
    return LEVEL_INFO


class LogRecord(object):
    """One immutable log line."""

    __slots__ = ("timestamp", "level", "text")

    def __init__(self, timestamp, level, text):
        self.timestamp = timestamp
        self.level = level
        self.text = text

    @property
    def is_spacer(self):
        return not self.text.strip()

    def to_text(self):
        if self.is_spacer:
            return ""
        return "[{0}] {1}".format(self.timestamp.strftime(TIMESTAMP_FORMAT),
                                  self.text)

    def to_html(self):
        if self.is_spacer:
            return "<div>&nbsp;</div>"

        color = LEVEL_COLOR.get(self.level, LEVEL_COLOR[LEVEL_INFO])
        weight = "600" if self.level in BOLD_LEVELS else "400"
        stamp = self.timestamp.strftime(TIMESTAMP_FORMAT)
        body = html.escape(self.text).replace("  ", "&nbsp;&nbsp;")
        return (
            '<div style="white-space:pre-wrap;">'
            '<span style="color:#98a1ab;">{stamp}</span>&nbsp;&nbsp;'
            '<span style="color:{color};font-weight:{weight};">{body}</span>'
            "</div>"
        ).format(stamp=stamp, color=color, weight=weight, body=body)


def _row(parent, *widgets):
    """Widgets kept side by side as one unit inside a FlowLayout."""
    row = QtWidgets.QWidget(parent)
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    for widget in widgets:
        layout.addWidget(widget)
    return row


class LogPanel(QtWidgets.QWidget):
    """Console widget: header controls + colour-coded, filterable log view."""

    def __init__(self, parent=None, title="Process Log"):
        super(LogPanel, self).__init__(parent)
        self._records = []
        self._counts = {LEVEL_WARNING: 0, LEVEL_ERROR: 0}
        self._build_ui(title)

    # -- construction ---------------------------------------------------
    def _build_ui(self, title):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        layout.addWidget(self._build_header(title))

        self.view = QtWidgets.QPlainTextEdit(self)
        self.view.setObjectName("logView")
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setMaximumBlockCount(MAX_LOG_RECORDS + 1)
        self.view.setLineWrapMode(QtWidgets.QPlainTextEdit.WidgetWidth)
        self.view.setFont(self._monospace_font())
        self.view.setPlaceholderText(
            "Pipeline output will appear here once a run starts.")
        self.view.setStyleSheet(
            "QPlainTextEdit { background-color: #fbfcfd; color: #24292f;"
            " border: 1px solid #d0d7de; border-radius: 4px; padding: 6px; }")
        layout.addWidget(self.view, 1)

    def _build_header(self, title):
        """Title, counts and controls; wraps onto more rows in a narrow column."""
        header = QtWidgets.QWidget(self)
        flow = FlowLayout(header, spacing=6)

        self.label_title = QtWidgets.QLabel(title, self)
        self.label_title.setStyleSheet(
            "font-weight: 600; color: #24292f; background: transparent;")
        self.label_counts = QtWidgets.QLabel("", self)
        self.label_counts.setStyleSheet(
            "color: #6a737d; background: transparent;")
        flow.addWidget(_row(header, self.label_title, self.label_counts))

        self.comboBox_Filter = QtWidgets.QComboBox(self)
        self.comboBox_Filter.setToolTip("Filter messages by severity")
        for label, rank in FILTER_CHOICES:
            self.comboBox_Filter.addItem(label, rank)
        self.comboBox_Filter.currentIndexChanged.connect(self._rerender)
        flow.addWidget(self.comboBox_Filter)

        self.checkBox_AutoScroll = QtWidgets.QCheckBox("Auto-scroll", self)
        self.checkBox_AutoScroll.setChecked(True)
        self.checkBox_AutoScroll.setToolTip(
            "Keep the newest message visible while the pipeline runs")
        flow.addWidget(self.checkBox_AutoScroll)

        buttons = []
        for text, tip, slot in (
            ("Copy", "Copy the visible log to the clipboard", self.copy_to_clipboard),
            ("Save", "Save the visible log to a text file", self.save_to_file),
            ("Clear", "Remove all messages", self.clear),
        ):
            button = QtWidgets.QPushButton(text, self)
            button.setToolTip(tip)
            button.setMaximumWidth(60)
            button.clicked.connect(slot)
            buttons.append(button)
        # The three actions wrap as one group rather than one by one.
        flow.addWidget(_row(header, *buttons))

        return header

    @staticmethod
    def _monospace_font():
        font = QtGui.QFont("Menlo")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setPointSize(9)
        return font

    # -- public logging API ---------------------------------------------
    def append_message(self, message, level=None):
        """Add `message`, splitting embedded newlines into separate records."""
        if message is None:
            return

        text = str(message)
        # Continuation lines (a traceback under an "[Error] ..." header) inherit
        # the block's severity; otherwise every line is classified on its own so
        # indented worker output stays a detail line.
        inherited = level
        counted = False
        for line in text.split("\n"):
            line_level = inherited or classify(line)
            record = LogRecord(datetime.now(), line_level, line.rstrip())
            # Only the block's first real line bumps the badge, so a traceback
            # does not read as a dozen separate errors.
            self._append_record(record, count=not counted and not record.is_spacer)
            counted = counted or not record.is_spacer
            if inherited is None and line_level in (LEVEL_ERROR, LEVEL_WARNING):
                inherited = line_level
        self._update_counts()
        self._scroll_to_end_if_following()

    def appendPlainText(self, message):
        """Drop-in for QPlainTextEdit so worker `log` signals connect unchanged."""
        self.append_message(message)

    def log_info(self, message):
        self.append_message(message, LEVEL_INFO)

    def log_step(self, message):
        self.append_message(message, LEVEL_STEP)

    def log_success(self, message):
        self.append_message(message, LEVEL_SUCCESS)

    def log_warning(self, message):
        self.append_message(message, LEVEL_WARNING)

    def log_error(self, message):
        self.append_message(message, LEVEL_ERROR)

    def clear(self):
        self._records = []
        self._counts = {LEVEL_WARNING: 0, LEVEL_ERROR: 0}
        self.view.clear()
        self._update_counts()

    def to_text(self):
        """Plain-text transcript of the currently visible records."""
        return "\n".join(record.to_text() for record in self._visible_records())

    # -- header actions --------------------------------------------------
    def copy_to_clipboard(self):
        clipboard = QtWidgets.QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self.to_text())

    def save_to_file(self):
        suggested = "{0}_{1}.txt".format(
            DEFAULT_LOG_BASENAME, datetime.now().strftime("%Y%m%d_%H%M%S"))
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Log", os.path.join(os.path.expanduser("~"), suggested),
            "Text files (*.txt);;All files (*)")
        if not path:
            return

        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self.to_text())
                handle.write("\n")
        except OSError as exc:
            QtWidgets.QMessageBox.warning(
                self, "Save Log",
                "Could not write the log file:\n{0}".format(exc))
            return

        self.log_info("[Log] Saved to: {0}".format(path))

    # -- rendering -------------------------------------------------------
    def _append_record(self, record, count=True):
        self._records.append(record)
        if len(self._records) > MAX_LOG_RECORDS:
            # Oldest lines fall off; the view caps itself via maxBlockCount.
            self._records = self._records[-MAX_LOG_RECORDS:]
        if count and record.level in self._counts:
            self._counts[record.level] += 1
        if self._passes_filter(record):
            self.view.appendHtml(record.to_html())

    def _selected_rank(self):
        rank = self.comboBox_Filter.currentData()
        return rank if rank is not None else 0

    def _passes_filter(self, record):
        if record.is_spacer:
            return self._selected_rank() == 0
        return LEVEL_RANK.get(record.level, 1) >= self._selected_rank()

    def _visible_records(self):
        return [r for r in self._records if self._passes_filter(r)]

    def _rerender(self):
        self.view.clear()
        for record in self._visible_records():
            self.view.appendHtml(record.to_html())
        self._scroll_to_end_if_following(force=True)

    def _scroll_to_end_if_following(self, force=False):
        if not (force or self.checkBox_AutoScroll.isChecked()):
            return
        scrollbar = self.view.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _update_counts(self):
        warnings = self._counts[LEVEL_WARNING]
        errors = self._counts[LEVEL_ERROR]
        parts = ["{0} lines".format(len(self._records))]
        if warnings:
            parts.append("{0} warning{1}".format(
                warnings, "" if warnings == 1 else "s"))
        if errors:
            parts.append("{0} error{1}".format(
                errors, "" if errors == 1 else "s"))
        self.label_counts.setText("· " + " · ".join(parts) if self._records else "")

        color = "#6a737d"
        if errors:
            color = LEVEL_COLOR[LEVEL_ERROR]
        elif warnings:
            color = LEVEL_COLOR[LEVEL_WARNING]
        self.label_counts.setStyleSheet(
            "color: {0}; background: transparent;".format(color))
