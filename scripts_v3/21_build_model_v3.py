"""
V3 Geostatistical Model — Random Forest + Residual Kriging

This is the main V3 computation script. It:
  1. Loads V3-scored wells
  2. Extracts geospatial covariates at well locations (geopandas spatial join)
  3. Trains a Random Forest trend model with cross-validation
  4. Kriges residuals onto a statewide grid
  5. Generates suitability + uncertainty PNG overlays
  6. Saves bounds_v3.json with metadata

Architecture (plan Section 6a):
  - Response variable: composite well productivity score (v3_score)
  - RF features: surficial geology score, DTB score, landform score,
    alluvial aquifer membership, distance to alluvial boundary
  - Residual kriging: ordinary kriging of (observed - RF predicted)
  - Final surface: RF prediction + kriged residual, clipped 0-1, scaled 0-100
  - Uncertainty: kriging variance (high variance = low confidence)

Prerequisites:
  pip install scikit-learn pykrige geopandas shapely numpy matplotlib

Input:  data/processed/iowa_sourcewater_wells_v3.geojson
        data/processed/iowa_surficial.geojson
        data/processed/iowa_dtb_scored_zones.geojson
        data/processed/iowa_landform_regions.geojson
        data/processed/aquifers_iowa.geojson
        data/processed/iowa_boundary.geojson
        data/processed/epa_constraints.geojson

Output: data/tiles_v3/suitability_overlay_v3.png
        data/tiles_v3/uncertainty_overlay_v3.png
        data/tiles_v3/bounds_v3.json
"""
import json
import sys
import time
import warnings
import numpy as np
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils_v3 import (
    PROCESSED_DIR, TILES_V3, IOWA_BBOX, GRID_RES_DEG,
    SUITABILITY_COLORS, ensure_dirs,
)

ensure_dirs()


# ============================================================
# 1. LOAD DATA WITH GEOPANDAS
# ============================================================
def load_wells_gdf():
    """Load V3-scored wells as a GeoDataFrame."""
    import geopandas as gpd

    path = PROCESSED_DIR / "iowa_sourcewater_wells_v3.geojson"
    if not path.exists():
        print(f"ERROR: {path} not found. Run 20_score_wells_v3.py first.")
        sys.exit(1)

    gdf = gpd.read_file(path)
    print(f"  Loaded {len(gdf)} wells")
    return gdf


def load_polygon_layer(filename, score_col, default=0.5):
    """Load a polygon GeoJSON as GeoDataFrame, ensuring score column exists."""
    import geopandas as gpd

    path = PROCESSED_DIR / filename
    if not path.exists():
        print(f"    WARNING: {filename} not found")
        return None

    gdf = gpd.read_file(path)
    if score_col not in gdf.columns:
        gdf[score_col] = default
    # Ensure valid geometries
    gdf["geometry"] = gdf.geometry.buffer(0)
    print(f"    Loaded {filename}: {len(gdf)} features")
    return gdf


# ============================================================
# 2. EXTRACT COVARIATES VIA SPATIAL JOIN
# ============================================================
def extract_covariates_at_points(points_gdf):
    """
    Extract geospatial covariates at point locations using
    geopandas spatial joins (R-tree indexed, fast).

    Covariates used (all have statewide coverage):
      - Landform region score: well-derived productivity averages per geologic
        province. 100% statewide coverage, 28 regions.
      - Depth-to-bedrock score: interpolated from IGS contours, scored for
        Darcy drilling window (50-150 ft ideal). Statewide coverage.
      - Alluvial aquifer membership: binary, from USGS Principal Aquifers.
        Marks mapped productive aquifer zones.

    NOT used:
      - Surficial geology: only ~20-30% of Iowa has mapped coverage.
        Unmapped cells get default 0.5, creating false boundaries.
      - Distance to alluvial: Euclidean distance creates circular artifacts
        that don't respect geological boundaries.
    """
    import geopandas as gpd

    print("  Loading covariate layers...")
    dtb = load_polygon_layer("iowa_dtb_scored_zones.geojson", "dtb_score", 0.6)
    landforms = load_polygon_layer("iowa_landform_regions.geojson", "regional_productivity", 0.5)
    aquifers = load_polygon_layer("aquifers_iowa.geojson", "AQ_NAME", 0.5)

    n = len(points_gdf)
    result = points_gdf[["geometry"]].copy()

    # Depth to bedrock score (statewide, from contour interpolation)
    print(f"    Spatial join: depth to bedrock ({n} points)...")
    if dtb is not None:
        joined = gpd.sjoin(result[["geometry"]], dtb[["geometry", "dtb_score"]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        result["dtb_score"] = joined["dtb_score"].fillna(0.6).values[:n]
    else:
        result["dtb_score"] = 0.6

    # Landform region score (statewide, 28 geologic provinces)
    print(f"    Spatial join: landform regions ({n} points)...")
    if landforms is not None:
        score_col = "regional_productivity"
        if score_col not in landforms.columns:
            score_col = "landform_hydro_score"
        if score_col not in landforms.columns:
            landforms[score_col] = 0.5
        joined = gpd.sjoin(result[["geometry"]], landforms[["geometry", score_col]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        result["landform_score"] = joined[score_col].fillna(0.5).values[:n]
    else:
        result["landform_score"] = 0.5

    # Alluvial aquifer membership (binary — inside/outside mapped aquifer)
    print(f"    Spatial join: alluvial aquifer membership ({n} points)...")
    if aquifers is not None:
        joined = gpd.sjoin(result[["geometry"]], aquifers[["geometry"]], how="left", predicate="within")
        joined = joined[~joined.index.duplicated(keep="first")]
        result["alluvial_membership"] = (~joined["index_right"].isna()).astype(float).values[:n]
    else:
        result["alluvial_membership"] = 0.5

    feature_names = [
        "dtb_score", "landform_score", "alluvial_membership",
    ]

    X = result[feature_names].values
    print(f"    Covariate matrix shape: {X.shape}")
    for j, name in enumerate(feature_names):
        col = X[:, j]
        print(f"      {name}: mean={np.mean(col):.3f}, "
              f"std={np.std(col):.3f}, "
              f"range=[{np.min(col):.3f}, {np.max(col):.3f}]")

    return X, feature_names


def build_grid_points():
    """Create grid points as a GeoDataFrame for covariate extraction."""
    import geopandas as gpd
    from shapely.geometry import Point

    west = IOWA_BBOX["west"]
    east = IOWA_BBOX["east"]
    south = IOWA_BBOX["south"]
    north = IOWA_BBOX["north"]

    grid_lons = np.arange(west, east, GRID_RES_DEG)
    grid_lats = np.arange(south, north, GRID_RES_DEG)
    nx, ny = len(grid_lons), len(grid_lats)

    print(f"  Grid: {nx} x {ny} = {nx * ny:,} cells "
          f"({GRID_RES_DEG}° ~ {GRID_RES_DEG * 111:.0f} km)")

    gx, gy = np.meshgrid(grid_lons, grid_lats)
    flat_x = gx.ravel()
    flat_y = gy.ravel()

    points = [Point(x, y) for x, y in zip(flat_x, flat_y)]
    gdf = gpd.GeoDataFrame(geometry=points, crs="EPSG:4326")

    return gdf, grid_lons, grid_lats


# ============================================================
# 3. TRAIN RANDOM FOREST
# ============================================================
def train_random_forest(X, y, feature_names, completeness):
    """Train RF with sample weighting by data completeness."""
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.model_selection import cross_val_score

    print("\n  Training Random Forest...")
    print(f"    Samples: {len(y)}, Features: {X.shape[1]}")

    # Sample weights: upweight wells with more productivity data
    weights = np.ones(len(y))
    weights[completeness >= 3] = 2.0
    weights[completeness >= 4] = 3.0
    weights[completeness == 0] = 0.5

    rf = RandomForestRegressor(
        n_estimators=200,
        max_depth=12,
        min_samples_leaf=10,
        min_samples_split=20,
        max_features="sqrt",
        random_state=42,
        n_jobs=-1,
    )
    rf.fit(X, y, sample_weight=weights)

    # Cross-validation
    cv_scores = cross_val_score(rf, X, y, cv=5, scoring="r2")
    print(f"    5-fold CV R²: {cv_scores.mean():.3f} (+/- {cv_scores.std():.3f})")

    # Feature importance
    importances = rf.feature_importances_
    print(f"    Feature importances:")
    for name, imp in sorted(zip(feature_names, importances), key=lambda x: -x[1]):
        bar = "#" * int(imp * 50)
        print(f"      {name:>25s}: {imp:.3f}  {bar}")

    # Training R²
    y_pred = rf.predict(X)
    ss_res = np.sum((y - y_pred) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1 - ss_res / ss_tot
    print(f"    Training R²: {r2:.3f}")
    print(f"    Residual range: [{np.min(y - y_pred):.3f}, {np.max(y - y_pred):.3f}]")

    return rf, y_pred


# ============================================================
# 4. KRIGE RESIDUALS
# ============================================================
def krige_residuals(lons, lats, residuals, grid_lons, grid_lats):
    """Ordinary kriging of RF residuals onto prediction grid."""
    try:
        from pykrige.ok import OrdinaryKriging
    except ImportError:
        print("  WARNING: pykrige not installed. Using IDW fallback.")
        return idw_fallback(lons, lats, residuals, grid_lons, grid_lats)

    print("\n  Fitting variogram and kriging residuals...")
    print(f"    Data points: {len(residuals)}")
    print(f"    Grid size: {len(grid_lons)} x {len(grid_lats)}")

    # Subsample if too many wells (kriging is O(n³) for the solve)
    max_wells = 1500
    if len(lons) > max_wells:
        print(f"    Subsampling to {max_wells} wells for kriging...")
        idx = np.random.RandomState(42).choice(len(lons), max_wells, replace=False)
        lons_k = lons[idx]
        lats_k = lats[idx]
        res_k = residuals[idx]
    else:
        lons_k = lons
        lats_k = lats
        res_k = residuals

    t0 = time.time()
    ok = OrdinaryKriging(
        lons_k, lats_k, res_k,
        variogram_model="spherical",
        verbose=False,
        enable_plotting=False,
        nlags=15,
    )
    print(f"    Variogram fitted in {time.time() - t0:.1f}s")

    t0 = time.time()
    z_krige, ss_krige = ok.execute("grid", grid_lons, grid_lats)
    print(f"    Kriging executed in {time.time() - t0:.1f}s")

    z_krige = np.array(z_krige)
    ss_krige = np.array(ss_krige)

    # Smooth kriged surfaces to eliminate bullseye artifacts around wells.
    # sigma=2 grid cells = ~4km at 0.02° resolution — blends well influence
    # zones into smooth regional trends rather than point-centered circles.
    from scipy.ndimage import gaussian_filter
    z_krige = gaussian_filter(z_krige, sigma=2)
    ss_krige = gaussian_filter(ss_krige, sigma=2)
    print(f"    Applied Gaussian smoothing (sigma=2 cells, ~4 km)")

    print(f"    Kriged residual range: [{np.nanmin(z_krige):.4f}, {np.nanmax(z_krige):.4f}]")
    print(f"    Kriging variance range: [{np.nanmin(ss_krige):.4f}, {np.nanmax(ss_krige):.4f}]")

    return z_krige, ss_krige


def idw_fallback(lons, lats, values, grid_lons, grid_lats, power=2):
    """Inverse distance weighting fallback when pykrige is unavailable."""
    print("  Using IDW interpolation (pykrige unavailable)...")
    gx, gy = np.meshgrid(grid_lons, grid_lats)
    ny, nx = gx.shape

    # Vectorized IDW
    z = np.zeros((ny, nx))
    variance = np.zeros((ny, nx))

    for i in range(ny):
        if i % 20 == 0:
            print(f"    Row {i}/{ny}...")
        for j in range(nx):
            dx = lons - gx[i, j]
            dy = lats - gy[i, j]
            dist = np.sqrt(dx**2 + dy**2)
            dist = np.maximum(dist, 1e-10)
            w = 1.0 / dist**power
            z[i, j] = np.sum(w * values) / np.sum(w)
            nearest_dists = np.sort(dist)[:5]
            variance[i, j] = np.mean(nearest_dists**2)

    if np.max(variance) > 0:
        variance = variance / np.max(variance)

    return z, variance


# ============================================================
# 5. IOWA MASK AND CONSTRAINTS
# ============================================================
def create_iowa_mask(grid_lons, grid_lats):
    """Create boolean mask using geopandas for speed."""
    import geopandas as gpd
    from shapely.geometry import Point

    boundary_path = PROCESSED_DIR / "iowa_boundary.geojson"
    if not boundary_path.exists():
        print("  WARNING: Iowa boundary not found, no mask applied.")
        return np.ones((len(grid_lats), len(grid_lons)), dtype=bool)

    iowa = gpd.read_file(boundary_path)
    iowa_union = iowa.geometry.union_all()

    gx, gy = np.meshgrid(grid_lons, grid_lats)
    ny, nx = gx.shape
    mask = np.zeros((ny, nx), dtype=bool)

    # Use prepared geometry for speed
    from shapely.prepared import prep
    prepared = prep(iowa_union)

    print(f"  Creating Iowa mask ({nx}x{ny})...")
    for i in range(ny):
        for j in range(nx):
            if prepared.contains(Point(gx[i, j], gy[i, j])):
                mask[i, j] = True

    inside_count = np.sum(mask)
    print(f"  Iowa mask: {inside_count:,} of {mask.size:,} cells inside "
          f"({100 * inside_count / mask.size:.1f}%)")

    return mask


def apply_constraint_mask(suitability_grid, grid_lons, grid_lats):
    """Apply EPA Superfund buffer constraint (set to 0)."""
    epa_path = PROCESSED_DIR / "epa_constraints.geojson"
    if not epa_path.exists():
        return suitability_grid

    with open(epa_path) as f:
        epa = json.load(f)

    gx, gy = np.meshgrid(grid_lons, grid_lats)
    buffer_deg = 500 / 111000  # ~500m in degrees

    masked = suitability_grid.copy()
    for feat in epa["features"]:
        geom = feat.get("geometry")
        if not geom:
            continue
        coords = geom.get("coordinates", [])
        if geom["type"] == "Point":
            cx, cy = coords[0], coords[1]
            dist = np.sqrt((gx - cx)**2 + (gy - cy)**2)
            masked[dist < buffer_deg] = 0.0

    zeroed = np.sum(suitability_grid > 0) - np.sum(masked > 0)
    if zeroed > 0:
        print(f"  EPA constraint mask: zeroed {zeroed} cells")

    return masked


# ============================================================
# 6. RENDER PNG OVERLAYS
# ============================================================
def render_suitability_png(suitability, mask, output_path, width=2048):
    """Render suitability grid as a transparent PNG."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    colors_rgb = []
    for hex_color in SUITABILITY_COLORS:
        h = hex_color.lstrip("#")
        colors_rgb.append(tuple(int(h[i:i+2], 16) / 255.0 for i in (0, 2, 4)))

    cmap = LinearSegmentedColormap.from_list("suitability", colors_rgb, N=256)

    data = suitability * 100.0

    # Histogram equalization (p2-p98)
    valid = data[mask]
    p2_val, p98_val = 0, 100
    if len(valid) > 0:
        p2_val = np.percentile(valid, 2)
        p98_val = np.percentile(valid, 98)
        if p98_val > p2_val:
            data = (data - p2_val) / (p98_val - p2_val) * 100.0
            data = np.clip(data, 0, 100)

    ny, nx = data.shape
    aspect = ny / nx
    target_h = int(width * aspect)

    fig, ax = plt.subplots(1, 1, figsize=(width / 100, target_h / 100), dpi=100)
    ax.set_position([0, 0, 1, 1])
    ax.set_axis_off()

    rgba_data = cmap(data / 100.0)
    rgba_data[~mask, 3] = 0.0

    ax.imshow(rgba_data, origin="lower", aspect="auto", interpolation="bilinear")

    fig.savefig(output_path, dpi=100, transparent=True,
                bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    size_kb = output_path.stat().st_size / 1024
    print(f"  Saved {output_path.name} ({size_kb:.0f} KB, {width}x{target_h})")

    return p2_val, p98_val


def render_uncertainty_png(variance, mask, output_path, width=2048):
    """Render kriging variance as a blue-to-red uncertainty overlay."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap

    colors = [
        (0.1, 0.2, 0.6),   # dark blue (confident)
        (0.2, 0.5, 0.8),
        (0.4, 0.8, 0.4),   # green
        (1.0, 0.9, 0.2),   # yellow
        (1.0, 0.5, 0.1),
        (0.8, 0.1, 0.1),   # red (uncertain)
    ]
    cmap = LinearSegmentedColormap.from_list("uncertainty", colors, N=256)

    valid_var = variance[mask]
    if len(valid_var) > 0:
        vmin = np.percentile(valid_var, 2)
        vmax = np.percentile(valid_var, 98)
        if vmax > vmin:
            data = (variance - vmin) / (vmax - vmin)
            data = np.clip(data, 0, 1)
        else:
            data = np.zeros_like(variance)
    else:
        data = np.zeros_like(variance)

    ny, nx = data.shape
    aspect = ny / nx
    target_h = int(width * aspect)

    fig, ax = plt.subplots(1, 1, figsize=(width / 100, target_h / 100), dpi=100)
    ax.set_position([0, 0, 1, 1])
    ax.set_axis_off()

    rgba_data = cmap(data)
    rgba_data[~mask, 3] = 0.0

    ax.imshow(rgba_data, origin="lower", aspect="auto", interpolation="bilinear")

    fig.savefig(output_path, dpi=100, transparent=True,
                bbox_inches="tight", pad_inches=0)
    plt.close(fig)

    size_kb = output_path.stat().st_size / 1024
    print(f"  Saved {output_path.name} ({size_kb:.0f} KB, {width}x{target_h})")


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    t_start = time.time()

    print("=" * 60)
    print("V3 GEOSTATISTICAL MODEL — RF + Residual Kriging")
    print("=" * 60)
    print()

    # 1. Load wells
    print("Step 1: Loading V3-scored wells...")
    wells_gdf = load_wells_gdf()
    well_scores = wells_gdf["v3_score"].values
    well_completeness = wells_gdf["data_completeness"].values
    well_lons = wells_gdf.geometry.x.values
    well_lats = wells_gdf.geometry.y.values
    print(f"  Score range: [{well_scores.min():.3f}, {well_scores.max():.3f}]")

    # 2. Extract covariates at well locations
    print("\nStep 2: Extracting covariates at well locations...")
    X_wells, feature_names = extract_covariates_at_points(wells_gdf)

    # 3. Train Random Forest
    print("\nStep 3: Training Random Forest trend model...")
    rf, well_predictions = train_random_forest(
        X_wells, well_scores, feature_names, well_completeness
    )

    # 4. Compute residuals
    residuals = well_scores - well_predictions
    print(f"\n  Residual stats: mean={np.mean(residuals):.4f}, "
          f"std={np.std(residuals):.4f}")

    # 5. Build prediction grid
    print("\nStep 4: Building prediction grid...")
    grid_gdf, grid_lons, grid_lats = build_grid_points()

    # 6. Extract covariates on grid and predict with RF
    print("\nStep 5: Extracting covariates on prediction grid...")
    X_grid, _ = extract_covariates_at_points(grid_gdf)

    print(f"  Predicting RF at {len(X_grid):,} grid cells...")
    rf_pred_flat = rf.predict(X_grid)
    rf_grid = rf_pred_flat.reshape((len(grid_lats), len(grid_lons)))
    print(f"  RF prediction range: [{np.nanmin(rf_grid):.3f}, {np.nanmax(rf_grid):.3f}]")

    # 7. Krige residuals — used ONLY for the confidence/uncertainty layer.
    # The kriged residual correction is NOT added to the suitability surface
    # because kriging is a distance-based interpolator that creates circular
    # gradient bands around well clusters. The RF prediction already captures
    # spatial variation through geological covariates (landform regions, DTB),
    # which define variation along geological boundaries rather than circles.
    print("\nStep 6: Kriging residuals (for confidence layer only)...")
    _krige_grid, variance_grid = krige_residuals(
        well_lons, well_lats, residuals, grid_lons, grid_lats
    )

    # Suitability = RF prediction only (no kriged residual added)
    print("\nStep 7: Generating final suitability surface...")
    suitability = np.clip(rf_grid, 0, 1)

    # 9. Create Iowa mask
    print("  Creating Iowa boundary mask...")
    iowa_mask = create_iowa_mask(grid_lons, grid_lats)

    # 10. Apply constraint mask
    suitability = apply_constraint_mask(suitability, grid_lons, grid_lats)

    # Stats
    valid_suit = suitability[iowa_mask]
    print(f"\n  Final suitability stats (inside Iowa):")
    print(f"    Mean: {np.mean(valid_suit):.3f}")
    print(f"    Std:  {np.std(valid_suit):.3f}")
    print(f"    p2={np.percentile(valid_suit, 2):.3f}, "
          f"p50={np.percentile(valid_suit, 50):.3f}, "
          f"p98={np.percentile(valid_suit, 98):.3f}")

    # 11. Render PNGs
    print("\nStep 8: Rendering PNG overlays...")
    suit_path = TILES_V3 / "suitability_overlay_v3.png"
    uncert_path = TILES_V3 / "uncertainty_overlay_v3.png"

    p2, p98 = render_suitability_png(suitability, iowa_mask, suit_path)
    render_uncertainty_png(variance_grid, iowa_mask, uncert_path)

    # 12. Save bounds
    print("\nStep 9: Saving metadata...")
    bounds_json = {
        "bounds": [
            IOWA_BBOX["west"], IOWA_BBOX["south"],
            IOWA_BBOX["east"], IOWA_BBOX["north"],
        ],
        "width": 2048,
        "height": 0,
        "model": "v3_rf_kriging",
        "grid_resolution_deg": GRID_RES_DEG,
        "architecture": "Random Forest (geological covariates) + Kriging variance confidence",
        "n_wells": int(len(well_scores)),
        "rf_features": feature_names,
        "stats": {
            "p2": round(float(np.percentile(valid_suit, 2) * 100), 1),
            "p10": round(float(np.percentile(valid_suit, 10) * 100), 1),
            "p25": round(float(np.percentile(valid_suit, 25) * 100), 1),
            "p50": round(float(np.percentile(valid_suit, 50) * 100), 1),
            "p75": round(float(np.percentile(valid_suit, 75) * 100), 1),
            "p90": round(float(np.percentile(valid_suit, 90) * 100), 1),
            "p98": round(float(np.percentile(valid_suit, 98) * 100), 1),
            "mean": round(float(np.mean(valid_suit) * 100), 1),
            "std": round(float(np.std(valid_suit) * 100), 1),
        },
    }

    with open(TILES_V3 / "bounds_v3.json", "w") as f:
        json.dump(bounds_json, f, indent=2)
    print(f"  Saved bounds_v3.json")

    elapsed = time.time() - t_start
    print()
    print("=" * 60)
    print(f"DONE — V3 Geostatistical Model ({elapsed:.0f}s)")
    print("=" * 60)
    print(f"  Suitability: {suit_path}")
    print(f"  Uncertainty: {uncert_path}")
    print(f"  Metadata:    {TILES_V3 / 'bounds_v3.json'}")
    print()
    print("Next: python scripts_v3/22_export_web_assets_v3.py")
