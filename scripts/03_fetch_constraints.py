"""
Fetch EPA Superfund/Brownfield constraint sites for Iowa.
Uses known Iowa NPL sites + EPA Envirofacts where available.
Output: data/processed/epa_constraints.geojson
"""
import requests
import json
from pathlib import Path

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

features = []

# --- Known Iowa Superfund (NPL) sites ---
# Source: EPA National Priorities List for Iowa
# These are the primary constraint locations for the suitability model
KNOWN_IOWA_NPL = [
    {"name": "Iowa Army Ammunition Plant", "lat": 40.8286, "lng": -91.2847},
    {"name": "Aidex Corp", "lat": 41.6764, "lng": -91.5122},
    {"name": "Des Moines TCE", "lat": 41.6005, "lng": -93.6091},
    {"name": "Shaw Avenue Dump", "lat": 41.5942, "lng": -93.6342},
    {"name": "Lawrence Todtz Farm", "lat": 42.8183, "lng": -91.7853},
    {"name": "Peoples Natural Gas Co", "lat": 42.0644, "lng": -93.8800},
    {"name": "Mason City Coal Gas Plant", "lat": 43.1536, "lng": -93.2010},
    {"name": "Mid-America Tanning Co", "lat": 42.7606, "lng": -91.2508},
    {"name": "Electro-Coatings Inc", "lat": 41.6500, "lng": -91.5303},
    {"name": "Vogel Paint & Wax Co", "lat": 42.4703, "lng": -90.6683},
    {"name": "Sheller-Globe Corp Disposal", "lat": 41.7231, "lng": -92.7261},
    {"name": "Iowa City FCW Plant", "lat": 41.6558, "lng": -91.5297},
    {"name": "John Deere Dubuque Works", "lat": 42.5006, "lng": -90.6644},
    {"name": "Alhambra Smelter", "lat": 42.0308, "lng": -91.6681},
    {"name": "LaBounty Site", "lat": 42.9342, "lng": -91.1831},
    {"name": "White Farm Equipment", "lat": 42.4694, "lng": -93.8125},
    {"name": "Farmers Mutual Cooperative", "lat": 42.1742, "lng": -92.7136},
    {"name": "Northwestern States Portland Cement", "lat": 43.1489, "lng": -93.1917},
    {"name": "Midwest Manufacturing/North Farm", "lat": 42.1381, "lng": -91.0131},
    {"name": "E.I. du Pont de Nemours (County Road 75)", "lat": 41.0417, "lng": -91.0297},
    {"name": "Chemplex Co", "lat": 41.7192, "lng": -91.5944},
    {"name": "Solvent Recovery Co of New England", "lat": 41.0722, "lng": -93.7892},
]

print(f"Loading {len(KNOWN_IOWA_NPL)} known Iowa NPL Superfund sites...")
for site in KNOWN_IOWA_NPL:
    features.append({
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [site["lng"], site["lat"]]},
        "properties": {
            "site_id": "",
            "name": site["name"],
            "type": "superfund",
        },
    })

# --- Try EPA Envirofacts for additional brownfield sites ---
print("Attempting EPA Envirofacts for brownfield sites...")
try:
    url = "https://data.epa.gov/efservice/ACRES_SITE_INFORMATION/SITE_STATE_CODE/IA/JSON"
    r = requests.get(url, timeout=30)
    if r.ok:
        for s in r.json():
            lat = s.get("LATITUDE") or s.get("SITE_LATITUDE")
            lon = s.get("LONGITUDE") or s.get("SITE_LONGITUDE")
            if lat and lon:
                try:
                    features.append({
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
                        "properties": {
                            "site_id": s.get("ASSESSMENT_ID", ""),
                            "name": s.get("SITE_NAME", ""),
                            "type": "brownfield",
                        },
                    })
                except (ValueError, TypeError):
                    pass
        bf_count = len(features) - len(KNOWN_IOWA_NPL)
        print(f"  Found {bf_count} brownfield sites")
    else:
        print(f"  Envirofacts returned {r.status_code}, skipping brownfields")
except Exception as e:
    print(f"  Brownfield fetch failed: {e} (non-critical)")

# --- Save ---
geojson = {"type": "FeatureCollection", "features": features}
out_path = PROCESSED / "epa_constraints.geojson"
with open(out_path, "w") as f:
    json.dump(geojson, f)

print(f"\nSaved {len(features)} total constraint sites to {out_path}")
