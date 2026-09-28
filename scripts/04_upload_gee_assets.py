"""
Upload processed vector data as GEE assets for use in the suitability model.

This script uses the Earth Engine Python API to ingest:
  - Iowa wells (GeoJSON -> FeatureCollection)
  - Iowa aquifers (GeoJSON -> FeatureCollection)
  - EPA constraints (GeoJSON -> FeatureCollection)

PREREQUISITES:
  - pip install earthengine-api
  - ee.Authenticate() run once
  - A GEE project ID set as EE_PROJECT env var

If you prefer, you can upload these manually via the GEE Code Editor asset manager:
  https://code.earthengine.google.com/ -> Assets -> New -> Table Upload

If GEE asset upload is blocked or slow, the suitability script (05) can fall back to
loading these as inline FeatureCollections from GeoJSON via the proxy.
"""
import os
import json
import ee
from pathlib import Path

PROCESSED = Path("data/processed")
PROJECT = os.environ.get("EE_PROJECT", "generalresearch-478019")

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
    print(f"  Uploading {name} -> {asset_id}")

    with open(path) as f:
        geojson = json.load(f)

    # Check if asset already exists
    try:
        ee.data.getAsset(asset_id)
        print(f"    Asset already exists, skipping")
        continue
    except ee.EEException:
        pass

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

print("\nUpload tasks submitted. Check status at:")
print("  https://code.earthengine.google.com/tasks")
print("\nAlternatively, upload GeoJSON files manually via the GEE Code Editor asset manager.")
