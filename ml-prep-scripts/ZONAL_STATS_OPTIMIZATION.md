# Zonal statistics optimization (Phase 2 of the zonal engine)

**Date:** 2026-10-13
**Scope:** Phase 2 of the Go zonal engine (`ml-prep-scripts`), which computes one
statistic per slope unit for every input raster of the EIL pipeline.
**Result on the full Cotabato dataset:** Phase 2 took **88 min** before and takes
**1 min 21 s** now. Peak memory dropped from **about 79 GB** to **10.1 GB**. The
gaps the old method left on coarse rasters are also gone: **71–88%** of slope
units used to get no value on those rasters.

---

## 1. Where Phase 2 sits in the pipeline

```
QGIS plugin (eil/pipeline.py)
  └─ stages inputs as <model name>.tif links: Elev, Slope, BUK, Clay, Sand,
     Silt, Prc, SoilThc, PGA2, type, ContributingFactor
  └─ runs:  zonal -dem <DEM> -rasterFolder <links> -outputDir EIL_Results ...

zonal (Go)
  Phase 1  GRASS r.slopeunits on 3x3 tiles -> merge -> sieve -> polygonize
           => Final_SlopeUnits.gpkg   (one polygon per unit, with row_id)
  Phase 2  one statistic per unit per raster        <-- this document
  Merge    join every result on row_id
           => Merged_PINN_Features.gpkg  (one column per input)

pinn_inference.py reads Merged_PINN_Features.gpkg
```

Phase 2's output contract is unchanged. Each raster still produces
`<name>_zonal.npz` with `row_id` and `values`, and the merge step and
`Merged_PINN_Features.gpkg` keep exactly the same rows, order and columns.
`pinn_inference.py` needed no change.

---

## 2. What the previous binary did

For **each** raster in the folder, the old `processRaster` (in `main.go`) ran
these steps. All rasters ran at once, one goroutine and one Python process each,
limited only by `runtime.NumCPU()`:

1. **Pick a rule.** A `switch` on the file name:
   - `SoilType`, `LULC`: majority, nearest-neighbour resampling
   - `DistanceTo*`: min
   - everything else: mean, bilinear resampling
2. **Reproject to disk.** It ran
   `gdalwarp -t_srs <targetCRS> -r <resample> -dstnodata -9999 <src> <outputDir>/<name>.tif`.
   If that output file already existed, it was reused without checking.
3. **Zonal statistics.** It ran an embedded Python script that called
   `rasterstats.zonal_stats(gdf, reprojected.tif, stats=<rule>, nodata=-9999)`
   over all slope-unit polygons and wrote `<name>_zonal.npz`.

### Why it was slow

- **One Python iteration per polygon.** `rasterstats` loops over the
  334,949 polygons. For each one it rasterizes a polygon mask, reads that
  window from the raster and computes the statistic. With 11 rasters that's
  about 3.7 million polygon iterations. Raster size barely mattered: the
  423×403 BUK raster finished within minutes of the 21,086×20,119 DEM, all
  after 79–88 minutes.
- **Huge read amplification.** `gdalwarp` writes strips one row tall
  (`Block=21086x1`), so every polygon's window read pulled whole
  21,086-pixel rows. After about 80 minutes, each process had read about
  **1.6 TB** through the page cache, for a raster of about 1.7 GB.
- **Memory.** Each process held about **7.1 GB** (RSS). With 11 running in
  parallel, the VM sat at about **79 GB used** for the whole phase. On a Mac
  with Docker's default ~7.6 GB VM, this couldn't run at all.

### Why it was incomplete

`rasterstats` (with `all_touched=False`) only counts a raster pixel if the
pixel's **centre** falls inside the polygon. Slope units are much smaller than
the coarse rasters' pixels:

| | Value |
|---|---|
| Slope-unit area p10 / p50 / p90 | 5,275 / 12,500 / 35,850 m² |
| One BUK/Clay/Sand/Silt pixel (250 m) | 62,500 m² |
| One SoilThc pixel (230.1 m) | 52,946 m² |
| One PGA2 pixel (368.6 m) | 135,866 m² |

| Raster | Units smaller than one pixel | Units with NaN | NaN that are such small units |
|---|---|---|---|
| BUK (250 m) | 326,923 | 244,951 | 244,015 |
| SoilThc (230 m) | 322,259 | 237,964 | 236,344 |
| PGA2 (369 m) | 334,365 | 293,281 | 293,179 |

These NaNs reached the model. `pinn_inference.py` drops units with slope below
10, which leaves 147,427 units, and then fills each NaN with the **median of
that same run's units**, not the training-data median:

| Model input | Units filled with the median (of 147,427) |
|---|---|
| BUK, and through `soil_texture_idx` also Clay/Sand/Silt | 102,716 (69.7%) |
| SoilThc | 98,924 (67.1%) |
| PGA2 | 126,447 (85.8%) |

### Other defects found in the old path

- **Soil type was averaged.** The plugin stages the soil-type raster as
  `type.tif`, but the switch only matched `SoilType`/`LULC`. Soil-type
  **class codes** were therefore bilinearly resampled and **averaged**, which
  produced 7,434 non-integer "classes" (e.g. 44.37).
- **Stale reprojected copies.** Because `gdalwarp` output was reused when it
  existed, changing an input raster but keeping its name silently reused the
  previous run's reprojected copy.
- **Disk use.** Each input's reprojected copy was left in `EIL_Results/`,
  about 1.7 GB per full-size raster.

---

## 3. What the new binary does

Phase 2 now runs as two subcommands of one embedded script,
`pyscripts/zonal_grid.py` (built into the binary with `//go:embed`), driven by
`zonal.go`.

### Step A: `zones` (once per run)

All slope units are burned into **`Zones.tif`**, an int32 raster on the
**DEM grid** (5 m), where each cell holds its unit's `row_id + 1` (0 = no unit).
The file is tiled 512×512, deflate-compressed, and tagged with
`MLPREP_UNIT_COUNT`. It's deleted after the merge.

> Without `-dem` (the `-slopeUnit` path), the finest of the value rasters is
> used as the grid.

### Step B: `stats` (per raster, in parallel)

1. **Rule** from `statRuleFor()` in `zonal.go`. It now includes `type`:
   - `type`, `SoilType`, `LULC`: **majority**, nearest-neighbour resampling
   - `DistanceTo*`: **min**, bilinear
   - everything else: **mean**, bilinear
2. **Warp in memory.** A `rasterio` `WarpedVRT` presents the value raster on
   `Zones.tif`'s exact grid. Nothing is written to disk. A 5 m raster lines up
   cell for cell, while a 250 m raster is sampled at every 5 m cell, so every
   unit gets the value of the coarse pixel(s) it lies in.
3. **Stream in 512-row blocks.** Each block reads the slope-unit IDs and the
   warped values, and drops cells that are outside a unit, nodata or
   non-finite.
4. **Add each block to per-unit running totals**, all vectorized with NumPy:
   - mean: per-unit sum and count with `np.bincount`
   - min: sort by unit, then `np.minimum.reduceat`
   - majority: per-class `np.bincount`; a tie goes to the smallest class, as
     in `rasterstats`
5. **Write** `<name>_zonal.npz` (`row_id`, `values`), in the same format as
   before.

`GDAL_CACHEMAX` is capped at 256 MB per process. GDAL's default is 5% of RAM,
which on a 98 GB host added about 5 GB per process for no benefit, because each
block is read once, in order.

### Removed

- The `gdalwarp` step and its reprojected copies in `EIL_Results/`.
- The embedded `rasterstats` script.
- `-targetCRS` no longer affects Phase 2: value rasters are warped onto the
  slope units' own grid.

---

## 4. Performance

**Hardware:** carsu_vm, x86_64, 64 cores, 98 GB RAM.
**Container:** `docker/eil-linux` (Ubuntu 24.04, GDAL CLI, Python 3.12).
**Data:** Cotabato DEM 21,086 × 20,119 cells at 5 m (424 M cells,
ESRI:102457), 334,949 slope units, 11 input rasters.

| | Old (`gdalwarp` + `rasterstats`) | New (`Zones.tif` + `bincount`) |
|---|---|---|
| Phase 2, 11 rasters | **88.0 min** | **1 min 20.8 s** (32.6 s of it builds `Zones.tif`) |
| Time per raster | 79–88 min (all in parallel) | all 11 done within 48 s in parallel; 5–23 s each when run one at a time (prototype) |
| Peak memory | about **79 GB** (host total during the phase; ~7.1 GB × 11 processes) | **10.1 GB** (container peak) |
| Peak memory before the `GDAL_CACHEMAX` cap | – | 39.4 GB |
| Data read per process | about 1.6 TB through the page cache | one pass over the raster |
| Files written to `EIL_Results` | 11 reprojected rasters (~1.7 GB each at full size) | none (`Zones.tif` is temporary) |

**Other measurements:**
- **Full plugin run** on carsu_vm (8 rasters): Phase 2 took 1 min 17 s,
  including 29.8 s for `Zones.tif`.
- **End-to-end test** on a 4 km test crop (800×800 cells, 2 rasters): Phase 2
  went from 17.1 s to 1.9 s.

**Measurement notes:**
- The old peak is the host's total "used" memory during the phase, sampled
  every 10 s. It includes about 3 GB used by other services on the VM.
- The new peak comes from `docker stats` on the engine container, sampled
  every 2 s.
- The new timings are from `zonal -slopeUnit Final_SlopeUnits.gpkg -dem …`,
  which reuses the same slope units, so the per-unit values can be compared
  directly.

---

## 5. Agreement with the previous method

Every column of the new `Merged_PINN_Features.gpkg` was compared with the old
one (same 334,949 rows, same order):
- **Median |diff|** and **correlation** are computed over units where both
  methods produced a value.
- The **median value** gives each difference a scale.

| Raster | Pixel size | Rule | NaN old | NaN new | Median value | Median \|diff\| | Correlation |
|---|---|---|---|---|---|---|---|
| Slope | 5 m | mean | 75 | 75 | 8.027 | 1.3e-07 | 1.00000 |
| Elev | 5 m | mean | 0 | 0 | 121.5 | 2.3e-06 | 1.00000 |
| ContributingFactor | 5 m | mean | 0 | 0 | 2,799 | 6.2e-05 | 1.00000 |
| Prc | 30 m | mean | 24,041 | 2,245 | 116.3 | 0.0020 | 1.00000 |
| SoilThc | 230.1 m | mean | 237,964 | 888 | 16.85 | 0.41 | 0.98729 |
| BUK | 250 m | mean | 244,951 | 182 | 123 | 0.40 | 0.97113 |
| Clay | 250 m | mean | 244,951 | 182 | 386 | 2.15 | 0.96896 |
| Sand | 250 m | mean | 244,951 | 182 | 321 | 1.71 | 0.97991 |
| Silt | 250 m | mean | 244,951 | 182 | 290 | 2.02 | 0.97814 |
| PGA2 | 368.6 m | mean | 293,281 | 1,012 | 0.2895 | 0.0016 | 0.99765 |
| type | 10 m | mean → **majority** | 68,422 | 55,036 | – | – | – |

**How to read this:**
- **Fine rasters (5 m)** are reproduced exactly, apart from float rounding.
  This confirms that burning the units onto the DEM grid matches
  `rasterstats`' pixel-centre rule.
- **Coarse rasters (≥ 230 m)** now have a value for almost every unit. Where
  both methods produced a value, they agree closely (r = 0.97–0.998). The
  small differences come from sampling: the new method samples every 5 m cell
  of a unit, while the old one used only the few coarse pixels whose centres
  fell inside it.
- **Soil type** is now a real class code (1, 45, 94, 132, 190, 194, 305, 325,
  431, 433, 434, 435, 448, 449, 451, 452, 453, 458, 583, 999), instead of
  7,434 averaged non-codes.

---

## 6. Impact on predictions and open items

- **The PINN's inputs change.** BUK, SoilThc and the soil-texture index change
  for about two-thirds of the scored units, and PGA2 for about 86%: each goes
  from one shared median to a location-specific value. These inputs feed the
  physics layers (cohesion, friction, factor of safety), so susceptibility
  will shift.
- **Training features.** If the model's training features were produced by the
  old `rasterstats` path, the model learned from median-filled soil and PGA
  values. Recompute the training features with the new method before
  comparing model quality.
- **Soil type still reaches the model as "Unknown".** "FIX 2" in
  `pinn_inference.py` turns any numeric `type` value into `"Unknown"`. The
  class codes are now correct, but the model needs a code-to-name mapping
  before soil type actually counts.
- **Not touched.** Phase 1 (GRASS slope units, about 10 min on this data) is
  unchanged.

---

## 7. Tests

| Test | Covers |
|---|---|
| `zonal_test.go` | the rule table, including `type` → majority/nearest; that the script is embedded |
| `tests/test_zonal_grid.py` (plugin repo, 11 tests) | mean/min/majority reducers across blocks, the tie rule, the valid-cell filter, and end-to-end against `rasterstats`: fine-raster means match (rtol 1e-6), a 15 m unit gets a value from a 50 m raster where `rasterstats` gives `None`, majority returns real codes, and `Zones.tif` records the unit count |

The reducer tests need only NumPy, so they run in CI. The end-to-end tests need
rasterio, geopandas and rasterstats (the EIL venv) and are skipped otherwise.

## 8. Reproducing the measurements

```bash
# Build the engine for all platforms (embeds pyscripts/zonal_grid.py)
./build_binaries.sh

# Phase 2 only, on existing slope units (as measured above)
zonal -dem Elevation.tif -slopeUnit EIL_Results/Final_SlopeUnits.gpkg \
      -rasterFolder <folder of <model name>.tif links> -outputDir out \
      -pythonExe ~/.mlprep_envs/venv/bin/python
```

The column-by-column comparison in section 5 was run on carsu_vm with
`~/mlprep/zonal_compare.py` (prototype check) and a direct comparison of the
old and new `Merged_PINN_Features.gpkg`.
