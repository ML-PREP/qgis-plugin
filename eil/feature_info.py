"""Hover help for the EIL input labels."""

from types import MappingProxyType

from qgis.PyQt import QtCore

# Keyed by the suffix of the label's objectName: label_<key>.
FEATURE_INFO = MappingProxyType(
    {
        "DEM": {
            "title": "Digital Elevation Model (DEM)",
            "desc": "A 3D computer graphics representation of elevation data to represent terrain. It serves as the foundational layer for deriving slope, wetness, and other topographic metrics.",
        },
        "BulkDensity": {
            "title": "Bulk Density",
            "desc": "The weight of soil per unit volume. High bulk density indicates low pore space and high compaction, affecting water movement.",
        },
        "Clay": {
            "title": "Clay Content",
            "desc": "Percentage of clay particles in the soil. High clay content can increase cohesion but may significantly reduce drainage.",
        },
        "Sand": {
            "title": "Sand Content",
            "desc": "Percentage of sand particles. Sand increases drainage and reduces the overall stickiness and cohesion of the soil.",
        },
        "Silt": {
            "title": "Silt Content",
            "desc": "Percentage of silt particles. Silt-heavy soils can be highly susceptible to erosion and landslides when saturated.",
        },
        "Eastness": {
            "title": "Eastness",
            "desc": "A quantitative terrain parameter used to analyze land-surface orientation (aspect). It measures the degree to which a slope faces east.",
        },
        "Northness": {
            "title": "Northness",
            "desc": "Measures the degree to which a slope faces north. Combined with Eastness, it creates a continuous gradient of slope direction.",
        },
        "Slope": {
            "title": "Slope Angle",
            "desc": "The steepness of the terrain, measured in degrees. Steeper slopes generally have a higher risk of landslide occurrence.",
        },
        "PGA": {
            "title": "Peak Ground Acceleration (PGA)",
            "desc": "Measures the maximum ground shaking intensity at a specific location during an earthquake, representing the largest amplitude of acceleration recorded on an accelerogram.",
        },
        "NDVI": {
            "title": "Normalized Difference Vegetation Index (NDVI)",
            "desc": "A remote sensing indicator used to measure and analyze the health and density of green vegetation. Plant roots help stabilize soil.",
        },
        "PRC": {
            "title": "Precipitation",
            "desc": "The process where water vapor condenses in the atmosphere to form water droplets that fall to the Earth as rain. Intense rainfall is a primary trigger for landslides.",
        },
        "HorizontalCurve": {
            "title": "Horizontal Curvature",
            "desc": "Also known as Plan Curvature. It describes the curvature of contour lines and influences the convergence or divergence of surface water flow.",
        },
        "VerticalCurve": {
            "title": "Vertical Curvature",
            "desc": "Also known as Profile Curvature. It measures the rate of change of slope, influencing the acceleration and deceleration of surface flow.",
        },
        "SoilThickness": {
            "title": "Soil Thickness",
            "desc": "The depth of the soil layer above bedrock. Thicker soils can absorb more water but also provide more mass that can potentially fail and slide.",
        },
        "LULC": {
            "title": "Land Use / Land Cover",
            "desc": "Categorizes the physical material at the surface of the earth (e.g., forest, agriculture, urban). Different land covers affect water infiltration and root cohesion.",
        },
        "DistanceToRiver": {
            "title": "Distance to River",
            "desc": "Proximity to the nearest river or stream. Rivers can undercut slopes through lateral erosion, increasing landslide susceptibility.",
        },
        "DistanceToRoad": {
            "title": "Distance to Road",
            "desc": "Proximity to the nearest road network. Road construction often involves cutting into slopes, which can alter drainage and destabilize the terrain.",
        },
        "SoilType": {
            "title": "Soil Type",
            "desc": "Categorical classification of soils based on their physical and chemical properties, which heavily influences internal friction and cohesion.",
        },
        "TWI": {
            "title": "Topographic Wetness Index",
            "desc": "A steady-state wetness index. It identifies areas where water tends to accumulate based on local topography.",
        },
        "DistanceToFault": {
            "title": "Distance to Fault",
            "desc": "Proximity to tectonic fault lines. Areas closer to active faults experience more structural rock damage and higher earthquake intensities.",
        },
        "ContributingFactor": {
            "title": "Contributing Factor",
            "desc": "Also known as Catchment Area. Represents the total area uphill that drains water into this specific unit, drastically affecting soil saturation.",
        },
    }
)

PLACEHOLDER_HTML = (
    "<p style='color: #666666; font-style: italic;'>"
    "Hover over a parameter label to view its details.</p>"
)


def details_html(info):
    return f"""
            <h1 style="color: #000000; margin-top: 20px; margin-bottom: 20px; margin-left: 20px;">{info['title']}</h1>
            <p style="font-size: 24px; margin-top: 0px; margin-bottom: 20px; margin-left: 20px; margin-right: 20px; line-height: 1.4;">
                {info['desc']}
            </p>
        """


class FeatureHoverFilter(QtCore.QObject):
    """Shows a label's description in the details box while it is hovered."""

    def __init__(self, details_box, parent=None):
        super().__init__(parent)
        self._details = details_box
        self._info_by_label = {}

    def watch(self, ui):
        """Install on every label_<feature> the form defines."""
        for name, info in FEATURE_INFO.items():
            label = getattr(ui, f"label_{name}", None)
            if label is None:
                continue
            label.setMouseTracking(True)
            label.installEventFilter(self)
            self._info_by_label[label] = info

    def eventFilter(self, source, event):
        if event.type() == QtCore.QEvent.Enter:
            info = self._info_by_label.get(source)
            if info is not None:
                self._details.clear()
                self._details.appendHtml(details_html(info))
                return True
        elif event.type() == QtCore.QEvent.Leave:
            self._details.clear()
            self._details.appendHtml(PLACEHOLDER_HTML)
            return True
        return super().eventFilter(source, event)
