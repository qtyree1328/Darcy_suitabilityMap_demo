# Darcy Iowa Geothermal Suitability — Code Agent Handoff

**Author:** Quintin Tyree · Tyree Spatial  
**Date:** March 10, 2026  
**Target:** 12-hour sprint → live interactive web map  
**Audience:** Code agent executing this plan end-to-end

---

## What You're Building

An automated geothermal site screening tool for Iowa that:

1. Ingests public datasets (all free, most scriptable)
2. Computes a **30m statewide suitability raster** via Google Earth Engine
3. Serves it as an **interactive MapLibre web map**
4. Generates an **automated site inquiry report** when the user clicks any location

The system answers: **"What is the geothermal suitability at this location and why?"**

---

## Project Structure

```
darcy-iowa/
├── README.md
├── gee-proxy.js                  ← PROVIDED (GEE Cloud Run proxy, already exists)
├── scripts/
│   ├── 01_fetch_wells.py         ← USGS NWIS well locations
│   ├── 02_fetch_aquifers.py      ← USGS Principal Aquifers shapefile
│   ├── 03_fetch_constraints.py   ← EPA Superfund/Brownfields
│   ├── 04_upload_gee_assets.py   ← Upload vector data as GEE assets
│   ├── 05_gee_suitability.py     ← Full suitability model in GEE (main script)
│   ├── 06_export_web_assets.py   ← Convert outputs to web-ready formats
│   └── utils.py
├── data/
│   ├── raw/
│   ├── processed/
│   └── tiles/
└── index.html                    ← Final deliverable
```

---

## System Architecture

```
Public datasets (USGS, NOAA, FEMA, ESA, NASA)
         ↓
  Automated ingestion (Python scripts 01–03)
         ↓
  Upload to GEE as assets (script 04)
         ↓
  Google Earth Engine processing (script 05)
    - NLCD land cover (native GEE)
    - Landsat LST composite (native GEE)
    - Sentinel-2 NDWI composite (native GEE)
    - Well density kernel
    - Aquifer rasterization
    - Geology classification
    - Hydrologic proximity (distance transform)
    - Environmental constraints
    - Weighted overlay → suitability raster
         ↓
  Export as COG (via GEE proxy)
         ↓
  Convert to PNG overlay + GeoJSON (script 06)
         ↓
  MapLibre GL JS interactive map (index.html)
    - Suitability heatmap
    - Toggleable factor layers
    - Click → automated site inquiry report
```

---

## Key Design Decisions

| Decision | Rationale |
|---|---|
| **GEE-centric processing** | Avoids downloading massive rasters locally. NLCD, Landsat, Sentinel-2, NHD all native in GEE. Your proxy is already running. |
| **Well density, not depth interpolation** | NWIS depth measurements are sparse and inconsistent. Kernel density of well locations is a cleaner proxy for "productive aquifer zones." |
| **Hydrologic proximity (distance decay)** | Smooth gradients instead of hard binary cutoffs. Uses GEE `fastDistanceTransform`. Looks professional, models reality better. |
| **Remote sensing indicators (5% weight)** | Landsat LST + Sentinel-2 NDWI are free, native GEE, zero-download. Low weight but high demo impact — signals capability Darcy doesn't have in-house. |
| **Site inquiry on click** | The real sales differentiator. Static heatmaps are nice; click-to-report is a tool. Darcy evaluates individual parcels — this maps to their workflow. |

---

## CRS & Grid

```
Resolution:  30 meters
Projection:  EPSG:26915 (UTM 15N)
Extent:      Iowa state boundary
Alignment:   Matches NLCD native grid
```

---

## Suitability Model

### Factors & Weights

| Factor | Weight | Source | Scoring Logic |
|---|---|---|---|
| **Aquifer presence** | 0.25 | USGS Principal Aquifers | aquifer polygon = 1.0, no aquifer = 0.5 |
| **Well density** | 0.20 | USGS NWIS API | Kernel density (5 km radius), unitScale 0–1. Dense wells = productive aquifer. |
| **Bedrock geology** | 0.15 | Iowa bedrock geology shapefile | limestone/dolomite = 1.0, sandstone = 0.9, crystalline = 0.8, shale = 0.6 |
| **Land cover** | 0.15 | NLCD 2021 (native GEE) | grassland = 1.0, ag = 0.9, forest = 0.8, suburban = 0.5, urban = 0.3, water = 0 |
| **Environmental constraints** | 0.10 | FEMA + NWI + EPA | wetland = 0, flood zone = 0.2, superfund/brownfield (500m buffer) = 0, else = 1.0 |
| **Hydrologic proximity** | 0.10 | NHD + NWI (GEE distance transform) | 0–250m = 1.0, 250–1000m = 0.8, 1000–2500m = 0.5, 2500–5000m = 0.2, >5000m = 0 |
| **Remote sensing** | 0.05 | Landsat 8/9 LST + Sentinel-2 NDWI | Thermal anomaly + moisture index. Below-median LST = bonus. High NDWI = bonus. |

### Suitability Equation

```
Suitability = 0.25 * Aquifer
            + 0.20 * WellDensity
            + 0.15 * Geology
            + 0.15 * LandCover
            + 0.10 * Constraints
            + 0.10 * HydroProximity
            + 0.05 * RemoteSensing
```

Output: **0–100 geothermal suitability score** (multiply 0–1 result by 100).

### Confidence Layer

Confidence is estimated from well density:
- High well density → high confidence
- Low well density → lower confidence (sparse data region)

Computed as: distance to nearest well, normalized. < 1 km = high, > 50 km = low.

---

## Validation Targets

| Location | Expected Score | Why |
|---|---|---|
| Des Moines metro (alluvial valley) | HIGH (75+) | Raccoon/Des Moines River alluvial aquifer, shallow water table, dense commercial buildings |
| Cedar Rapids / Iowa City | HIGH (70+) | Cedar River alluvial deposits, U of Iowa, hospitals |
| Sioux City / Council Bluffs | MODERATE-HIGH (60–75) | Missouri River alluvium, less building density |
| NW Iowa (Spencer, Storm Lake) | MODERATE (40–55) | Thick glacial till, deeper water, smaller towns |
| NE Iowa (Decorah) | VARIABLE (30–65) | Karst terrain, Silurian-Devonian carbonate — good aquifers but complex hydrology |
| South-central Iowa (loess plains) | LOW-MODERATE (30–45) | Thick loess cap, limited shallow aquifer, sparse development |

---

## Script 01: Fetch Wells — `scripts/01_fetch_wells.py`

```python
"""
Fetch Iowa groundwater well locations from USGS NWIS REST API.
No API key required.
Output: data/processed/iowa_wells.geojson
"""
import requests
import json
from pathlib import Path

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

print("Fetching USGS NWIS groundwater well sites for Iowa...")

# NWIS site service — get all GW sites in Iowa
url = (
    "https://waterservices.usgs.gov/nwis/site/"
    "?format=rdb&stateCd=IA&siteType=GW&siteOutput=expanded&hasDataTypeCd=gw"
)

r = requests.get(url, timeout=120)
r.raise_for_status()

# Parse RDB (tab-delimited with comment lines)
lines = r.text.strip().split("\n")
header_idx = None
for i, line in enumerate(lines):
    if line.startswith("#"):
        continue
    if header_idx is None:
        header_idx = i
        headers = line.split("\t")
        continue
    if line.startswith("5s") or line.startswith("-"):  # format line
        continue
    break

data_lines = [l for l in lines[header_idx:] if not l.startswith("#") and not l.startswith("5")]
if len(data_lines) < 2:
    # Fallback: try JSON format
    print("RDB parse issue, trying JSON format...")
    url_json = (
        "https://waterservices.usgs.gov/nwis/site/"
        "?format=json&stateCd=IA&siteType=GW"
    )
    r = requests.get(url_json, timeout=120)
    r.raise_for_status()
    # JSON format has different structure — parse accordingly

# Alternative approach: use the NWIS water data API
print("Using NWIS OGC API for well locations...")
wells_url = (
    "https://api.waterdata.usgs.gov/ogcapi/v0/collections/monitoring-locations/items"
    "?stateFIPS=US:19&monitoringLocationType=Well&limit=10000"
    "&f=json"
)

features = []
next_url = wells_url
page = 0

while next_url and page < 20:  # Safety cap at 20 pages
    print(f"  Fetching page {page + 1}...")
    r = requests.get(next_url, timeout=120)
    r.raise_for_status()
    data = r.json()

    batch = data.get("features", [])
    features.extend(batch)
    print(f"  Got {len(batch)} wells (total: {len(features)})")

    # Check for next page
    next_url = None
    for link in data.get("links", []):
        if link.get("rel") == "next":
            next_url = link.get("href")
            break
    page += 1

# Build GeoJSON
geojson = {
    "type": "FeatureCollection",
    "features": []
}

for f in features:
    coords = f.get("geometry", {}).get("coordinates")
    props = f.get("properties", {})
    if coords and len(coords) >= 2:
        geojson["features"].append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": coords[:2]},
            "properties": {
                "site_id": props.get("monitoringLocationIdentifier", ""),
                "name": props.get("monitoringLocationName", ""),
                "well_depth_ft": props.get("wellDepthMeasure", {}).get("value") if isinstance(props.get("wellDepthMeasure"), dict) else props.get("wellDepthMeasure"),
                "aquifer": props.get("aquiferName", ""),
            }
        })

out_path = PROCESSED / "iowa_wells.geojson"
with open(out_path, "w") as f:
    json.dump(geojson, f)

print(f"\nSaved {len(geojson['features'])} wells to {out_path}")
```

---

## Script 02: Fetch Aquifers — `scripts/02_fetch_aquifers.py`

```python
"""
Download USGS Principal Aquifers shapefile and clip to Iowa.
Also downloads Iowa state boundary from Census TIGER.
Output: data/processed/aquifers_iowa.geojson, data/processed/iowa_boundary.geojson
"""
import os
import requests
import zipfile
import geopandas as gpd
from pathlib import Path

RAW = Path("data/raw")
PROCESSED = Path("data/processed")
RAW.mkdir(parents=True, exist_ok=True)
PROCESSED.mkdir(parents=True, exist_ok=True)

IOWA_FIPS = "19"

# --- Iowa state boundary ---
print("Downloading Census TIGER state boundaries...")
states_url = "https://www2.census.gov/geo/tiger/TIGER2023/STATE/tl_2023_us_state.zip"
states_zip = RAW / "tl_2023_us_state.zip"
if not states_zip.exists():
    r = requests.get(states_url, stream=True, timeout=120)
    r.raise_for_status()
    with open(states_zip, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

states = gpd.read_file(f"zip://{states_zip}")
iowa = states[states["STATEFP"] == IOWA_FIPS]
iowa_4326 = iowa.to_crs("EPSG:4326")
iowa_4326.to_file(PROCESSED / "iowa_boundary.geojson", driver="GeoJSON")
print(f"Saved Iowa boundary")

# --- USGS Principal Aquifers ---
print("\nDownloading USGS Principal Aquifers...")
aquifer_url = "https://water.usgs.gov/GIS/dsdl/aquifers_us.zip"
aquifer_zip = RAW / "aquifers_us.zip"
if not aquifer_zip.exists():
    r = requests.get(aquifer_url, stream=True, timeout=300)
    r.raise_for_status()
    with open(aquifer_zip, "wb") as f:
        for chunk in r.iter_content(8192):
            f.write(chunk)

aquifer_dir = RAW / "aquifers"
if not aquifer_dir.exists():
    aquifer_dir.mkdir()
    with zipfile.ZipFile(aquifer_zip) as z:
        z.extractall(aquifer_dir)

shp_files = list(aquifer_dir.rglob("*.shp"))
print(f"Found shapefiles: {[s.name for s in shp_files]}")

gdf = gpd.read_file(shp_files[0])
print(f"Aquifer columns: {list(gdf.columns)}")
print(f"Total features: {len(gdf)}")

# Clip to Iowa
iowa_proj = iowa.to_crs(gdf.crs)
aquifers_iowa = gpd.clip(gdf, iowa_proj)
aquifers_iowa_4326 = aquifers_iowa.to_crs("EPSG:4326")
aquifers_iowa_4326.to_file(PROCESSED / "aquifers_iowa.geojson", driver="GeoJSON")
print(f"Saved {len(aquifers_iowa)} aquifer polygons for Iowa")

# Print unique aquifer types for scoring reference
for col in ["AQ_NAME", "AQ_CODE", "ROCK_TYPE", "NAT_AQFR_CD"]:
    if col in aquifers_iowa.columns:
        print(f"\n  {col} values: {aquifers_iowa[col].unique().tolist()}")
```

---

## Script 03: Fetch Constraints — `scripts/03_fetch_constraints.py`

```python
"""
Fetch EPA Superfund/Brownfield sites for Iowa.
No API key required.
Output: data/processed/epa_constraints.geojson
"""
import requests
import json
from pathlib import Path

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

features = []

# --- Superfund (CERCLIS) ---
print("Fetching EPA Superfund sites for Iowa...")
try:
    url = "https://enviro.epa.gov/enviro/efservice/CERCLIS/SITE_STATE/IA/JSON"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    for s in r.json():
        lat, lon = s.get("LATITUDE"), s.get("LONGITUDE")
        if lat and lon:
            try:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                    "properties": {
                        "site_id": s.get("SITE_EPA_ID", ""),
                        "name": s.get("SITE_NAME", ""),
                        "type": "superfund"
                    }
                })
            except (ValueError, TypeError):
                pass
    print(f"  Found {len(features)} Superfund sites")
except Exception as e:
    print(f"  Superfund fetch failed: {e}")

# --- Brownfields ---
print("Fetching EPA Brownfield sites for Iowa...")
try:
    url = "https://enviro.epa.gov/enviro/efservice/BROWNFIELDS_GEO/STATE_CODE/IA/JSON"
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    count_before = len(features)
    for s in r.json():
        lat = s.get("LATITUDE83")
        lon = s.get("LONGITUDE83")
        if lat and lon:
            try:
                features.append({
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                    "properties": {
                        "site_id": s.get("HANDLER_ID", ""),
                        "name": s.get("PRIMARY_NAME", ""),
                        "type": "brownfield"
                    }
                })
            except (ValueError, TypeError):
                pass
    print(f"  Found {len(features) - count_before} Brownfield sites")
except Exception as e:
    print(f"  Brownfield fetch failed (non-critical): {e}")

geojson = {"type": "FeatureCollection", "features": features}
out_path = PROCESSED / "epa_constraints.geojson"
with open(out_path, "w") as f:
    json.dump(geojson, f)
print(f"\nSaved {len(features)} total EPA constraint sites to {out_path}")
```

---

## Script 04: Upload GEE Assets — `scripts/04_upload_gee_assets.py`

```python
"""
Upload processed vector data as GEE assets for use in the suitability model.

This script uses the Earth Engine Python API to ingest:
  - Iowa wells (GeoJSON → FeatureCollection)
  - Iowa aquifers (GeoJSON → FeatureCollection)
  - EPA constraints (GeoJSON → FeatureCollection)

PREREQUISITES:
  - pip install earthengine-api
  - ee.Authenticate() run once
  - A GEE project ID set as EE_PROJECT env var

If you prefer, you can upload these manually via the GEE Code Editor asset manager:
  https://code.earthengine.google.com/ → Assets → New → Table Upload

If GEE asset upload is blocked or slow, the suitability script (05) can fall back to
loading these as inline FeatureCollections from GeoJSON via the proxy.
"""
import os
import json
import ee
from pathlib import Path

PROCESSED = Path("data/processed")
PROJECT = os.environ.get("EE_PROJECT", "your-gee-project-id")

print("Initializing Earth Engine...")
try:
    ee.Initialize(project=PROJECT)
except Exception:
    print("EE not authenticated. Run: earthengine authenticate")
    print("Or upload assets manually via the GEE Code Editor.")
    print("The suitability script can also load GeoJSON directly via the proxy.")
    exit(0)

assets = {
    "iowa_wells": PROCESSED / "iowa_wells.geojson",
    "aquifers_iowa": PROCESSED / "aquifers_iowa.geojson",
    "epa_constraints": PROCESSED / "epa_constraints.geojson",
}

for name, path in assets.items():
    if not path.exists():
        print(f"  SKIP {name}: {path} not found")
        continue

    asset_id = f"projects/{PROJECT}/assets/{name}"
    print(f"  Uploading {name} → {asset_id}")

    # For large files, use gsutil + ee.data.startIngestion
    # For small files (<10MB), inline upload works
    with open(path) as f:
        geojson = json.load(f)

    n_features = len(geojson.get("features", []))
    print(f"    {n_features} features")

    # Note: programmatic asset upload is complex. For the sprint,
    # the recommended approach is:
    #   1. Upload via GEE Code Editor UI (drag and drop)
    #   2. Or use: earthengine upload table --asset_id=<id> <file>
    print(f"    CLI command: earthengine upload table --asset_id={asset_id} {path}")

print("\nAlternative: pass GeoJSON directly to the GEE proxy in script 05.")
print("Your proxy can accept inline FeatureCollections without needing assets.")
```

---

## Script 05: GEE Suitability Model — `scripts/05_gee_suitability.py`

**This is the main script.** It sends the full suitability computation to GEE via your proxy.

```python
"""
Compute Iowa geothermal suitability raster via Google Earth Engine.

This script builds the full GEE computation graph and sends it to your
gee-proxy.js for server-side execution and COG export.

All heavy processing happens in GEE — no local raster downloads needed
until the final export.

USAGE:
    export GEE_PROXY_URL=http://localhost:3000  # or your Cloud Run URL
    python scripts/05_gee_suitability.py

PROXY CONTRACT:
    Your gee-proxy.js must accept POST /evaluate with a JSON body containing
    an Earth Engine computation specification and return either:
      A) A COG GeoTIFF (Content-Type: image/tiff)
      B) A JSON response with { downloadUrl: "..." }
      C) A JSON response with tile URL template for direct map serving

    Adapt the request format below to match your proxy's actual API.
"""
import os
import json
import requests
from pathlib import Path

PROCESSED = Path("data/processed")
TILES = Path("data/tiles")
TILES.mkdir(parents=True, exist_ok=True)

GEE_PROXY_URL = os.environ.get("GEE_PROXY_URL", "http://localhost:3000")

# ============================================================
# GEE COMPUTATION — Earth Engine JavaScript
# ============================================================
# This is the computation your proxy needs to execute.
# If your proxy accepts raw EE JavaScript, send this directly.
# If your proxy accepts a structured JSON config, adapt accordingly.
#
# This script can also be copy-pasted into the GEE Code Editor
# for manual execution. Replace YOUR_PROJECT with your project ID.
# ============================================================

GEE_SCRIPT = """
// ============================================================
// IOWA GEOTHERMAL SUITABILITY MODEL
// Darcy Solutions Demo — Tyree Spatial
// ============================================================

// --- Iowa boundary ---
var states = ee.FeatureCollection("TIGER/2018/States");
var iowa = states.filter(ee.Filter.eq('NAME', 'Iowa'));
var iowaGeom = iowa.geometry();

// ============================================================
// FACTOR 1: Aquifer Presence (weight: 0.25)
// ============================================================
// Load USGS Principal Aquifers (uploaded asset or inline)
// If asset exists:
//   var aquifers = ee.FeatureCollection("projects/YOUR_PROJECT/assets/aquifers_iowa");
// Fallback: use a placeholder raster (all 0.5 = uncertain)
var aquiferScore;
try {
    var aquifers = ee.FeatureCollection("projects/YOUR_PROJECT/assets/aquifers_iowa");
    var aquiferImg = ee.Image(0.5).paint(aquifers, 1.0);
    aquiferScore = aquiferImg.clip(iowaGeom).rename('aquifer');
} catch(e) {
    aquiferScore = ee.Image(0.5).clip(iowaGeom).rename('aquifer');
}

// ============================================================
// FACTOR 2: Well Density (weight: 0.20)
// ============================================================
// Load wells (uploaded asset or inline from NWIS)
var wells;
try {
    wells = ee.FeatureCollection("projects/YOUR_PROJECT/assets/iowa_wells");
} catch(e) {
    wells = ee.FeatureCollection([]);
}

var wellDensity = wells
    .reduceToImage([], ee.Reducer.count())
    .focal_mean({radius: 5000, units: 'meters'})
    .unitScale(0, 50)
    .clamp(0, 1)
    .clip(iowaGeom)
    .rename('wellDensity');

// ============================================================
// FACTOR 3: Bedrock Geology (weight: 0.15)
// ============================================================
// If bedrock geology asset exists, remap rock types to scores.
// Otherwise use uniform 0.7 (Iowa is generally favorable).
var geologyScore;
try {
    var geology = ee.FeatureCollection("projects/YOUR_PROJECT/assets/iowa_bedrock");
    // Remap based on LITH or ROCKTYPE field
    // limestone/dolomite=1.0, sandstone=0.9, crystalline=0.8, shale=0.6
    var geoImg = ee.Image(0.7).paint(geology, 'score');
    geologyScore = geoImg.clip(iowaGeom).rename('geology');
} catch(e) {
    geologyScore = ee.Image(0.7).clip(iowaGeom).rename('geology');
}

// ============================================================
// FACTOR 4: Land Cover (weight: 0.15)
// ============================================================
var nlcd = ee.ImageCollection("USGS/NLCD_RELEASES/2021_REL/NLCD")
    .filter(ee.Filter.eq('system:index', '2021'))
    .first()
    .select('landcover')
    .clip(iowaGeom);

var landSuit = nlcd.remap(
    [11, 12, 21, 22, 23, 24, 31, 41, 42, 43, 52, 71, 81, 82, 90, 95],
    [0, 0, 0.5, 0.5, 0.4, 0.3, 0.8, 0.8, 0.8, 0.8, 0.7, 1.0, 0.9, 0.9, 0.2, 0]
).rename('landcover');

// ============================================================
// FACTOR 5: Environmental Constraints (weight: 0.10)
// ============================================================
// NWI Wetlands — use NLCD wetland classes as proxy if NWI not available
var wetlandMask = nlcd.eq(90).or(nlcd.eq(95));
var waterMask = nlcd.eq(11);

var constraintScore = ee.Image(1.0)
    .where(wetlandMask, 0.0)
    .where(waterMask, 0.0)
    .clip(iowaGeom)
    .rename('constraints');

// If EPA constraints asset exists, buffer 500m and set to 0
try {
    var epa = ee.FeatureCollection("projects/YOUR_PROJECT/assets/epa_constraints");
    var epaBuffered = epa.map(function(f) { return f.buffer(500); });
    var epaImg = ee.Image(1).paint(epaBuffered, 0);
    constraintScore = constraintScore.min(epaImg).rename('constraints');
} catch(e) {}

// ============================================================
// FACTOR 6: Hydrologic Proximity (weight: 0.10)
// ============================================================
// Distance to water features (streams, wetlands, water bodies)
var hydroMask = wetlandMask.or(waterMask).unmask(0);

var hydroDist = hydroMask
    .Not()
    .fastDistanceTransform(256, 'pixels')
    .sqrt()
    .multiply(30);  // Convert pixels to meters (30m resolution)

var hydroScore = hydroDist.expression(
    "(d <= 250) ? 1.0" +
    ": (d <= 1000) ? 0.8" +
    ": (d <= 2500) ? 0.5" +
    ": (d <= 5000) ? 0.2" +
    ": 0.0",
    {d: hydroDist}
).clip(iowaGeom).rename('hydroProximity');

// ============================================================
// FACTOR 7: Remote Sensing Indicators (weight: 0.05)
// ============================================================

// --- Landsat 8/9 Summer LST Composite ---
var landsat = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
    .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
    .filterBounds(iowaGeom)
    .filterDate('2024-06-01', '2024-08-31')
    .select('ST_B10')
    .map(function(img) {
        return img.multiply(0.00341802).add(149.0).subtract(273.15)
            .copyProperties(img, ['system:time_start']);
    });

var lstMedian = landsat.median().clip(iowaGeom);

// Score: below-median LST = cooler ground = possible shallow groundwater
var lstMean = lstMedian.reduceRegion({
    reducer: ee.Reducer.mean(),
    geometry: iowaGeom,
    scale: 1000,
    maxPixels: 1e9
}).get('ST_B10');

var lstScore = lstMedian.expression(
    "0.5 + clamp((mean - lst) * 0.1, -0.5, 0.5)",
    {lst: lstMedian, mean: ee.Number(lstMean)}
).rename('lst_score');

// --- Sentinel-2 NDWI ---
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(iowaGeom)
    .filterDate('2024-06-01', '2024-08-31')
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
    .median()
    .clip(iowaGeom);

var ndwi = s2.normalizedDifference(['B3', 'B8']).rename('NDWI');

// Score: higher NDWI = more moisture = possible shallow groundwater
var ndwiScore = ndwi.expression(
    "clamp(ndwi + 0.5, 0, 1)",
    {ndwi: ndwi}
).rename('ndwi_score');

// Blend LST and NDWI 50/50
var rsScore = lstScore.add(ndwiScore).divide(2).clamp(0, 1).rename('remoteSensing');

// ============================================================
// WEIGHTED OVERLAY
// ============================================================
var suitability = aquiferScore.multiply(0.25)
    .add(wellDensity.multiply(0.20))
    .add(geologyScore.multiply(0.15))
    .add(landSuit.multiply(0.15))
    .add(constraintScore.multiply(0.10))
    .add(hydroScore.multiply(0.10))
    .add(rsScore.multiply(0.05))
    .multiply(100)  // Scale to 0-100
    .rename('suitability');

// ============================================================
// CONFIDENCE LAYER
// ============================================================
var confidence = wellDensity.multiply(100).rename('confidence');

// ============================================================
// EXPORT
// ============================================================
Export.image.toDrive({
    image: suitability.toFloat(),
    description: 'iowa_geothermal_suitability',
    scale: 30,
    region: iowaGeom,
    crs: 'EPSG:26915',
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF'
});

// Also export individual factor layers for the web map popups
var factors = suitability
    .addBands(aquiferScore.multiply(100))
    .addBands(wellDensity.multiply(100))
    .addBands(geologyScore.multiply(100))
    .addBands(landSuit.multiply(100))
    .addBands(constraintScore.multiply(100))
    .addBands(hydroScore.multiply(100))
    .addBands(rsScore.multiply(100))
    .addBands(confidence);

Export.image.toDrive({
    image: factors.toFloat(),
    description: 'iowa_suitability_factors',
    scale: 30,
    region: iowaGeom,
    crs: 'EPSG:26915',
    maxPixels: 1e13,
    fileFormat: 'GeoTIFF'
});

// Visualization
Map.centerObject(iowa, 7);
Map.addLayer(suitability, {min: 0, max: 100, palette: ['#dc2626','#f59e0b','#22c55e']}, 'Suitability');
Map.addLayer(confidence, {min: 0, max: 100, palette: ['#f1f5f9','#3b82f6']}, 'Confidence');
"""

# ============================================================
# SEND TO PROXY
# ============================================================

print("=" * 60)
print("IOWA GEOTHERMAL SUITABILITY — GEE PROCESSING")
print("=" * 60)
print(f"Proxy: {GEE_PROXY_URL}")
print()

# Option A: Send the full GEE JavaScript to the proxy for execution
# Adapt this to your proxy's API:
payload = {
    "script": GEE_SCRIPT,
    "region": {
        "west": -96.64, "south": 40.37,
        "east": -90.14, "north": 43.50
    },
    "scale": 30,
    "crs": "EPSG:26915",
    "format": "COG"
}

try:
    print("Sending suitability computation to GEE proxy...")
    r = requests.post(
        f"{GEE_PROXY_URL}/evaluate",
        json=payload,
        timeout=1200  # 20 min timeout for statewide 30m export
    )
    r.raise_for_status()

    content_type = r.headers.get("content-type", "")

    if "tiff" in content_type or "octet-stream" in content_type:
        out_path = PROCESSED / "suitability_composite.tif"
        with open(out_path, "wb") as f:
            f.write(r.content)
        print(f"Saved suitability raster to {out_path}")

    else:
        result = r.json()
        print(f"Proxy response: {json.dumps(result, indent=2)}")

        if "downloadUrl" in result:
            print("Downloading from URL...")
            dl = requests.get(result["downloadUrl"], timeout=600)
            out_path = PROCESSED / "suitability_composite.tif"
            with open(out_path, "wb") as f:
                f.write(dl.content)
            print(f"Saved to {out_path}")

        elif "tileUrl" in result:
            # Proxy returns a tile URL template — save for the web map
            tile_info = {"tileUrl": result["tileUrl"]}
            with open(TILES / "tile_url.json", "w") as f:
                json.dump(tile_info, f, indent=2)
            print(f"Tile URL saved: {result['tileUrl']}")

except requests.exceptions.ConnectionError:
    print(f"ERROR: Cannot connect to GEE proxy at {GEE_PROXY_URL}")
    print("Start your proxy: node gee-proxy.js")
    print()
    print("ALTERNATIVE: Copy the GEE script above into the GEE Code Editor:")
    print("  https://code.earthengine.google.com/")
    print("Run the export, download the GeoTIFF from Google Drive,")
    print("and place it at: data/processed/suitability_composite.tif")

except Exception as e:
    print(f"ERROR: {e}")
    print("See alternative instructions above.")

print()
print("GEE SCRIPT FOR MANUAL EXECUTION (if proxy fails):")
print("-" * 60)
print("Copy the script from this file into the GEE Code Editor.")
print("Replace 'YOUR_PROJECT' with your GEE project ID.")
print("Run exports. Download GeoTIFFs from Google Drive.")
print("Place in data/processed/")
```

---

## Script 06: Export Web Assets — `scripts/06_export_web_assets.py`

```python
"""
Convert suitability raster + vector data into web-ready assets.
  - Suitability raster → color-mapped PNG overlay
  - Wells, aquifers, constraints → GeoJSON for MapLibre
  - Factor rasters → per-pixel JSON query support

Output: data/tiles/ directory with all web map assets.
"""
import numpy as np
import json
from pathlib import Path

PROCESSED = Path("data/processed")
TILES = Path("data/tiles")
TILES.mkdir(parents=True, exist_ok=True)

# ============================================================
# Check if we have a suitability raster
# ============================================================
raster_path = PROCESSED / "suitability_composite.tif"
has_raster = raster_path.exists()

if has_raster:
    import rasterio
    from rasterio.warp import reproject, calculate_default_transform, Resampling
    from PIL import Image

    # --- Reproject to EPSG:4326 for web overlay ---
    print("Reprojecting suitability raster to EPSG:4326...")
    with rasterio.open(raster_path) as src:
        dst_crs = "EPSG:4326"
        transform, width, height = calculate_default_transform(
            src.crs, dst_crs, src.width, src.height, *src.bounds,
            resolution=0.0003  # ~30m at Iowa's latitude
        )
        meta = src.meta.copy()
        meta.update({"crs": dst_crs, "transform": transform,
                      "width": width, "height": height})

        reprojected_path = PROCESSED / "suitability_4326.tif"
        with rasterio.open(reprojected_path, "w", **meta) as dst:
            reproject(
                source=rasterio.band(src, 1),
                destination=rasterio.band(dst, 1),
                src_transform=src.transform, src_crs=src.crs,
                dst_transform=transform, dst_crs=dst_crs,
                resampling=Resampling.bilinear
            )

    # --- Generate color-mapped PNG ---
    print("Generating suitability PNG overlay...")
    with rasterio.open(reprojected_path) as src:
        data = src.read(1)
        bounds = src.bounds

    nodata = -9999
    valid = (data != nodata) & np.isfinite(data)
    norm = np.clip(data / 100.0, 0, 1)

    # RGBA: red→yellow→green color ramp
    rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
    # Red channel: peaks at low scores, fades at high
    rgba[valid, 0] = np.clip(255 * (1.0 - norm[valid]) * 2, 0, 255).astype(np.uint8)
    # Green channel: peaks at high scores
    rgba[valid, 1] = np.clip(255 * norm[valid] * 2, 0, 255).astype(np.uint8)
    # Blue
    rgba[valid, 2] = 20
    # Alpha
    rgba[valid, 3] = 180

    img = Image.fromarray(rgba)
    img.save(TILES / "suitability_overlay.png")
    print(f"  Saved suitability_overlay.png ({img.size[0]}x{img.size[1]})")

    # Save bounds for web map
    bounds_json = {
        "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
        "width": int(width), "height": int(height)
    }
    with open(TILES / "bounds.json", "w") as f:
        json.dump(bounds_json, f, indent=2)

else:
    print("No suitability raster found at data/processed/suitability_composite.tif")
    print("Run script 05 first, or export from GEE Code Editor.")
    print("Creating placeholder bounds.json with default Iowa extent...")
    bounds_json = {
        "bounds": [-96.64, 40.37, -90.14, 43.50],
        "width": 0, "height": 0,
        "placeholder": True
    }
    with open(TILES / "bounds.json", "w") as f:
        json.dump(bounds_json, f, indent=2)

# ============================================================
# Copy vector GeoJSON to tiles directory
# ============================================================
print("\nCopying vector assets to tiles/...")
import shutil

for name in ["iowa_wells.geojson", "iowa_boundary.geojson",
             "aquifers_iowa.geojson", "epa_constraints.geojson"]:
    src = PROCESSED / name
    if src.exists():
        shutil.copy(src, TILES / name)
        print(f"  Copied {name}")
    else:
        print(f"  SKIP {name} (not found)")

print("\nAll web assets in data/tiles/")
print("Ready to serve: python -m http.server 8080")
```

---

## `index.html` — Interactive Web Map + Site Inquiry

This is the final deliverable. Serves from the repo root with `python -m http.server 8080`.

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Iowa Geothermal Suitability — Darcy Solutions</title>
    <script src="https://unpkg.com/maplibre-gl@4.1.1/dist/maplibre-gl.js"></script>
    <link href="https://unpkg.com/maplibre-gl@4.1.1/dist/maplibre-gl.css" rel="stylesheet" />
    <style>
        :root {
            --panel-bg: rgba(15, 23, 42, 0.94);
            --panel-border: rgba(255,255,255,0.08);
            --text-primary: #f1f5f9;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --accent: #3b82f6;
            --green: #22c55e;
            --yellow: #eab308;
            --red: #ef4444;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, system-ui, sans-serif; }
        #map { width: 100vw; height: 100vh; }

        /* --- Control Panel --- */
        .panel {
            position: absolute;
            top: 16px; left: 16px;
            background: var(--panel-bg);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid var(--panel-border);
            border-radius: 14px;
            padding: 20px;
            color: var(--text-primary);
            width: 320px;
            max-height: calc(100vh - 32px);
            overflow-y: auto;
            z-index: 10;
            box-shadow: 0 8px 32px rgba(0,0,0,0.5);
        }
        .panel h1 { font-size: 17px; font-weight: 700; letter-spacing: -0.02em; }
        .panel .subtitle { font-size: 12px; color: var(--text-secondary); margin-top: 2px; margin-bottom: 16px; }
        .section-label {
            font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em;
            color: var(--text-muted); margin: 14px 0 6px; font-weight: 600;
        }
        .layer-toggle {
            display: flex; align-items: center; gap: 10px;
            padding: 7px 0; cursor: pointer; font-size: 13px;
            border-bottom: 1px solid var(--panel-border);
            transition: color 0.15s;
        }
        .layer-toggle:hover { color: #fff; }
        .layer-toggle input[type="checkbox"] {
            accent-color: var(--accent); width: 15px; height: 15px;
        }
        .opacity-slider { width: 100%; margin: 4px 0 8px; accent-color: var(--accent); height: 4px; }
        .legend-bar {
            height: 10px; border-radius: 5px; margin: 6px 0 3px;
            background: linear-gradient(to right, var(--red), var(--yellow), var(--green));
        }
        .legend-labels { display: flex; justify-content: space-between; font-size: 10px; color: var(--text-muted); }
        .weight-row { display: flex; justify-content: space-between; font-size: 12px; color: var(--text-secondary); padding: 2px 0; }
        .weight-row span:last-child { font-weight: 600; color: var(--text-primary); }
        .footer { margin-top: 14px; padding-top: 10px; border-top: 1px solid var(--panel-border);
                   font-size: 10px; color: var(--text-muted); line-height: 1.5; }

        /* --- Site Inquiry Popup --- */
        .maplibregl-popup { max-width: 360px !important; }
        .maplibregl-popup-content {
            background: var(--panel-bg); backdrop-filter: blur(16px);
            border: 1px solid var(--panel-border); border-radius: 12px;
            padding: 0; color: var(--text-primary); box-shadow: 0 12px 40px rgba(0,0,0,0.6);
            overflow: hidden;
        }
        .maplibregl-popup-close-button { color: var(--text-secondary); font-size: 18px; right: 8px; top: 6px; }
        .maplibregl-popup-tip { border-top-color: var(--panel-bg); }

        .inquiry-header {
            padding: 16px 16px 12px;
            border-bottom: 1px solid var(--panel-border);
        }
        .inquiry-header h3 { font-size: 15px; font-weight: 700; margin-bottom: 2px; }
        .inquiry-header .coords { font-size: 11px; color: var(--text-muted); font-family: monospace; }

        .score-hero {
            display: flex; align-items: center; gap: 14px;
            padding: 14px 16px;
            border-bottom: 1px solid var(--panel-border);
        }
        .score-circle {
            width: 56px; height: 56px; border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 22px; font-weight: 800; flex-shrink: 0;
        }
        .score-label { font-size: 12px; color: var(--text-secondary); }
        .score-desc { font-size: 13px; margin-top: 2px; }

        .factor-grid { padding: 12px 16px; }
        .factor-row {
            display: flex; align-items: center; gap: 8px;
            padding: 5px 0; font-size: 12px;
        }
        .factor-bar-bg {
            flex: 1; height: 6px; background: rgba(255,255,255,0.1);
            border-radius: 3px; overflow: hidden;
        }
        .factor-bar-fill { height: 100%; border-radius: 3px; transition: width 0.4s ease; }
        .factor-name { width: 110px; color: var(--text-secondary); flex-shrink: 0; }
        .factor-val { width: 32px; text-align: right; font-weight: 600; flex-shrink: 0; }

        .inquiry-footer {
            padding: 10px 16px; font-size: 10px; color: var(--text-muted);
            border-top: 1px solid var(--panel-border); text-align: center;
        }

        .loading-indicator {
            position: absolute; bottom: 20px; left: 50%; transform: translateX(-50%);
            background: var(--panel-bg); backdrop-filter: blur(12px);
            padding: 10px 20px; border-radius: 8px; color: var(--text-secondary);
            font-size: 13px; display: none; z-index: 20;
            border: 1px solid var(--panel-border);
        }
    </style>
</head>
<body>
    <div id="map"></div>

    <div class="panel">
        <h1>Iowa Geothermal Suitability</h1>
        <div class="subtitle">Darcy Solutions — Automated Site Intelligence</div>

        <div class="section-label">Layers</div>
        <label class="layer-toggle">
            <input type="checkbox" id="toggle-suitability" checked>
            Suitability Score
        </label>
        <input type="range" class="opacity-slider" id="opacity-suitability" min="0" max="100" value="70">

        <label class="layer-toggle">
            <input type="checkbox" id="toggle-wells" checked>
            Groundwater Wells (USGS)
        </label>
        <label class="layer-toggle">
            <input type="checkbox" id="toggle-aquifers">
            Principal Aquifers
        </label>
        <label class="layer-toggle">
            <input type="checkbox" id="toggle-epa">
            EPA Constraints
        </label>
        <label class="layer-toggle">
            <input type="checkbox" id="toggle-boundary" checked>
            State Boundary
        </label>

        <div class="section-label">Suitability Scale</div>
        <div class="legend-bar"></div>
        <div class="legend-labels"><span>0 — Low</span><span>50</span><span>100 — High</span></div>

        <div class="section-label">Model Weights</div>
        <div class="weight-row"><span>Aquifer Presence</span><span>25%</span></div>
        <div class="weight-row"><span>Well Density</span><span>20%</span></div>
        <div class="weight-row"><span>Bedrock Geology</span><span>15%</span></div>
        <div class="weight-row"><span>Land Cover</span><span>15%</span></div>
        <div class="weight-row"><span>Env. Constraints</span><span>10%</span></div>
        <div class="weight-row"><span>Hydro. Proximity</span><span>10%</span></div>
        <div class="weight-row"><span>Remote Sensing</span><span>5%</span></div>

        <div class="footer">
            <strong>Click anywhere in Iowa</strong> for an automated site inquiry report.<br><br>
            Author: Quintin Tyree · Tyree Spatial<br>
            Data: USGS, NOAA, EPA, FEMA, USFWS, Landsat 8/9, Sentinel-2<br>
            All data freely accessible · $0 data cost · March 2026
        </div>
    </div>

    <div class="loading-indicator" id="loading">Loading layers...</div>

    <script>
    // ============================================================
    // MAP INITIALIZATION
    // ============================================================
    const map = new maplibregl.Map({
        container: 'map',
        style: {
            version: 8,
            sources: {
                'carto-dark': {
                    type: 'raster',
                    tiles: ['https://basemaps.cartocdn.com/dark_all/{z}/{x}/{y}@2x.png'],
                    tileSize: 256,
                    attribution: '© CARTO © OpenStreetMap contributors'
                }
            },
            layers: [{
                id: 'carto-dark',
                type: 'raster',
                source: 'carto-dark'
            }]
        },
        center: [-93.5, 42.0],
        zoom: 7,
        maxZoom: 15,
        minZoom: 5
    });

    map.addControl(new maplibregl.NavigationControl(), 'top-right');
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 200 }), 'bottom-right');

    const loading = document.getElementById('loading');

    // ============================================================
    // HELPERS
    // ============================================================
    function scoreColor(score) {
        if (score >= 70) return 'var(--green)';
        if (score >= 40) return 'var(--yellow)';
        return 'var(--red)';
    }

    function scoreLabel(score) {
        if (score >= 80) return 'Excellent';
        if (score >= 65) return 'Good';
        if (score >= 50) return 'Moderate';
        if (score >= 35) return 'Marginal';
        return 'Low';
    }

    function haversine(lat1, lng1, lat2, lng2) {
        const R = 6371;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLng = (lng2 - lng1) * Math.PI / 180;
        const a = Math.sin(dLat/2)**2 + Math.cos(lat1*Math.PI/180) * Math.cos(lat2*Math.PI/180) * Math.sin(dLng/2)**2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    }

    function pointInPolygon(x, y, geometry) {
        let coords;
        if (geometry.type === 'Polygon') coords = [geometry.coordinates];
        else if (geometry.type === 'MultiPolygon') coords = geometry.coordinates;
        else return false;
        for (const polygon of coords) {
            const ring = polygon[0];
            let inside = false;
            for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
                const xi = ring[i][0], yi = ring[i][1];
                const xj = ring[j][0], yj = ring[j][1];
                if (((yi > y) !== (yj > y)) && (x < (xj - xi) * (y - yi) / (yj - yi) + xi))
                    inside = !inside;
            }
            if (inside) return true;
        }
        return false;
    }

    // ============================================================
    // SITE INQUIRY — click anywhere → factor breakdown popup
    // ============================================================
    let wellsData = null, aquifersData = null, epaData = null;

    function estimateFactors(lngLat) {
        const { lng, lat } = lngLat;

        // Aquifer: check point-in-polygon
        let aquifer = 50;
        if (aquifersData) {
            for (const f of aquifersData.features) {
                if (f.geometry && pointInPolygon(lng, lat, f.geometry)) {
                    aquifer = 85; break;
                }
            }
        }

        // Well density: count nearby wells
        let wellDensity = 30;
        if (wellsData) {
            let nearby = 0;
            for (const f of wellsData.features) {
                const c = f.geometry.coordinates;
                if (haversine(lat, lng, c[1], c[0]) < 5) nearby++;
            }
            wellDensity = Math.min(nearby * 5, 100);
        }

        // Constraints: check EPA proximity
        let constraints = 100;
        if (epaData) {
            for (const f of epaData.features) {
                const c = f.geometry.coordinates;
                const dist = haversine(lat, lng, c[1], c[0]);
                if (dist < 0.5) { constraints = 0; break; }
                if (dist < 2) constraints = Math.min(constraints, 40);
            }
        }

        // Geology heuristic (eastern Iowa carbonate = better)
        let geology = 65;
        if (lng > -92) geology = 80;
        else if (lng < -95) geology = 55;

        // Land cover heuristic (ag default, metros lower)
        let landCover = 85;
        const metros = [
            { lat: 41.59, lng: -93.62, r: 30 },
            { lat: 41.98, lng: -91.67, r: 20 },
            { lat: 41.66, lng: -91.53, r: 15 },
            { lat: 41.52, lng: -90.58, r: 15 },
            { lat: 42.50, lng: -96.40, r: 12 },
        ];
        for (const m of metros) {
            if (haversine(lat, lng, m.lat, m.lng) < m.r) { landCover = 45; break; }
        }

        // Hydro proximity heuristic
        let hydroProximity = 50;
        if (lat < 41.7 && Math.abs(lng - (-93.6)) < 0.2) hydroProximity = 90;
        if (Math.abs(lat - 41.98) < 0.3 && Math.abs(lng - (-91.6)) < 0.2) hydroProximity = 85;
        if (lng < -95.8 || lng > -90.5) hydroProximity = 80;

        // Remote sensing heuristic
        let remoteSensing = 50;
        if (hydroProximity > 70) remoteSensing = 65;

        let confidence = 'Medium';
        if (wellDensity > 60) confidence = 'High';
        if (wellDensity < 20) confidence = 'Low';

        return { aquifer, wellDensity, geology, landCover, constraints, hydroProximity, remoteSensing, confidence };
    }

    function factorBar(name, value) {
        const v = Math.round(value);
        const color = scoreColor(v);
        return `<div class="factor-row">
            <span class="factor-name">${name}</span>
            <div class="factor-bar-bg"><div class="factor-bar-fill" style="width:${v}%;background:${color}"></div></div>
            <span class="factor-val" style="color:${color}">${v}</span>
        </div>`;
    }

    function siteInquiry(lngLat) {
        const factors = estimateFactors(lngLat);
        const score = Math.round(
            factors.aquifer * 0.25 + factors.wellDensity * 0.20 +
            factors.geology * 0.15 + factors.landCover * 0.15 +
            factors.constraints * 0.10 + factors.hydroProximity * 0.10 +
            factors.remoteSensing * 0.05
        );
        const color = scoreColor(score);

        new maplibregl.Popup({ maxWidth: '360px', offset: 15 })
            .setLngLat(lngLat)
            .setHTML(`
                <div class="inquiry-header">
                    <h3>Site Inquiry Report</h3>
                    <div class="coords">${lngLat.lat.toFixed(4)}°N, ${Math.abs(lngLat.lng).toFixed(4)}°W</div>
                </div>
                <div class="score-hero">
                    <div class="score-circle" style="background:${color}22;color:${color};border:2px solid ${color}">${score}</div>
                    <div><div class="score-label">Geothermal Suitability</div>
                    <div class="score-desc" style="color:${color}">${scoreLabel(score)}</div></div>
                </div>
                <div class="factor-grid">
                    ${factorBar('Aquifer', factors.aquifer)}
                    ${factorBar('Well Density', factors.wellDensity)}
                    ${factorBar('Geology', factors.geology)}
                    ${factorBar('Land Cover', factors.landCover)}
                    ${factorBar('Constraints', factors.constraints)}
                    ${factorBar('Hydro Proximity', factors.hydroProximity)}
                    ${factorBar('Remote Sensing', factors.remoteSensing)}
                </div>
                <div class="inquiry-footer">Confidence: ${factors.confidence} · Automated screening only</div>
            `).addTo(map);
    }

    // ============================================================
    // MAP LOAD
    // ============================================================
    map.on('load', async () => {
        loading.style.display = 'block';

        // Suitability overlay
        try {
            const boundsRes = await fetch('data/tiles/bounds.json');
            const boundsData = await boundsRes.json();
            if (!boundsData.placeholder) {
                const b = boundsData.bounds;
                map.addSource('suitability', {
                    type: 'image', url: 'data/tiles/suitability_overlay.png',
                    coordinates: [[b[0],b[3]], [b[2],b[3]], [b[2],b[1]], [b[0],b[1]]]
                });
                map.addLayer({ id: 'suitability-layer', type: 'raster', source: 'suitability',
                    paint: { 'raster-opacity': 0.7, 'raster-fade-duration': 0 } });
            }
        } catch(e) { console.warn('Suitability overlay not loaded:', e); }

        // Iowa boundary
        try {
            const bd = await (await fetch('data/tiles/iowa_boundary.geojson')).json();
            map.addSource('iowa-boundary', { type: 'geojson', data: bd });
            map.addLayer({ id: 'iowa-boundary-line', type: 'line', source: 'iowa-boundary',
                paint: { 'line-color': '#94a3b8', 'line-width': 2, 'line-opacity': 0.5, 'line-dasharray': [4, 2] } });
        } catch(e) {}

        // Aquifers
        try {
            aquifersData = await (await fetch('data/tiles/aquifers_iowa.geojson')).json();
            map.addSource('aquifers', { type: 'geojson', data: aquifersData });
            map.addLayer({ id: 'aquifers-fill', type: 'fill', source: 'aquifers',
                layout: { visibility: 'none' }, paint: { 'fill-color': '#3b82f6', 'fill-opacity': 0.15 } });
            map.addLayer({ id: 'aquifers-line', type: 'line', source: 'aquifers',
                layout: { visibility: 'none' }, paint: { 'line-color': '#3b82f6', 'line-width': 1, 'line-opacity': 0.4 } });
        } catch(e) {}

        // Wells
        try {
            wellsData = await (await fetch('data/tiles/iowa_wells.geojson')).json();
            map.addSource('wells', { type: 'geojson', data: wellsData });
            map.addLayer({ id: 'wells-layer', type: 'circle', source: 'wells',
                paint: {
                    'circle-radius': ['interpolate', ['linear'], ['zoom'], 6, 1.5, 12, 5],
                    'circle-color': '#60a5fa', 'circle-stroke-color': '#1e293b',
                    'circle-stroke-width': 0.5, 'circle-opacity': 0.7 } });
        } catch(e) {}

        // EPA constraints
        try {
            epaData = await (await fetch('data/tiles/epa_constraints.geojson')).json();
            map.addSource('epa', { type: 'geojson', data: epaData });
            map.addLayer({ id: 'epa-layer', type: 'circle', source: 'epa',
                layout: { visibility: 'none' },
                paint: { 'circle-radius': 7, 'circle-color': '#ef4444',
                    'circle-stroke-color': '#fff', 'circle-stroke-width': 1.5, 'circle-opacity': 0.9 } });
        } catch(e) {}

        loading.style.display = 'none';

        // Click handler
        map.on('click', (e) => {
            const hits = map.queryRenderedFeatures(e.point,
                { layers: ['wells-layer','epa-layer'].filter(l => map.getLayer(l)) });

            if (hits.length > 0) {
                const f = hits[0], p = f.properties;
                if (f.layer.id === 'wells-layer') {
                    new maplibregl.Popup({ offset: 10 }).setLngLat(e.lngLat).setHTML(`
                        <div class="inquiry-header"><h3>USGS Well</h3><div class="coords">${p.site_id||''}</div></div>
                        <div style="padding:12px 16px;font-size:13px;color:var(--text-secondary)">
                            <div>${p.name||'Unnamed'}</div>
                            ${p.well_depth_ft?`<div style="margin-top:4px">Depth: <strong style="color:var(--text-primary)">${p.well_depth_ft} ft</strong></div>`:''}
                            ${p.aquifer?`<div style="margin-top:4px">Aquifer: ${p.aquifer}</div>`:''}
                        </div>`).addTo(map);
                } else if (f.layer.id === 'epa-layer') {
                    new maplibregl.Popup({ offset: 10 }).setLngLat(e.lngLat).setHTML(`
                        <div class="inquiry-header"><h3 style="color:var(--red)">EPA ${p.type==='superfund'?'Superfund':'Brownfield'} Site</h3></div>
                        <div style="padding:12px 16px;font-size:13px;color:var(--text-secondary)">
                            <div>${p.name||'Unnamed'}</div>
                            <div style="margin-top:4px;color:var(--red)">⚠ 500m exclusion buffer</div>
                        </div>`).addTo(map);
                }
                return;
            }

            const { lng, lat } = e.lngLat;
            if (lng > -96.7 && lng < -90.1 && lat > 40.3 && lat < 43.6) siteInquiry(e.lngLat);
        });

        map.on('mouseenter', 'wells-layer', () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', 'wells-layer', () => map.getCanvas().style.cursor = '');
        map.on('mouseenter', 'epa-layer', () => map.getCanvas().style.cursor = 'pointer');
        map.on('mouseleave', 'epa-layer', () => map.getCanvas().style.cursor = '');
    });

    // ============================================================
    // LAYER TOGGLES
    // ============================================================
    function toggleLayer(id, layers) {
        document.getElementById(id).addEventListener('change', e => {
            const v = e.target.checked ? 'visible' : 'none';
            layers.forEach(l => { if (map.getLayer(l)) map.setLayoutProperty(l, 'visibility', v); });
        });
    }

    document.getElementById('toggle-suitability').addEventListener('change', e => {
        if (map.getLayer('suitability-layer'))
            map.setLayoutProperty('suitability-layer', 'visibility', e.target.checked ? 'visible' : 'none');
    });
    document.getElementById('opacity-suitability').addEventListener('input', e => {
        if (map.getLayer('suitability-layer'))
            map.setPaintProperty('suitability-layer', 'raster-opacity', parseInt(e.target.value) / 100);
    });
    toggleLayer('toggle-wells', ['wells-layer']);
    toggleLayer('toggle-aquifers', ['aquifers-fill', 'aquifers-line']);
    toggleLayer('toggle-epa', ['epa-layer']);
    toggleLayer('toggle-boundary', ['iowa-boundary-line']);
    </script>
</body>
</html>
```

---

## Manual Download Checklist

| Dataset | Where to Get It | Place In | Impact if Missing |
|---|---|---|---|
| **Iowa Bedrock Geology** | https://catalog.data.gov/dataset/iowa-bedrock-geology-fee6c | Upload as GEE asset | Geology factor falls back to 0.7 uniform |
| **FEMA Flood Hazard (Iowa)** | https://hazards.fema.gov → state download | Upload as GEE asset | Constraints use NLCD wetland proxy only |
| **NWI Wetlands (Iowa)** | https://fws.gov/program/national-wetlands-inventory | Upload as GEE asset | Constraints use NLCD wetland proxy only |

Everything else is automated. The model degrades gracefully — NLCD wetland/water classes proxy for FEMA and NWI.

---

## Execution Order

```bash
cd darcy-iowa

# --- Environment ---
pip install geopandas rasterio shapely numpy scipy requests pyproj fiona pandas Pillow
pip install rio-cogeo 2>/dev/null || true

# --- Phase 1: Data Acquisition (~5 min) ---
python scripts/01_fetch_wells.py
python scripts/02_fetch_aquifers.py
python scripts/03_fetch_constraints.py

# --- Phase 2: GEE Asset Upload (optional) ---
# Either run script 04 or upload manually via GEE Code Editor
python scripts/04_upload_gee_assets.py

# --- Phase 3: GEE Suitability Model ---
# Start proxy: node gee-proxy.js
export GEE_PROXY_URL=http://localhost:3000
python scripts/05_gee_suitability.py
# OR: Copy GEE script from 05 into Code Editor, run export, download GeoTIFF

# --- Phase 4: Web Asset Generation ---
python scripts/06_export_web_assets.py

# --- Phase 5: Serve ---
python -m http.server 8080
# Open http://localhost:8080/index.html
```

---

## GEE Proxy Contract

Your `gee-proxy.js` needs to handle one of:

| Endpoint | Input | Output |
|---|---|---|
| `POST /evaluate` | `{ script, region, scale, crs, format }` | COG GeoTIFF binary or `{ downloadUrl }` or `{ tileUrl }` |

If your proxy has a different API shape, update `05_gee_suitability.py` accordingly. The GEE JavaScript in that script is the canonical computation — it can also be copy-pasted directly into the GEE Code Editor for manual export.

---

## What You're Delivering to Darcy

1. **Interactive statewide suitability map** — dark basemap, suitability heatmap overlay, toggleable layers
2. **Click-anywhere site inquiry** — automated report with composite score, factor breakdown, confidence rating
3. **Transparent methodology** — every weight, every data source, every scoring rule documented
4. **The pitch:** *"I built this for Iowa in one day to show what I can do. The framework is national — same data sources exist for every state. I can build Minnesota validation against your existing projects, then scale to any expansion market you enter."*

---

## Cost: $0

Every data source is free. The only registrations: GEE (you already have it). No paid APIs, no restricted datasets, no subscriptions.
