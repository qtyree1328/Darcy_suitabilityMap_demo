"""
Download Iowa bedrock geology from Iowa DNR ArcGIS REST service.
Source: https://programs.iowadnr.gov/geospatial/rest/services/Geology/BedrockGeology/MapServer/6

Assigns geothermal suitability scores based on rock type / thermal conductivity:
  - Carbonates (limestone/dolomite): high conductivity → high score
  - Sandstone: moderate-high conductivity → moderate-high score
  - Shale / mudstone: low conductivity → low score
  - Crystalline / metamorphic: variable → moderate score

Output: data/processed/iowa_bedrock.geojson
"""
import json
import requests
from pathlib import Path

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

BASE_URL = (
    "https://programs.iowadnr.gov/geospatial/rest/services/"
    "Geology/BedrockGeology/MapServer/6/query"
)
PAGE_SIZE = 500  # server rejects 2000; 500 works reliably

# ============================================================
# Geothermal suitability scores by SYSTEM (geologic period)
# Based on dominant lithology and thermal conductivity:
#   Carbonates (Silurian, Devonian, Ordovician Galena) → 0.85–1.0
#   Sandstone (Cambrian, Ordovician St. Peter) → 0.75–0.85
#   Mixed carbonate/clastic (Mississippian) → 0.65–0.75
#   Shale/clastic (Pennsylvanian, Cretaceous, Jurassic) → 0.3–0.5
#   Crystalline (Precambrian) → 0.6
# ============================================================
UNIT_SCORES = {
    # Silurian — dolomite/limestone, excellent thermal conductivity
    "Sg": 0.95,   # Gower Formation (dolomite)
    "Sh": 0.90,   # Hopkinton, Blanding, etc. (dolomite)
    "Sl": 0.85,   # LaPorte City Formation
    "Ss": 0.90,   # Scotch Grove Formation (dolomite)
    "Sw": 0.90,   # Waucoma Formation (dolomite)
    # Devonian — limestone/dolomite, very good
    "Dc": 0.90,   # Cedar Valley Group (limestone)
    "Df": 0.70,   # Fammenian (mixed shale/carbonate)
    "Dl": 0.65,   # Lime Creek Formation (shale-rich)
    "Dw": 0.85,   # Wapsipinicon Group (dolomite/limestone)
    # Ordovician — carbonate + sandstone
    "Og": 0.95,   # Galena Group / Platteville (dolomite, excellent)
    "Om": 0.45,   # Maquoketa Formation (shale! poor conductor)
    "Op": 0.80,   # St. Peter Sandstone / Prairie du Chien (sandstone/dolomite)
    # Cambrian — sandstone-dominated, good
    "Ce": 0.65,   # Eau Claire Formation (siltstone/shale)
    "Cj": 0.80,   # Jordan Sandstone / St. Lawrence (sandstone)
    "Cm": 0.75,   # Mt. Simon Formation (sandstone)
    "Cw": 0.80,   # Wonewoc Formation (sandstone)
    # Mississippian — limestone, moderate-good
    "Ma": 0.70,   # Augusta Group (limestone/dolomite)
    "Mg": 0.75,   # Gilmore City Formation (limestone)
    "Mk": 0.60,   # Kinderhookian (mixed)
    "Ms": 0.75,   # Pella / St. Louis (limestone)
    # Pennsylvanian — shale/sandstone/coal, poor thermal conductivity
    "Pb": 0.40,   # Bronson Group
    "Pcl": 0.35,  # lower Cherokee Group (shale/coal)
    "Pcu": 0.35,  # upper Cherokee Group (shale/coal)
    "Pd": 0.40,   # Douglas Group
    "Pk": 0.40,   # Kansas City Group
    "Pl": 0.40,   # Lansing Group
    "Pm": 0.40,   # Marmaton Group
    "Ps": 0.40,   # Shawnee Group
    "Pw": 0.40,   # Waubonsee Group
    # Cretaceous — sandstone/shale, variable
    "Kd": 0.50,   # Dakota (sandstone, but often shaley)
    "Kf": 0.35,   # Fort Benton Group (shale)
    "Kmc": 0.30,  # Manson impact structure
    "Kmm": 0.30,  # Manson moat
    "Kmt": 0.30,  # Manson impact
    "Kn": 0.35,   # Niobrara Formation (chalk/shale)
    # Jurassic
    "Jf": 0.40,   # Fort Dodge Formation (gypsum)
    # Precambrian
    "Xs": 0.60,   # Sioux Quartzite (metamorphic, decent conductor)
}

# Fallback by SYSTEM if UNITCODE not found
SYSTEM_SCORES = {
    "Silurian": 0.90,
    "Devonian": 0.80,
    "Ordovician": 0.75,
    "Cambrian": 0.75,
    "Mississippian": 0.70,
    "Pennsylvanian": 0.38,
    "Cretaceous": 0.45,
    "Jurassic": 0.40,
    "Precambrian": 0.60,
}


def fetch_all_features():
    """Paginate through all features from the ArcGIS REST service."""
    all_features = []
    offset = 0

    while True:
        params = (
            f"?where=1%3D1"
            f"&outFields=SYSTEM,UNITCODE,UNITNAME"
            f"&resultRecordCount={PAGE_SIZE}"
            f"&resultOffset={offset}"
            f"&f=geojson"
        )
        url = BASE_URL + params
        print(f"  Fetching offset {offset}...")

        resp = requests.get(url, timeout=120)
        resp.raise_for_status()
        data = resp.json()

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        print(f"    Got {len(features)} features (total: {len(all_features)})")

        if len(features) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_features


def score_features(features):
    """Add geothermal suitability score to each feature."""
    scored = 0
    for f in features:
        props = f.get("properties", {})
        code = props.get("UNITCODE", "")
        system = props.get("SYSTEM", "")

        score = UNIT_SCORES.get(code, SYSTEM_SCORES.get(system, 0.5))
        props["geo_score"] = score
        scored += 1

    return features


if __name__ == "__main__":
    print("=" * 60)
    print("IOWA BEDROCK GEOLOGY — DNR ArcGIS REST Service")
    print("=" * 60)
    print()

    print("Fetching bedrock geology polygons...")
    features = fetch_all_features()
    print(f"\nTotal features: {len(features)}")

    print("\nScoring formations for geothermal suitability...")
    features = score_features(features)

    # Summary stats
    by_system = {}
    for f in features:
        p = f["properties"]
        sys_name = p["SYSTEM"]
        if sys_name not in by_system:
            by_system[sys_name] = {"count": 0, "score_sum": 0}
        by_system[sys_name]["count"] += 1
        by_system[sys_name]["score_sum"] += p["geo_score"]

    print(f"\n{'SYSTEM':>16s}  {'Count':>6s}  {'Avg Score':>9s}")
    print("-" * 38)
    for sys_name in sorted(by_system.keys()):
        info = by_system[sys_name]
        avg = info["score_sum"] / info["count"]
        print(f"{sys_name:>16s}  {info['count']:>6d}  {avg:>9.2f}")

    # Save GeoJSON
    geojson = {"type": "FeatureCollection", "features": features}
    out_path = PROCESSED / "iowa_bedrock.geojson"
    with open(out_path, "w") as fp:
        json.dump(geojson, fp)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\nSaved {out_path} ({size_mb:.1f} MB)")
    print(f"  {len(features)} polygons with geo_score attribute")
