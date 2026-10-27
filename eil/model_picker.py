"""The EIL tab's PINN model switcher.

Lists model/pinn_models.json in a combo box, remembers the last choice in
QSettings, and leaves models whose file is missing visible but unselectable,
so a newly configured model shows up as soon as its file is put in place.
"""

from typing import Optional

from qgis.PyQt.QtCore import QObject, Qt

from ..log_panel import LEVEL_DETAIL, LEVEL_ERROR
from .pinn_models import PinnModel, PinnModelConfigError, choose, load_catalog

SETTINGS_KEY = "mlprep/eil/pinn_model"
COLOR_ERROR = "#c0392b"
COLOR_NOTE = "#555555"


class PinnModelPicker(QObject):
    def __init__(self, combo, description_label, config_path, settings, log, parent=None):
        super().__init__(parent)
        self._combo = combo
        self._label = description_label
        self._settings = settings
        self._log = log
        self._catalog = None

        self._label.setWordWrap(True)
        try:
            self._catalog = load_catalog(config_path)
        except PinnModelConfigError as exc:
            self._show_config_error(str(exc))
            return

        self._populate()
        self._combo.currentIndexChanged.connect(self._on_changed)
        self._on_changed(self._combo.currentIndex(), remember=False)

    def selected(self) -> Optional[PinnModel]:
        """The chosen model, or None when none can be run."""
        if self._catalog is None:
            return None
        model = self._catalog.find(self._combo.currentData())
        return model if model is not None and model.is_available else None

    def _populate(self):
        combo = self._combo
        combo.blockSignals(True)
        combo.clear()
        for model in self._catalog.models:
            combo.addItem(model.name, model.id)
            if not model.is_available:
                item = combo.model().item(combo.count() - 1)
                item.setEnabled(False)
                missing = "No fold models in" if model.is_grid else "Model file not found:"
                item.setToolTip(f"{missing}\n{model.path}")

        preselected = choose(self._catalog, self._settings.value(SETTINGS_KEY))
        if preselected is not None:
            combo.setCurrentIndex(combo.findData(preselected.id))
        combo.blockSignals(False)

    def _on_changed(self, _index, remember=True):
        model = self.selected()
        if model is None:
            self._show_note(
                "No model file found. Put a model in place or edit "
                "model/pinn_models.json.",
                COLOR_ERROR,
            )
            return
        self._show_note(model.description or model.path, COLOR_NOTE)
        self._combo.setToolTip(model.path)
        if remember:
            self._settings.setValue(SETTINGS_KEY, model.id)
            self._log(f"[Model] Selected PINN model: {model.name}", LEVEL_DETAIL)

    def _show_config_error(self, message):
        self._combo.clear()
        self._combo.addItem("No models configured")
        self._combo.setEnabled(False)
        self._show_note(message, COLOR_ERROR)
        self._log(f"[ERROR] {message}", LEVEL_ERROR)

    def _show_note(self, text, color):
        self._label.setText(text)
        self._label.setStyleSheet(f"color: {color}; background: transparent;")
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
