import re
from typing import Dict

# Canonical mapping of drilling and depth unit variations
# Used by Module 05.9 (OCR normalization) and Module 06.2 (depth regex prefilter)
DEPTH_UNIT_MAP: Dict[str, str] = {
    "mtrs": "m",
    "metres": "m",
    "meters": "m",
    "meter": "m",
    "metre": "m",
    "m.": "m",
    "Mtrs": "m",
    "Metres": "m",
    "Meters": "m",
    "MTRS": "m",
    "METRES": "m",
    "METERS": "m",
    "M.": "m",
}

PRESSURE_UNIT_MAP: Dict[str, str] = {
    "psi": "psi",
    "PSI": "psi",
    "Psi": "psi",
    "bar": "bar",
    "BAR": "bar",
    "Bar": "bar",
    "kpa": "kPa",
    "KPA": "kPa",
    "kPa": "kPa",
}

MUD_WEIGHT_UNIT_MAP: Dict[str, str] = {
    "ppg": "ppg",
    "PPG": "ppg",
    "Ppg": "ppg",
    "sg": "sg",
    "SG": "sg",
    "Sg": "sg",
    "g/cc": "g/cc",
}

ALL_UNIT_VARIANTS: Dict[str, str] = {
    **DEPTH_UNIT_MAP,
    **PRESSURE_UNIT_MAP,
    **MUD_WEIGHT_UNIT_MAP,
}


def build_unit_regex_pattern() -> re.Pattern:
    """Build a regex pattern that matches numbers followed by unit variants."""
    sorted_variants = sorted(ALL_UNIT_VARIANTS.keys(), key=len, reverse=True)
    escaped_variants = [re.escape(v) for v in sorted_variants]
    pattern = r"(\b\d+(?:\.\d+)?)\s*(" + "|".join(escaped_variants) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


UNIT_NORMALIZATION_REGEX = build_unit_regex_pattern()
