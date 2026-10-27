"""The EIL tab's PINN model switcher. Needs QGIS's PyQt (skipped otherwise)."""

import importlib
import json
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

picker_module = importlib.import_module("mlprep_plugin.eil.model_picker")


class FakeSettings:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


@pytest.fixture(scope="module")
def qt_app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def config(tmp_path):
    (tmp_path / "quake.keras").write_bytes(b"x")
    (tmp_path / "rain.keras").write_bytes(b"x")
    path = tmp_path / "pinn_models.json"
    path.write_text(json.dumps({
        "default": "quake",
        "models": [
            {"id": "quake", "name": "Earthquake Induced Model", "path": "quake.keras",
             "description": "Earthquake only."},
            {"id": "rain", "name": "Rainfall-aware EIL Model", "path": "rain.keras",
             "description": "With rainfall."},
            {"id": "future", "name": "Future Model", "path": "not_here.keras"},
        ],
    }), encoding="utf-8")
    return str(path)


def make_picker(config_path, settings=None):
    combo = QtWidgets.QComboBox()
    label = QtWidgets.QLabel()
    logged = []
    picker = picker_module.PinnModelPicker(
        combo, label, config_path, settings or FakeSettings(),
        log=lambda message, level=None: logged.append(message),
    )
    return picker, combo, label, logged


class TestPinnModelPicker:
    def test_lists_every_configured_model_by_name(self, qt_app, config):
        # Act
        _, combo, _, _ = make_picker(config)

        # Assert
        assert [combo.itemText(i) for i in range(combo.count())] == [
            "Earthquake Induced Model", "Rainfall-aware EIL Model", "Future Model",
        ]

    def test_preselects_the_default_and_shows_its_description(self, qt_app, config):
        # Act
        picker, combo, label, _ = make_picker(config)

        # Assert
        assert picker.selected().id == "quake"
        assert combo.currentText() == "Earthquake Induced Model"
        assert "Earthquake only." in label.text()

    def test_a_model_without_its_file_cannot_be_chosen(self, qt_app, config):
        # Act
        _, combo, _, _ = make_picker(config)
        item = combo.model().item(2)

        # Assert
        assert not item.isEnabled()
        assert "not_here.keras" in item.toolTip()

    def test_switching_updates_selection_description_and_settings(self, qt_app, config):
        # Arrange
        settings = FakeSettings()
        picker, combo, label, _ = make_picker(config, settings)

        # Act
        combo.setCurrentIndex(1)

        # Assert
        assert picker.selected().id == "rain"
        assert "With rainfall." in label.text()
        assert settings.values[picker_module.SETTINGS_KEY] == "rain"

    def test_restores_the_last_choice(self, qt_app, config):
        # Act
        picker, _, _, _ = make_picker(
            config, FakeSettings({picker_module.SETTINGS_KEY: "rain"}))

        # Assert
        assert picker.selected().id == "rain"

    def test_broken_config_disables_the_picker_and_says_why(self, qt_app, tmp_path):
        # Arrange
        broken = tmp_path / "pinn_models.json"
        broken.write_text("{not json", encoding="utf-8")

        # Act
        picker, combo, label, logged = make_picker(str(broken))

        # Assert
        assert picker.selected() is None
        assert not combo.isEnabled()
        assert "not valid JSON" in label.text()
        assert any("not valid JSON" in message for message in logged)
