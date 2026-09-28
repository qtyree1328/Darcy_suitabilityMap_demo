"""
Export V2 web-ready assets for the Darcy-style interactive map.

Copies processed GeoJSON and suitability overlay to data/tiles_v2/.
Output: data/tiles_v2/ directory with all V2 web map assets.
"""
import json
import shutil
from pathlib import Path

PROCESSED = Path("data/processed")
TILES_V1 = Path("data/tiles")
TILES_V2 = Path("data/tiles_v2")
TILES_V2.mkdir(parents=True, exist_ok=True)

# ============================================================
# Check suitability overlay
# ============================================================
overlay_path = TILES_V2 / "suitability_overlay_v2.png"
bounds_path = TILES_V2 / "bounds_v2.json"

if overlay_path.exists() and bounds_path.exists():
    with open(bounds_path) as f:
        bd = json.load(f)
    print(f"V2 suitability overlay exists: {overlay_path}")
    print(f"  Bounds: {bd.get('bounds')}")
    print(f"  Stats: {bd.get('stats')}")
else:
    print("WARNING: V2 suitability overlay not found.")
    print("  Run: python scripts/13_gee_suitability_v2.py")
    # Create placeholder
    bounds_json = {
        "bounds": [-96.64, 40.37, -90.14, 43.50],
        "width": 0, "height": 0,
        "placeholder": True,
        "model": "v2_darcy",
    }
    with open(bounds_path, "w") as f:
        json.dump(bounds_json, f, indent=2)

# ============================================================
# Copy vector assets to tiles_v2 directory
# ============================================================
print("\nCopying V2 vector assets...")

# V2-specific assets
v2_assets = {
    "iowa_landform_regions.geojson": PROCESSED / "iowa_landform_regions.geojson",
    "iowa_dtb_scored_zones.geojson": PROCESSED / "iowa_dtb_scored_zones.geojson",
}

# Shared assets
shared_assets = {
    "iowa_sourcewater_wells.geojson": PROCESSED / "iowa_sourcewater_wells.geojson",
    "iowa_boundary.geojson": PROCESSED / "iowa_boundary.geojson",
    "epa_constraints.geojson": PROCESSED / "epa_constraints.geojson",
}

for dest_name, src_path in {**v2_assets, **shared_assets}.items():
    if src_path.exists():
        shutil.copy(src_path, TILES_V2 / dest_name)
        size_kb = src_path.stat().st_size / 1024
        print(f"  Copied {dest_name} ({size_kb:.0f} KB)")
    else:
        # Try V1 tiles as fallback for shared assets
        v1_path = TILES_V1 / dest_name
        if v1_path.exists():
            shutil.copy(v1_path, TILES_V2 / dest_name)
            print(f"  Copied {dest_name} from V1 tiles")
        else:
            print(f"  SKIP {dest_name} (not found)")

print(f"\nAll V2 web assets in {TILES_V2}/")
print("Ready to serve: python -m http.server 8081")
