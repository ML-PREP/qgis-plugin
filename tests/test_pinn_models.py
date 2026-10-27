"""The PINN model catalog (model/pinn_models.json) the EIL tab picks from."""

import importlib
import json
import os
import sys
import types

import pytest

PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_pkg = types.ModuleType("mlprep_plugin")
_pkg.__path__ = [PLUGIN_DIR]
sys.modules.setdefault("mlprep_plugin", _pkg)

pinn_models = importlib.import_module("mlprep_plugin.eil.pinn_models")
paths = importlib.import_module("mlprep_plugin.common.paths")


def write_config(directory, data):
    path = os.path.join(str(directory), "pinn_models.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(data if isinstance(data, str) else json.dumps(data))
    return path


def entry(model_id, name=None, path=None, **extra):
    return {"id": model_id, "name": name or model_id.title(),
            "path": path or f"{model_id}.keras", **extra}


class TestBundledConfig:
    def test_lists_both_eil_models_in_order(self):
        # Act
        catalog = pinn_models.load_catalog(paths.PINN_MODEL_CONFIG)

        # Assert
        assert [m.name for m in catalog.models] == [
            "Earthquake Induced Model",
            "Rainfall-aware EIL Model",
            "Grid Model v2-9 (25 m cells)",
        ]

    def test_the_grid_model_ships_all_five_folds(self):
        # Act
        model = pinn_models.load_catalog(paths.PINN_MODEL_CONFIG).find("grid-v2-9")

        # Assert
        assert model.is_grid and model.is_available
        assert model.cell_size_m == 25.0
        for fold in range(1, 6):
            assert os.path.isfile(os.path.join(model.path, f"fold-{fold}-model-v3.keras"))
            assert os.path.isfile(os.path.join(
                model.path, f"v1_cotabato_transforms_fold{fold}.json"))

    def test_defaults_to_the_model_used_before_the_switcher(self):
        # Act
        catalog = pinn_models.load_catalog(paths.PINN_MODEL_CONFIG)
        default = catalog.find(catalog.default_id)

        # Assert -- the earthquake model is the original my_model.keras.
        assert default.name == "Earthquake Induced Model"
        assert os.path.basename(default.path) == "my_model.keras"
        assert default.is_available


class TestLoadCatalog:
    def test_relative_paths_resolve_beside_the_config(self, tmp_path):
        # Arrange
        config = write_config(tmp_path, {"default": "a", "models": [entry("a")]})

        # Act
        model = pinn_models.load_catalog(config).find("a")

        # Assert
        assert model.path == os.path.join(str(tmp_path), "a.keras")

    def test_absolute_paths_are_kept(self, tmp_path):
        # Arrange
        elsewhere = str(tmp_path / "elsewhere" / "b.keras")
        config = write_config(
            tmp_path, {"default": "b", "models": [entry("b", path=elsewhere)]})

        # Act / Assert
        assert pinn_models.load_catalog(config).find("b").path == elsewhere

    def test_reports_whether_the_model_file_exists(self, tmp_path):
        # Arrange
        (tmp_path / "present.keras").write_bytes(b"x")
        config = write_config(tmp_path, {
            "default": "present",
            "models": [entry("present"), entry("absent")],
        })

        # Act
        catalog = pinn_models.load_catalog(config)

        # Assert
        assert catalog.find("present").is_available
        assert not catalog.find("absent").is_available

    def test_description_is_optional(self, tmp_path):
        # Arrange
        config = write_config(tmp_path, {
            "default": "a",
            "models": [entry("a"), entry("b", description="Uses rainfall.")],
        })

        # Act
        catalog = pinn_models.load_catalog(config)

        # Assert
        assert catalog.find("a").description == ""
        assert catalog.find("b").description == "Uses rainfall."

    def test_unknown_id_finds_nothing(self, tmp_path):
        config = write_config(tmp_path, {"default": "a", "models": [entry("a")]})
        assert pinn_models.load_catalog(config).find("zzz") is None

    def test_missing_config_names_the_file(self, tmp_path):
        missing = str(tmp_path / "pinn_models.json")
        with pytest.raises(pinn_models.PinnModelConfigError, match="pinn_models.json"):
            pinn_models.load_catalog(missing)

    @pytest.mark.parametrize("data,problem", [
        ("{not json", "JSON"),
        ({"default": "a"}, "models"),
        ({"default": "a", "models": []}, "models"),
        ({"default": "a", "models": [{"id": "a", "path": "a.keras"}]}, "name"),
        ({"default": "a", "models": [{"id": "a", "name": "A"}]}, "path"),
        ({"default": "a", "models": [entry("a"), entry("a")]}, "duplicate"),
        ({"default": "zzz", "models": [entry("a")]}, "default"),
        ({"default": "a", "models": [entry("a", kind="raster")]}, "kind"),
        ({"default": "a", "models": [entry("a", kind="grid")]}, "cell_size_m"),
        ({"default": "a", "models": [entry("a", kind="grid", cell_size_m=0)]},
         "cell_size_m"),
    ])
    def test_rejects_invalid_configs(self, tmp_path, data, problem):
        config = write_config(tmp_path, data)
        with pytest.raises(pinn_models.PinnModelConfigError, match=problem):
            pinn_models.load_catalog(config)


class TestGridModels:
    def test_models_are_slope_unit_unless_marked_grid(self, tmp_path):
        # Arrange
        config = write_config(tmp_path, {"default": "a", "models": [
            entry("a"), entry("g", path="g_dir", kind="grid", cell_size_m=25)]})

        # Act
        catalog = pinn_models.load_catalog(config)

        # Assert
        assert catalog.find("a").kind == pinn_models.KIND_SLOPE_UNIT
        assert not catalog.find("a").is_grid
        assert catalog.find("g").is_grid
        assert catalog.find("g").cell_size_m == 25.0

    def test_a_grid_model_is_available_once_its_folder_has_a_fold(self, tmp_path):
        # Arrange
        config = write_config(tmp_path, {"default": "g", "models": [
            entry("g", path="g_dir", kind="grid", cell_size_m=25)]})
        (tmp_path / "g_dir").mkdir()
        model = pinn_models.load_catalog(config).find("g")
        assert not model.is_available

        # Act
        (tmp_path / "g_dir" / "fold-1-model-v3.keras").write_bytes(b"x")

        # Assert
        assert model.is_available


class TestChoose:
    @pytest.fixture
    def catalog(self, tmp_path):
        for name in ("a", "b"):
            (tmp_path / f"{name}.keras").write_bytes(b"x")
        config = write_config(tmp_path, {
            "default": "b",
            "models": [entry("a"), entry("b"), entry("gone")],
        })
        return pinn_models.load_catalog(config)

    def test_keeps_a_saved_choice_that_still_exists(self, catalog):
        assert pinn_models.choose(catalog, "a").id == "a"

    def test_falls_back_to_the_default(self, catalog):
        assert pinn_models.choose(catalog, None).id == "b"

    def test_a_saved_choice_whose_file_vanished_falls_back(self, catalog):
        assert pinn_models.choose(catalog, "gone").id == "b"

    def test_falls_back_to_any_available_model(self, tmp_path):
        # Arrange -- the default's file is missing.
        (tmp_path / "a.keras").write_bytes(b"x")
        config = write_config(tmp_path, {
            "default": "missing", "models": [entry("missing"), entry("a")]})

        # Act / Assert
        assert pinn_models.choose(pinn_models.load_catalog(config), None).id == "a"

    def test_none_when_no_model_file_exists(self, tmp_path):
        config = write_config(tmp_path, {"default": "a", "models": [entry("a")]})
        assert pinn_models.choose(pinn_models.load_catalog(config), None) is None
