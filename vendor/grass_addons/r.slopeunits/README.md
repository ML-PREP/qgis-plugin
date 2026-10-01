# r.slopeunits (vendored)

Source: <https://github.com/OSGeo/grass-addons> — `src/raster/r.slopeunits`
Authors: Ivan Marchesini, Massimiliano Alvioli, Markus Metz (refactoring)
Copyright: (C) 2004-2024 by the GRASS Development Team
License: GPL v2 or later (see the COPYING file distributed with GRASS)

## Why these files are bundled

The EIL slope-unit stage calls `r.slopeunits.create`. The normal installation
route is:

    g.extension extension=r.slopeunits

On Windows that downloads a precompiled archive from `wingrass.fsv.cvut.cz`,
which fails on networks that cannot resolve or reach that host:

    ERROR: Cannot open URL <http://wingrass.fsv.cvut.cz/grass84/addons/
    grass-8.4.2/r.slopeunits.zip>: <urlopen error [Errno 11001] getaddrinfo failed>

All four modules are pure Python, so nothing needs compiling and a copy into
the GRASS addon directory is a complete installation. Bundling them removes the
network dependency entirely.

## Updating

Refresh from upstream when GRASS addons change:

    BASE=https://raw.githubusercontent.com/OSGeo/grass-addons/grass8/src/raster/r.slopeunits
    for m in r.slopeunits.create r.slopeunits.clean r.slopeunits.metrics r.slopeunits.optimize; do
      curl -sL -o "$m.py" "$BASE/$m/$m.py"
    done
