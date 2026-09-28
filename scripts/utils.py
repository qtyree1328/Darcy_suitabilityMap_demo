"""
Shared utilities for the Iowa Geothermal Suitability pipeline.
"""
from pathlib import Path

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
TILES_DIR = DATA_DIR / "tiles"

# Iowa bounding box (EPSG:4326)
IOWA_BBOX = {
    "west": -96.64,
    "south": 40.37,
    "east": -90.14,
    "north": 43.50,
}

IOWA_FIPS = "19"

# GEE proxy URL (deployed Cloud Run instance)
GEE_PROXY_URL = "https://gee-proxy-787413290356.us-east1.run.app"


def ensure_dirs():
    """Create all required data directories."""
    for d in [RAW_DIR, PROCESSED_DIR, TILES_DIR]:
        d.mkdir(parents=True, exist_ok=True)
