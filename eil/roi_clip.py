"""Clipping EIL results to the chosen region of interest.

Only the copies shown on the map are clipped. The full-extent outputs stay on
disk and keep feeding later stages: depositional routing needs the slopes
above the region, not just the ones inside it.
"""

import os

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
)

from ..common.roi import attribute_filter, clipped_path

BOUNDARY_FILE = "ROI_Boundary.gpkg"


def extract_boundary(selection, dest_crs, output_folder, log):
    """Write the selected unit, reprojected to `dest_crs`, as a GeoPackage.

    Returns its path, or None after logging why it could not be made.
    """
    import processing

    choice = selection.choice
    if not os.path.exists(selection.boundary_path):
        log(f"[ERROR] Boundary file not found: {selection.boundary_path}")
        return None

    layer = QgsVectorLayer(selection.boundary_path, "roi_source", "ogr")
    if not layer.isValid():
        log(f"[ERROR] Could not open boundary file: {selection.boundary_path}")
        return None
    layer.setSubsetString(attribute_filter(selection.pcode_column, choice.pcode))
    if layer.featureCount() == 0:
        log(f"[ERROR] {choice.label} ({choice.pcode}) is not in the boundary file.")
        return None

    output = os.path.join(output_folder, BOUNDARY_FILE)
    try:
        processing.run(
            "native:reprojectlayer",
            {
                "INPUT": layer,
                "TARGET_CRS": QgsCoordinateReferenceSystem(dest_crs),
                "OUTPUT": output,
            },
        )
    except Exception as e:
        log(f"[ERROR] Could not prepare the {choice.label} boundary: {e}")
        return None
    return output if os.path.exists(output) else None


def overlaps_raster(boundary_path, raster_path):
    """True when the boundary's extent intersects the raster's."""
    boundary = QgsVectorLayer(boundary_path, "roi_boundary", "ogr")
    raster = QgsRasterLayer(raster_path, "roi_raster")
    if not boundary.isValid() or not raster.isValid():
        return False
    extent = boundary.extent()
    if boundary.crs() != raster.crs():
        transform = QgsCoordinateTransform(
            boundary.crs(), raster.crs(), QgsProject.instance()
        )
        extent = transform.transformBoundingBox(extent)
    return extent.intersects(raster.extent())


def _clip(algorithm, params, source, log, label):
    """Run a clip into the `_ROI` copy of `source`; `source` if it fails."""
    import processing

    output = clipped_path(source)
    try:
        # gdalwarp writes into an existing file rather than replacing it, so a
        # re-run would keep the previous run's pixels.
        if os.path.exists(output):
            os.remove(output)
        processing.run(algorithm, {**params, "OUTPUT": output})
    except Exception as e:
        log(f"[Warning] {label}: clipping failed, showing the full extent: {e}")
        return source
    if not os.path.exists(output):
        log(f"[Warning] {label}: clipping produced no output, showing the full extent.")
        return source
    log(f"--> {label}: clipped to the region of interest.")
    return output


def clip_vector(source, boundary_path, log, label="Layer"):
    """Cut `source`'s features at the boundary."""
    return _clip(
        "native:clip",
        {"INPUT": source, "OVERLAY": boundary_path},
        source,
        log,
        label,
    )


def clip_raster(source, boundary_path, log, label="Raster"):
    """Mask `source` to the boundary and crop it to the boundary's extent."""
    return _clip(
        "gdal:cliprasterbymasklayer",
        {
            "INPUT": source,
            "MASK": boundary_path,
            "CROP_TO_CUTLINE": True,
            "KEEP_RESOLUTION": True,
        },
        source,
        log,
        label,
    )
