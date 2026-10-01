"""Preflight panel listing the external programs the plugin depends on.

The plugin shells out to GRASS, GDAL, TauDEM and MPI. Without this panel a
missing program is only discovered at the moment it is needed -- typically
several minutes into a run -- and one failure at a time. The panel resolves
everything up front, says how to install what is absent, and lets the user
point at a tool that lives somewhere the search table does not know about.

Tools are grouped by pipeline, so a missing GRASS blocks EIL Hazard Mapping
without touching Landslide Detection, which needs none of these.
"""

import os

from qgis.PyQt import QtWidgets
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QBrush, QColor

from . import grass_addons
from . import platform_utils as pu

COLOR_OK = "#1a7f37"
COLOR_MISSING = "#c0392b"
ROW_HEIGHT = 34

COL_STATUS, COL_COMPONENT, COL_FEATURE, COL_LOCATION, COL_ACTION = range(5)
HEADERS = ["", "Component", "Required for", "Location / how to install", ""]


class DependencyPanel(QtWidgets.QWidget):
    """Table of external dependencies with per-tool overrides."""

    statusChanged = pyqtSignal()
    setupRequested = pyqtSignal()

    def __init__(self, plugin_dir, parent=None):
        super().__init__(parent)
        self.plugin_dir = plugin_dir
        self._statuses = []
        self._build_ui()
        self.refresh()

    # -- construction --------------------------------------------------------

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        heading = QtWidgets.QLabel("External Dependencies")
        heading.setStyleSheet("font-size: 15px; font-weight: bold; color: black;")
        layout.addWidget(heading)

        blurb = QtWidgets.QLabel(
            "These programs are installed separately from the plugin. Anything "
            "marked ✗ must be installed before the pipeline that needs it "
            "can run. If a program is already installed somewhere unusual, use "
            "Browse… to point at it — the location is remembered."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #444444;")
        layout.addWidget(blurb)

        self.table = QtWidgets.QTableWidget(0, len(HEADERS), self)
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.Fixed
        )
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_STATUS, QtWidgets.QHeaderView.Fixed)
        header.setSectionResizeMode(COL_COMPONENT, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_FEATURE, QtWidgets.QHeaderView.ResizeToContents)
        header.setSectionResizeMode(COL_LOCATION, QtWidgets.QHeaderView.Stretch)
        header.setSectionResizeMode(COL_ACTION, QtWidgets.QHeaderView.ResizeToContents)
        self.table.setColumnWidth(COL_STATUS, 30)
        layout.addWidget(self.table, stretch=1)

        footer = QtWidgets.QHBoxLayout()
        self.summary = QtWidgets.QLabel()
        self.summary.setWordWrap(True)
        footer.addWidget(self.summary, stretch=1)

        # Environments are created automatically on first run, but a failed or
        # partial setup must be retryable without reinstalling the plugin.
        self.button_setup = QtWidgets.QPushButton("Set up environments")
        self.button_setup.setToolTip(
            "Create or repair the Python environments under ~/.mlprep_envs"
        )
        self.button_setup.clicked.connect(self.setupRequested.emit)
        footer.addWidget(self.button_setup)

        self.button_recheck = QtWidgets.QPushButton("Re-check")
        self.button_recheck.setToolTip(
            "Search again after installing something. No QGIS restart needed."
        )
        self.button_recheck.clicked.connect(self.refresh)
        footer.addWidget(self.button_recheck)
        layout.addLayout(footer)

    # -- state ---------------------------------------------------------------

    def refresh(self):
        """Re-resolve every dependency and redraw the table."""
        # GRASS module lookups are cached because each costs a second; a
        # deliberate re-check must not reuse them.
        pu.clear_module_cache()
        self._statuses = pu.check_dependencies(self.plugin_dir)
        self._populate()
        self._update_summary()
        self.statusChanged.emit()

    def statuses(self):
        return list(self._statuses)

    def missing_for(self, feature=None):
        """Unresolved tools, optionally narrowed to one pipeline."""
        return [
            status for status in self._statuses
            if not status.found
            and (feature is None or status.spec.feature == feature)
        ]

    def is_ready(self, feature=None):
        return not self.missing_for(feature)

    def blocking_message(self, feature):
        """User-facing explanation of why `feature` cannot run, or None."""
        missing = self.missing_for(feature)
        if not missing:
            return None

        lines = [
            f"{pu.FEATURE_LABELS.get(feature, feature)} needs "
            f"{len(missing)} program(s) that are not installed:\n"
        ]
        for status in missing:
            lines.append(f"  ✗  {status.spec.label}")
            if status.spec.hint:
                lines.append(f"      {status.spec.hint}")
        lines.append(
            "\nSee the Dependencies tab to install them, or to point at a copy "
            "already on this machine."
        )
        return "\n".join(lines)

    # -- rendering -----------------------------------------------------------

    def _populate(self):
        self.table.setRowCount(len(self._statuses))
        for row, status in enumerate(self._statuses):
            self._render_row(row, status)

    def _render_row(self, row, status):
        spec = status.spec

        mark = QtWidgets.QTableWidgetItem("✓" if status.found else "✗")
        mark.setTextAlignment(Qt.AlignCenter)
        mark.setToolTip("Found" if status.found else "Not found")
        self.table.setItem(row, COL_STATUS, mark)
        self._paint(mark, COLOR_OK if status.found else COLOR_MISSING, bold=True)

        component = QtWidgets.QTableWidgetItem(spec.label)
        self.table.setItem(row, COL_COMPONENT, component)

        feature = QtWidgets.QTableWidgetItem(
            pu.FEATURE_LABELS.get(spec.feature, spec.feature)
        )
        self.table.setItem(row, COL_FEATURE, feature)

        location = QtWidgets.QTableWidgetItem(status.path or spec.hint)
        location.setToolTip(status.path or spec.hint)
        self.table.setItem(row, COL_LOCATION, location)
        if not status.found:
            self._paint(location, "#666666")

        action = self._action_widget(status)
        if action is not None:
            self.table.setCellWidget(row, COL_ACTION, action)
        else:
            self.table.removeCellWidget(row, COL_ACTION)

    def _action_widget(self, status):
        # Bundled engines and managed virtualenvs are not user-locatable:
        # they either shipped with the plugin or are created on first run.
        # A missing GRASS addon is installable from the copy bundled with the
        # plugin -- no network, which is the case that fails in practice.
        if status.spec.key == "grass_addon":
            if status.found:
                return None
            button = QtWidgets.QPushButton("Install")
            button.setFixedHeight(ROW_HEIGHT - 10)
            button.setToolTip(
                "Install the bundled copy into your GRASS addon directory"
            )
            button.clicked.connect(lambda _, s=status: self._install_addon(s))
            return button

        if status.spec.key in {pu.ENGINE_SPEC.key, pu.ROUTING_ENGINE_SPEC.key,
                               "env_ls", "env_eil",
                               "mod_numpy", "mod_matplotlib"}:
            return None

        button = QtWidgets.QPushButton("Browse…" if not status.found else "Change…")
        button.setFixedHeight(ROW_HEIGHT - 10)
        button.setToolTip(f"Select the {status.spec.name} executable manually")
        button.clicked.connect(lambda _, s=status: self._choose_override(s))
        return button

    @staticmethod
    def _paint(item, color, bold=False):
        item.setForeground(QBrush(QColor(color)))
        if bold:
            font = item.font()
            font.setBold(True)
            item.setFont(font)

    def _update_summary(self):
        missing = self.missing_for()
        envs_missing = [m for m in missing if m.spec.key in {"env_ls", "env_eil"}]
        self.button_setup.setEnabled(bool(envs_missing))

        if not missing:
            self.summary.setText("All dependencies found.")
            self.summary.setStyleSheet(f"color: {COLOR_OK}; font-weight: bold;")
            return

        blocked = sorted({
            pu.FEATURE_LABELS.get(s.spec.feature, s.spec.feature) for s in missing
        })
        self.summary.setText(
            f"{len(missing)} of {len(self._statuses)} dependencies missing. "
            f"Blocked: {', '.join(blocked)}."
        )
        self.summary.setStyleSheet(f"color: {COLOR_MISSING}; font-weight: bold;")

    # -- overrides -----------------------------------------------------------

    def _install_addon(self, status):
        """Install the bundled GRASS addon, then re-check."""
        QtWidgets.QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            installed = grass_addons.install_bundled_addon(self.plugin_dir)
        except RuntimeError as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.critical(self, "Addon install failed", str(exc))
            return
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()

        self.refresh()

        if self._is_found(status.spec):
            QtWidgets.QMessageBox.information(
                self, "Addon installed",
                "Installed into:\n"
                f"{pu.grass_addon_dir()}\n\n" + "\n".join(installed),
            )
        else:
            QtWidgets.QMessageBox.warning(
                self, "Addon installed but not detected",
                "The files were copied to:\n"
                f"{pu.grass_addon_dir()}\n\n"
                "GRASS still does not report the module. Check that "
                "GRASS_ADDON_PATH points at that directory.",
            )

    def _choose_override(self, status):
        spec = status.spec
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            f"Locate {spec.label}",
            os.path.dirname(status.path) if status.path else "",
            "Executables (*.exe *.bat *.cmd);;All files (*)" if pu.IS_WINDOWS
            else "All files (*)",
        )
        if not path:
            return

        if not pu.is_plausible_exe(spec.name, path):
            proceed = QtWidgets.QMessageBox.question(
                self,
                "Unexpected file name",
                f"{os.path.basename(path)} does not look like {spec.name}.\n\n"
                "Use it anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if proceed != QtWidgets.QMessageBox.Yes:
                return

        pu.set_exe_override(spec.key, path)
        self.refresh()

        # An override is stored as a directory, so a file whose name the search
        # does not recognise resolves to nothing. Say so rather than leaving the
        # user to wonder why the row is still marked missing.
        if not self._is_found(spec):
            QtWidgets.QMessageBox.warning(
                self,
                "Still not found",
                f"{spec.label} was not found in\n{os.path.dirname(path)}\n\n"
                f"The plugin looks for a file named {spec.name}"
                f"{pu.EXE_SUFFIX or ''} in that folder.",
            )

    def _is_found(self, spec):
        return any(
            s.found for s in self._statuses
            if s.spec.name == spec.name and s.spec.key == spec.key
        )
