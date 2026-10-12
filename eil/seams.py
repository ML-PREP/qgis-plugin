"""Removing the tile-boundary slivers left by the parallel Go tiling."""

import os

from qgis.core import QgsVectorLayer

# qgis:eliminateselectedpolygons: merge with the neighbour sharing the longest
# boundary.
MERGE_WITH_LONGEST_BOUNDARY = 2


def eliminate_seams(input_gpkg, output_gpkg, area_min, log, log_label="Layer"):
    """Merge polygons under `area_min` into their neighbour.

    Returns the cleaned file, or `input_gpkg` when there was nothing to merge
    or the cleanup failed (the raw geometry is still usable).
    """
    import processing

    try:
        layer = QgsVectorLayer(input_gpkg, "eliminate_src", "ogr")
        if not layer.isValid():
            log(f"[Warning] {log_label}: could not open {input_gpkg} for seam cleanup.")
            return input_gpkg

        layer.selectByExpression(f"$area < {area_min}")
        selected = layer.selectedFeatureCount()
        if selected == 0:
            log(f"--> {log_label}: no slivers under {area_min} found, skipping cleanup.")
            return input_gpkg

        processing.run(
            "qgis:eliminateselectedpolygons",
            {
                # The layer object itself: the selection lives on it.
                "INPUT": layer,
                "MODE": MERGE_WITH_LONGEST_BOUNDARY,
                "OUTPUT": output_gpkg,
            },
        )
        if os.path.exists(output_gpkg):
            log(f"--> {log_label}: {selected} slivers merged, seams erased.")
            return output_gpkg

    except Exception as e:
        log(f"[Warning] {log_label} seam cleanup failed, using raw geometry: {e}")
    return input_gpkg
