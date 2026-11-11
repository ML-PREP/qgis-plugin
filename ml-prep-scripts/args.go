package main

import (
	"flag"
	"fmt"
	"strings"
	"time"
)

// resolveDEMArg lets the DEM be given positionally for standalone runs:
//
//	zonal path/to/dem.tif -outputDir out
//
// The flag package stops parsing at the first non-flag argument, so any flags
// after the path are parsed here; otherwise they would be silently dropped.
func resolveDEMArg(fs *flag.FlagSet, demFlag string) (string, error) {
	if fs.NArg() == 0 {
		return demFlag, nil
	}
	if demFlag != "" {
		return "", fmt.Errorf("DEM given twice: -dem %q and %q", demFlag, fs.Arg(0))
	}

	dem := fs.Arg(0)
	if err := fs.Parse(fs.Args()[1:]); err != nil {
		return "", err
	}
	if fs.NArg() > 0 {
		return "", fmt.Errorf("unexpected arguments: %s", strings.Join(fs.Args(), " "))
	}
	return dem, nil
}

// reportTiming prints how long a stage took. The "[TIMING]" prefix matches
// none of the markers the plugin's progress parser looks for.
func reportTiming(stage string, start time.Time) {
	fmt.Printf("[TIMING] %s: %s\n", stage, time.Since(start).Round(time.Millisecond))
}
