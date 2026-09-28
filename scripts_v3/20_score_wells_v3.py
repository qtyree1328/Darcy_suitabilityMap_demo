"""
V3 Well Scoring — Percentile-Based Composite Productivity Score

Reads Iowa DNR Sourcewater wells and computes V3 composite productivity
scores using percentile normalization within the shallow well population.

V3 scoring differs from V2:
  - Percentile normalization instead of absolute thresholds for GPM, SPC, T
  - Explicit fixed weights from plan Section 3
  - Null fields get neutral scores (0.4-0.5) rather than 0
  - Data completeness index for each well
  - Wells filtered to those with at least AQUIFER + WL_DPTH_FT

Input:  data/processed/iowa_sourcewater_wells.geojson (from script 15)
Output: data/processed/iowa_sourcewater_wells_v3.geojson
"""
import json
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from utils_v3 import (
    PROCESSED_DIR, WELL_SCORE_WEIGHTS,
    AQUIFER_TYPE_SCORES, AQUIFER_BEDROCK_KEYWORDS,
    AQUIFER_UNCONSOLIDATED_KEYWORDS, ensure_dirs,
)

ensure_dirs()


def compute_percentile_scores(values, null_score=0.4):
    """
    Map non-null values to scores based on percentile rank.
    p90+ = 1.0, p75 = 0.8, p50 = 0.6, p25 = 0.3, <p10 = 0.1
    Null values get null_score.
    """
    valid = np.array([v for v in values if v is not None and v > 0])
    if len(valid) == 0:
        return [null_score] * len(values)

    p10 = np.percentile(valid, 10)
    p25 = np.percentile(valid, 25)
    p50 = np.percentile(valid, 50)
    p75 = np.percentile(valid, 75)
    p90 = np.percentile(valid, 90)

    scores = []
    for v in values:
        if v is None or v <= 0:
            scores.append(null_score)
        elif v >= p90:
            scores.append(1.0)
        elif v >= p75:
            scores.append(0.8)
        elif v >= p50:
            scores.append(0.6)
        elif v >= p25:
            scores.append(0.3)
        else:
            scores.append(0.1)
    return scores


def score_aquifer_type(aquifer_str):
    """Score aquifer type per V3 plan Section 3."""
    if not aquifer_str:
        return 0.4  # Unknown

    aq_lower = aquifer_str.lower().strip()

    # Check direct matches first
    for key, score in AQUIFER_TYPE_SCORES.items():
        if key in aq_lower:
            return score

    # Check keyword matches
    if any(kw in aq_lower for kw in AQUIFER_UNCONSOLIDATED_KEYWORDS):
        return 0.7  # Glacial Drift/Outwash default
    if any(kw in aq_lower for kw in AQUIFER_BEDROCK_KEYWORDS):
        return 0.2  # Other Bedrock
    return 0.4  # Unknown


def score_aquifer_thickness(thickness):
    """Score aquifer thickness per V3 plan: >=50=1.0, 30-50=0.8, 15-30=0.5, <15=0.2."""
    if thickness is None or thickness <= 0:
        return 0.4  # Null
    if thickness >= 50:
        return 1.0
    if thickness >= 30:
        return 0.8
    if thickness >= 15:
        return 0.5
    return 0.2


def score_well_depth(depth):
    """Score well depth per V3 plan: 50-150=1.0, 20-50=0.6, 150-250=0.4, <20=0.2, >250=0.1."""
    if depth is None or depth <= 0:
        return 0.4  # Null
    if 50 <= depth <= 150:
        return 1.0
    if 20 <= depth < 50:
        return 0.6
    if 150 < depth <= 250:
        return 0.4
    if depth < 20:
        return 0.2
    return 0.1  # >250


def score_static_water_level(swl):
    """Score SWL per V3 plan: <20=1.0, 20-50=0.8, 50-100=0.5, >100=0.2."""
    if swl is None or swl <= 0:
        return 0.5  # Null
    if swl < 20:
        return 1.0
    if swl <= 50:
        return 0.8
    if swl <= 100:
        return 0.5
    return 0.2


def score_confinement(conf):
    """Score confined/unconfined: U=1.0, C=0.6, null=0.7."""
    if not conf:
        return 0.7
    if conf.upper() == "U":
        return 1.0
    if conf.upper() == "C":
        return 0.6
    return 0.7


def compute_data_completeness(props):
    """Count non-null fields among key productivity fields (0-5)."""
    fields = ["PMPTST_GPM", "SPC_GPM_FT", "T_FT2_D", "AQ_THK_FT", "SWL_FT"]
    count = 0
    for f in fields:
        val = props.get(f)
        if val is not None and val > 0:
            count += 1
    return count


if __name__ == "__main__":
    input_path = PROCESSED_DIR / "iowa_sourcewater_wells.geojson"
    output_path = PROCESSED_DIR / "iowa_sourcewater_wells_v3.geojson"

    if not input_path.exists():
        print(f"ERROR: {input_path} not found.")
        print("  Run: python scripts/15_fetch_sourcewater_wells.py")
        sys.exit(1)

    print("=" * 60)
    print("V3 WELL SCORING — Percentile-Based Composite Productivity")
    print("=" * 60)
    print()

    # Load wells
    with open(input_path) as f:
        data = json.load(f)
    features = data["features"]
    print(f"Loaded {len(features)} wells from {input_path.name}")

    # Filter: need at least valid coordinates
    valid_features = []
    for feat in features:
        geom = feat.get("geometry")
        if not geom or not geom.get("coordinates"):
            continue
        props = feat["properties"]
        # Keep wells with at least AQUIFER or WL_DPTH_FT populated
        has_aquifer = bool(props.get("AQUIFER"))
        has_depth = (props.get("WL_DPTH_FT") or 0) > 0
        has_productivity = (
            (props.get("PMPTST_GPM") or 0) > 0
            or (props.get("SPC_GPM_FT") or 0) > 0
            or (props.get("T_FT2_D") or 0) > 0
        )
        if has_aquifer or has_depth or has_productivity:
            valid_features.append(feat)

    print(f"Valid wells (with AQUIFER, depth, or productivity): {len(valid_features)}")

    # Step 1: Collect field values for percentile computation
    gpm_vals = [f["properties"].get("PMPTST_GPM") for f in valid_features]
    spc_vals = [f["properties"].get("SPC_GPM_FT") for f in valid_features]
    t_vals = [f["properties"].get("T_FT2_D") for f in valid_features]

    # Use log-scale for transmissivity
    t_log_vals = []
    for v in t_vals:
        if v is not None and v > 0:
            t_log_vals.append(np.log10(v))
        else:
            t_log_vals.append(None)

    # Step 2: Compute percentile scores
    print("\nComputing percentile scores...")
    gpm_scores = compute_percentile_scores(gpm_vals, null_score=0.4)
    spc_scores = compute_percentile_scores(spc_vals, null_score=0.4)
    t_scores = compute_percentile_scores(t_log_vals, null_score=0.4)

    # Print percentile thresholds
    valid_gpm = [v for v in gpm_vals if v and v > 0]
    valid_spc = [v for v in spc_vals if v and v > 0]
    valid_t = [v for v in t_vals if v and v > 0]
    if valid_gpm:
        print(f"  GPM percentiles: p10={np.percentile(valid_gpm, 10):.1f}, "
              f"p25={np.percentile(valid_gpm, 25):.1f}, p50={np.percentile(valid_gpm, 50):.1f}, "
              f"p75={np.percentile(valid_gpm, 75):.1f}, p90={np.percentile(valid_gpm, 90):.1f}")
    if valid_spc:
        print(f"  SPC percentiles: p10={np.percentile(valid_spc, 10):.2f}, "
              f"p25={np.percentile(valid_spc, 25):.2f}, p50={np.percentile(valid_spc, 50):.2f}, "
              f"p75={np.percentile(valid_spc, 75):.2f}, p90={np.percentile(valid_spc, 90):.2f}")
    if valid_t:
        print(f"  T percentiles:   p10={np.percentile(valid_t, 10):.0f}, "
              f"p25={np.percentile(valid_t, 25):.0f}, p50={np.percentile(valid_t, 50):.0f}, "
              f"p75={np.percentile(valid_t, 75):.0f}, p90={np.percentile(valid_t, 90):.0f}")

    # Step 3: Compute composite V3 score for each well
    print("\nComputing V3 composite scores...")
    w = WELL_SCORE_WEIGHTS

    for i, feat in enumerate(valid_features):
        props = feat["properties"]

        # Individual field scores
        s_gpm = gpm_scores[i]
        s_spc = spc_scores[i]
        s_t = t_scores[i]
        s_aq = score_aquifer_type(props.get("AQUIFER"))
        s_thk = score_aquifer_thickness(props.get("AQ_THK_FT"))
        s_depth = score_well_depth(props.get("WL_DPTH_FT"))
        s_swl = score_static_water_level(props.get("SWL_FT"))
        s_conf = score_confinement(props.get("CONF_UNCON"))

        # Composite score
        v3_score = (
            w["PMPTST_GPM"] * s_gpm
            + w["SPC_GPM_FT"] * s_spc
            + w["T_FT2_D"] * s_t
            + w["AQUIFER"] * s_aq
            + w["AQ_THK_FT"] * s_thk
            + w["WL_DPTH_FT"] * s_depth
            + w["SWL_FT"] * s_swl
            + w["CONF_UNCON"] * s_conf
        )
        v3_score = round(min(max(v3_score, 0.0), 1.0), 4)

        # Data completeness
        completeness = compute_data_completeness(props)

        # Store scores
        props["v3_score"] = v3_score
        props["data_completeness"] = completeness
        # Store component scores for transparency
        props["v3_gpm_score"] = round(s_gpm, 3)
        props["v3_spc_score"] = round(s_spc, 3)
        props["v3_t_score"] = round(s_t, 3)
        props["v3_aquifer_score"] = round(s_aq, 3)

    # Statistics
    scores = [f["properties"]["v3_score"] for f in valid_features]
    completeness_vals = [f["properties"]["data_completeness"] for f in valid_features]

    print(f"\n  V3 Score Distribution:")
    bins = [(0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)]
    for lo, hi in bins:
        count = sum(1 for s in scores if lo <= s < hi)
        print(f"    {lo:.1f}-{hi:.1f}: {count:>5d} wells")
    print(f"  Mean: {np.mean(scores):.3f}")
    print(f"  Std:  {np.std(scores):.3f}")
    print(f"  Min:  {min(scores):.3f}  Max: {max(scores):.3f}")

    print(f"\n  Data Completeness (0-5 productivity fields):")
    for c in range(6):
        count = sum(1 for v in completeness_vals if v == c)
        print(f"    {c} fields: {count:>5d} wells")

    # High-confidence wells (>= 1 productivity field)
    high_conf = sum(1 for v in completeness_vals if v >= 1)
    print(f"\n  High-confidence wells (>=1 productivity field): {high_conf}")

    # Save
    geojson = {"type": "FeatureCollection", "features": valid_features}
    with open(output_path, "w") as fp:
        json.dump(geojson, fp)

    size_kb = output_path.stat().st_size / 1024
    print(f"\nSaved {output_path} ({size_kb:.0f} KB)")
    print(f"  {len(valid_features)} wells with v3_score attribute")
