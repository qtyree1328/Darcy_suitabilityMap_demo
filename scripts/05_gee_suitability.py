"""
Iowa Geothermal Suitability Model — GEE Processing

Computes the full weighted-overlay suitability raster using the
Earth Engine Python API, then downloads a color-mapped PNG overlay
for the web map. Authenticates with the GEE service account.

Factors & Weights:
  - Aquifer presence:            0.27
  - Well density:                0.22
  - Bedrock geology:             0.15
  - Land cover (NLCD):           0.15
  - Environmental constraints:   0.10
  - Hydrologic proximity:        0.11

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
TILES = Path("data/tiles")
PROCESSED.mkdir(parents=True, exist_ok=True)
TILES.mkdir(parents=True, exist_ok=True)

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
print("IOWA GEOTHERMAL SUITABILITY — GEE PROCESSING")
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
    # Fall back to default credentials (e.g. `earthengine authenticate`)
    try:
        ee.Initialize(project=PROJECT)
        print("  Using default credentials")
    except Exception as exc:
        print(f"ERROR: Cannot authenticate with Earth Engine.")
        print(f"  No service account at {SA_PATH}")
        print(f"  And default auth failed: {exc}")
        print()
        print("Fix: set GEE_SERVICE_ACCOUNT_PATH or run `earthengine authenticate`")
        sys.exit(1)

# ============================================================
# 2. IOWA BOUNDARY
# ============================================================
print("\nBuilding suitability model...")

states = ee.FeatureCollection("TIGER/2018/States")
iowa = states.filter(ee.Filter.eq("NAME", "Iowa"))
iowa_geom = iowa.geometry()

# ============================================================
# FACTOR 1: Aquifer Quality (weight: 0.35)
# ============================================================
# USGS Principal Aquifers cover 100% of Iowa — "Other rocks" (999) is the
# catch-all for areas without a named aquifer system.  We assign scores
# by aquifer type so that high-conductivity carbonate/sandstone systems
# dominate the map while "Other rocks" areas score near zero.
#
# AQ_CODE -> geothermal suitability score:
#   312  Cambrian-Ordovician aquifer system   -> 1.0  (deep sandstone, excellent)
#   410  Silurian-Devonian aquifers           -> 0.9  (carbonate, very good)
#   412  Upper carbonate aquifer              -> 0.8  (carbonate, good)
#   503  Mississippian aquifers               -> 0.6  (variable quality)
#   304  Lower Cretaceous aquifers            -> 0.5  (limited extent)
#   999  Other rocks                          -> 0.1  (poor/no aquifer)
print("  Factor 1/7: Aquifer quality...")
try:
    aquifers = ee.FeatureCollection(f"projects/{PROJECT}/assets/aquifers_iowa")
    aquifers.size().getInfo()

    # Map AQ_CODE to a geothermal suitability score property
    def score_aquifer(feat):
        code = ee.Number(feat.get("AQ_CODE"))
        score = (
            ee.Algorithms.If(code.eq(312), 1.0,
            ee.Algorithms.If(code.eq(410), 0.9,
            ee.Algorithms.If(code.eq(412), 0.8,
            ee.Algorithms.If(code.eq(503), 0.6,
            ee.Algorithms.If(code.eq(304), 0.5,
            0.1)))))  # 999 / Other rocks
        )
        return feat.set("aq_score", score)

    aquifers_scored = aquifers.map(score_aquifer)
    aquifer_score = (
        ee.Image(0.1)
        .paint(aquifers_scored, "aq_score")
        .clip(iowa_geom)
        .rename("aquifer")
    )
    print("    -> Using uploaded aquifer asset (scored by AQ_CODE)")
except Exception as exc:
    aquifer_score = ee.Image(0.1).clip(iowa_geom).rename("aquifer")
    print(f"    -> Fallback: uniform 0.1 (no aquifer asset: {exc})")

# ============================================================
# FACTOR 2: Well Density (weight: 0.20)
# ============================================================
print("  Factor 2/7: Well density...")
try:
    wells = ee.FeatureCollection(f"projects/{PROJECT}/assets/iowa_wells")
    wells.size().getInfo()
    # Add a numeric property for reduceToImage, then count occurrences
    wells_with_const = wells.map(lambda f: f.set("ones", 1))
    well_density = (
        wells_with_const
        .reduceToImage(["ones"], ee.Reducer.sum())
        .focal_mean(radius=5000, units="meters")
        .unitScale(0, 50)
        .clamp(0, 1)
        .clip(iowa_geom)
        .rename("wellDensity")
    )
    print("    -> Using uploaded wells asset")
except Exception:
    well_density = ee.Image(0.3).clip(iowa_geom).rename("wellDensity")
    print("    -> Fallback: uniform 0.3 (no wells asset found)")

# ============================================================
# FACTOR 3: Bedrock Geology (weight: 0.15)
# ============================================================
print("  Factor 3/7: Bedrock geology (Iowa DNR)...")
try:
    geology = ee.FeatureCollection(f"projects/{PROJECT}/assets/iowa_bedrock")
    count = geology.size().getInfo()
    # 'score' property contains geothermal suitability score (0.3–0.95)
    # based on thermal conductivity of each formation's dominant lithology
    geo_img = ee.Image(0.4).paint(geology, "score")
    geology_score = geo_img.clip(iowa_geom).rename("geology")
    print(f"    -> Using Iowa DNR bedrock ({count} polygons, scored by lithology)")
except Exception as exc:
    geology_score = ee.Image(0.4).clip(iowa_geom).rename("geology")
    print(f"    -> Fallback: uniform 0.4 ({exc})")

# ============================================================
# FACTOR 4: Land Cover (weight: 0.15)
# ============================================================
print("  Factor 4/7: Land cover (NLCD 2021)...")
nlcd = (
    ee.ImageCollection("USGS/NLCD_RELEASES/2021_REL/NLCD")
    .filter(ee.Filter.eq("system:index", "2021"))
    .first()
    .select("landcover")
    .clip(iowa_geom)
)

land_suit = nlcd.remap(
    [11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95],
    [0, 0, 0.5, 0.5, 0.4, 0.3, 0.8, 0.8, 0.8, 0.8, 0.7, 1.0, 0.9, 0.9, 0.2, 0],
).rename("landcover")

# ============================================================
# FACTOR 5: Environmental Constraints (weight: 0.10)
# ============================================================
print("  Factor 5/7: Environmental constraints...")
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
# FACTOR 6: Hydrologic Proximity (weight: 0.10)
# ============================================================
print("  Factor 6/7: Hydrologic proximity...")
hydro_mask = wetland_mask.Or(water_mask).unmask(0)

hydro_dist = (
    hydro_mask
    .Not()
    .fastDistanceTransform(256, "pixels")
    .sqrt()
    .multiply(30)  # pixels -> meters at 30m resolution
)

hydro_score = (
    hydro_dist.expression(
        "(d <= 250) ? 1.0"
        ": (d <= 1000) ? 0.8"
        ": (d <= 2500) ? 0.5"
        ": (d <= 5000) ? 0.2"
        ": 0.0",
        {"d": hydro_dist},
    )
    .clip(iowa_geom)
    .rename("hydroProximity")
)

# ============================================================
# 3. WEIGHTED OVERLAY (6 factors, remote sensing dropped)
# ============================================================
print("\nComputing weighted overlay (6 factors)...")

# Weights sum to 1.0 — aquifer presence is dominant factor
# Non-aquifer areas score 0.1 vs 1.0 in aquifer areas, creating
# a ~0.315 raw-score gap (the largest single-factor swing)
suitability_raw = (
    aquifer_score.multiply(0.35)
    .add(well_density.multiply(0.20))
    .add(geology_score.multiply(0.12))
    .add(land_suit.multiply(0.12))
    .add(constraint_score.multiply(0.10))
    .add(hydro_score.multiply(0.11))
    .multiply(100)
)

# Smooth to remove satellite swath artifacts that get amplified by equalization
suitability = (
    suitability_raw
    .focal_mean(radius=3, kernelType="circle", units="pixels")
    .clip(iowa_geom)
    .rename("suitability")
)

# ============================================================
# 4. COMPUTE PERCENTILE STATS & EQUALIZE
# ============================================================
print("Computing suitability statistics for histogram equalization...")

stats = suitability.reduceRegion(
    reducer=ee.Reducer.percentile([2, 10, 25, 50, 75, 90, 98])
        .combine(ee.Reducer.mean(), sharedInputs=True)
        .combine(ee.Reducer.stdDev(), sharedInputs=True),
    geometry=iowa_geom,
    scale=500,
    maxPixels=1e9,
).getInfo()

p2 = stats.get("suitability_p2", 40)
p10 = stats.get("suitability_p10", 45)
p25 = stats.get("suitability_p25", 50)
p50 = stats.get("suitability_p50", 55)
p75 = stats.get("suitability_p75", 65)
p90 = stats.get("suitability_p90", 70)
p98 = stats.get("suitability_p98", 80)
mean_val = stats.get("suitability_mean", 60)
std_val = stats.get("suitability_stdDev", 10)

print(f"  p2={p2:.1f}  p10={p10:.1f}  p25={p25:.1f}  p50={p50:.1f}")
print(f"  p75={p75:.1f}  p90={p90:.1f}  p98={p98:.1f}")
print(f"  mean={mean_val:.1f}  std={std_val:.1f}")

# Apply cumulative-distribution equalization:
# Remap the suitability so that the full 0-1 range is used
# across the actual data distribution. This spreads the narrow
# cluster of values into a full visual gradient.
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
print("Downloading suitability overlay from GEE (this may take 1-2 minutes)...")

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

r = requests.get(url, timeout=300)
r.raise_for_status()

out_path = TILES / "suitability_overlay.png"
with open(out_path, "wb") as f:
    f.write(r.content)
print(f"  Saved {out_path} ({len(r.content) // 1024} KB)")

# ============================================================
# 6. SAVE BOUNDS FOR WEB MAP
# ============================================================
print("Fetching Iowa geometry bounds...")
# Use the exact bounding box from the GEE geometry to match the thumbnail
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
    "stats": {
        "p2": round(p2, 1), "p50": round(p50, 1), "p98": round(p98, 1),
        "mean": round(mean_val, 1), "std": round(std_val, 1),
    },
}

with open(TILES / "bounds.json", "w") as f:
    json.dump(bounds_json, f, indent=2)
print(f"  Bounds: [{west:.2f}, {south:.2f}, {east:.2f}, {north:.2f}]")

# ============================================================
# 6. ALSO SAVE GEE SCRIPT FOR REFERENCE / CODE EDITOR
# ============================================================
GEE_SCRIPT = """
// ============================================================
// IOWA GEOTHERMAL SUITABILITY MODEL
// Darcy Solutions Demo — Tyree Spatial
// Paste into https://code.earthengine.google.com/ to run
// ============================================================

var states = ee.FeatureCollection("TIGER/2018/States");
var iowa = states.filter(ee.Filter.eq('NAME', 'Iowa'));
var iowaGeom = iowa.geometry();

// Factor 1: Aquifer Presence (0.25)
var aquiferScore = ee.Image(0.5).clip(iowaGeom).rename('aquifer');

// Factor 2: Well Density (0.20)
var wellDensity = ee.Image(0.3).clip(iowaGeom).rename('wellDensity');

// Factor 3: Bedrock Geology (0.15)
var geologyScore = ee.Image(0.7).clip(iowaGeom).rename('geology');

// Factor 4: Land Cover (0.15)
var nlcd = ee.ImageCollection("USGS/NLCD_RELEASES/2021_REL/NLCD")
    .filter(ee.Filter.eq('system:index', '2021')).first()
    .select('landcover').clip(iowaGeom);
var landSuit = nlcd.remap(
    [11,12,21,22,23,24,31,41,42,43,52,71,81,82,90,95],
    [0,0,0.5,0.5,0.4,0.3,0.8,0.8,0.8,0.8,0.7,1.0,0.9,0.9,0.2,0]
).rename('landcover');

// Factor 5: Environmental Constraints (0.10)
var wetlandMask = nlcd.eq(90).or(nlcd.eq(95));
var waterMask = nlcd.eq(11);
var constraintScore = ee.Image(1.0).where(wetlandMask,0).where(waterMask,0)
    .clip(iowaGeom).rename('constraints');

// Factor 6: Hydrologic Proximity (0.10)
var hydroMask = wetlandMask.or(waterMask).unmask(0);
var hydroDist = hydroMask.Not().fastDistanceTransform(256,'pixels').sqrt().multiply(30);
var hydroScore = hydroDist.expression(
    "(d<=250)?1.0:(d<=1000)?0.8:(d<=2500)?0.5:(d<=5000)?0.2:0.0",{d:hydroDist}
).clip(iowaGeom).rename('hydroProximity');

// Factor 7: Remote Sensing (0.05)
var landsat = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
    .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
    .filterBounds(iowaGeom).filterDate('2024-06-01','2024-08-31').select('ST_B10')
    .map(function(img){return img.multiply(0.00341802).add(149).subtract(273.15)
        .copyProperties(img,['system:time_start']);});
var lstMedian = landsat.median().clip(iowaGeom);
var lstMean = lstMedian.reduceRegion({reducer:ee.Reducer.mean(),geometry:iowaGeom,
    scale:1000,maxPixels:1e9}).get('ST_B10');
var lstScore = lstMedian.expression("0.5+clamp((mean-lst)*0.1,-0.5,0.5)",
    {lst:lstMedian,mean:ee.Number(lstMean)}).rename('lst_score');
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(iowaGeom)
    .filterDate('2024-06-01','2024-08-31')
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE',20)).median().clip(iowaGeom);
var ndwi = s2.normalizedDifference(['B3','B8']).rename('NDWI');
var ndwiScore = ndwi.expression("clamp(ndwi+0.5,0,1)",{ndwi:ndwi}).rename('ndwi_score');
var rsScore = lstScore.add(ndwiScore).divide(2).clamp(0,1).rename('remoteSensing');

// Weighted Overlay
var suitability = aquiferScore.multiply(0.25).add(wellDensity.multiply(0.20))
    .add(geologyScore.multiply(0.15)).add(landSuit.multiply(0.15))
    .add(constraintScore.multiply(0.10)).add(hydroScore.multiply(0.10))
    .add(rsScore.multiply(0.05)).multiply(100).rename('suitability');

Map.centerObject(iowa, 7);
Map.addLayer(suitability, {min:0,max:100,palette:['dc2626','f97316','eab308','84cc16','22c55e']}, 'Suitability');
"""

script_path = PROCESSED / "gee_suitability_script.js"
with open(script_path, "w") as f:
    f.write(GEE_SCRIPT)

print()
print("=" * 60)
print("DONE")
print("=" * 60)
print(f"  Suitability overlay: {TILES / 'suitability_overlay.png'}")
print(f"  Bounds metadata:     {TILES / 'bounds.json'}")
print(f"  GEE script (ref):    {script_path}")
print()
print("Next: python scripts/06_export_web_assets.py")
print("Then: python -m http.server 8080")
