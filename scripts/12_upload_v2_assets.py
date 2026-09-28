"""
Upload V2 processed vector data as GEE assets for the Darcy-style suitability model.

Assets:
  - iowa_surficial: Surficial geology with surf_score (0–1)
  - iowa_dtb_zones: Depth-to-bedrock scored zones with dtb_score (0–1)
  - iowa_wells: USGS NWIS wells (reused from V1)
  - epa_constraints: EPA Superfund/brownfield sites (reused from V1)

PREREQUISITES:
  - pip install earthengine-api
  - ee.Authenticate() run once
  - A GEE project ID set as EE_PROJECT env var
"""
import os
import json
import ee
from pathlib import Path

PROCESSED = Path("data/processed")
PROJECT = os.environ.get("EE_PROJECT", "generalresearch-478019")

print("Initializing Earth Engine...")
try:
    # Try service account first
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

    if SA_PATH and os.path.exists(SA_PATH):
        with open(SA_PATH) as f:
            sa_info = json.load(f)
        credentials = ee.ServiceAccountCredentials(sa_info["client_email"], SA_PATH)
        ee.Initialize(credentials=credentials, project=PROJECT)
        print(f"  Authenticated as {sa_info['client_email']}")
    else:
        ee.Initialize(project=PROJECT)
        print("  Using default credentials")
except Exception:
    print("EE not authenticated. Run: earthengine authenticate")
    print("Or upload assets manually via the GEE Code Editor.")
    exit(0)

# V2-specific assets
assets = {
    "iowa_surficial": PROCESSED / "iowa_surficial_simplified.geojson",
    "iowa_dtb_zones": PROCESSED / "iowa_dtb_scored_zones.geojson",
    # Reuse V1 assets
    "iowa_wells": PROCESSED / "iowa_wells.geojson",
    "epa_constraints": PROCESSED / "epa_constraints.geojson",
}

for name, path in assets.items():
    if not path.exists():
        print(f"  SKIP {name}: {path} not found")
        continue

    asset_id = f"projects/{PROJECT}/assets/{name}"
    print(f"\n  Checking {name} -> {asset_id}")

    # Check if asset already exists
    try:
        ee.data.getAsset(asset_id)
        print(f"    Asset already exists, skipping")
        continue
    except ee.EEException:
        pass

    # Load GeoJSON
    with open(path) as f:
        geojson = json.load(f)

    file_size_mb = path.stat().st_size / (1024 * 1024)
    n_features = len(geojson.get("features", []))
    print(f"    File: {file_size_mb:.1f} MB, {n_features} features")

    # Check size limit (GEE inline limit is ~10MB)
    if file_size_mb > 10:
        print(f"    WARNING: File exceeds 10 MB GEE inline limit.")
        print(f"    Upload manually via: earthengine upload table --asset_id={asset_id} {path}")
        continue

    # Upload as table
    try:
        features = []
        for feat in geojson.get("features", []):
            geom = feat.get("geometry")
            props = feat.get("properties", {})
            if geom:
                ee_feat = ee.Feature(ee.Geometry(geom), props)
                features.append(ee_feat)

        if features:
            fc = ee.FeatureCollection(features)
            task = ee.batch.Export.table.toAsset(
                collection=fc,
                description=f"upload_{name}",
                assetId=asset_id,
            )
            task.start()
            print(f"    Started upload task: {task.status()['id']}")
        else:
            print(f"    No features to upload for {name}")

    except Exception as e:
        print(f"    ERROR uploading {name}: {e}")
        print(f"    Upload manually via GEE Code Editor")

print("\n" + "=" * 60)
print("Upload tasks submitted. Check status at:")
print("  https://code.earthengine.google.com/tasks")
print()
print("Once complete, run: python scripts/13_gee_suitability_v2.py")
