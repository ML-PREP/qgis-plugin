"""Single-band raster read/write on top of GDAL.

The plugin previously used rasterio here. rasterio is bundled with QGIS on
macOS but **not** with OSGeo4W QGIS on Windows, so `import rasterio` at module
scope stopped the plugin loading there entirely:

    ModuleNotFoundError: No module named 'rasterio'

osgeo.gdal is a hard dependency of QGIS itself, so it is present on every
platform QGIS runs on. Using it removes the third-party requirement rather than
shipping a second GDAL alongside the one QGIS already loads.

Semantics deliberately match the rasterio code that was replaced: band 1 only,
float32 output, uncompressed GTiff (the TauDEM-based C++ engine reads these),
and `nodata` returned as None when unset, so `array != nodata` keeps behaving
the way it did.
"""

from osgeo import gdal

# numpy is imported inside write_band rather than here: QGIS does not bundle it
# on every build, and this module sits on the plugin's load path.

NODATA = -9999.0


class RasterProfile:
    """Geometry and georeferencing carried from a source raster to its output."""

    def __init__(self, width, height, geotransform, projection, nodata):
        self.width = width
        self.height = height
        self.geotransform = geotransform
        self.projection = projection
        self.nodata = nodata


def read_band(path, band=1):
    """Read one band. Returns (array, RasterProfile).

    Raises IOError rather than returning None, so a missing or unreadable file
    surfaces at the call site instead of as an AttributeError further down.
    """
    dataset = gdal.Open(path, gdal.GA_ReadOnly)
    if dataset is None:
        raise IOError(f"Cannot open raster: {path}")

    try:
        raster_band = dataset.GetRasterBand(band)
        if raster_band is None:
            raise IOError(f"Raster has no band {band}: {path}")

        array = raster_band.ReadAsArray()
        if array is None:
            raise IOError(f"Cannot read band {band} from: {path}")

        profile = RasterProfile(
            width=dataset.RasterXSize,
            height=dataset.RasterYSize,
            geotransform=dataset.GetGeoTransform(),
            projection=dataset.GetProjection(),
            nodata=raster_band.GetNoDataValue(),
        )
        return array, profile
    finally:
        dataset = None


def write_band(path, array, profile, nodata=NODATA):
    """Write a single float32 band, carrying georeferencing from `profile`."""
    import numpy as np

    driver = gdal.GetDriverByName("GTiff")
    if driver is None:
        raise IOError("GDAL GTiff driver unavailable")

    dataset = driver.Create(path, profile.width, profile.height, 1, gdal.GDT_Float32)
    if dataset is None:
        raise IOError(f"Cannot create raster: {path}")

    try:
        dataset.SetGeoTransform(profile.geotransform)
        dataset.SetProjection(profile.projection)

        band = dataset.GetRasterBand(1)
        band.WriteArray(np.asarray(array, dtype=np.float32))
        band.SetNoDataValue(float(nodata))
        band.FlushCache()
    finally:
        dataset = None
