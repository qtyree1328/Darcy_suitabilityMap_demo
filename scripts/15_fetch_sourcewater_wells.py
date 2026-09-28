"""
Fetch Iowa DNR Sourcewater public wells with hydrogeologic productivity data.
Source: https://programs.iowadnr.gov/geospatial/rest/services/waterquality/Sourcewater/MapServer/0

This layer contains 7,299 public water supply wells with actual pump test data,
specific capacity, transmissivity, hydraulic conductivity, aquifer thickness,
and static water levels — direct indicators of shallow aquifer productivity.

Each well is scored for Darcy-style shallow geothermal suitability based on:
  - Pump test yield (GPM) and/or specific capacity (GPM/ft)
  - Aquifer type preference (unconsolidated > bedrock)
  - Well depth preference (shallow < 150 ft preferred)

Output: data/processed/iowa_sourcewater_wells.geojson
"""
import json
import sys
import requests
from pathlib import Path

PROCESSED = Path("data/processed")
PROCESSED.mkdir(parents=True, exist_ok=True)

BASE_URL = (
    "https://programs.iowadnr.gov/geospatial/rest/services/"
    "waterquality/Sourcewater/MapServer/0/query"
)
PAGE_SIZE = 1000

# Aquifer types that indicate shallow unconsolidated / Darcy-favorable targets
SHALLOW_AQUIFER_KEYWORDS = [
    "sand", "gravel", "alluvial", "glacial", "drift",
    "buried channel", "surficial", "quaternary",
]

# Aquifer types that are deep bedrock — not Darcy targets
BEDROCK_AQUIFER_KEYWORDS = [
    "cambrian", "ordovician", "silurian", "devonian",
    "mississippian", "pennsylvanian", "cretaceous",
    "precambrian", "jordan", "st. peter", "galena",
    "prairie du chien", "maquoketa",
]


def score_well_productivity(props):
    """
    Score a well for Darcy-style shallow geothermal suitability using
    ALL available hydrogeologic data.

    Scoring components (weighted average of available signals):
      1. Pump test yield (GPM) or specific capacity (GPM/ft) — direct productivity
      2. Transmissivity (ft²/day) — aquifer flow capacity
      3. Hydraulic conductivity (ft/day) — material permeability
      4. Static water level (ft) — shallow water table preferred
      5. Aquifer thickness (ft) — thicker = more thermal mass
      6. Aquifer type — unconsolidated strongly preferred
      7. Confined/unconfined — unconfined preferred
      8. Well depth — shallow (≤150 ft) preferred

    Returns a score from 0.0 to 1.0.
    """
    gpm = props.get("PMPTST_GPM") or 0
    spc = props.get("SPC_GPM_FT") or 0
    depth = props.get("WL_DPTH_FT") or 0
    aquifer = (props.get("AQUIFER") or "").lower()
    swl = props.get("SWL_FT") or 0
    thickness = props.get("AQ_THK_FT") or 0
    transmissivity = props.get("T_FT2_D") or 0
    conductivity = props.get("K_FT_D") or 0
    confined = (props.get("CONF_UNCON") or "").upper()

    # Collect scored signals with weights
    signals = []  # list of (score, weight) tuples

    # --- Signal 1: Pump test yield / specific capacity (weight: 3) ---
    if gpm > 0:
        if gpm >= 100:    s = 1.0
        elif gpm >= 50:   s = 0.85
        elif gpm >= 20:   s = 0.7
        elif gpm >= 5:    s = 0.4
        else:             s = 0.15
        signals.append((s, 3.0))
    elif spc > 0:
        if spc >= 10:     s = 1.0
        elif spc >= 5:    s = 0.85
        elif spc >= 1:    s = 0.7
        elif spc >= 0.2:  s = 0.4
        else:             s = 0.15
        signals.append((s, 3.0))

    # --- Signal 2: Transmissivity (weight: 2.5) ---
    # T > 1000 ft²/day = excellent, T > 100 = good, T < 10 = poor
    if transmissivity > 0:
        if transmissivity >= 2000:   s = 1.0
        elif transmissivity >= 1000: s = 0.85
        elif transmissivity >= 500:  s = 0.7
        elif transmissivity >= 100:  s = 0.5
        elif transmissivity >= 10:   s = 0.3
        else:                        s = 0.1
        signals.append((s, 2.5))

    # --- Signal 3: Hydraulic conductivity (weight: 2) ---
    # K > 50 ft/day = gravel/coarse sand, K > 10 = sand, K < 1 = clay/silt
    if conductivity > 0:
        if conductivity >= 100:   s = 1.0
        elif conductivity >= 50:  s = 0.9
        elif conductivity >= 10:  s = 0.75
        elif conductivity >= 1:   s = 0.5
        elif conductivity >= 0.1: s = 0.25
        else:                     s = 0.1
        signals.append((s, 2.0))

    # --- Signal 4: Static water level (weight: 1.5) ---
    # Shallow water table = better for heat exchange
    if swl > 0:
        if swl <= 20:     s = 1.0   # very shallow — excellent
        elif swl <= 50:   s = 0.8
        elif swl <= 100:  s = 0.6
        elif swl <= 200:  s = 0.4
        else:             s = 0.2
        signals.append((s, 1.5))

    # --- Signal 5: Aquifer thickness (weight: 1.5) ---
    # Thicker aquifer = more thermal mass and groundwater volume
    if thickness > 0:
        if thickness >= 100:  s = 1.0
        elif thickness >= 50: s = 0.8
        elif thickness >= 20: s = 0.6
        elif thickness >= 10: s = 0.4
        else:                 s = 0.2
        signals.append((s, 1.5))

    # --- Signal 6: Aquifer type (weight: 2) ---
    if aquifer:
        if any(kw in aquifer for kw in SHALLOW_AQUIFER_KEYWORDS):
            signals.append((0.9, 2.0))
        elif any(kw in aquifer for kw in BEDROCK_AQUIFER_KEYWORDS):
            signals.append((0.3, 2.0))
        else:
            signals.append((0.5, 1.0))

    # --- Signal 7: Confined vs unconfined (weight: 1) ---
    if confined == "U":
        signals.append((0.85, 1.0))   # unconfined — accessible
    elif confined == "C":
        signals.append((0.4, 1.0))    # confined — less accessible

    # --- Signal 8: Well depth (weight: 1.5) ---
    if depth > 0:
        if depth <= 100:    s = 1.0
        elif depth <= 150:  s = 0.85
        elif depth <= 300:  s = 0.6
        elif depth <= 500:  s = 0.35
        else:               s = 0.15
        signals.append((s, 1.5))

    # Weighted average of all available signals
    if signals:
        total_weight = sum(w for _, w in signals)
        score = sum(s * w for s, w in signals) / total_weight
    else:
        score = 0.5  # no data at all — neutral

    return round(min(max(score, 0.0), 1.0), 3)


def fetch_all_wells():
    """Paginate through all wells from the MapServer."""
    all_features = []
    offset = 0

    while True:
        params = {
            "where": "1=1",
            "outFields": (
                "PMPTST_GPM,SPC_GPM_FT,AQUIFER,WL_DPTH_FT,"
                "SWL_FT,AQ_THK_FT,T_FT2_D,K_FT_D,CONF_UNCON,"
                "NAME_OWNER,PWSID"
            ),
            "resultRecordCount": PAGE_SIZE,
            "resultOffset": offset,
            "outSR": "4326",
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


if __name__ == "__main__":
    out_path = PROCESSED / "iowa_sourcewater_wells.geojson"

    if out_path.exists():
        size_kb = out_path.stat().st_size / 1024
        print(f"Sourcewater wells already exist: {out_path} ({size_kb:.0f} KB)")
        print("Delete to re-download.")

        # Show stats
        with open(out_path) as f:
            data = json.load(f)
        features = data["features"]
        scores = [f["properties"].get("productivity_score", 0) for f in features]
        print(f"  {len(features)} wells")
        if scores:
            print(f"  Score range: {min(scores):.2f} – {max(scores):.2f}")
            print(f"  Mean score: {sum(scores)/len(scores):.2f}")
        sys.exit(0)

    print("=" * 60)
    print("IOWA DNR SOURCEWATER WELLS — Aquifer Productivity Data")
    print("=" * 60)
    print()

    print("Fetching public water supply wells...")
    features = fetch_all_wells()
    print(f"\nTotal wells: {len(features)}")

    if not features:
        print("ERROR: No features returned.")
        sys.exit(1)

    # Score each well
    print("\nScoring wells for Darcy-style productivity...")
    has_pump_test = 0
    has_spc = 0
    shallow_count = 0
    unconsolidated_count = 0

    for f in features:
        p = f["properties"]
        score = score_well_productivity(p)
        p["productivity_score"] = score

        if (p.get("PMPTST_GPM") or 0) > 0:
            has_pump_test += 1
        if (p.get("SPC_GPM_FT") or 0) > 0:
            has_spc += 1
        depth = p.get("WL_DPTH_FT") or 0
        if 0 < depth <= 150:
            shallow_count += 1
        aquifer = (p.get("AQUIFER") or "").lower()
        if any(kw in aquifer for kw in SHALLOW_AQUIFER_KEYWORDS):
            unconsolidated_count += 1

    print(f"  Wells with pump test: {has_pump_test}")
    print(f"  Wells with specific capacity: {has_spc}")
    print(f"  Wells with transmissivity: {sum(1 for f in features if (f['properties'].get('T_FT2_D') or 0) > 0)}")
    print(f"  Wells with hydraulic conductivity: {sum(1 for f in features if (f['properties'].get('K_FT_D') or 0) > 0)}")
    print(f"  Wells with static water level: {sum(1 for f in features if (f['properties'].get('SWL_FT') or 0) > 0)}")
    print(f"  Wells with aquifer thickness: {sum(1 for f in features if (f['properties'].get('AQ_THK_FT') or 0) > 0)}")
    print(f"  Shallow wells (≤150 ft): {shallow_count}")
    print(f"  Unconsolidated aquifer wells: {unconsolidated_count}")

    # Score distribution
    scores = [f["properties"]["productivity_score"] for f in features]
    print(f"\n  Score distribution:")
    bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in bins:
        count = sum(1 for s in scores if lo <= s < hi)
        print(f"    {lo:.1f}–{hi:.1f}: {count:>5d} wells")
    print(f"  Mean: {sum(scores)/len(scores):.3f}")

    # Aquifer type summary
    print(f"\n  Top aquifer types:")
    from collections import Counter
    aq_counts = Counter()
    aq_scores = {}
    for f in features:
        p = f["properties"]
        aq = p.get("AQUIFER") or "Unknown"
        aq_counts[aq] += 1
        if aq not in aq_scores:
            aq_scores[aq] = []
        aq_scores[aq].append(p["productivity_score"])

    for aq, count in aq_counts.most_common(15):
        avg = sum(aq_scores[aq]) / len(aq_scores[aq])
        print(f"    {aq:>35s}: {count:>5d} wells  avg_score={avg:.2f}")

    # Save wells
    geojson = {"type": "FeatureCollection", "features": features}
    with open(out_path, "w") as fp:
        json.dump(geojson, fp)

    size_kb = out_path.stat().st_size / 1024
    print(f"\nSaved {out_path} ({size_kb:.0f} KB)")
    print(f"  {len(features)} wells with productivity_score attribute")

    # ================================================================
    # SPATIAL PROPAGATION: Compute regional productivity averages
    # ================================================================
    # Join wells to landform regions and compute the average productivity
    # score per region. This allows the GEE model to propagate ground-truth
    # well data to areas without wells but with similar geology.
    print("\n" + "=" * 60)
    print("SPATIAL PROPAGATION — Regional Productivity Averages")
    print("=" * 60)

    landform_path = PROCESSED / "iowa_landform_regions.geojson"
    if not landform_path.exists():
        print("  WARNING: iowa_landform_regions.geojson not found.")
        print("  Run: python scripts/10_fetch_surficial_geology.py (or fetch landforms)")
        sys.exit(0)

    try:
        from shapely.geometry import shape, Point

        with open(landform_path) as f:
            landform_data = json.load(f)

        # Build landform region polygons
        regions = []
        for lf in landform_data["features"]:
            name = lf["properties"].get("LANDFORM_R", "Unknown")
            geom = shape(lf["geometry"])
            regions.append((name, geom))

        # Assign each well to a landform region
        print(f"  Assigning {len(features)} wells to {len(regions)} landform regions...")
        region_scores = {}  # region_name -> list of productivity_scores
        assigned = 0

        for feat in features:
            coords = feat["geometry"]["coordinates"]
            pt = Point(coords[0], coords[1])
            ps = feat["properties"]["productivity_score"]

            for name, geom in regions:
                if geom.contains(pt):
                    if name not in region_scores:
                        region_scores[name] = []
                    region_scores[name].append(ps)
                    assigned += 1
                    break

        print(f"  Assigned {assigned} of {len(features)} wells to regions")

        # Compute regional averages and update landform GeoJSON
        print(f"\n  Regional productivity averages (from ground-truth wells):")
        print(f"  {'Region':>40s}  {'Wells':>6s}  {'Avg':>6s}  {'Old':>6s}")
        print("  " + "-" * 65)

        for lf in landform_data["features"]:
            name = lf["properties"].get("LANDFORM_R", "Unknown")
            old_score = lf["properties"].get("landform_hydro_score", 0.5)
            if name in region_scores and len(region_scores[name]) >= 3:
                avg = sum(region_scores[name]) / len(region_scores[name])
                # Use well-derived average — this is ground truth
                lf["properties"]["regional_productivity"] = round(avg, 3)
                lf["properties"]["regional_well_count"] = len(region_scores[name])
                print(f"  {name:>40s}  {len(region_scores[name]):>6d}  {avg:>6.3f}  {old_score:>6.2f}")
            else:
                # Too few wells — fall back to landform_hydro_score
                lf["properties"]["regional_productivity"] = old_score
                lf["properties"]["regional_well_count"] = len(region_scores.get(name, []))
                n = len(region_scores.get(name, []))
                print(f"  {name:>40s}  {n:>6d}  (fallback to {old_score:.2f})")

        # Save updated landform regions with well-derived averages
        with open(landform_path, "w") as f:
            json.dump(landform_data, f)
        print(f"\n  Updated {landform_path} with regional_productivity from well data")

    except ImportError:
        print("  WARNING: shapely not installed, skipping spatial propagation.")
        print("  pip install shapely")
    except Exception as e:
        print(f"  ERROR in spatial propagation: {e}")
        import traceback
        traceback.print_exc()
