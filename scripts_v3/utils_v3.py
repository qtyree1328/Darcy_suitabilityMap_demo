"""
Shared utilities for V3 Iowa Geothermal Suitability pipeline.

V3 uses Random Forest + Residual Kriging instead of weighted overlay.
Wells are the ground truth; geological layers guide interpolation.
"""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
TILES_V3 = DATA_DIR / "tiles_v3"

# Iowa bounding box (EPSG:4326)
IOWA_BBOX = {
    "west": -96.64,
    "south": 40.37,
    "east": -90.14,
    "north": 43.50,
}

# Grid resolution for prediction surface (~2km in degrees)
GRID_RES_DEG = 0.02

# V3 composite well score weights (Section 3 of plan)
WELL_SCORE_WEIGHTS = {
    "PMPTST_GPM": 0.25,
    "SPC_GPM_FT": 0.20,
    "T_FT2_D": 0.15,
    "AQUIFER": 0.15,
    "AQ_THK_FT": 0.10,
    "WL_DPTH_FT": 0.05,
    "SWL_FT": 0.05,
    "CONF_UNCON": 0.05,
}

# Aquifer type scores (Section 3)
AQUIFER_TYPE_SCORES = {
    "alluvial": 1.0,
    "alluvium": 1.0,
    "sand and gravel": 0.9,
    "buried sand and gravel": 0.9,
    "buried sand & gravel": 0.9,
    "glacial drift": 0.7,
    "drift": 0.7,
    "outwash": 0.7,
    "glacial outwash": 0.7,
    "buried channel": 0.8,
    "dakota sandstone": 0.4,
    "dakota": 0.4,
}
AQUIFER_BEDROCK_KEYWORDS = [
    "cambrian", "ordovician", "silurian", "devonian",
    "mississippian", "pennsylvanian", "cretaceous",
    "precambrian", "jordan", "st. peter", "galena",
    "prairie du chien", "maquoketa", "limestone",
    "dolomite", "sandstone", "shale",
]
AQUIFER_UNCONSOLIDATED_KEYWORDS = [
    "sand", "gravel", "alluvial", "alluvium", "glacial",
    "drift", "buried channel", "surficial", "quaternary",
    "outwash",
]

# Color palette for suitability PNG (matching V2)
SUITABILITY_COLORS = [
    "#67000d", "#a50f15", "#cb181d", "#ef3b2c", "#fb6a4a",
    "#fc9272", "#fcbba1", "#fee0d2", "#fee5ce", "#fdd0a2",
    "#fdae6b", "#fd8d3c", "#f16913", "#d94801", "#e6550d",
    "#fdae6b", "#fee391", "#fff7bc", "#ffffe5", "#f7fcb9",
    "#d9f0a3", "#addd8e", "#78c679", "#41ab5d", "#238443",
    "#006837", "#004529",
]


def ensure_dirs():
    """Create all required data directories."""
    for d in [RAW_DIR, PROCESSED_DIR, TILES_V3]:
        d.mkdir(parents=True, exist_ok=True)
