"""
Shared config for the DengueWatch LK pipeline.

DISTRICTS is the canonical list of Sri Lanka's 25 health/administrative
districts, in the same order used throughout the workbook and the app.
COORDS gives each district's approximate centroid (lat, lon) for weather
lookups, taken from the coordinates already used in the data collection
workbook's Open-Meteo instructions.

ALIASES maps how a district or reporting unit might appear in a source
PDF onto one of the 25 canonical districts. Sri Lanka's dengue reports
sometimes break out sub-units that are NOT official districts:
  - "CMC" (Colombo Municipal Council) -> folded into Colombo
  - "Kalmunai"                        -> folded into Ampara
  - "NIHS" (National Institute of Health Sciences, a referral-hospital
    catchment, not a district) -> EXCLUDED (see EXCLUDED_UNITS below).
    This is a real judgment call, not an invented one: NIHS cases are
    already counted under patients' home districts elsewhere in the
    surveillance system, so folding it into any one district would
    double count. Historically it's ~1% of the national total, which
    is why a rebuilt district-level total will sit ~1% under the
    country total reported on the source PDF -- that gap is expected,
    not a bug.
"""

DISTRICTS = [
    "Colombo", "Gampaha", "Kalutara", "Kandy", "Matale", "Nuwara Eliya",
    "Galle", "Matara", "Hambantota", "Jaffna", "Kilinochchi", "Mannar",
    "Vavuniya", "Mullaitivu", "Batticaloa", "Ampara", "Trincomalee",
    "Kurunegala", "Puttalam", "Anuradhapura", "Polonnaruwa", "Badulla",
    "Monaragala", "Ratnapura", "Kegalle",
]

_LAT = [6.9271, 7.0917, 6.5854, 7.2906, 7.4675, 6.9497, 6.0535, 5.9549,
        6.1246, 9.6615, 9.3961, 8.981, 8.7514, 9.267, 7.717, 7.2975,
        8.5874, 7.4818, 8.0362, 8.3114, 7.9403, 6.9934, 6.8726, 6.6828,
        7.2513]
_LON = [79.8612, 80.0, 79.9607, 80.6337, 80.6234, 80.7891, 80.221, 80.555,
        81.1185, 80.0255, 80.3982, 79.9044, 80.4971, 80.8142, 81.7,
        81.6747, 81.2152, 80.3609, 79.8283, 80.4037, 81.0188, 81.055,
        81.351, 80.4012, 80.3464]

COORDS = {d: (lat, lon) for d, lat, lon in zip(DISTRICTS, _LAT, _LON)}

ALIASES = {
    "cmc": "Colombo",
    "colombo mc": "Colombo",
    "kalmunai": "Ampara",
    "nuwaraeliya": "Nuwara Eliya",
    "nuwara-eliya": "Nuwara Eliya",
    "nuwara eliya": "Nuwara Eliya",
}

# Units that appear in the source PDF but are deliberately dropped rather
# than folded into a district, to avoid double-counting. See docstring.
EXCLUDED_UNITS = {"nihs"}


def normalize_district(name: str):
    """Map a raw label from a source PDF to a canonical district name,
    or None if it's an excluded non-district unit."""
    key = name.strip().lower()
    if key in EXCLUDED_UNITS:
        return None
    if key in ALIASES:
        return ALIASES[key]
    for d in DISTRICTS:
        if d.lower() == key:
            return d
    return None
