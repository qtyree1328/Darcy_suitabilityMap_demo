"""
Iowa Darcy-Style Geothermal Suitability Model V2 — GEE Processing

Computes a 4-factor weighted-overlay suitability raster aligned with
Darcy Solutions' shallow geothermal screening approach.

V2 Factors & Weights:
  - Aquifer Productivity:              0.65  (primary: ground-truth well data + regional propagation)
  - Depth to Bedrock:                  0.15  (supplemental)
  - Land Cover / Developability:       0.10  (supplemental)
  - Environmental Constraints:         0.10  (supplemental)

Aquifer Productivity uses pure geologic propagation (no spatial buffers):
  - 7,299 Iowa DNR Sourcewater wells are scored 0–1 using 8 hydrogeologic
    parameters (pump test GPM, specific capacity, transmissivity, hydraulic
    conductivity, static water level, aquifer thickness, aquifer type,
    confined/unconfined status).
  - Each well is assigned to its Iowa DNR Landform Region — a geologic
    province defined by shared depositional history, surficial material,
    and hydrogeomorphic character.
  - The average well score per province is painted onto the landform
    polygons, producing a continuous statewide raster with no gaps.
  - Areas without nearby wells receive the measured average of wells
    in the same geologic province.

The supplemental factors (DTB, land cover, constraints) provide additional
spatial differentiation for practical siting considerations.

Prerequisites:
  pip install earthengine-api requests
  Service account JSON at ../../service-account.json (or GEE_SERVICE_ACCOUNT_PATH)
"""
import os
import sys
import json
import requests
from pathlib import Path

PROCESSED = Path("data/processed")
TILES_V2 = Path("data/tiles_v2")
PROCESSED.mkdir(parents=True, exist_ok=True)
TILES_V2.mkdir(parents=True, exist_ok=True)

PROJECT = os.environ.get("EE_PROJECT", "generalresearch-478019")
_default_sa_paths = [
    Path(__file__).resolve().parent.parent.parent / "service-account.json",
    Path.home() / "Downloads" / "service-account.json",
    Path(__file__).resolve().parent.parent / "service-account.json",
]
SA_PATH = os.environ.get("GEE_SERVICE_ACCOUNT_PATH", "")
if not SA_PATH:
    for p in _default_sa_paths:
        if p.exists():
            SA_PATH = str(p)
            break
    else:
        SA_PATH = str(_default_sa_paths[0])

# ============================================================
# 1. AUTHENTICATE
# ============================================================
try:
    import ee
except ImportError:
    print("ERROR: earthengine-api not installed.")
    print("  pip install earthengine-api")
    sys.exit(1)

print("=" * 60)
print("IOWA DARCY-STYLE GEOTHERMAL SUITABILITY — V2")
print("=" * 60)
print()
print("Authenticating with Google Earth Engine...")

if os.path.exists(SA_PATH):
    with open(SA_PATH) as f:
        sa_info = json.load(f)
    credentials = ee.ServiceAccountCredentials(sa_info["client_email"], SA_PATH)
    ee.Initialize(credentials=credentials, project=PROJECT)
    print(f"  Authenticated as {sa_info['client_email']}")
else:
    try:
        ee.Initialize(project=PROJECT)
        print("  Using default credentials")
    except Exception as exc:
        print(f"ERROR: Cannot authenticate with Earth Engine.")
        print(f"  No service account at {SA_PATH}")
        print(f"  And default auth failed: {exc}")
        sys.exit(1)

# ============================================================
# 2. IOWA BOUNDARY
# ============================================================
print("\nBuilding Darcy-style suitability model (4 factors)...")

states = ee.FeatureCollection("TIGER/2018/States")
iowa = states.filter(ee.Filter.eq("NAME", "Iowa"))
iowa_geom = iowa.geometry()

# ============================================================
# FACTOR 1: Aquifer Productivity (weight: 0.65) — PRIMARY FACTOR
# ============================================================
# Pure geologic propagation — NO spatial buffers, NO focal_mean:
#
# Ground-truth well data (7,299 DNR Sourcewater wells scored 0–1 using
# 8 hydrogeologic parameters) is propagated to ALL areas via geologic
# similarity. Each Iowa DNR Landform Region (a geologic province defined
# by shared depositional history, surficial material, and hydrogeomorphic
# character) receives the average productivity_score of all wells within it.
#
# This is NOT a spatial buffer. It is geologic classification:
#   - Wells in alluvial provinces → alluvial province average
#   - Wells in glacial drift provinces → drift province average
#   - Wells in loess provinces → loess province average
#
# Areas without nearby wells still receive the score of their geologic
# province, because they share the same surficial geology, depositional
# history, and aquifer characteristics as the wells in that province.
#
# The resulting raster is continuous, gap-free, and driven entirely by
# measured hydrogeologic data propagated through geologic features.
print("  Factor 1/4: Aquifer productivity (0.65)...")
try:
    landforms = ee.FeatureCollection(f"projects/{PROJECT}/assets/iowa_landform_regions")
    lf_count = landforms.size().getInfo()

    # Paint well-derived regional_productivity onto landform polygons.
    # Each polygon's score is the average of all DNR Sourcewater well
    # productivity_scores within that geologic province.
    # Default 0.5 for any unpainted area (should not occur — statewide coverage).
    aquifer_productivity = (
        ee.Image(0.5)
        .paint(landforms, "regional_productivity")
        .clip(iowa_geom)
        .rename("aquiferProductivity")
    )
    print(f"    -> Geologic propagation: {lf_count} landform features")
    print(f"    -> Well ground truth averaged per geologic province (no spatial buffers)")

except Exception as exc:
    aquifer_productivity = ee.Image(0.5).clip(iowa_geom).rename("aquiferProductivity")
    print(f"    -> Fallback: uniform 0.5 ({exc})")

# ============================================================
# FACTOR 2: Depth to Bedrock / Shallow Drilling Window (weight: 0.15)
# ============================================================
# Scored contour zones: 50–150 ft = 1.0 (ideal Darcy target)
# 0–20 ft = 0.2, 20–50 ft = 0.6, 150–250 ft = 0.6, >250 ft = 0.3
print("  Factor 2/4: Depth to bedrock (0.15)...")
try:
    dtb_zones = ee.FeatureCollection(f"projects/{PROJECT}/assets/iowa_dtb_zones")
    count = dtb_zones.size().getInfo()
    dtb_score = (
        ee.Image(0.6)
        .paint(dtb_zones, "dtb_score")
        .clip(iowa_geom)
        .rename("depthToBedrock")
    )
    print(f"    -> Using interpolated depth-to-bedrock zones ({count} features)")
except Exception as exc:
    dtb_score = ee.Image(0.6).clip(iowa_geom).rename("depthToBedrock")
    print(f"    -> Fallback: uniform 0.6 ({exc})")

# ============================================================
# FACTOR 3: Land Cover / Developability (weight: 0.10)
# ============================================================
# NLCD 2021 scored for developability:
#   Grassland/open = 1.0, Agriculture = 0.9, Forest = 0.7,
#   Suburban = 0.5, Urban = 0.2, Wetlands/Water = 0.0
print("  Factor 3/4: Land cover (0.10)...")
nlcd = (
    ee.ImageCollection("USGS/NLCD_RELEASES/2021_REL/NLCD")
    .filter(ee.Filter.eq("system:index", "2021"))
    .first()
    .select("landcover")
    .clip(iowa_geom)
)

land_suit = nlcd.remap(
    [11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95],
    [0, 0, 0.5, 0.5, 0.4, 0.2, 0.8, 0.7, 0.7, 0.7, 0.7, 1.0, 0.9, 0.9, 0.0, 0],
).rename("landcover")

# ============================================================
# FACTOR 4: Environmental Constraints (weight: 0.10)
# ============================================================
# Wetlands/water = 0, Superfund 500m buffer = 0, elsewhere = 1
print("  Factor 4/4: Environmental constraints (0.10)...")
wetland_mask = nlcd.eq(90).Or(nlcd.eq(95))
water_mask = nlcd.eq(11)

constraint_score = (
    ee.Image(1.0)
    .where(wetland_mask, 0.0)
    .where(water_mask, 0.0)
    .clip(iowa_geom)
    .rename("constraints")
)

try:
    epa = ee.FeatureCollection(f"projects/{PROJECT}/assets/epa_constraints")
    epa.size().getInfo()
    epa_buffered = epa.map(lambda f: f.buffer(500))
    epa_img = ee.Image(1).paint(epa_buffered, 0)
    constraint_score = constraint_score.min(epa_img).rename("constraints")
    print("    -> Including EPA constraint buffers")
except Exception:
    print("    -> Using NLCD wetland/water proxy only")

# ============================================================
# 3. WEIGHTED OVERLAY (4 factors)
# ============================================================
W_AQUIFER = 0.650
W_DTB = 0.150
W_LAND = 0.100
W_CONSTRAINTS = 0.100

print(f"\nComputing weighted overlay (4 factors)...")
print(f"  Weights: Aquifer={W_AQUIFER}, DTB={W_DTB}, "
      f"Land={W_LAND}, Constraints={W_CONSTRAINTS}")
print(f"  Sum: {W_AQUIFER + W_DTB + W_LAND + W_CONSTRAINTS}")

suitability_raw = (
    aquifer_productivity.multiply(W_AQUIFER)
    .add(dtb_score.multiply(W_DTB))
    .add(land_suit.multiply(W_LAND))
    .add(constraint_score.multiply(W_CONSTRAINTS))
    .multiply(100)
)

# Smooth to reduce artifacts
suitability = (
    suitability_raw
    .focal_mean(radius=3, kernelType="circle", units="pixels")
    .clip(iowa_geom)
    .rename("suitability")
)

# ============================================================
# 4. COMPUTE PERCENTILE STATS & EQUALIZE
# ============================================================
print("Computing suitability statistics...")

stats = suitability.reduceRegion(
    reducer=ee.Reducer.percentile([2, 10, 25, 50, 75, 90, 98])
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.stdDev(), sharedInputs=True),
    geometry=iowa_geom,
    scale=500,
    maxPixels=1e9,
).getInfo()

p2 = stats.get("suitability_p2", 30)
p10 = stats.get("suitability_p10", 35)
p25 = stats.get("suitability_p25", 40)
p50 = stats.get("suitability_p50", 50)
p75 = stats.get("suitability_p75", 60)
p90 = stats.get("suitability_p90", 70)
p98 = stats.get("suitability_p98", 80)
mean_val = stats.get("suitability_mean", 50)
std_val = stats.get("suitability_stdDev", 15)

print(f"  p2={p2:.1f}  p10={p10:.1f}  p25={p25:.1f}  p50={p50:.1f}")
print(f"  p75={p75:.1f}  p90={p90:.1f}  p98={p98:.1f}")
print(f"  mean={mean_val:.1f}  std={std_val:.1f}")

# Histogram equalization
suit_equalized = (
    suitability
    .unitScale(p2, p98)
    .clamp(0, 1)
    .multiply(100)
    .rename("suitability")
)

# ============================================================
# 5. DOWNLOAD SUITABILITY PNG OVERLAY
# ============================================================
print("\nDownloading V2 suitability overlay (this may take 1-2 minutes)...")

thumb_params = {
    "min": 0,
    "max": 100,
    "palette": [
        "67000d", "a50f15", "cb181d", "ef3b2c", "fb6a4a",
        "fc9272", "fcbba1", "fee0d2", "fee5ce", "fdd0a2",
        "fdae6b", "fd8d3c", "f16913", "d94801", "e6550d",
        "fdae6b", "fee391", "fff7bc", "ffffe5", "f7fcb9",
        "d9f0a3", "addd8e", "78c679", "41ab5d", "238443",
        "006837", "004529",
    ],
    "dimensions": 2048,
    "region": iowa_geom,
    "crs": "EPSG:4326",
    "format": "png",
}

url = suit_equalized.getThumbURL(thumb_params)
print(f"  Thumbnail URL obtained, downloading...")

r = requests.get(url, timeout=600)
r.raise_for_status()

out_path = TILES_V2 / "suitability_overlay_v2.png"
with open(out_path, "wb") as f:
    f.write(r.content)
print(f"  Saved {out_path} ({len(r.content) // 1024} KB)")

# ============================================================
# 6. SAVE BOUNDS FOR WEB MAP
# ============================================================
print("Fetching Iowa geometry bounds...")
bbox = iowa_geom.bounds()
bbox_coords = bbox.getInfo()["coordinates"][0]
west = bbox_coords[0][0]
south = bbox_coords[0][1]
east = bbox_coords[2][0]
north = bbox_coords[2][1]

bounds_json = {
    "bounds": [west, south, east, north],
    "width": 2048,
    "height": 0,
    "model": "v2_darcy",
    "factors": {
        "aquifer_productivity": W_AQUIFER,
        "depth_to_bedrock": W_DTB,
        "land_cover": W_LAND,
        "environmental_constraints": W_CONSTRAINTS,
    },
    "stats": {
        "p2": round(p2, 1), "p50": round(p50, 1), "p98": round(p98, 1),
        "mean": round(mean_val, 1), "std": round(std_val, 1),
    },
}

with open(TILES_V2 / "bounds_v2.json", "w") as f:
    json.dump(bounds_json, f, indent=2)
print(f"  Bounds: [{west:.2f}, {south:.2f}, {east:.2f}, {north:.2f}]")

print()
print("=" * 60)
print("DONE — V2 Darcy-Style Model")
print("=" * 60)
print(f"  Suitability overlay: {TILES_V2 / 'suitability_overlay_v2.png'}")
print(f"  Bounds metadata:     {TILES_V2 / 'bounds_v2.json'}")
print()
print("Next: python scripts/14_export_web_assets_v2.py")
print("Then: python -m http.server 8081")
