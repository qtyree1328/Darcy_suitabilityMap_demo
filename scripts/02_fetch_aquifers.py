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
print("Fetching Iowa state boundary from Census TIGER...")
tiger_url = (
    "https://www2.census.gov/geo/tiger/TIGER2023/STATE/tl_2023_us_state.zip"
)

tiger_zip = RAW / "tl_2023_us_state.zip"
if not tiger_zip.exists():
    r = requests.get(tiger_url, timeout=120)
    r.raise_for_status()
    with open(tiger_zip, "wb") as f:
        f.write(r.content)
    print(f"  Downloaded {tiger_zip.name} ({len(r.content) // 1024} KB)")

states = gpd.read_file(f"zip://{tiger_zip}")
iowa = states[states["STATEFP"] == IOWA_FIPS]
iowa_4326 = iowa.to_crs("EPSG:4326")
iowa_4326.to_file(PROCESSED / "iowa_boundary.geojson", driver="GeoJSON")
print(f"  Saved Iowa boundary")

# --- USGS Principal Aquifers ---
print("\nFetching USGS Principal Aquifers shapefile...")
aquifer_url = (
    "https://water.usgs.gov/GIS/dsdl/aquifers_us.zip"
)

aquifer_zip = RAW / "aquifers_us.zip"
if not aquifer_zip.exists():
    r = requests.get(aquifer_url, timeout=300)
    r.raise_for_status()
    with open(aquifer_zip, "wb") as f:
        f.write(r.content)
    print(f"  Downloaded {aquifer_zip.name} ({len(r.content) // (1024*1024)} MB)")

# Extract and read
aquifer_dir = RAW / "aquifers_us"
if not aquifer_dir.exists():
    with zipfile.ZipFile(aquifer_zip, "r") as z:
        z.extractall(aquifer_dir)

# Find the shapefile
shp_files = list(aquifer_dir.rglob("*.shp"))
if not shp_files:
    print("ERROR: No .shp file found in aquifers archive")
    exit(1)

print(f"  Reading {shp_files[0].name}...")
aquifers = gpd.read_file(shp_files[0])

# Clip to Iowa
iowa_geom = iowa.to_crs(aquifers.crs).geometry.unary_union
aquifers_iowa = gpd.clip(aquifers, iowa_geom)

# Convert to WGS84
aquifers_iowa_4326 = aquifers_iowa.to_crs("EPSG:4326")
aquifers_iowa_4326.to_file(PROCESSED / "aquifers_iowa.geojson", driver="GeoJSON")
print(f"Saved {len(aquifers_iowa)} aquifer polygons for Iowa")

# Print unique aquifer types for scoring reference
for col in ["AQ_NAME", "AQ_CODE", "ROCK_TYPE", "NAT_AQFR_CD"]:
    if col in aquifers_iowa.columns:
        print(f"\n  {col} values: {aquifers_iowa[col].unique().tolist()}")
