"""Tests for cross-platform executable and environment resolution.

platform_utils imports QGIS lazily, so these run outside a QGIS session.
"""

import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_utils as pu  # noqa: E402


def make_stub(directory, name):
    path = os.path.join(str(directory), name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("stub")
    os.chmod(path, 0o755)
    return path


class TestFindExe:
    def test_finds_tool_in_an_extra_directory(self, tmp_path):
        # Arrange
        want = make_stub(tmp_path, "pitremove" + pu.EXE_SUFFIX)

        # Act
        got = pu.find_exe("pitremove", extra_dirs=[str(tmp_path)])

        # Assert
        assert got == want

    def test_returns_none_when_tool_is_absent(self, tmp_path):
        # Act
        got = pu.find_exe("mlprep-nonexistent-tool", extra_dirs=[str(tmp_path)])

        # Assert — None means "not installed", never a bare command name.
        assert got is None

    def test_extra_directories_take_precedence_over_search_table(self, tmp_path):
        # Arrange
        want = make_stub(tmp_path, "dinfflowdir" + pu.EXE_SUFFIX)

        # Act
        got = pu.find_exe("dinfflowdir", extra_dirs=[str(tmp_path)])

        # Assert
        assert got == want

    def test_expands_glob_patterns_in_extra_directories(self, tmp_path):
        # Arrange
        versioned = tmp_path / "GRASS GIS 8.4"
        versioned.mkdir()
        want = make_stub(versioned, "grass" + pu.EXE_SUFFIX)

        # Act
        got = pu.find_exe("grass", extra_dirs=[str(tmp_path / "GRASS GIS *")])

        # Assert
        assert got == want

    def test_prefers_the_highest_versioned_directory(self, tmp_path):
        # Arrange
        for version in ("8.3", "8.4"):
            (tmp_path / f"GRASS GIS {version}").mkdir()
            make_stub(tmp_path / f"GRASS GIS {version}", "grass" + pu.EXE_SUFFIX)

        # Act
        got = pu.find_exe("grass", extra_dirs=[str(tmp_path / "GRASS GIS *")])

        # Assert
        assert "8.4" in got


class TestOverrideDirectory:
    """Overrides are stored as directories, not executables.

    pitremove and dinfflowdir share the "taudem" settings key. Storing one
    executable path made the sibling resolve to the wrong binary, and the panel
    then reported the whole feature as ready.
    """

    def test_override_directory_is_searched_for_the_requested_tool(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        want = make_stub(tmp_path, "pitremove" + pu.EXE_SUFFIX)
        monkeypatch.setattr(pu, "_override_dir", lambda key: str(tmp_path))

        # Act
        got = pu.find_exe("pitremove", "taudem")

        # Assert
        assert got == want

    def test_sibling_sharing_a_key_does_not_resolve_to_the_wrong_binary(
        self, tmp_path, monkeypatch
    ):
        # Arrange -- only pitremove was supplied.
        make_stub(tmp_path, "pitremove" + pu.EXE_SUFFIX)
        monkeypatch.setattr(pu, "_override_dir", lambda key: str(tmp_path))
        monkeypatch.setattr(pu, "_SEARCH_DIRS", {pu._platform_key(): []})
        monkeypatch.setattr(pu.shutil, "which", lambda name: None)

        # Act
        got = pu.find_exe("dinfflowdir", "taudem")

        # Assert
        assert got is None

    def test_sibling_resolves_once_it_exists_in_the_same_directory(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        make_stub(tmp_path, "pitremove" + pu.EXE_SUFFIX)
        want = make_stub(tmp_path, "dinfflowdir" + pu.EXE_SUFFIX)
        monkeypatch.setattr(pu, "_override_dir", lambda key: str(tmp_path))

        # Act
        got = pu.find_exe("dinfflowdir", "taudem")

        # Assert
        assert got == want

    def test_a_stale_override_falls_back_to_normal_search(self, monkeypatch):
        # Arrange -- the directory the user picked no longer exists.
        monkeypatch.setattr(pu, "_override_dir", lambda key: None)

        # Act / Assert -- resolution proceeds without raising.
        pu.find_exe("gdalwarp", "gdalwarp")


class TestVenvPaths:
    def test_interpreter_lives_under_the_managed_envs_directory(self):
        # Act
        got = pu.venv_python("ls_detect_env")

        # Assert
        assert got.startswith(pu.ENVS_DIR)
        assert "ls_detect_env" in got

    def test_uses_the_platform_specific_layout(self):
        # Act
        got = pu.venv_python("venv")

        # Assert
        expected_dir = "Scripts" if pu.IS_WINDOWS else "bin"
        assert os.path.basename(os.path.dirname(got)) == expected_dir

    def test_bin_dir_is_the_parent_of_the_interpreter(self):
        # Act / Assert
        assert os.path.dirname(pu.venv_python("venv")) == pu.venv_bin_dir("venv")


class TestBasePython:
    """The interpreter used to build the managed virtualenvs.

    Under QGIS `sys.executable` is qgis-bin, not Python, so the old code fell
    through to a bare "python3" that depended entirely on PATH, and had no
    Linux branch at all.
    """

    def test_returns_an_existing_interpreter(self):
        # Act
        got = pu.base_python()

        # Assert
        assert os.path.isfile(got)

    def test_the_result_actually_runs(self):
        # Act
        completed = subprocess.run(
            [pu.base_python(), "-c", "import sys; print(sys.version_info[0])"],
            capture_output=True, **pu.subprocess_kwargs()
        )

        # Assert
        assert completed.returncode == 0
        assert completed.stdout.strip() == "3"

    def test_can_create_a_virtualenv(self, tmp_path):
        # Act -- this is the only thing SetupWorker uses it for.
        completed = subprocess.run(
            [pu.base_python(), "-m", "venv", str(tmp_path / "probe")],
            capture_output=True, **pu.subprocess_kwargs()
        )

        # Assert
        assert completed.returncode == 0, completed.stderr
        assert os.path.isfile(pu.venv_python("probe").replace(
            pu.ENVS_DIR, str(tmp_path)))

    def test_never_returns_a_bare_command_name(self):
        # Assert -- a bare name defers failure into a worker thread.
        assert os.sep in pu.base_python()

    def test_raises_with_guidance_when_nothing_is_found(self, monkeypatch):
        # Arrange
        monkeypatch.setattr(pu.os.path, "isfile", lambda p: False)
        monkeypatch.setattr(pu.shutil, "which", lambda name: None)

        # Act / Assert
        with pytest.raises(RuntimeError) as excinfo:
            pu.base_python()
        assert "Install Python 3" in str(excinfo.value)

    def test_result_meets_the_minimum_version(self):
        # Act
        completed = subprocess.run(
            [pu.base_python(), "-c",
             "import sys; print(sys.version_info[0], sys.version_info[1])"],
            capture_output=True, **pu.subprocess_kwargs()
        )

        # Assert
        version = tuple(int(part) for part in completed.stdout.split())
        assert version >= pu.MIN_PYTHON

    def test_skips_an_interpreter_older_than_the_minimum(self, monkeypatch):
        # Arrange -- QGIS launched from Finder sees only /usr/bin, whose
        # python3 is Apple's 3.9; the scripts need 3.10+ union syntax.
        versions = {"/usr/bin/python3": (3, 9), "/opt/homebrew/bin/python3.11": (3, 11)}
        monkeypatch.setattr(pu, "_python_candidates", lambda: list(versions))
        monkeypatch.setattr(pu, "_python_version", versions.get)

        # Act / Assert
        assert pu.base_python() == "/opt/homebrew/bin/python3.11"

    def test_names_the_rejected_version_when_only_old_ones_exist(
        self, monkeypatch
    ):
        # Arrange
        monkeypatch.setattr(pu, "_python_candidates", lambda: ["/usr/bin/python3"])
        monkeypatch.setattr(pu, "_python_version", lambda exe: (3, 9))

        # Act / Assert
        with pytest.raises(RuntimeError) as excinfo:
            pu.base_python()
        assert "/usr/bin/python3 (3.9)" in str(excinfo.value)
        assert "3.10" in str(excinfo.value)


class TestBundledBinary:
    def test_reports_the_hardware_arch_when_translated_by_rosetta(
        self, monkeypatch
    ):
        # Arrange -- QGIS ships x86_64 macOS builds, so its Python reports
        # "x86_64" on an Apple Silicon Mac and every arm64 binary looked absent.
        monkeypatch.setattr(pu, "IS_MACOS", True)
        monkeypatch.setattr(pu.sys, "platform", "darwin")
        monkeypatch.setattr(pu.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(pu, "_rosetta_translated", lambda: True)

        # Act / Assert
        assert pu.platform_tag() == "darwin_arm64"

    def test_trusts_the_reported_arch_when_not_translated(self, monkeypatch):
        # Arrange -- a real Intel Mac must still resolve to darwin_amd64.
        monkeypatch.setattr(pu, "IS_MACOS", True)
        monkeypatch.setattr(pu.sys, "platform", "darwin")
        monkeypatch.setattr(pu.platform, "machine", lambda: "x86_64")
        monkeypatch.setattr(pu, "_rosetta_translated", lambda: False)

        # Act / Assert
        assert pu.platform_tag() == "darwin_amd64"

    def test_platform_tag_matches_go_naming(self):
        # Act
        got = pu.platform_tag()

        # Assert
        goos, _, goarch = got.partition("_")
        assert goos in {"darwin", "linux", "windows"}
        assert goarch in {"amd64", "arm64"}

    def test_returns_none_when_no_binary_ships_for_this_host(self, tmp_path):
        # Act / Assert
        assert pu.bundled_binary(str(tmp_path), "zonal") is None

    def test_finds_a_binary_shipped_for_this_host(self, tmp_path):
        # Arrange
        target = tmp_path / "bin" / pu.platform_tag()
        target.mkdir(parents=True)
        want = make_stub(target, "zonal" + pu.EXE_SUFFIX)

        # Act
        got = pu.bundled_binary(str(tmp_path), "zonal")

        # Assert
        assert got == want

    def test_restores_an_execute_bit_the_zip_installer_dropped(self, tmp_path):
        # Arrange -- QGIS installs plugins with zipfile.extractall, which
        # discards Unix permission bits, so a shipped 0755 binary lands as 0644.
        target = tmp_path / "bin" / pu.platform_tag()
        target.mkdir(parents=True)
        want = make_stub(target, "zonal" + pu.EXE_SUFFIX)
        os.chmod(want, 0o644)

        # Act
        got = pu.bundled_binary(str(tmp_path), "zonal")

        # Assert
        assert got == want
        assert pu.IS_WINDOWS or os.access(want, os.X_OK)

    def test_reports_missing_when_the_execute_bit_cannot_be_restored(
        self, tmp_path, monkeypatch
    ):
        # Arrange -- a read-only install directory is the case that cannot be
        # repaired; reporting it as present would fail later inside a worker.
        if pu.IS_WINDOWS:
            pytest.skip("permission bits are not meaningful on Windows")
        target = tmp_path / "bin" / pu.platform_tag()
        target.mkdir(parents=True)
        stub = make_stub(target, "zonal")
        os.chmod(stub, 0o644)

        def refuse(*args, **kwargs):
            raise PermissionError("read-only file system")

        monkeypatch.setattr(os, "chmod", refuse)

        # Act
        got = pu.bundled_binary(str(tmp_path), "zonal")

        # Assert
        assert got is None


class TestFindTaudem:
    """pitremove and dinfflowdir ship in bin/<tag>/ beside the routing engine.

    Order: a directory the user picked, then the bundled copy, then a system
    install -- so an unrelated TauDEM on PATH never shadows the tested build.
    """

    @staticmethod
    def ship(plugin_dir, name):
        target = plugin_dir / "bin" / pu.platform_tag()
        target.mkdir(parents=True, exist_ok=True)
        return make_stub(target, name + pu.EXE_SUFFIX)

    @pytest.mark.parametrize("tool", pu.BUNDLED_TAUDEM_TOOLS)
    def test_bundled_copy_beats_a_system_install(
        self, tmp_path, monkeypatch, tool
    ):
        # Arrange
        plugin_dir = tmp_path / "plugin"
        want = self.ship(plugin_dir, tool)
        system = tmp_path / "system"
        system.mkdir()
        make_stub(system, tool + pu.EXE_SUFFIX)
        monkeypatch.setattr(pu, "_override_dir", lambda key: None)
        monkeypatch.setattr(pu.shutil, "which", lambda name: str(system / name))

        # Act
        got = pu.find_taudem(tool, str(plugin_dir))

        # Assert
        assert got == want

    def test_user_override_beats_the_bundled_copy(self, tmp_path, monkeypatch):
        # Arrange
        plugin_dir = tmp_path / "plugin"
        self.ship(plugin_dir, "pitremove")
        chosen = tmp_path / "chosen"
        chosen.mkdir()
        want = make_stub(chosen, "pitremove" + pu.EXE_SUFFIX)
        monkeypatch.setattr(pu, "_override_dir", lambda key: str(chosen))

        # Act
        got = pu.find_taudem("pitremove", str(plugin_dir))

        # Assert
        assert got == want

    def test_falls_back_to_a_system_install_when_nothing_is_bundled(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        system = tmp_path / "system"
        system.mkdir()
        want = make_stub(system, "dinfflowdir" + pu.EXE_SUFFIX)
        monkeypatch.setattr(pu, "_override_dir", lambda key: None)
        monkeypatch.setattr(pu.shutil, "which", lambda name: None)

        # Act
        got = pu.find_taudem(
            "dinfflowdir", str(tmp_path / "plugin"), extra_dirs=[str(system)]
        )

        # Assert
        assert got == want

    def test_dependency_report_counts_the_bundled_tools_as_found(
        self, tmp_path, monkeypatch
    ):
        # Arrange
        for tool in pu.BUNDLED_TAUDEM_TOOLS:
            self.ship(tmp_path, tool)
        monkeypatch.setattr(pu, "_override_dir", lambda key: None)

        # Act
        rows = [s for s in pu.check_dependencies(str(tmp_path))
                if s.spec.key == pu.TAUDEM_KEY]

        # Assert
        assert {r.spec.name for r in rows} == set(pu.BUNDLED_TAUDEM_TOOLS)
        assert all(r.found for r in rows)


class TestBundledEnvironment:
    """PROJ_DATA reaches only the programs shipped in bin/<tag>/.

    Each proj.db is tied to the PROJ release that wrote it; a GRASS or system
    GDAL handed the bundled one would reject it and lose all CRS support.
    """

    @staticmethod
    def ship_proj_db(plugin_dir):
        proj = plugin_dir / "bin" / pu.platform_tag() / "share" / "proj"
        proj.mkdir(parents=True)
        (proj / "proj.db").write_text("db", encoding="utf-8")
        return str(proj)

    def test_points_proj_data_at_the_shipped_database(self, tmp_path):
        # Arrange
        want = self.ship_proj_db(tmp_path)

        # Act
        env = pu.bundled_environment({"PATH": "/usr/bin"}, str(tmp_path))

        # Assert
        assert env["PROJ_DATA"] == want
        assert env["PATH"] == "/usr/bin"

    def test_overrides_a_proj_data_the_user_already_set(self, tmp_path):
        # Arrange -- the bundled libproj needs its own database regardless.
        want = self.ship_proj_db(tmp_path)

        # Act
        env = pu.bundled_environment({"PROJ_DATA": "/opt/proj"}, str(tmp_path))

        # Assert
        assert env["PROJ_DATA"] == want

    def test_leaves_the_shared_environment_untouched(self, tmp_path):
        # Arrange
        self.ship_proj_db(tmp_path)
        shared = {"PROJ_DATA": "/opt/proj"}

        # Act
        pu.bundled_environment(shared, str(tmp_path))

        # Assert -- GRASS and system GDAL keep their own database.
        assert shared == {"PROJ_DATA": "/opt/proj"}

    def test_changes_nothing_when_no_database_ships(self, tmp_path):
        # Act
        env = pu.bundled_environment({"PROJ_DATA": "/opt/proj"}, str(tmp_path))

        # Assert
        assert env == {"PROJ_DATA": "/opt/proj"}


class TestIsBundled:
    def test_true_for_a_binary_in_this_hosts_bin_dir(self, tmp_path):
        # Arrange
        path = TestFindTaudem.ship(tmp_path, "pitremove")

        # Act / Assert
        assert pu.is_bundled(str(tmp_path), path)

    def test_false_for_a_system_install(self, tmp_path):
        # Arrange
        system = tmp_path / "usr" / "bin"
        system.mkdir(parents=True)
        path = make_stub(system, "pitremove" + pu.EXE_SUFFIX)

        # Act / Assert
        assert not pu.is_bundled(str(tmp_path), path)

    def test_false_for_nothing_resolved(self, tmp_path):
        # Act / Assert
        assert not pu.is_bundled(str(tmp_path), None)


class TestSubprocessKwargs:
    def test_forces_utf8_decoding(self):
        # Act
        got = pu.subprocess_kwargs()

        # Assert — the Windows default is cp1252, which raises on non-ASCII.
        assert got["encoding"] == "utf-8"
        assert got["errors"] == "replace"

    def test_caller_can_override_defaults(self):
        # Act
        got = pu.subprocess_kwargs(encoding="latin-1")

        # Assert
        assert got["encoding"] == "latin-1"

    @pytest.mark.skipif(not pu.IS_WINDOWS, reason="Windows-only flag")
    def test_suppresses_the_console_window_on_windows(self):
        assert "creationflags" in pu.subprocess_kwargs()

    @pytest.mark.skipif(pu.IS_WINDOWS, reason="POSIX-only assertion")
    def test_does_not_set_windows_flags_on_posix(self):
        assert "creationflags" not in pu.subprocess_kwargs()


class TestNonAsciiHandling:
    """Regression cover for the cp1252 decoding failures (F12/F13).

    Python's text mode and subprocess text mode both default to the *locale*
    encoding, which is cp1252 on Windows. Philippine place names in roi_data and
    GDAL's own progress output both contain non-ASCII, so the default raised
    UnicodeDecodeError on Windows while working fine on macOS.
    """

    SAMPLE = "municipality,value\nDoña Remedios Trinidad,0.87\nPeñaranda,0.42\n"

    def test_utf8_read_round_trips(self, tmp_path):
        # Arrange -- a CSV like the workers produce.
        csv_path = tmp_path / "stats.csv"
        csv_path.write_text(self.SAMPLE, encoding="utf-8")

        # Act
        with open(csv_path, "r", encoding="utf-8") as handle:
            decoded = handle.read()

        # Assert
        assert decoded == self.SAMPLE
        assert "Doña Remedios Trinidad" in decoded

    def test_the_windows_default_silently_corrupts_names(self, tmp_path):
        # Arrange
        csv_path = tmp_path / "stats.csv"
        csv_path.write_text(self.SAMPLE, encoding="utf-8")

        # Act -- what Windows did before the fix.
        with open(csv_path, "r", encoding="cp1252") as handle:
            decoded = handle.read()

        # Assert -- no exception; the name is just wrong. Silent corruption in a
        # results table is worse than a crash, because nobody notices.
        assert "Doña" not in decoded
        assert "DoÃ±a" in decoded

    def test_the_windows_default_raises_on_some_names(self, tmp_path):
        # Arrange -- cp1252 leaves 0x81/0x8D/0x8F/0x90/0x9D undefined, and those
        # appear as UTF-8 continuation bytes (here U+0101 -> 0xC4 0x81).
        csv_path = tmp_path / "stats.csv"
        csv_path.write_text("municipality,value\nMāhina,0.5\n", encoding="utf-8")

        # Act / Assert
        with pytest.raises(UnicodeDecodeError):
            with open(csv_path, "r", encoding="cp1252") as handle:
                handle.read()

        # And the fix reads it correctly.
        with open(csv_path, "r", encoding="utf-8") as handle:
            assert "Māhina" in handle.read()

    def test_subprocess_output_decodes(self, tmp_path):
        # Arrange -- stand in for GDAL emitting a non-ASCII progress line.
        script = tmp_path / "emit.py"
        script.write_text(
            "import sys\n"
            "sys.stdout.reconfigure(encoding='utf-8')\n"
            "print('Processing Peñaranda… 100%')\n",
            encoding="utf-8",
        )

        # Act
        completed = subprocess.run(
            [pu.base_python(), str(script)],
            capture_output=True, **pu.subprocess_kwargs()
        )

        # Assert
        assert completed.returncode == 0
        assert "Peñaranda" in completed.stdout

    def test_undecodable_bytes_do_not_crash_the_worker(self, tmp_path):
        # Arrange -- a tool emitting bytes that are not valid UTF-8 must not
        # take down the QThread; errors="replace" degrades instead of raising.
        script = tmp_path / "garbage.py"
        script.write_text(
            "import sys\n"
            "sys.stdout.buffer.write(b'ok \\xff\\xfe done\\n')\n",
            encoding="utf-8",
        )

        # Act
        completed = subprocess.run(
            [pu.base_python(), str(script)],
            capture_output=True, **pu.subprocess_kwargs()
        )

        # Assert
        assert completed.returncode == 0
        assert "ok" in completed.stdout and "done" in completed.stdout

    def test_output_paths_with_non_ascii_are_usable(self, tmp_path):
        # Arrange -- a user choosing an output folder named in their language.
        out_dir = tmp_path / "Résultats_Peñaranda"
        out_dir.mkdir()
        target = out_dir / "stats.csv"

        # Act
        target.write_text(self.SAMPLE, encoding="utf-8")
        with open(target, "r", encoding="utf-8") as handle:
            decoded = handle.read()

        # Assert
        assert decoded == self.SAMPLE


class TestToolEnvironment:
    def test_drops_inherited_qgis_interpreter_variables(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("PYTHONHOME", "/qgis/python")
        monkeypatch.setenv("PYTHONPATH", "/qgis/python/site-packages")

        # Act
        env = pu.tool_environment()

        # Assert
        assert "PYTHONHOME" not in env
        assert "PYTHONPATH" not in env

    def test_does_not_mutate_the_process_environment(self, monkeypatch):
        # Arrange
        monkeypatch.setenv("PYTHONHOME", "/qgis/python")

        # Act
        pu.tool_environment()

        # Assert
        assert os.environ["PYTHONHOME"] == "/qgis/python"

    def test_exports_gdal_directory_when_gdal_is_installed(self):
        # Act
        env = pu.tool_environment()

        # Assert
        if pu.gdal_bin_dir():
            assert env[pu.ENV_GDAL_BIN] == pu.gdal_bin_dir()
            assert env[pu.ENV_GDAL_BIN] in env["PATH"]
        else:
            assert pu.ENV_GDAL_BIN not in env


class TestDependencyReport:
    def test_reports_every_tool_and_managed_environment(self):
        # Act
        report = pu.check_dependencies()

        # Assert
        labels = {s.spec.label for s in report}
        assert {t.label for t in pu.REQUIRED_TOOLS} <= labels
        assert {e.label for e in pu.PYTHON_ENVS} <= labels

    def test_managed_environments_are_reported(self):
        # A missing virtualenv breaks a pipeline as completely as a missing
        # executable, so it belongs in the same report.
        report = pu.check_dependencies()
        keys = {s.spec.key for s in report}
        assert {"env_ls", "env_eil"} <= keys

    def test_includes_bundled_engines_when_a_plugin_dir_is_given(self, tmp_path):
        # Act
        without = pu.check_dependencies()
        with_dir = pu.check_dependencies(str(tmp_path))

        # Assert -- both the zonal and the routing engine are added.
        assert len(with_dir) == len(without) + 2
        added = {s.spec.key for s in with_dir} - {s.spec.key for s in without}
        assert added == {pu.ENGINE_SPEC.key, pu.ROUTING_ENGINE_SPEC.key}

    def test_engines_are_reported_missing_when_not_shipped(self, tmp_path):
        # Act
        engines = [s for s in pu.check_dependencies(str(tmp_path))
                   if s.spec.key in {pu.ENGINE_SPEC.key, pu.ROUTING_ENGINE_SPEC.key}]

        # Assert
        assert len(engines) == 2
        assert not any(e.found for e in engines)

    def test_every_tool_declares_a_known_feature(self):
        # Act / Assert
        for status in pu.check_dependencies():
            assert status.spec.feature in pu.FEATURE_LABELS

    def test_every_tool_offers_an_install_hint(self):
        # Act / Assert -- a missing tool with no hint is a dead end for the user.
        for status in pu.check_dependencies():
            assert status.spec.hint

    def test_found_tracks_whether_a_path_was_resolved(self):
        # Arrange
        spec = pu.REQUIRED_TOOLS[0]

        # Act / Assert
        assert pu.ToolStatus(spec, "/somewhere/tool").found
        assert not pu.ToolStatus(spec, None).found

    def test_missing_can_be_narrowed_to_one_pipeline(self):
        # Act
        deposition = pu.missing_dependencies(feature=pu.FEATURE_DEPOSITION)

        # Assert
        assert all(s.spec.feature == pu.FEATURE_DEPOSITION for s in deposition)

    def test_landslide_detection_is_never_blocked_by_external_tools(self):
        # Landslide Detection uses none of the external programs -- only its
        # own virtualenv -- so no external tool may claim it as its feature.
        features = {spec.feature for spec in pu.REQUIRED_TOOLS}
        assert features == {pu.FEATURE_EIL, pu.FEATURE_DEPOSITION}
        assert pu.FEATURE_LANDSLIDE not in features

    def test_narrowed_missing_is_a_subset_of_all_missing(self):
        # Act
        every = {s.spec.label for s in pu.missing_dependencies()}
        eil = {s.spec.label for s in pu.missing_dependencies(feature=pu.FEATURE_EIL)}

        # Assert
        assert eil <= every


class TestPlausibleExe:
    def test_accepts_the_expected_basename(self):
        assert pu.is_plausible_exe("pitremove", "/opt/taudem/pitremove")

    def test_accepts_a_known_alias(self):
        # mpirun is a valid launcher for the mpiexec slot.
        assert pu.is_plausible_exe("mpiexec", "/usr/bin/mpirun")

    def test_ignores_the_file_extension(self):
        # Arrange -- GRASS ships as grass84.bat on Windows, grass84 elsewhere.
        path = os.path.join("some", "install", "grass84.bat")

        # Act / Assert
        assert pu.is_plausible_exe("grass", path)

    def test_rejects_an_unrelated_file(self):
        assert not pu.is_plausible_exe("pitremove", "/usr/bin/notepad")

    def test_is_case_insensitive(self):
        assert pu.is_plausible_exe("gdalwarp", "/usr/bin/GDALWARP.EXE")


class TestGrassAddonDetection:
    """r.slopeunits is a separately installed GRASS addon.

    Its absence surfaced only mid-pipeline as
    `OSError: Cannot find the executable r.slopeunits.create`, after tiles had
    already been reported as generated.
    """

    def test_addon_dir_is_platform_correct(self):
        # Act
        got = pu.grass_addon_dir()

        # Assert
        assert os.path.isabs(got)
        assert ("GRASS8" in got) if pu.IS_WINDOWS else (".grass8" in got)

    def test_user_installed_addon_is_found_without_running_grass(
        self, tmp_path, monkeypatch
    ):
        # Arrange -- the g.extension default location. POSIX installs the
        # script without its .py; only Windows keeps it.
        scripts = tmp_path / "scripts"
        scripts.mkdir()
        target = scripts / ("r.slopeunits.create" + (".py" if pu.IS_WINDOWS else ""))
        target.write_text("# stub\n", encoding="utf-8")
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))

        # Act / Assert -- no GRASS invocation needed for the common case.
        monkeypatch.setattr(pu, "_ask_grass_for_module",
                            lambda *a, **k: pytest.fail("should not run GRASS"))
        assert pu.grass_addon_installed("r.slopeunits.create") == str(target)

    def test_falls_back_to_asking_grass(self, tmp_path, monkeypatch):
        # Arrange -- nothing in the user addon dir. `g.extension -s` installs
        # system-wide, so a filesystem-only check would report it missing.
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pu, "_ask_grass_for_module",
                            lambda module, exe=None: f"{module} (installed in GRASS)")

        # Act / Assert
        assert pu.grass_addon_installed("r.slopeunits.create")

    def test_reports_missing_when_neither_finds_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pu, "grass_addon_dir", lambda: str(tmp_path))
        monkeypatch.setattr(pu, "_ask_grass_for_module", lambda *a, **k: None)
        assert pu.grass_addon_installed("r.slopeunits.create") is None

    def test_addon_is_reported_as_an_eil_dependency(self):
        # A missing addon must block EIL, not Landslide Detection.
        rows = [s for s in pu.check_dependencies() if s.spec.key == "grass_addon"]
        assert rows
        assert all(r.spec.feature == pu.FEATURE_EIL for r in rows)

    def test_module_cache_can_be_cleared(self, monkeypatch):
        # Arrange
        pu._MODULE_CACHE["probe.module"] = "cached"

        # Act
        pu.clear_module_cache()

        # Assert -- Re-check must not reuse a stale answer.
        assert "probe.module" not in pu._MODULE_CACHE
