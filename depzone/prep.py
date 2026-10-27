"""Raster preparation for the depositional-zone stage (run in the EIL venv).

Each subcommand streams its rasters through depzone/rasters.py in row blocks,
so a full-size DEM never has to fit in memory. A subcommand that has numbers
for the worker prints them as the last line, as one JSON object.

    python prep.py slope-degrees --tangent slp.tif --out slp_degrees.tif
"""

import argparse
import json
import os
import sys

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.warp import reproject
from rasterio.windows import Window

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rasters as dz  # noqa: E402

BLOCK_ROWS = 512
BULK_SAMPLE_SIZE = 1000
# Exit code when no cell can seed the routing engine.
EXIT_NO_SOURCES = 3
MASK_NODATA = 255


def _window(block, width, padded=False):
    """The block's rows (with its halo when `padded`) across the full width."""
    row0, row1 = (block.pad0, block.pad1) if padded else (block.row0, block.row1)
    return Window(0, row0, width, row1 - row0)


def _float_profile(src, **extra):
    profile = src.profile.copy()
    profile.update(dtype="float32", nodata=dz.NODATA, count=1, **extra)
    return profile


def _masked(src, window):
    """Band 1 over `window` as float64 with NaN where the raster has no data."""
    data = src.read(1, window=window, masked=True).astype(np.float64)
    return np.ma.filled(data, np.nan)


def slope_degrees(args):
    with rasterio.open(args.tangent) as src, rasterio.open(
        args.out, "w", **_float_profile(src)
    ) as dst:
        for block in dz.blocks(src.height, BLOCK_ROWS):
            win = _window(block, src.width)
            degrees = dz.slope_degrees(src.read(1, window=win), src.nodata)
            dst.write(degrees, 1, window=win)


def curvatures(args):
    with rasterio.open(args.dem) as src:
        profile = _float_profile(src)
        cell_size = abs(src.transform.a)
        with rasterio.open(args.profile, "w", **profile) as prof_dst, rasterio.open(
            args.plan, "w", **profile
        ) as plan_dst:
            for block in dz.blocks(src.height, BLOCK_ROWS, halo=1):
                z = _masked(src, _window(block, src.width, padded=True))
                prof, plan = dz.curvatures(z, cell_size)
                win = _window(block, src.width)
                prof_dst.write(block.trim(prof), 1, window=win)
                plan_dst.write(block.trim(plan), 1, window=win)


def align(args):
    """Warp `src` onto `ref`'s exact grid (bilinear, or nearest for masks)."""
    resampling = Resampling.nearest if args.nearest else Resampling.bilinear
    with rasterio.open(args.ref) as ref, rasterio.open(args.src) as src:
        nodata = src.nodata if src.nodata is not None else dz.NODATA
        profile = ref.profile.copy()
        profile.update(dtype=src.dtypes[0], nodata=nodata, count=1)
        with rasterio.open(args.out, "w", **profile) as dst:
            for block in dz.blocks(ref.height, BLOCK_ROWS):
                win = _window(block, ref.width)
                out = np.full((win.height, win.width), nodata, dtype=src.dtypes[0])
                reproject(
                    source=rasterio.band(src, 1),
                    destination=out,
                    src_transform=src.transform,
                    src_crs=src.crs,
                    dst_transform=ref.window_transform(win),
                    dst_crs=ref.crs,
                    src_nodata=nodata,
                    dst_nodata=nodata,
                    resampling=resampling,
                )
                dst.write(out, 1, window=win)


def seed_outlets(args):
    """Copy of the stream raster with the flow domain's boundary as outlets."""
    seeded = 0
    with rasterio.open(args.ang) as ang, rasterio.open(args.src) as src:
        if (ang.width, ang.height) != (src.width, src.height):
            raise SystemExit("ang and src are not on the same grid")
        with rasterio.open(args.out, "w", **src.profile) as dst:
            for block in dz.blocks(src.height, BLOCK_ROWS, halo=1):
                halo = _window(block, src.width, padded=True)
                valid = ~np.ma.getmaskarray(ang.read(1, window=halo, masked=True))
                ring = block.trim(dz.boundary_ring(valid))
                win = _window(block, src.width)
                data = src.read(1, window=win)
                data[ring] = 1
                dst.write(data, 1, window=win)
                seeded += int(ring.sum())
    print(f"Seeded {seeded} domain-boundary outlet cells")


def twi(args):
    with rasterio.open(args.sca) as sca, rasterio.open(args.slope) as slp:
        with rasterio.open(args.out, "w", **_float_profile(sca)) as dst:
            for block in dz.blocks(sca.height, BLOCK_ROWS):
                win = _window(block, sca.width)
                out = dz.twi(
                    sca.read(1, window=win).astype(np.float32),
                    slp.read(1, window=win).astype(np.float32),
                    sca.nodata,
                    slp.nodata,
                )
                dst.write(out, 1, window=win)


def lowpass_path(out_dir, path):
    stem = os.path.splitext(os.path.basename(path))[0]
    return os.path.join(out_dir, f"{stem}_lpf.tif")


def lowpass(args):
    halo = dz.LPF_WINDOW // 2
    for path in args.rasters:
        with rasterio.open(path) as src:
            nodata = src.nodata if src.nodata is not None else dz.NODATA
            profile = src.profile.copy()
            profile.update(dtype="float32", nodata=nodata, count=1)
            out_path = lowpass_path(args.out_dir, path)
            with rasterio.open(out_path, "w", **profile) as dst:
                for block in dz.blocks(src.height, BLOCK_ROWS, halo=halo):
                    data = _masked(src, _window(block, src.width, padded=True))
                    valid = np.isfinite(data)
                    out = dz.window_mean(np.nan_to_num(data), valid, nodata)
                    dst.write(block.trim(out), 1, window=_window(block, src.width))
    size = dz.LPF_WINDOW
    print(f"Low-pass filtered ({size}x{size} mean) {len(args.rasters)} predictors")


def clean_dfi(args):
    with rasterio.open(args.src) as src, rasterio.open(
        args.out, "w", **_float_profile(src)
    ) as dst:
        for block in dz.blocks(src.height, BLOCK_ROWS):
            win = _window(block, src.width)
            dst.write(dz.clamp_dfi(src.read(1, window=win)), 1, window=win)


def _median_positive(src):
    shape = (min(BULK_SAMPLE_SIZE, src.height), min(BULK_SAMPLE_SIZE, src.width))
    sample = src.read(1, out_shape=shape, masked=True).compressed().astype(np.float64)
    sample = sample[np.isfinite(sample) & (sample > 0)]
    return float(np.median(sample)) if sample.size else float("nan")


def sanitize_bulk(args):
    """Bulk density with non-positive cells as NoData, plus its unit scale."""
    masked = 0
    with rasterio.open(args.src) as src:
        median = _median_positive(src)
        with rasterio.open(args.out, "w", **_float_profile(src)) as dst:
            for block in dz.blocks(src.height, BLOCK_ROWS):
                win = _window(block, src.width)
                data = np.ma.filled(
                    src.read(1, window=win, masked=True).astype(np.float32), dz.NODATA
                )
                clean, count = dz.mask_non_positive(data, dz.NODATA)
                masked += count
                dst.write(clean, 1, window=win)
    scale, note = dz.unit_weight_scale(median)
    summary = {"masked": masked, "median": median, "scale": scale, "note": note}
    print(json.dumps(summary))


def _on_grid(path, template, resampling):
    """`path` presented on `template`'s grid (a no-op when already aligned)."""
    src = rasterio.open(path)
    same = (src.crs, src.transform, src.shape) == (
        template.crs,
        template.transform,
        template.shape,
    )
    if same:
        return src
    return WarpedVRT(
        src,
        crs=template.crs,
        transform=template.transform,
        width=template.width,
        height=template.height,
        resampling=resampling,
    )


def engine_inputs(args):
    totals = {}
    names = ("alpha_final.tif", "dfi_final.tif", "source_final.tif")
    with rasterio.open(args.ang) as ang, rasterio.open(args.fel) as fel:
        alpha = _on_grid(args.alpha, ang, Resampling.nearest)
        dfi = _on_grid(args.dfi, ang, Resampling.nearest)
        mask = _on_grid(args.source_mask, ang, Resampling.nearest)
        profile = _float_profile(ang)
        outs = [
            rasterio.open(os.path.join(args.out_dir, n), "w", **profile) for n in names
        ]
        try:
            for block in dz.blocks(ang.height, BLOCK_ROWS):
                win = _window(block, ang.width)
                a = ang.read(1, window=win).astype(np.float32)
                f = fel.read(1, window=win).astype(np.float32)
                terrain = np.isfinite(a) & np.isfinite(f)
                if ang.nodata is not None:
                    terrain &= a != np.float32(ang.nodata)
                if fel.nodata is not None:
                    terrain &= f != np.float32(fel.nodata)
                mask_data = mask.read(1, window=win)
                result = dz.engine_inputs(
                    terrain,
                    alpha.read(1, window=win).astype(np.float32),
                    alpha.nodata,
                    dfi.read(1, window=win).astype(np.float32),
                    dfi.nodata,
                    np.where(mask_data == 1, 1, 0).astype(np.uint8),
                )
                for dst, data in zip(outs, (result.alpha, result.dfi, result.source)):
                    dst.write(data, 1, window=win)
                for key, value in result.counts.items():
                    totals[key] = totals.get(key, 0) + value
        finally:
            for dst in outs:
                dst.close()
            for reader in (alpha, dfi, mask):
                reader.close()
    print(json.dumps(totals))
    if totals.get("legal_sources", 0) == 0:
        sys.exit(EXIT_NO_SOURCES)


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, func, *flags):
        p = sub.add_parser(name)
        for flag in flags:
            p.add_argument(f"--{flag}", required=True)
        p.set_defaults(func=func)
        return p

    command("slope-degrees", slope_degrees, "tangent", "out")
    command("curvatures", curvatures, "dem", "profile", "plan")
    aligner = command("align", align, "src", "ref", "out")
    aligner.add_argument("--nearest", action="store_true")
    command("seed-outlets", seed_outlets, "src", "ang", "out")
    command("twi", twi, "sca", "slope", "out")
    command("lowpass", lowpass, "out-dir").add_argument("rasters", nargs="+")
    command("clean-dfi", clean_dfi, "src", "out")
    command("sanitize-bulk", sanitize_bulk, "src", "out")
    command(
        "engine-inputs",
        engine_inputs,
        "ang",
        "fel",
        "alpha",
        "dfi",
        "source-mask",
        "out-dir",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
