package main

import (
	"context"
	_ "embed"
	"fmt"
	"path/filepath"
	"strings"
	"sync"
)

// ── Phase 2: Zonal Statistics ───────────────────────────────────────────────
//
// The slope units are burned once into a label raster (Zones.tif) on the DEM
// grid; each value raster is then warped onto that grid and reduced per unit
// with numpy (pyscripts/zonal_grid.py). This replaced a per-polygon
// rasterstats pass that took ~90 min for 335k units and left units smaller
// than a coarse pixel with no value at all.

//go:embed pyscripts/zonal_grid.py
var zonalGridScript string

const zonesFileName = "Zones.tif"

// Rasters reduced by something other than a bilinear mean, keyed by file base
// name. "type" is the name the QGIS plugin stages the soil-type raster under.
var statRules = map[string][2]string{
	"type":            {"majority", "nearest"},
	"SoilType":        {"majority", "nearest"},
	"LULC":            {"majority", "nearest"},
	"DistanceToFault": {"min", "bilinear"},
	"DistanceToRoad":  {"min", "bilinear"},
	"DistanceToRiver": {"min", "bilinear"},
}

// statRuleFor returns the statistic and resampling used for a value raster.
// Categorical rasters must not be interpolated between class codes.
func statRuleFor(baseName string) (rule, resample string) {
	if r, ok := statRules[baseName]; ok {
		return r[0], r[1]
	}
	return "mean", "bilinear"
}

// buildZoneRaster burns the slope units into Zones.tif on the finest of the
// given grids (the DEM when Phase 1 ran). Returns the raster's path.
func buildZoneRaster(ctx context.Context, unitsGpkg, outputDir string, grids []string) (string, error) {
	zones := filepath.Join(outputDir, zonesFileName)
	args := append([]string{"zones", unitsGpkg, zones}, grids...)
	if _, err := runPythonJSON(ctx, zonalGridScript, "Zone Raster", args...); err != nil {
		return "", err
	}
	return zones, nil
}

// processRaster writes <name>_zonal.npz (row_id, values) for one value raster.
func processRaster(ctx context.Context, rasterPath, zonesTif, outputDir string, wg *sync.WaitGroup, sem chan struct{}) {
	defer wg.Done()
	defer func() { <-sem }()

	baseName := strings.TrimSuffix(filepath.Base(rasterPath), ".tif")
	rule, resample := statRuleFor(baseName)

	outPath := filepath.Join(outputDir, fmt.Sprintf("%s_zonal.npz", baseName))
	_, err := runPythonJSON(ctx, zonalGridScript, "Zonal Stats",
		"stats", zonesTif, rasterPath, outPath, noDataValue, rule, resample)
	if err != nil {
		fmt.Printf("❌ Zonal Error on %s: %v\n", baseName, err)
		return
	}

	fmt.Printf("[RASTER_DONE] %s\n", baseName)
}
