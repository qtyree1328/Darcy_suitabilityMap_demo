"""
Fetch Iowa surficial geology from Iowa DNR ArcGIS REST service (100k scale).
Source: https://programs.iowadnr.gov/geospatial/rest/services/Geology/SurficialGeology/MapServer/2

Assigns Darcy-style geothermal suitability scores based on material permeability:
  - Coarse outwash/gravel: high permeability → high score (1.0)
  - Alluvium: moderate-high permeability → moderate-high score (0.8)
  - Till: moderate permeability → moderate score (0.5)
  - Lacustrine/fine-grained: low permeability → low score (0.1)

Output: data/processed/iowa_surficial.geojson
        data/processed/iowa_surficial_simplified.geojson (dissolved by score)
"""
import json
import requests
import sys
from pathlib import Path
from pyproj import Transformer

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

BASE_URL = (
    "https://programs.iowadnr.gov/geospatial/rest/services/"
    "Geology/SurficialGeology/MapServer/2/query"
)
PAGE_SIZE = 500

# Native CRS is NAD 1983 UTM Zone 15N (WKID 26915)
# We need to reproject to WGS84 (EPSG:4326) for GeoJSON
transformer = Transformer.from_crs("EPSG:26915", "EPSG:4326", always_xy=True)

# ============================================================
# Surficial material scoring for Darcy-style shallow geothermal
# ============================================================
UNIT_SCORES = {
    # === SAND & GRAVEL / COARSE OUTWASH (1.0) ===
    "Qoch":  1.0,   # Valley Train Outwash
    "Qnw":   1.0,   # Sand and Gravel
    "Qnw2":  1.0,   # Sand and Gravel
    "Qnw_T2": 1.0,  # Sand and Gravel, Terrace 2
    "Qnw_T3": 1.0,  # Sand and Gravel Terrace
    "Qnw_T1": 1.0,  # Sand and Gravel Terrace
    "Qhm":   1.0,   # Outwash Sand and Gravel
    "Qhs":   0.9,   # Outwash Sand and Pebbly Sand
    "Qhsb":  0.9,   # Outwash Sand and Pebbly Sand
    "Qof":   0.9,   # Outwash Fan
    "Qgfp":  0.9,   # Complex Glaciofluvial Plain

    # === MIXED ALLUVIUM / MIXED COARSE DRIFT (0.7–0.8) ===
    "Qal":   0.8,   # Alluvium
    "Qal2":  0.8,   # Stream Valley Thick Alluvium
    "Qallt": 0.8,   # Low Terrace alluvium
    "Qalit": 0.7,   # Intermediate Terrace
    "Qalht": 0.7,   # High Terrace
    "Qali-ht": 0.7, # Intermediate-High Terrace
    "Qaf":   0.7,   # Alluvial Fan
    "Qpt":   0.7,   # High Terrace (Late/Early Phase)
    "Qptep": 0.7,   # Early Phase High Terrace
    "Qptlp": 0.7,   # Late Phase High Terrace
    "Qwa1":  0.8,   # Sand and Gravel Shallow to Till
    "Qoch(s)": 0.7, # Slackwater over Outwash
    "Qe":    0.7,   # Sand Dunes / Eolian Sand (permeable)
    "Qps2":  0.6,   # Eolian Sand and Interbedded Loess

    # === TILL / DIAMICTON / MIXED GLACIAL DRIFT (0.5) ===
    "Qtp":   0.5,   # Till Plain
    "Qtr":   0.5,   # Till Ridge
    "Qtpl":  0.5,   # Till Plain Elongated Ridge
    "Qtpl1": 0.5,   # Till Plain Aligned Ridge
    "Qtpl3": 0.5,   # Till Plain Aligned Ridge
    "Qtpld4": 0.5,  # Till Plain Linked Depression
    "Qtpofb": 0.5,  # Till Plain Buried Outwash
    "Qtr_am1": 0.5, # Till Ridge
    "Qtr_amc": 0.5, # Till Ridge - Altamont Moraine
    "Qtr_bm": 0.5,  # Till Ridge - Bemis Moraine
    "Qtb_bm": 0.5,  # Till Bench
    "Qte":   0.5,   # Till Escarpment
    "Qgla":  0.5,   # Glacial Till
    "Qsc":   0.5,   # Glacial Till
    "Qwa3":  0.5,   # Glacial Till
    "Qsgc":  0.5,   # Supraglacial Complex
    "Qsgclp": 0.5,  # Supraglacial Complex with Ridge Forms
    "Qi":    0.5,   # Cut and Fill
    "Qwa2":  0.5,   # Loamy/Sandy Shallow to Till
    "Qwa4":  0.5,   # Loamy Shallow to Till
    "Qgla2": 0.5,   # Loamy Shallow to Till
    "Qsc2":  0.5,   # Loamy Shallow to Till

    # === SHALLOW TO BEDROCK (0.3–0.4) ===
    "Qalb":  0.4,   # Alluvium Shallow to Bedrock
    "Qnw3":  0.4,   # Sand and Gravel Shallow to Bedrock
    "Qhmb":  0.4,   # Outwash Shallow to Bedrock
    "Qochb": 0.4,   # Outwash Shallow to Bedrock
    "Qwa5":  0.3,   # Loamy/Sandy Shallow to Rock
    "Qsc3":  0.3,   # Loamy Shallow to Rock
    "Qdlgc": 0.3,   # Loamy Shallow to Limestone/Shale
    "Qdsr":  0.3,   # Loamy Shallow to Limestone/Shale
    "Qbr":   0.2,   # Bedrock (exposed)
    "bedrock": 0.2,  # Bedrock
    "br":    0.2,   # Bedrock

    # === LOESS / SILTY COVER (0.3) ===
    "Qps":   0.3,   # Loess
    "Qps1":  0.3,   # Loess and Interbedded Eolian Sand
    "Qps1b": 0.3,   # Thick Loess and Interbedded Eolian Sand
    "Qps4":  0.4,   # Loess Shallow to Sand and Gravel (better)
    "Qps5":  0.3,   # Loess and Eolian Sand
    "Qps_gla": 0.3, # Loess

    # === CLAY-RICH LACUSTRINE / FINE-GRAINED (0.1) ===
    "Qglp":  0.1,   # Lake Plain
    "Qglp_ls": 0.1, # Lake Plain (Large Scale)
    "Qglp_ss": 0.1, # Lake Sediment (Small-scale)
    "Qlglp": 0.1,   # Lake Plain with Ridge Forms
    "Qglhc": 0.2,   # Collapsed Lake Sediments

    # === WATER / ORGANIC / UNSUITABLE (0.0) ===
    "Qo":    0.0,   # Muck and Peat
    "Qdb":   0.0,   # Muck and Peat
    "W":     0.0,   # Water
    "Water": 0.0,
    "water": 0.0,
    "w":     0.0,

    # === BEDROCK FORMATIONS (exposed, use as low score) ===
    "Dc":    0.2, "Dcv":  0.2, "Dl":   0.2, "Dlc":  0.2,
    "Dlgc":  0.2, "Dsr":  0.2, "Du":   0.2, "Dw":   0.2,
    "Dw-Sg": 0.2, "Mu":   0.2, "Prc":  0.2, "Pu":   0.2,
    "Sg":    0.2, "Shb":  0.2, "Ss":   0.2, "Su":   0.2,

    # === HUMAN-MODIFIED / OTHER ===
    "Qf":    0.3,   # Fill
    "Qpq":   0.3,   # Pits and Quarries
    "Qq":    0.3,   # Quarries and Pits
    "upland": 0.5,
    "x":     0.5,
}
DEFAULT_SCORE = 0.5


def esri_rings_to_geojson(rings):
    """Convert ESRI JSON rings (UTM 15N) to GeoJSON Polygon coordinates (WGS84)."""
    geojson_rings = []
    for ring in rings:
        transformed = []
        for pt in ring:
            lng, lat = transformer.transform(pt[0], pt[1])
            transformed.append([round(lng, 6), round(lat, 6)])
        geojson_rings.append(transformed)
    return geojson_rings


def fetch_all_features():
    """Paginate through all features using OBJECTID range."""
    all_features = []
    max_oid = 0

    while True:
        params = {
            "where": f"surficial_geology_100k.OBJECTID>{max_oid}",
            "outFields": "*",
            "resultRecordCount": PAGE_SIZE,
            "orderByFields": "surficial_geology_100k.OBJECTID",
            "f": "json",
        }
        print(f"  Fetching OID > {max_oid}...")

        resp = requests.get(BASE_URL, params=params, timeout=180)
        resp.raise_for_status()

        try:
            data = resp.json()
        except Exception:
            print(f"    JSON decode error, checking response...")
            print(f"    Status: {resp.status_code}, Content-Type: {resp.headers.get('content-type')}")
            print(f"    First 200 chars: {resp.text[:200]}")
            break

        if "error" in data:
            print(f"    Server error: {data['error']}")
            break

        esri_features = data.get("features", [])
        if not esri_features:
            break

        # Convert ESRI JSON to GeoJSON features
        for ef in esri_features:
            attrs = ef.get("attributes", {})
            geom = ef.get("geometry", {})
            rings = geom.get("rings", [])

            oid = attrs.get("surficial_geology_100k.OBJECTID", 0)
            if oid > max_oid:
                max_oid = oid

            unit_sym = attrs.get("surficial_geology_100k.unit_sym", "")
            unit_name = attrs.get("surficial_units.Unit_name", "")
            formation = attrs.get("surficial_units.Formation", "")

            if not rings:
                continue

            geojson_coords = esri_rings_to_geojson(rings)
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": geojson_coords,
                },
                "properties": {
                    "unit_sym": unit_sym,
                    "Unit_name": unit_name,
                    "Formation": formation,
                    "surf_score": UNIT_SCORES.get(unit_sym, DEFAULT_SCORE),
                },
            }
            all_features.append(feature)

        print(f"    Got {len(esri_features)} features (total: {len(all_features)}, max OID: {max_oid})")

        if len(esri_features) < PAGE_SIZE:
            break

    return all_features


def simplify_for_upload(input_path):
    """Dissolve features by surf_score and simplify geometry for GEE upload."""
    try:
        import geopandas as gpd

        gdf = gpd.read_file(input_path)

        # Fix invalid geometries
        gdf["geometry"] = gdf.geometry.buffer(0)

        # Dissolve by surf_score (fewer groups = smaller output)
        dissolved = gdf.dissolve(by="surf_score", aggfunc="first").reset_index()

        # Simplify geometry (0.005 degrees ≈ 500m)
        dissolved["geometry"] = dissolved.geometry.simplify(0.005, preserve_topology=True)
        dissolved["geometry"] = dissolved.geometry.buffer(0)  # fix any post-simplification issues

        # Explode multipolygons
        exploded = dissolved.explode(index_parts=False).reset_index(drop=True)

        # Keep only needed columns
        keep_cols = ["geometry", "unit_sym", "Unit_name", "surf_score"]
        for col in keep_cols:
            if col not in exploded.columns and col != "geometry":
                exploded[col] = DEFAULT_SCORE if col == "surf_score" else ""

        out_path = PROCESSED / "iowa_surficial_simplified.geojson"
        exploded[keep_cols].to_file(out_path, driver="GeoJSON")
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"\nSaved simplified: {out_path} ({size_mb:.1f} MB)")
        print(f"  {len(exploded)} features (dissolved from {len(gdf)})")
        return True

    except Exception as e:
        print(f"  Simplification error: {e}")
        return False


if __name__ == "__main__":
    out_path = PROCESSED / "iowa_surficial.geojson"

    if out_path.exists():
        size_mb = out_path.stat().st_size / (1024 * 1024)
        print(f"Surficial geology already exists: {out_path} ({size_mb:.1f} MB)")
        print("Delete it to re-download.")

        simplified_path = PROCESSED / "iowa_surficial_simplified.geojson"
        if not simplified_path.exists():
            print("\nSimplifying existing data...")
            simplify_for_upload(out_path)
        sys.exit(0)

    print("=" * 60)
    print("IOWA SURFICIAL GEOLOGY — DNR ArcGIS REST Service (100k)")
    print("=" * 60)
    print()

    print("Fetching surficial geology polygons...")
    features = fetch_all_features()
    print(f"\nTotal features: {len(features)}")

    if not features:
        print("ERROR: No features returned.")
        sys.exit(1)

    # Save full GeoJSON first (before summary, so data is preserved on error)
    geojson = {"type": "FeatureCollection", "features": features}
    with open(out_path, "w") as fp:
        json.dump(geojson, fp)

    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"\nSaved {out_path} ({size_mb:.1f} MB)")
    print(f"  {len(features)} polygons with surf_score attribute")

    # Summary stats
    by_unit = {}
    for f in features:
        p = f["properties"]
        sym = p.get("unit_sym") or "unknown"
        if sym not in by_unit:
            by_unit[sym] = {"count": 0, "name": p.get("Unit_name", ""), "score": p.get("surf_score", 0.5)}
        by_unit[sym]["count"] += 1

    print(f"\n{'Unit':>10s}  {'Count':>6s}  {'Score':>6s}  Name")
    print("-" * 60)
    for sym in sorted(by_unit.keys()):
        info = by_unit[sym]
        print(f"{str(sym):>10s}  {info['count']:>6d}  {info['score']:>6.2f}  {info['name']}")

    # Create simplified version
    print("\nCreating simplified version for GEE upload...")
    simplify_for_upload(out_path)
