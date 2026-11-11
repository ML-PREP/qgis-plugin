// ml-prep-scripts/main.go
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"log"
	"os"
	"os/exec"
	"crypto/sha1"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"zonal_parallel/procgroup"
	"zonal_parallel/toolpath"
)

const noDataValue = "-9999"

var pythonPath string

// ── Python Scripts ──────────────────────────────────────────────────────────

var tileDEMScript = `
import sys, json, os
import rasterio
from rasterio.windows import Window
import rasterio.windows

dem_path   = sys.argv[1]
tiles_dir  = sys.argv[2]
n_cols     = int(sys.argv[3])
n_rows     = int(sys.argv[4])
overlap_px = int(sys.argv[5])

os.makedirs(tiles_dir, exist_ok=True)

with rasterio.open(dem_path) as src:
    W, H      = src.width, src.height
    transform = src.transform
    crs       = src.crs
    nodata    = src.nodata
    dtype     = src.dtypes[0]

    tile_w = W // n_cols
    tile_h = H // n_rows

    tiles = []
    for row in range(n_rows):
        for col in range(n_cols):
            x0 = max(0, col * tile_w - overlap_px)
            y0 = max(0, row * tile_h - overlap_px)
            x1 = min(W, (col + 1) * tile_w + overlap_px) if col < n_cols - 1 else W
            y1 = min(H, (row + 1) * tile_h + overlap_px) if row < n_rows - 1 else H

            cx0 = col * tile_w
            cy0 = row * tile_h
            cx1 = min(W, (col + 1) * tile_w)
            cy1 = min(H, (row + 1) * tile_h)

            window = Window(x0, y0, x1 - x0, y1 - y0)
            tile_transform = rasterio.windows.transform(window, transform)
            tile_path = os.path.join(tiles_dir, f"tile_{row}_{col}.tif")
            data = src.read(1, window=window)

            with rasterio.open(tile_path, "w", driver="GTiff", height=y1 - y0, width=x1 - x0,
                count=1, dtype=dtype, crs=crs, transform=tile_transform, nodata=nodata) as dst:
                dst.write(data, 1)

            # We pass the local core boundaries so Python knows exactly who owns what
            tiles.append({
                "path"     : tile_path,
                "global_x0": cx0,
                "global_y0": cy0,
                "global_x1": cx1,
                "global_y1": cy1,
                "local_x0" : cx0 - x0,
                "local_y0" : cy0 - y0,
                "local_x1" : cx1 - x0,
                "local_y1" : cy1 - y0,
            })

print(json.dumps({"tiles": tiles}))
`

var mergeSlopeUnitsScript = `
import sys, json, os
import numpy as np
import rasterio
from rasterio.merge import merge
import scipy.ndimage as ndi

dem_path    = sys.argv[1]
tiles_json  = sys.argv[2]
output_path = sys.argv[3]

tiles = json.loads(tiles_json)

id_offset = 0
tmp_files = []

for tile in tiles:
    slu_path = tile.get("slu_path")
    if not slu_path or not os.path.exists(slu_path): continue
    
    lx0, ly0 = tile["local_x0"], tile["local_y0"]
    lx1, ly1 = tile["local_x1"], tile["local_y1"]
    
    with rasterio.open(slu_path) as src:
        data = src.read(1)
        meta = src.meta.copy()
        nodata_val = src.nodata if src.nodata is not None else -9999
        
    valid_mask = data > 0
    if not valid_mask.any():
        continue
        
    # --- THE CENTROID OWNERSHIP ALGORITHM ---
    # 1. Get all unique polygon IDs in this tile
    unique_ids = np.unique(data[valid_mask])
    
    # 2. Calculate the exact center of mass for every polygon
    centers = ndi.center_of_mass(np.ones_like(data), data, unique_ids)
    
    # 3. Keep ONLY the polygons whose centroid falls inside this tile's CORE territory.
    keep_ids = set()
    for uid, (cy, cx) in zip(unique_ids, centers):
        if np.isnan(cy) or np.isnan(cx): continue
        if ly0 <= cy < ly1 and lx0 <= cx < lx1:
            keep_ids.add(uid)
            
    # 4. Erase all polygons that belong to a neighboring tile
    mask = ~np.isin(data, list(keep_ids))
    data[mask] = nodata_val
    
    # 5. Renumber the kept polygons so there are no duplicate IDs globally
    final_valid = data > 0
    if final_valid.any():
        final_ids = np.unique(data[final_valid])
        mapping = {old_id: new_id + id_offset for new_id, old_id in enumerate(final_ids, start=1)}
        
        palette = np.zeros(int(data.max()) + 1, dtype=np.int32)
        for old_id, new_id in mapping.items():
            palette[int(old_id)] = new_id
            
        data[final_valid] = palette[data[final_valid]]
        id_offset += len(final_ids)

    tmp_path = slu_path.replace(".tif", "_centroid_jagged.tif")
    with rasterio.open(tmp_path, "w", **meta) as dst:
        dst.write(data, 1)
    tmp_files.append(tmp_path)

if not tmp_files:
    print(json.dumps({"status": "error", "message": "No tiles generated valid data"}))
    sys.exit(1)

# Because ownership is strictly assigned by centroid, the jagged tiles will interlock 
# perfectly without leaving any straight lines or any gaps!
datasets = [rasterio.open(p) for p in tmp_files]
mosaic, out_tf = merge(datasets, method="first", nodata=-9999)

with rasterio.open(dem_path) as ref:
    out_meta = ref.meta.copy()
    
out_meta.update({
    "height": mosaic.shape[1], 
    "width": mosaic.shape[2], 
    "transform": out_tf, 
    "dtype": "int32", 
    "nodata": -9999
})

with rasterio.open(output_path, "w", **out_meta) as dst:
    dst.write(mosaic)

for ds in datasets: ds.close()
for p in tmp_files:
    try: os.remove(p)
    except: pass

print(json.dumps({"status": "ok", "output": output_path}))
`

var polygonizeScript = `
import sys, json, traceback
import rasterio
from rasterio.features import shapes, sieve
import geopandas as gpd
from shapely.geometry import shape

input_tif = sys.argv[1]
output_gpkg = sys.argv[2]
area_min = float(sys.argv[3])

try:
    with rasterio.open(input_tif) as src:
        image = src.read(1)
        mask = image > 0

        pixel_area = abs(src.transform.a * src.transform.e)
        sieve_size = max(1, int(area_min / pixel_area))

        # Melt away tiny artifact splinters
        sieved_image = sieve(image, size=sieve_size, connectivity=8, mask=mask)

        geoms = []
        su_ids = []
        for s, v in shapes(sieved_image, mask=(sieved_image > 0), transform=src.transform):
            geoms.append(shape(s))
            su_ids.append(int(v))

        crs = src.crs.to_wkt() if src.crs else None

    if not geoms:
        sys.stderr.write(
            "Polygonize produced no slope units. The merged raster held no "
            "cells above 0, or area_min sieved every polygon away.\n"
        )
        sys.exit(1)

    # Written via pyogrio (geopandas' default engine) -- deliberately NOT fiona,
    # which nothing in requirements_eil.txt installs any more.
    # row_id is the join key the zonal stage writes its values against.
    # su_id cannot serve: shapes() emits one polygon per connected region, so a
    # label split across a tile seam yields several polygons sharing one su_id.
    gdf = gpd.GeoDataFrame({"su_id": su_ids}, geometry=geoms, crs=crs)
    gdf.insert(0, "row_id", range(len(gdf)))
    gdf.to_file(output_gpkg, driver="GPKG")

    print(json.dumps({"status": "ok", "count": len(geoms)}))
except Exception as e:
    sys.stderr.write(f"Polygonize Python Error:\n{traceback.format_exc()}\n")
    sys.exit(1)
`

var mergeScript = `
import sys, os, glob
import numpy as np
import pandas as pd
import geopandas as gpd

zone_gpkg  = sys.argv[1]
output_dir = sys.argv[2]
final_out  = sys.argv[3]

master = gpd.read_file(zone_gpkg)
if "row_id" not in master.columns:
    sys.stderr.write("Slope-unit layer has no row_id column; re-run polygonize.\n")
    sys.exit(1)
master = master.set_index("row_id")

parts = sorted(glob.glob(os.path.join(output_dir, "*_zonal.npz")))
if not parts:
    sys.stderr.write("No zonal results found to merge.\n")
    sys.exit(1)

suffix = "_zonal.npz"
for path in parts:
    name = os.path.basename(path)[:-len(suffix)]

    with np.load(path) as data:
        column = pd.Series(data["values"], index=data["row_id"].astype("int64"))

    # Joined on row_id, not on row order.  The previous merge assigned
    # positionally (master[c] = temp[c]), so a short or reordered result turned
    # into silent NaNs rather than an error.
    if len(column) != len(master):
        sys.stderr.write(
            "%s: %d zonal rows vs %d slope units -- refusing to merge.\n"
            % (name, len(column), len(master))
        )
        sys.exit(1)

    aligned = column.reindex(master.index)
    if aligned.isna().all():
        sys.stderr.write("%s: no value survived the join (all NoData).\n" % name)
        sys.exit(1)

    master[name] = aligned

# row_id is an internal join key, not a model feature.
master = master.reset_index(drop=True)
master.to_file(final_out, driver="GPKG")

for path in parts:
    try:
        os.remove(path)
    except OSError:
        pass
`

// grassSlopeUnitsScript runs inside "grass --exec <python> <script>".
//
// It replaces a #!/bin/bash heredoc, which could not run on Windows. GRASS puts
// its own etc/python on PYTHONPATH for the child process, so grass.script is
// importable from any Python 3, and run_command raises on a non-zero exit --
// the equivalent of the old script's "set -e".
const grassSlopeUnitsScript = `
import sys

import grass.script as gs

# r.slopeunits is a separately installed addon. Without this check the failure
# surfaces as a bare OSError from deep inside run_command.
if not gs.find_program("r.slopeunits.create", "--help"):
    sys.exit(
        "r.slopeunits.create not found.\n"
        "Install the addon:  g.extension extension=r.slopeunits\n"
        "Or run: scripts/install_grass.sh --addon-only  (macOS/Linux)\n"
        "        scripts/install_grass_windows.bat /addon-only  (Windows)"
    )

dem_path, out_tif = sys.argv[1], sys.argv[2]
areamin, thresh, cvmin, rf, maxiteration = sys.argv[3:8]

gs.run_command(
    "r.in.gdal", flags="o",
    input=dem_path, output="dem", band=1, overwrite=True,
)
gs.run_command("g.region", raster="dem")
gs.run_command(
    "r.slopeunits.create",
    demmap="dem", slumap="slu",
    areamin=areamin, thresh=thresh, cvmin=cvmin,
    rf=rf, maxiteration=maxiteration,
    overwrite=True,
)
gs.run_command(
    "r.out.gdal", flags="c",
    input="slu", output=out_tif,
    format="GTiff", type="Int32",
    createopt="COMPRESS=LZW,TILED=YES",
    overwrite=True,
)
`

// ── Core Utilities ──────────────────────────────────────────────────────────

// newCommand builds every child process the pipeline runs. Binding it to ctx
// and its own process group means a cancelled run kills the child's whole
// tree, rather than orphaning the GRASS modules underneath it.
func newCommand(ctx context.Context, exe string, args ...string) *exec.Cmd {
	cmd := toolpath.Command(ctx, exe, args...)
	procgroup.Configure(cmd)
	return cmd
}

// acquire takes a worker slot, or reports false once ctx is cancelled so no
// further children are launched.
func acquire(ctx context.Context, sem chan struct{}) bool {
	select {
	case sem <- struct{}{}:
		return true
	case <-ctx.Done():
		return false
	}
}

func runPythonJSON(ctx context.Context, script, desc string, args ...string) ([]byte, error) {
	tmp, err := os.CreateTemp("", "*.py")
	if err != nil {
		return nil, err
	}
	defer os.Remove(tmp.Name())
	tmp.WriteString(script)
	tmp.Close()

	cmdArgs := append([]string{tmp.Name()}, args...)
	cmd := newCommand(ctx, pythonPath, cmdArgs...)
	var stderrBuf strings.Builder
	cmd.Stderr = io.MultiWriter(os.Stderr, &stderrBuf)

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("%s failed: %w\nStdout: %s\nStderr: %s", desc, err, string(out), stderrBuf.String())
	}
	return out, nil
}

// ── Phase 1: Parallel Slope Unit Generation ─────────────────────────────────

type SlopeUnitParams struct {
	AreaMin      int
	Thresh       int
	CvMin        float64
	Rf           int
	MaxIteration int
}

type TileInfo struct {
	Path     string `json:"path"`
	SluPath  string `json:"slu_path"`
	GlobalX0 int    `json:"global_x0"`
	GlobalY0 int    `json:"global_y0"`
	GlobalX1 int    `json:"global_x1"`
	GlobalY1 int    `json:"global_y1"`
	LocalX0  int    `json:"local_x0"`
	LocalY0  int    `json:"local_y0"`
	LocalX1  int    `json:"local_x1"`
	LocalY1  int    `json:"local_y1"`
}

func tileDEM(ctx context.Context, demPath, tilesDir string, nCols, nRows, overlapPx int) ([]TileInfo, error) {
	out, err := runPythonJSON(ctx, tileDEMScript, "Tile DEM", demPath, tilesDir, fmt.Sprint(nCols), fmt.Sprint(nRows), fmt.Sprint(overlapPx))
	if err != nil {
		return nil, err
	}
	var res struct{ Tiles []TileInfo `json:"tiles"` }
	if err := json.Unmarshal(out, &res); err != nil {
		return nil, err
	}
	return res.Tiles, nil
}

// shortID gives a stable 8-character name for a path. GRASS creates deeply
// nested directories inside its mapset, all of it under %TEMP% on Windows where
// the limit is 260 characters, so intermediate names are kept short rather than
// echoing a DEM basename that can itself be 40+ characters.
func shortID(s string) string {
	return fmt.Sprintf("%x", sha1.Sum([]byte(s)))[:8]
}

func generateGrassSlopeUnits(ctx context.Context, demPath, outputDir, crs string, p SlopeUnitParams) (string, error) {
	baseName := strings.TrimSuffix(filepath.Base(demPath), ".tif")
	gisdbase := filepath.Join(os.TempDir(), "g"+shortID(demPath))
	grassMapset := filepath.Join(gisdbase, "loc", "PERMANENT")
	outputPath := filepath.Join(outputDir, baseName+"_slopeunit.tif")

	os.MkdirAll(filepath.Dir(outputPath), 0755)
	os.RemoveAll(gisdbase)
	os.MkdirAll(gisdbase, 0755) 
	defer os.RemoveAll(gisdbase)

	tmp, err := os.CreateTemp("", "slu_*.py")
	if err != nil {
		return "", fmt.Errorf("cannot stage grass script: %w", err)
	}
	if _, err := tmp.WriteString(grassSlopeUnitsScript); err != nil {
		tmp.Close()
		return "", fmt.Errorf("cannot write grass script: %w", err)
	}
	tmp.Close()
	defer os.Remove(tmp.Name())

	// Parameters travel as argv rather than being interpolated into the script,
	// so paths containing spaces ("Program Files", "Application Support") need
	// no quoting of their own.
	cmd := newCommand(ctx, toolpath.Grass(),
		"-c", demPath, grassMapset,
		"--exec", toolpath.GrassPython(pythonPath), tmp.Name(),
		demPath, outputPath,
		strconv.Itoa(p.AreaMin),
		strconv.Itoa(p.Thresh),
		strconv.FormatFloat(p.CvMin, 'f', -1, 64),
		strconv.Itoa(p.Rf),
		strconv.Itoa(p.MaxIteration),
	)
	// GRASS_ADDON_BASE, not GRASS_ADDON_PATH: GRASS searches <base>/scripts
	// and <base>/bin, where the addon actually lives, but adds ADDON_PATH
	// entries to PATH as-is. It also overrides GRASS's own default, which on
	// macOS is ~/Library/GRASS/<ver>/Addons rather than where the plugin
	// installs r.slopeunits.
	cmd.Env = append(os.Environ(),
		"GRASS_ADDON_BASE="+toolpath.GrassAddons(),
		"GISRC="+filepath.Join(gisdbase, ".grassrc"),
		"GRASS_SKIP_MAPSET_OWNER_CHECK=1",
	)
	var stderr bytes.Buffer
	cmd.Stderr = io.MultiWriter(os.Stderr, &stderr)

	runErr := cmd.Run()
	if ctx.Err() != nil {
		return "", ctx.Err()
	}

	// The exit code alone is not trustworthy here: GRASS is launched through a
	// .bat shim on Windows, and a failing --exec child was seen returning 0.
	// A tile reported as generated but never written produces empty output far
	// downstream ("No tiles generated valid data"), so verify the raster.
	info, statErr := os.Stat(outputPath)
	if runErr != nil || statErr != nil || info.Size() == 0 {
		detail := strings.TrimSpace(stderr.String())
		if detail == "" && runErr != nil {
			detail = runErr.Error()
		}
		if statErr != nil {
			detail = fmt.Sprintf("%s (no output raster at %s)", detail, outputPath)
		}
		return "", fmt.Errorf("grass slope units failed: %s", detail)
	}
	return outputPath, nil
}

func generateSlopeUnitsParallel(ctx context.Context, demPath, outputDir, crs string, p SlopeUnitParams, cols, rows, overlap int) (string, error) {
	fmt.Println("[Phase 1] Tiling DEM for Parallel Slope Unit Generation...")
	tilesDir := filepath.Join(outputDir, "tiles")
	os.MkdirAll(tilesDir, 0755)
	defer os.RemoveAll(tilesDir)

	tileStart := time.Now()
	tiles, err := tileDEM(ctx, demPath, tilesDir, cols, rows, overlap)
	if err != nil {
		return "", err
	}
	reportTiming(fmt.Sprintf("tiling (%dx%d grid, %dpx overlap)", cols, rows, overlap), tileStart)

	var wg sync.WaitGroup
	sem := make(chan struct{}, runtime.NumCPU())
	errs := make([]error, len(tiles))
	slopeUnitStart := time.Now()

	for i := range tiles {
		if !acquire(ctx, sem) {
			break
		}
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			defer func() { <-sem }()
			sluDir := filepath.Join(tilesDir, fmt.Sprintf("s%d", idx))
			os.MkdirAll(sluDir, 0755)
			
			start := time.Now()
			sluPath, e := generateGrassSlopeUnits(ctx, tiles[idx].Path, sluDir, crs, p)
			if e == nil {
				tiles[idx].SluPath = sluPath
				fmt.Printf("   > Tile %d generated in %s.\n", idx, time.Since(start).Round(time.Millisecond))
			} else {
				errs[idx] = e
			}
		}(i)
	}
	wg.Wait()
	if err := ctx.Err(); err != nil {
		return "", err
	}
	reportTiming(fmt.Sprintf("slope units, all %d tiles", len(tiles)), slopeUnitStart)

	for _, e := range errs {
		if e != nil {
			return "", fmt.Errorf("a tile failed to process: %v", e)
		}
	}

	fmt.Println("[Phase 1] Merging Interlocking Tiles...")
	mergedTif := filepath.Join(outputDir, "Merged_SlopeUnits.tif")
	tilesJSON, _ := json.Marshal(tiles)
	mergeStart := time.Now()
	
	_, err = runPythonJSON(ctx, mergeSlopeUnitsScript, "Merge Tiles", demPath, string(tilesJSON), mergedTif)
	if err != nil {
		return "", err
	}
	reportTiming("tile merge", mergeStart)

	fmt.Println("[Phase 1] Sieve-Cleaning Seams and Polygonizing to GPKG...")
	finalGpkg := filepath.Join(outputDir, "Final_SlopeUnits.gpkg")
	polygonizeStart := time.Now()
	_, err = runPythonJSON(ctx, polygonizeScript, "Polygonize", mergedTif, finalGpkg, fmt.Sprint(p.AreaMin))
	if err == nil {
		reportTiming("sieve + polygonize", polygonizeStart)
	}
	
	os.Remove(mergedTif) 
	return finalGpkg, err
}

func main() {
	var (
		demPath       string
		slopeUnitPath string
		rasterFolder  string
		outputDir     string
		targetCRS     string
		areaMin       int
		thresh        int
		cvMin         float64
		rf            int
		maxIter       int
		tileCols      int
		tileRows      int
		tileOverlap   int
	)

	flag.Usage = func() {
		out := flag.CommandLine.Output()
		fmt.Fprintf(out, "Usage: %s [flags] [dem.tif] [flags]\n\n", filepath.Base(os.Args[0]))
		fmt.Fprintln(out, "The DEM may be passed positionally or with -dem. Without -rasterFolder")
		fmt.Fprintln(out, "only Phase 1 (slope unit generation) runs.")
		fmt.Fprintln(out)
		flag.PrintDefaults()
	}
	flag.StringVar(&demPath, "dem", "", "Path to DEM for slope unit generation")
	flag.StringVar(&slopeUnitPath, "slopeUnit", "", "Path to existing slope unit GPKG (skips generation)")
	flag.StringVar(&rasterFolder, "rasterFolder", "", "Directory with value rasters")
	flag.StringVar(&outputDir, "outputDir", "output", "Output directory")
	flag.StringVar(&pythonPath, "pythonExe", "python3", "Path to Python executable")
	flag.StringVar(&targetCRS, "targetCRS", "EPSG:4326", "Target CRS")
	
	flag.IntVar(&areaMin, "areaMin", 5000, "r.slopeunits: area min")
	flag.IntVar(&thresh, "thresh", 1000, "r.slopeunits: threshold")
	flag.Float64Var(&cvMin, "cvMin", 0.15, "r.slopeunits: cv min")
	flag.IntVar(&rf, "rf", 10, "r.slopeunits: reduction factor")
	flag.IntVar(&maxIter, "maxIter", 1, "r.slopeunits: max iterations")
	flag.IntVar(&tileCols, "tileCols", 1, "Number of tile columns")
	flag.IntVar(&tileRows, "tileRows", 1, "Number of tile rows")
	flag.IntVar(&tileOverlap, "tileOverlap", 50, "Tile overlap in pixels")
	flag.Parse()

	demPath, err := resolveDEMArg(flag.CommandLine, demPath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		flag.Usage()
		os.Exit(2)
	}

	ctx, stop := cancelOnShutdownSignal(context.Background())
	defer stop()

	runStart := time.Now()
	defer reportTiming("total", runStart)

	os.MkdirAll(outputDir, 0755)

	zoneRaster := slopeUnitPath
	if zoneRaster == "" && demPath != "" {
		phase1Start := time.Now()
		p := SlopeUnitParams{AreaMin: areaMin, Thresh: thresh, CvMin: cvMin, Rf: rf, MaxIteration: maxIter}
		out, err := generateSlopeUnitsParallel(ctx, demPath, outputDir, targetCRS, p, tileCols, tileRows, tileOverlap)
		if err != nil {
			exitIfCancelled(ctx)
			log.Fatalf("Slope Unit Generation Failed: %v", err)
		}
		zoneRaster = out
		reportTiming("Phase 1 total", phase1Start)
	}

	if zoneRaster == "" {
		log.Fatalf("Must provide either a DEM (positional or -dem) or -slopeUnit")
	}

	// Lets Phase 1 be run and timed on its own. The plugin always passes a
	// raster folder, so its runs are unaffected.
	if rasterFolder == "" {
		fmt.Println("[Phase 2] Skipped: no -rasterFolder given. Slope units at " + zoneRaster)
		return
	}

	entries, _ := os.ReadDir(rasterFolder)
	var rasters []string
	for _, e := range entries {
		if !e.IsDir() && strings.HasSuffix(strings.ToLower(e.Name()), ".tif") {
			rasters = append(rasters, filepath.Join(rasterFolder, e.Name()))
		}
	}

	phase2Start := time.Now()

	// Value rasters are warped onto the zone grid: the DEM's when Phase 1
	// ran, otherwise the finest value raster's. -targetCRS no longer applies
	// here; the slope units' own grid is the reference.
	grids := rasters
	if demPath != "" {
		grids = []string{demPath}
	}
	fmt.Println("[Phase 2] Rasterizing slope units onto the zone grid...")
	zonesStart := time.Now()
	zonesTif, err := buildZoneRaster(ctx, zoneRaster, outputDir, grids)
	if err != nil {
		exitIfCancelled(ctx)
		log.Fatalf("Zone Raster Failed: %v", err)
	}
	defer os.Remove(zonesTif)
	reportTiming("zone raster", zonesStart)

	var wg sync.WaitGroup
	sem := make(chan struct{}, runtime.NumCPU())
	for _, raster := range rasters {
		if !acquire(ctx, sem) {
			break
		}
		wg.Add(1)
		go processRaster(ctx, raster, zonesTif, outputDir, &wg, sem)
	}
	wg.Wait()
	exitIfCancelled(ctx)
	reportTiming(fmt.Sprintf("Phase 2 zonal stats, %d rasters", len(rasters)), phase2Start)

	fmt.Println("[Merging] Combining all features...")
	finalGPKG := filepath.Join(outputDir, "Merged_PINN_Features.gpkg")
	featureMergeStart := time.Now()
	_, err = runPythonJSON(ctx, mergeScript, "Merge Script", zoneRaster, outputDir, finalGPKG)
	if err != nil {
		exitIfCancelled(ctx)
		log.Fatalf("Merge Failed: %v", err)
	}
	reportTiming("feature merge", featureMergeStart)

	fmt.Println("[MERGE_DONE] " + finalGPKG)
}
