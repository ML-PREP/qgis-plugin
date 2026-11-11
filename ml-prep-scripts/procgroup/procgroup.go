// Package procgroup makes a cancelled exec.Cmd take its whole process tree
// down with it.
//
// exec.CommandContext on its own only kills the direct child. GRASS is a
// launcher: grass -> python -> r.slopeunits.create -> r.patch/r.mapcalc. When
// only the launcher died, the modules beneath it were reparented to init and
// kept a core each busy at 100% long after the plugin's Cancel button was hit.
package procgroup

import "time"

// killGrace is how long a cancelled tree gets to exit on a polite signal
// before it is force-killed.
const killGrace = 3 * time.Second

// waitDelay bounds how long cmd.Wait blocks after cancellation, so a
// descendant holding stdout/stderr open cannot hang the pipeline.
const waitDelay = killGrace + 2*time.Second
