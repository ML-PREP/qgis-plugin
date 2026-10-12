"""Tests for the Windows DLL bundler used by CI (scripts/bundle_windows_deps.py).

The PE-parsing step is injected, so these run on any platform.
"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "scripts"),
)

import bundle_windows_deps as bwd  # noqa: E402


def make_dlls(directory, *names):
    for name in names:
        (directory / name).write_text("dll", encoding="utf-8")


class TestDllClosure:
    def test_follows_dependencies_of_dependencies(self, tmp_path):
        # Arrange
        make_dlls(tmp_path, "gdal.dll", "proj_9.dll", "sqlite3.dll")
        imports = {
            "pitremove.exe": ["gdal.dll", "KERNEL32.dll"],
            "gdal.dll": ["proj_9.dll"],
            "proj_9.dll": ["sqlite3.dll"],
        }

        # Act
        got = bwd.dll_closure(["pitremove.exe"], str(tmp_path),
                              lambda p: imports.get(os.path.basename(p), []))

        # Assert
        assert got == ["gdal.dll", "proj_9.dll", "sqlite3.dll"]

    def test_skips_system_dlls_not_in_the_search_dir(self, tmp_path):
        # Arrange -- KERNEL32 and the UCRT come from Windows itself.
        make_dlls(tmp_path, "gdal.dll")
        imports = {"a.exe": ["gdal.dll", "KERNEL32.dll", "ucrtbase.dll"]}

        # Act
        got = bwd.dll_closure(["a.exe"], str(tmp_path),
                              lambda p: imports.get(os.path.basename(p), []))

        # Assert
        assert got == ["gdal.dll"]

    def test_never_bundles_msmpi(self, tmp_path):
        # Arrange -- the host's mpiexec launches the tools; a bundled msmpi.dll
        # that disagrees with it breaks startup (same as libmpi on Linux).
        make_dlls(tmp_path, "msmpi.dll", "gdal.dll")
        imports = {"a.exe": ["msmpi.dll", "gdal.dll"]}

        # Act
        got = bwd.dll_closure(["a.exe"], str(tmp_path),
                              lambda p: imports.get(os.path.basename(p), []))

        # Assert
        assert got == ["gdal.dll"]

    def test_matches_names_case_insensitively(self, tmp_path):
        # Arrange -- PE import tables and file names disagree on case freely.
        make_dlls(tmp_path, "GDAL.dll")
        imports = {"a.exe": ["gdal.DLL"]}

        # Act
        got = bwd.dll_closure(["a.exe"], str(tmp_path),
                              lambda p: imports.get(os.path.basename(p), []))

        # Assert
        assert got == ["GDAL.dll"]

    def test_handles_dependency_cycles(self, tmp_path):
        # Arrange
        make_dlls(tmp_path, "a.dll", "b.dll")
        imports = {"x.exe": ["a.dll"], "a.dll": ["b.dll"], "b.dll": ["a.dll"]}

        # Act
        got = bwd.dll_closure(["x.exe"], str(tmp_path),
                              lambda p: imports.get(os.path.basename(p), []))

        # Assert
        assert got == ["a.dll", "b.dll"]


class TestBundle:
    def test_copies_dlls_beside_the_exes_and_proj_db_into_share(self, tmp_path):
        # Arrange
        search = tmp_path / "conda_bin"
        search.mkdir()
        make_dlls(search, "gdal.dll")
        proj = tmp_path / "conda_proj"
        proj.mkdir()
        (proj / "proj.db").write_text("db", encoding="utf-8")
        (proj / "proj.ini").write_text("ini", encoding="utf-8")
        out = tmp_path / "windows_amd64"
        out.mkdir()
        exe = out / "pitremove.exe"
        exe.write_text("exe", encoding="utf-8")

        # Act
        bwd.bundle([str(exe)], str(search), str(proj), str(out),
                   imports_of=lambda p: ["gdal.dll"])

        # Assert -- Windows has no rpath: DLLs must sit next to the exe.
        assert (out / "gdal.dll").is_file()
        assert (out / "share" / "proj" / "proj.db").is_file()
        assert (out / "share" / "proj" / "proj.ini").is_file()
