"""Philippine administrative boundaries used to pick a region of interest.

Names repeat across the country (hundreds of barangays are "Poblacion"), so a
choice is identified by its PSA PCODE and shown with its parent units.
"""

import os
from collections import Counter
from dataclasses import dataclass

from .paths import ROI_DIR

ADMIN_LEVELS = ("Region", "Province", "Municipality", "Barangay")

CLIPPED_SUFFIX = "_ROI"

# level -> (shapefile, name column, PCODE column, parent name columns)
_BOUNDARY_FILES = {
    "Region": ("phl_admbnda_adm1_psa_namria_20231106.shp", "ADM1_EN", "ADM1_PCODE", ()),
    "Province": (
        "phl_admbnda_adm2_psa_namria_20231106.shp",
        "ADM2_EN",
        "ADM2_PCODE",
        ("ADM1_EN",),
    ),
    "Municipality": (
        "phl_admbnda_adm3_psa_namria_20231106.shp",
        "ADM3_EN",
        "ADM3_PCODE",
        ("ADM2_EN",),
    ),
    "Barangay": (
        "phl_admbnda_adm4_psa_namria_20231106.shp",
        "ADM4_EN",
        "ADM4_PCODE",
        ("ADM3_EN", "ADM2_EN"),
    ),
}


@dataclass(frozen=True)
class RoiChoice:
    """One selectable admin unit."""

    level: str
    pcode: str
    name: str
    label: str


@dataclass(frozen=True)
class RoiSelection:
    """The unit a run is clipped to, and where its boundary lives."""

    choice: RoiChoice
    boundary_path: str
    pcode_column: str


def admin_boundaries(roi_dir=ROI_DIR):
    """{level: {"path", "col", "pcode", "parents"}} for every admin level."""
    return {
        level: {
            "path": os.path.join(roi_dir, filename),
            "col": name_column,
            "pcode": pcode_column,
            "parents": parents,
        }
        for level, (filename, name_column, pcode_column, parents) in _BOUNDARY_FILES.items()
    }


def _label(name, parents):
    return ", ".join([name] + [str(p) for p in parents if p])


def label_choices(level, rows):
    """RoiChoices sorted by label from (pcode, name, parent names) rows.

    Labels that still collide after naming the parents get the PCODE appended,
    so every label in a level is unique.
    """
    named = [
        (str(pcode), str(name), _label(str(name), parents))
        for pcode, name, parents in rows
        if pcode and name
    ]
    counts = Counter(label for _, _, label in named)
    choices = (
        RoiChoice(level, pcode, name, f"{label} [{pcode}]" if counts[label] > 1 else label)
        for pcode, name, label in named
    )
    return tuple(sorted(choices, key=lambda c: c.label))


def attribute_filter(column, value):
    """`"column" = 'value'`, valid as both a QGIS expression and OGR SQL."""
    escaped = str(value).replace("'", "''")
    return f"\"{column}\" = '{escaped}'"


def clipped_path(path):
    """Where the ROI-clipped copy of `path` is written: beside it, suffixed."""
    stem, ext = os.path.splitext(path)
    return f"{stem}{CLIPPED_SUFFIX}{ext}"
