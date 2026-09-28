"""
Fetch Iowa depth-to-bedrock contour data from ArcGIS Online.
Source: https://services3.arcgis.com/kd9gaiUExYqUbnoq/arcgis/rest/services/depth_to_bedrock/FeatureServer/0

The data consists of contour polylines at 50-foot intervals showing approximate
depth from land surface to bedrock. We interpolate these to create a continuous
surface and classify into Darcy-style scoring bins:
  - 0–20 ft:   0.2  (too shallow for useful aquifer)
  - 20–50 ft:  0.6  (marginal)
  - 50–150 ft: 1.0  (ideal Darcy target zone)
  - 150–250 ft: 0.6  (deeper, higher cost)
  - >250 ft:   0.3  (deep, expensive)

Output: data/processed/iowa_depth_to_bedrock.geojson (contour lines with scores)
        data/processed/iowa_dtb_scored_zones.geojson (interpolated scored polygon zones)
"""
import json
import sys
import numpy as np
import requests
from pathlib import Path

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

BASE_URL = (
    "https://services3.arcgis.com/kd9gaiUExYqUbnoq/arcgis/rest/services/"
    "depth_to_bedrock/FeatureServer/0/query"
)
PAGE_SIZE = 2000  # ArcGIS Online typically supports 2000


def score_depth(depth_ft):
    """Score depth-to-bedrock value for Darcy suitability."""
    if depth_ft <= 20:
        return 0.2
    elif depth_ft <= 50:
        return 0.6
    elif depth_ft <= 150:
        return 1.0
    elif depth_ft <= 250:
        return 0.6
    else:
        return 0.3


def fetch_contour_lines():
    """Download all contour polylines from the ArcGIS FeatureServer."""
    all_features = []
    offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": "CONTOUR",
            "resultRecordCount": PAGE_SIZE,
            "resultOffset": offset,
            "outSR": "4326",
            "maxAllowableOffset": "0.005",  # ~500m simplification in degrees
            "f": "geojson",
        }
        print(f"  Fetching offset {offset}...")

        try:
            resp = requests.get(BASE_URL, params=params, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"    Error at offset {offset}: {e}")
            if offset == 0:
                raise
            break

        features = data.get("features", [])
        if not features:
            break

        all_features.extend(features)
        print(f"    Got {len(features)} features (total: {len(all_features)})")

        if len(features) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_features


def extract_sample_points(features):
    """Extract vertex points from contour polylines with their depth values."""
    points = []
    for f in features:
        contour = f["properties"].get("CONTOUR")
        if contour is None:
            continue
        geom = f.get("geometry")
        if geom is None:
            continue
        coords = geom.get("coordinates", [])
        if geom.get("type") == "MultiLineString":
            for line in coords:
                for x, y in line:
                    points.append((x, y, float(contour)))
        elif geom.get("type") == "LineString":
            for x, y in coords:
                points.append((x, y, float(contour)))
    return np.array(points)


def interpolate_and_score(points, iowa_bounds):
    """Interpolate depth values to a grid and create scored polygon zones."""
    from scipy.interpolate import griddata

    west, south, east, north = iowa_bounds
    # ~2km resolution grid
    nx = int((east - west) / 0.02) + 1
    ny = int((north - south) / 0.02) + 1

    print(f"  Creating {nx}x{ny} interpolation grid...")
    grid_x = np.linspace(west, east, nx)
    grid_y = np.linspace(south, north, ny)
    grid_xx, grid_yy = np.meshgrid(grid_x, grid_y)

    xs, ys, vals = points[:, 0], points[:, 1], points[:, 2]

    print(f"  Interpolating from {len(points)} sample points...")
    depth_grid = griddata(
        (xs, ys), vals, (grid_xx, grid_yy),
        method="linear", fill_value=np.nan
    )

    # Fill NaN edges with nearest-neighbor
    nan_mask = np.isnan(depth_grid)
    if nan_mask.any():
        depth_nn = griddata(
            (xs, ys), vals, (grid_xx, grid_yy),
            method="nearest"
        )
        depth_grid[nan_mask] = depth_nn[nan_mask]

    print(f"  Depth range: {np.nanmin(depth_grid):.0f} – {np.nanmax(depth_grid):.0f} ft")

    # Score the grid
    score_grid = np.vectorize(score_depth)(depth_grid)

    return grid_x, grid_y, depth_grid, score_grid


def create_scored_zones(grid_x, grid_y, score_grid, iowa_boundary_path):
    """Create polygon zones from scored grid cells, clipped to Iowa."""
    import geopandas as gpd
    from shapely.geometry import box as shapely_box

    dx = grid_x[1] - grid_x[0]
    dy = grid_y[1] - grid_y[0]
    ny, nx = score_grid.shape

    # Build grid-cell polygons grouped by score
    print(f"  Building polygon zones from {nx}x{ny} grid...")
    records = []
    for i in range(ny):
        for j in range(nx):
            score = score_grid[i, j]
            if np.isnan(score):
                continue
            cell = shapely_box(
                grid_x[j] - dx / 2, grid_y[i] - dy / 2,
                grid_x[j] + dx / 2, grid_y[i] + dy / 2,
            )
            records.append({"geometry": cell, "dtb_score": float(score)})

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

    # Dissolve by score to create 5 zone multipolygons
    print("  Dissolving into scored zones...")
    dissolved = gdf.dissolve(by="dtb_score").reset_index()

    # Simplify geometry
    dissolved["geometry"] = dissolved.geometry.simplify(0.005, preserve_topology=True)

    # Clip to Iowa boundary if available
    if iowa_boundary_path.exists():
        print("  Clipping to Iowa boundary...")
        iowa = gpd.read_file(iowa_boundary_path)
        dissolved = gpd.clip(dissolved, iowa.geometry.union_all())

    # Explode multipolygons
    exploded = dissolved.explode(index_parts=False).reset_index(drop=True)

    return exploded


if __name__ == "__main__":
    contour_path = PROCESSED / "iowa_depth_to_bedrock.geojson"
    zones_path = PROCESSED / "iowa_dtb_scored_zones.geojson"

    print("=" * 60)
    print("IOWA DEPTH TO BEDROCK — ArcGIS Online Contour Data")
    print("=" * 60)
    print()

    # Step 1: Download contour lines
    if contour_path.exists():
        size_mb = contour_path.stat().st_size / (1024 * 1024)
        print(f"Contour data already exists: {contour_path} ({size_mb:.1f} MB)")
        print("Loading cached data...")
        with open(contour_path) as fp:
            cached = json.load(fp)
        features = cached["features"]
    else:
        print("Fetching depth-to-bedrock contour lines...")
        features = fetch_contour_lines()
        print(f"\nTotal contour features: {len(features)}")

        if not features:
            print("ERROR: No features returned.")
            sys.exit(1)

        # Score contour lines
        for f in features:
            contour = f["properties"].get("CONTOUR", 0)
            f["properties"]["dtb_score"] = score_depth(float(contour))

        # Save contour lines
        geojson = {"type": "FeatureCollection", "features": features}
        with open(contour_path, "w") as fp:
            json.dump(geojson, fp)
        size_mb = contour_path.stat().st_size / (1024 * 1024)
        print(f"Saved contour lines: {contour_path} ({size_mb:.1f} MB)")

    # Show contour value distribution
    contour_vals = {}
    for f in features:
        v = f["properties"].get("CONTOUR", 0)
        contour_vals[v] = contour_vals.get(v, 0) + 1
    print(f"\nContour values (depth in ft):")
    for v in sorted(contour_vals.keys()):
        print(f"  {v:>6.0f} ft: {contour_vals[v]:>6d} features  (score: {score_depth(v):.1f})")

    # Step 2: Extract sample points
    print("\nExtracting sample points from contour lines...")
    points = extract_sample_points(features)
    print(f"  {len(points)} sample points extracted")

    if len(points) < 100:
        print("ERROR: Too few sample points for interpolation.")
        sys.exit(1)

    # Step 3: Interpolate and score
    # Iowa approximate bounds
    iowa_bounds = (-96.64, 40.37, -90.14, 43.50)
    print("\nInterpolating depth-to-bedrock surface...")
    grid_x, grid_y, depth_grid, score_grid = interpolate_and_score(points, iowa_bounds)

    # Step 4: Create scored polygon zones
    print("\nCreating scored polygon zones...")
    iowa_boundary_path = PROCESSED / "iowa_boundary.geojson"

    try:
        zones = create_scored_zones(grid_x, grid_y, score_grid, iowa_boundary_path)
        zones.to_file(zones_path, driver="GeoJSON")
        size_mb = zones_path.stat().st_size / (1024 * 1024)
        print(f"\nSaved scored zones: {zones_path} ({size_mb:.1f} MB)")
        print(f"  {len(zones)} polygon features across {zones['dtb_score'].nunique()} score bins")
    except ImportError as e:
        print(f"  Cannot create polygon zones (missing dependency: {e})")
        print("  The contour line GeoJSON can still be used.")
    except Exception as e:
        print(f"  Error creating zones: {e}")

    print("\nDone!")
