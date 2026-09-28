"""
Export web-ready assets for the interactive map.

  - If script 05 already generated suitability_overlay.png + bounds.json, keep them.
  - If a GeoTIFF was exported manually from GEE Code Editor, convert it to PNG.
  - Copy vector GeoJSON files to data/tiles/ for the web map.

Output: data/tiles/ directory with all web map assets.
"""
import json
from pathlib import Path

PROCESSED = Path("data/processed")
TILES = Path("data/tiles")
TILES.mkdir(parents=True, exist_ok=True)

# ============================================================
# Suitability overlay
# ============================================================
overlay_path = TILES / "suitability_overlay.png"
bounds_path = TILES / "bounds.json"

if overlay_path.exists() and bounds_path.exists():
    # Script 05 already generated the overlay via EE Python API
    with open(bounds_path) as f:
        bd = json.load(f)
    if not bd.get("placeholder"):
        print(f"Suitability overlay already exists: {overlay_path}")
        print(f"  Bounds: {bd['bounds']}")
    else:
        print("bounds.json is a placeholder — overlay may be missing.")
        print("Run script 05 first to generate the suitability raster.")

else:
    # Check for manually exported GeoTIFF from GEE Code Editor
    raster_path = PROCESSED / "suitability_composite.tif"
    if raster_path.exists():
        import numpy as np
        import rasterio
        from rasterio.warp import reproject, calculate_default_transform, Resampling
        from PIL import Image

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

        print("Generating suitability PNG overlay...")
        with rasterio.open(reprojected_path) as src:
            data = src.read(1)
            bounds = src.bounds

        nodata = -9999
        valid = (data != nodata) & np.isfinite(data)
        norm = np.clip(data / 100.0, 0, 1)

        # RGBA: red->yellow->green color ramp
        rgba = np.zeros((*data.shape, 4), dtype=np.uint8)
        rgba[valid, 0] = np.clip(255 * (1.0 - norm[valid]) * 2, 0, 255).astype(np.uint8)
        rgba[valid, 1] = np.clip(255 * norm[valid] * 2, 0, 255).astype(np.uint8)
        rgba[valid, 2] = 20
        rgba[valid, 3] = 180

        img = Image.fromarray(rgba)
        img.save(overlay_path)
        print(f"  Saved suitability_overlay.png ({img.size[0]}x{img.size[1]})")

        bounds_json = {
            "bounds": [bounds.left, bounds.bottom, bounds.right, bounds.top],
            "width": int(width), "height": int(height)
        }
        with open(bounds_path, "w") as f:
            json.dump(bounds_json, f, indent=2)
    else:
        print("No suitability data found.")
        print("  Run script 05 to compute via EE Python API,")
        print("  or export from GEE Code Editor to data/processed/suitability_composite.tif")
        print("Creating placeholder bounds.json...")
        bounds_json = {
            "bounds": [-96.64, 40.37, -90.14, 43.50],
            "width": 0, "height": 0,
            "placeholder": True
        }
        with open(bounds_path, "w") as f:
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
