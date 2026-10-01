#!/usr/bin/env python3
"""Make a macOS binary self-contained by bundling its non-system dylibs.

The Step 4 engine linked against Homebrew's GDAL by absolute path, so it ran
only on a machine with that exact major version installed (libgdal.39 vs the
libgdal.38 on this host -- the shipped binary silently fails to launch here).

This copies every non-system dependency next to the binary, rewrites all the
install names to @rpath, and points the binary at @loader_path/lib.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys

# Libraries guaranteed present on macOS; bundling them would be wrong.
SYSTEM_PREFIXES = ("/usr/lib/", "/System/")

DEP_RE = re.compile(r"^\s+(\S+\.dylib|\S+/[^/\s]+)\s+\(compatibility", re.M)


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def dependencies(binary):
    """Non-system dylibs `binary` links against."""
    out = run("otool", "-L", binary)
    deps = []
    for line in out.splitlines()[1:]:
        path = line.strip().split(" (")[0]
        if not path or path.startswith(SYSTEM_PREFIXES):
            continue
        if path.startswith("@"):          # already relocated
            continue
        deps.append(path)
    return deps


def collect(binary, seen=None):
    """Every non-system dylib reachable from `binary`, transitively."""
    seen = seen if seen is not None else {}
    for dep in dependencies(binary):
        real = os.path.realpath(dep)
        if not os.path.isfile(real) or real in seen:
            continue
        seen[real] = os.path.basename(dep)
        collect(real, seen)
    return seen


def bundle(binary, libdir):
    os.makedirs(libdir, exist_ok=True)
    libs = collect(binary)
    print(f"  {len(libs)} dependencies to bundle")

    # Copy first so the rewrite pass can see every file it must point at.
    staged = {}
    for source, name in libs.items():
        target = os.path.join(libdir, name)
        if not os.path.exists(target):
            shutil.copy2(source, target)
            os.chmod(target, 0o755)
        staged[source] = target

    by_basename = {name: f"@rpath/{name}" for name in libs.values()}

    def rewrite(path, is_main):
        if not is_main:
            run("install_name_tool", "-id",
                f"@rpath/{os.path.basename(path)}", path)
        for dep in dependencies(path):
            new = by_basename.get(os.path.basename(dep))
            if new:
                run("install_name_tool", "-change", dep, new, path)

    for target in staged.values():
        rewrite(target, is_main=False)
    rewrite(binary, is_main=True)

    # @loader_path is relative to the binary, so the whole tree stays portable.
    existing = run("otool", "-l", binary)
    if "@loader_path/lib" not in existing:
        run("install_name_tool", "-add_rpath", "@loader_path/lib", binary)

    # Any modification invalidates the signature; arm64 refuses unsigned code.
    for target in list(staged.values()) + [binary]:
        subprocess.run(["codesign", "--force", "--sign", "-", target],
                       capture_output=True)

    return len(libs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary")
    parser.add_argument("--libdir", default=None)
    args = parser.parse_args()

    binary = os.path.abspath(args.binary)
    libdir = args.libdir or os.path.join(os.path.dirname(binary), "lib")

    print(f"Bundling {os.path.basename(binary)}")
    count = bundle(binary, libdir)

    print("\nResulting linkage:")
    print(run("otool", "-L", binary))
    remaining = [d for d in dependencies(binary)]
    if remaining:
        print(f"error: still linked to absolute paths: {remaining}", file=sys.stderr)
        return 1
    print(f"Self-contained: {count} dylibs in {libdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
