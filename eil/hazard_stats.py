"""Per-municipality hazard tallies for the EIL dashboard. No Qt or QGIS here."""

HIGH = "High"
MODERATE = "Moderate"
LOW = "Low"
HAZARD_CLASSES = (HIGH, MODERATE, LOW)

HIGH_THRESHOLD = 0.66
MODERATE_THRESHOLD = 0.33
TOP_MUNICIPALITIES = 10


def classify_susceptibility(value):
    """Hazard class of one slope unit; an unreadable value counts as Low."""
    try:
        score = float(value)
    except Exception:
        return LOW
    if score > HIGH_THRESHOLD:
        return HIGH
    if score >= MODERATE_THRESHOLD:
        return MODERATE
    return LOW


def tally_by_municipality(municipality_values):
    """{municipality: {class: count}} from (municipality, susceptibility) pairs."""
    tallies = {}
    for municipality, value in municipality_values:
        counts = tallies.get(municipality) or dict.fromkeys(HAZARD_CLASSES, 0)
        hazard = classify_susceptibility(value)
        tallies[municipality] = {**counts, hazard: counts[hazard] + 1}
    return tallies


def most_hazardous(tallies, limit=TOP_MUNICIPALITIES):
    """[(municipality, counts)] ordered by High count, most first."""
    ranked = sorted(tallies.items(), key=lambda item: item[1][HIGH], reverse=True)
    return ranked[:limit]
