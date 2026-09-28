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
    if i == header_idx + 1:
        # Skip the format line (e.g., "15s 15s ...")
        continue
    break

data_lines = lines[header_idx + 2:]
print(f"  Parsing {len(data_lines)} well records...")

# Find column indices
lat_col = headers.index("dec_lat_va") if "dec_lat_va" in headers else None
lng_col = headers.index("dec_long_va") if "dec_long_va" in headers else None
id_col = headers.index("site_no") if "site_no" in headers else None
name_col = headers.index("station_nm") if "station_nm" in headers else None
depth_col = headers.index("well_depth_va") if "well_depth_va" in headers else None
aq_col = headers.index("nat_aqfr_cd") if "nat_aqfr_cd" in headers else None

if lat_col is None or lng_col is None:
    print("ERROR: Could not find lat/lng columns in NWIS response")
    print(f"  Available columns: {headers}")
    exit(1)

geojson = {
    "type": "FeatureCollection",
    "features": []
}

skipped = 0
for line in data_lines:
    if not line.strip():
        continue
    fields = line.split("\t")
    try:
        lat = float(fields[lat_col])
        lng = float(fields[lng_col])
    except (ValueError, IndexError):
        skipped += 1
        continue

    props = {
        "site_id": fields[id_col] if id_col is not None else "",
        "name": fields[name_col] if name_col is not None else "",
        "well_depth_ft": fields[depth_col] if depth_col is not None else "",
        "aquifer": fields[aq_col] if aq_col is not None else "",
    }

    geojson["features"].append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lng, lat]},
        "properties": props
    })

out_path = PROCESSED / "iowa_wells.geojson"
with open(out_path, "w") as f:
    json.dump(geojson, f)

print(f"\nSaved {len(geojson['features'])} wells to {out_path}")
if skipped:
    print(f"  Skipped {skipped} records with missing coordinates")
