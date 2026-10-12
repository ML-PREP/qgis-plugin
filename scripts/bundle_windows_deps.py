#!/usr/bin/env python3
"""Make Windows executables self-contained by copying their DLLs beside them.

Windows has no rpath: a process looks for DLLs in its own directory first. So
the GDAL/PROJ closure the CI build linked against is copied next to the .exe
files in bin/windows_amd64/, together with the proj.db that matches the
bundled PROJ (picked up through PROJ_DATA, see platform_utils.bundled_environment).

    python bundle_windows_deps.py --search-dir <conda>/Library/bin \
        --proj-dir <conda>/Library/share/proj --out bin/windows_amd64 \
        bin/windows_amd64/pitremove.exe ...
"""

import argparse
import os
import shutil
import sys
from collections import deque

# The host's mpiexec launches every tool. A bundled msmpi.dll that disagrees
# with it fails at startup -- the same reason libmpi is not bundled on Linux.
EXCLUDED = {"msmpi.dll"}
PROJ_FILES = ("proj.db", "proj.ini")


def pe_imports(path):
    """DLL names `path` imports, including delay-loaded ones."""
    import pefile

    pe = pefile.PE(path, fast_load=True)
    pe.parse_data_directories(directories=[
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
        pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
    ])
    entries = (getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])
               + getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []))
    return [entry.dll.decode("ascii", "replace") for entry in entries]


def dll_closure(roots, search_dir, imports_of=pe_imports):
    """Every DLL the roots need that lives in `search_dir`, sorted by name.

    DLLs not found there (KERNEL32, the UCRT, ...) are Windows' own and are
    left out. Names compare case-insensitively, as Windows does.
    """
    available = {name.lower(): name for name in os.listdir(search_dir)}
    found = {}
    queue = deque(roots)
    while queue:
        for dll in imports_of(queue.popleft()):
            key = dll.lower()
            if key in EXCLUDED or key in found or key not in available:
                continue
            found[key] = available[key]
            queue.append(os.path.join(search_dir, available[key]))
    return sorted(found.values(), key=str.lower)


def bundle(exes, search_dir, proj_dir, out_dir, imports_of=pe_imports):
    """Copy the DLL closure next to the exes and proj.db into share/proj."""
    dlls = dll_closure(exes, search_dir, imports_of)
    for dll in dlls:
        shutil.copy2(os.path.join(search_dir, dll), os.path.join(out_dir, dll))

    share = os.path.join(out_dir, "share", "proj")
    os.makedirs(share, exist_ok=True)
    for name in PROJ_FILES:
        shutil.copy2(os.path.join(proj_dir, name), os.path.join(share, name))
    return dlls


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--search-dir", required=True,
                        help="directory holding the build's DLLs")
    parser.add_argument("--proj-dir", required=True,
                        help="directory holding the matching proj.db")
    parser.add_argument("--out", required=True, help="bin/windows_amd64")
    parser.add_argument("exes", nargs="+")
    args = parser.parse_args(argv)

    for path in [args.search_dir, args.proj_dir, args.out, *args.exes]:
        if not os.path.exists(path):
            parser.error(f"not found: {path}")

    dlls = bundle(args.exes, args.search_dir, args.proj_dir, args.out)
    print(f"bundled {len(dlls)} DLLs + proj.db into {args.out}")
    for dll in dlls:
        print(f"  {dll}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
