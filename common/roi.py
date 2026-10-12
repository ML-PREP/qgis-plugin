"""Philippine administrative boundaries used to pick a region of interest."""

import os

from .paths import ROI_DIR

ADMIN_LEVELS = ("Region", "Province", "Municipality", "Barangay")

# level -> (shapefile, name column)
_BOUNDARY_FILES = {
    "Region": ("phl_admbnda_adm1_psa_namria_20231106.shp", "ADM1_EN"),
    "Province": ("phl_admbnda_adm2_psa_namria_20231106.shp", "ADM2_EN"),
    "Municipality": ("phl_admbnda_adm3_psa_namria_20231106.shp", "ADM3_EN"),
    "Barangay": ("phl_admbnda_adm4_psa_namria_20231106.shp", "ADM4_EN"),
}


def admin_boundaries(roi_dir=ROI_DIR):
    """{level: {"path": shapefile, "col": name column}} for every admin level."""
    return {
        level: {"path": os.path.join(roi_dir, filename), "col": column}
        for level, (filename, column) in _BOUNDARY_FILES.items()
    }
