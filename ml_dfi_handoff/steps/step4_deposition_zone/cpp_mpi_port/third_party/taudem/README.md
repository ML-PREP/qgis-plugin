# Vendored TauDEM Compatibility Sources

This directory contains the small TauDEM source subset required to compile the
Step 4 C++/MPI executable:

- raster partition and neighborhood templates
- GeoTIFF I/O helpers
- shared utility code

The subset was copied from the TauDEM sources previously used to build Step 4
so the workspace no longer depends on a separate TauDEM checkout on one
developer's desktop. The retained `license.txt` applies to these files.

## pitremove and dinfflowdir

`flood.cpp`, `PitRemovemn.cpp`, `dinf.cpp`, `DinfFlowDirmn.cpp`, `Node.cpp`,
`Node.h`, `flood.h` and `tardemlib.h` are copied unmodified from upstream
TauDEM **v5.5.0** (https://github.com/dtarb/TauDEM/tree/v5.5.0/src). They
build the two TauDEM tools the plugin runs before Step 4 when CMake is given
`-DMLPREP_BUILD_TAUDEM_TOOLS=ON` (the Linux Docker build does this). The
shared subset above is byte-identical to v5.5.0 apart from the fix below.

One portability-only correction was made in `src/linearpart.h`: a diagnostic
`printf` uses a format compatible with the unsigned 64-bit partition index
type. No routing or raster-processing logic was changed.
