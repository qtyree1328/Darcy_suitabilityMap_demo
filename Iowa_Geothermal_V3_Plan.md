# Iowa Geothermal Suitability Model V3

## Well-Anchored Geostatistical Architecture

*Tyree Spatial | Darcy Solutions Alignment | March 2026*

This document defines the data architecture, scoring methodology, and implementation plan for V3 of the Iowa geothermal suitability model. V3 replaces the V2 weighted linear sum with a geostatistical interpolation model where well observations are the ground truth and geological layers guide spatial extrapolation.

# 1. Architectural Shift from V2

V2 used a flat weighted sum of five independently scored raster layers, treating every pixel as an isolated assessment. Each layer received a fixed weight regardless of data quality or spatial context, and no single layer directly answered the core siting question: is there a productive shallow aquifer here?

The fundamental insight driving V3: well data is the only dataset that integrates depth, lithology, saturation, and yield at a single point. Every other layer provides partial, indirect evidence. Surficial geology tells you what material is at the surface but not whether it’s saturated or productive. Depth to bedrock tells you how much unconsolidated material exists but not whether any of it is permeable. Water table elevation confirms saturation but not productivity. Only a well with pump test data gives you the full picture.

**V3 core principle:** Wells are the ground truth. Geological and geomorphological layers are covariates that guide interpolation between wells, defining zones of expected similarity and providing continuous predictors for the trend model. The model asks: given what nearby wells in similar geological settings tell us, what is the predicted aquifer suitability at this unsampled location?

# 2. Available Well Data Fields

The Iowa DNR Sourcewater wells dataset contains the following fields relevant to suitability modeling. Each field’s role in the model is classified as: response variable (feeds into the per-well productivity score), covariate (used in the spatial model), constraint (hard filter), or metadata.

|            |                                            |                |                                                                                                                                                                                                                                                                                              |                                         |
|------------|--------------------------------------------|----------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------|
| **Field**  | **Description**                            | **Example**    | **Significance for Suitability Model**                                                                                                                                                                                                                                                       | **Role in Model**                       |
| PMPTST_GPM | Pump test flow rate (GPM)                  | 60.0           | Direct measure of how much water the well produces. Primary productivity indicator. Wells with high GPM in the target depth range are the strongest positive signal for Darcy suitability.                                                                                                   | Response variable (productivity)        |
| SPC_GPM_FT | Specific capacity (GPM per ft of drawdown) | 20.0           | Integrates yield and drawdown into a single efficiency metric. A well producing 60 GPM with 3 ft of drawdown (SPC=20) is far more favorable than one producing 60 GPM with 30 ft of drawdown (SPC=2). Better discriminator than GPM alone.                                                   | Response variable (productivity)        |
| T_FT2_D    | Transmissivity (ft²/day)                   | 4000.0         | Aquifer-level property: the rate at which water moves through the full saturated thickness. High transmissivity = aquifer can sustain extraction without excessive drawdown. Directly relevant to Darcy system thermal exchange capacity.                                                    | Response variable (aquifer quality)     |
| K_FT_D     | Hydraulic conductivity (ft/day)            | 181.82         | Per-unit-thickness permeability. K = T / aquifer thickness. High K means the material itself is highly permeable (clean sand/gravel vs. silty clay). Can be derived from T and AQ_THK_FT but useful as independent check.                                                                    | Response variable (material quality)    |
| AQUIFER    | Aquifer name/type                          | "Alluvial"     | Categorical classification of the aquifer the well taps. Alluvial and Buried Sand & Gravel are the primary targets for shallow geothermal. This field defines interpolation zones — wells in the same aquifer type can inform each other spatially.                                          | Categorical covariate + zone definition |
| AQ_THK_FT  | Aquifer thickness (ft)                     | 22.0           | Thickness of the productive zone. Thicker aquifers have more thermal mass and sustain longer extraction. A 50-ft alluvial aquifer is substantially better than a 10-ft lens. Also needed to derive K from T.                                                                                 | Response variable (capacity)            |
| WL_DPTH_FT | Total well depth (ft)                      | 105.0          | Hard filter: wells deeper than 150 ft are outside the Darcy U-line target window. Wells shallower than 50 ft may have insufficient aquifer thickness. Depth also indicates which aquifer system the well accesses.                                                                           | Hard constraint + covariate             |
| SWL_FT     | Static water level (ft below surface)      | 38.0           | Depth to the top of the saturated zone at rest. Shallow SWL means the water table is high and the unconsolidated zone is largely saturated — critical for confirming that mapped surficial deposits are wet, not dry. Combined with well depth, it tells you the saturated column thickness. | Response variable (saturation)          |
| CONF_UNCON | Confined vs. unconfined                    | "U" or "C"     | Unconfined aquifers are directly recharged from the surface and typically shallower — more aligned with the Darcy target. Confined aquifers have a clay/till cap, which affects both access and thermal behavior. Unconfined is generally preferred for shallow geothermal.                  | Binary covariate                        |
| NAME_OWNER | Well owner name                            | "City of Ames" | Identifies the entity. Municipal wells tend to have better pump test data and higher production rates. Useful for data quality assessment and for identifying high-capacity systems.                                                                                                         | Metadata / data quality flag            |
| PWSID      | Public water system ID                     | "IA1977011"    | Links to Iowa DNR SDWIS for regulatory data, source water assessments, and capture zone delineations. Enables cross-referencing with the Source Water Protection database for additional context.                                                                                            | Join key to SDWIS                       |

# 3. Well Productivity Scoring

Each well receives a composite productivity score (0–1) computed as a weighted sum of its field-level scores. The weights are tiered: the three direct productivity measures (GPM, specific capacity, transmissivity) carry 60% of the total weight. Aquifer type carries 15% as both a score component and a zone-defining variable. Physical characteristics (thickness, depth, water level, confinement) carry the remaining 25%.

Wells with null values in scored fields receive neutral scores (0.4–0.5) for those fields rather than being excluded, preserving spatial coverage while acknowledging uncertainty.

|            |                       |                                                                                                                                                            |            |                  |                                                                                                                                                            |
|------------|-----------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|------------|------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Field**  | **Attribute**         | **Scoring Rule**                                                                                                                                           | **Weight** | **Tier**         | **Rationale**                                                                                                                                              |
| PMPTST_GPM | Pump test GPM         | Percentile normalization within shallow (<150 ft) wells. p90+ = 1.0, p75 = 0.8, p50 = 0.6, p25 = 0.3, <p10 = 0.1. Null = 0.4 (neutral).                  | **0.25**   | Primary          | Direct productivity measure. Percentile normalization avoids absolute thresholds that may not reflect Iowa-specific distributions.                         |
| SPC_GPM_FT | Specific capacity     | Percentile normalization. p90+ = 1.0, p75 = 0.8, p50 = 0.6, p25 = 0.3, <p10 = 0.1. Null = 0.4.                                                            | **0.20**   | Primary          | Better than GPM alone because it accounts for drawdown. A high-GPM well with massive drawdown is less sustainable.                                         |
| T_FT2_D    | Transmissivity        | Log-scale percentile normalization. p90+ = 1.0, p50 = 0.6, <p10 = 0.1. Null = 0.4.                                                                        | **0.15**   | Primary          | Aquifer-level property. Directly relates to thermal exchange sustainability. Log-scale because transmissivity spans orders of magnitude.                   |
| AQUIFER    | Aquifer type          | Alluvial = 1.0, Buried Sand & Gravel = 0.9, Glacial Drift/Outwash = 0.7, Buried Channel = 0.8, Dakota Sandstone = 0.4, Other Bedrock = 0.2, Unknown = 0.4. | **0.15**   | Primary + Zone   | Defines both a score component and interpolation zone boundaries. Alluvial/sand-gravel are the target formations.                                          |
| AQ_THK_FT  | Aquifer thickness     | ≥50 ft = 1.0, 30–50 ft = 0.8, 15–30 ft = 0.5, <15 ft = 0.2. Null = 0.4.                                                                                   | **0.10**   | Secondary        | Thicker aquifers have more thermal mass and sustain longer extraction cycles.                                                                              |
| WL_DPTH_FT | Well depth            | 50–150 ft = 1.0, 20–50 ft = 0.6, 150–250 ft = 0.4, <20 ft = 0.2, >250 ft = 0.1.                                                                          | **0.05**   | Constraint       | Hard filter for target window. Low weight because depth alone doesn’t indicate productivity — it just defines the drilling envelope.                       |
| SWL_FT     | Static water level    | <20 ft = 1.0, 20–50 ft = 0.8, 50–100 ft = 0.5, >100 ft = 0.2. Null = 0.5.                                                                                | **0.05**   | Secondary        | Shallow water table confirms saturation of the unconsolidated zone. Low weight because it’s partially redundant with transmissivity and specific capacity. |
| CONF_UNCON | Confined / unconfined | Unconfined (U) = 1.0, Confined (C) = 0.6. Null = 0.7.                                                                                                      | **0.05**   | Binary covariate | Unconfined preferred for shallow geothermal access and recharge. Confined aquifers are usable but less ideal for the Darcy system.                         |

**Composite Well Score = 0.25×GPM + 0.20×SPC + 0.15×T + 0.15×AquiferType + 0.10×AqThickness + 0.05×WellDepth + 0.05×SWL + 0.05×Confinement**

# 4. Derived Fields

Several useful quantities can be computed from the raw well fields to improve model performance or validate data quality.

|                         |                                                                                    |                                                                                                                                                                                                             |                                                                                                                                  |
|-------------------------|------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------|
| **Derived Field**       | **Calculation**                                                                    | **Purpose**                                                                                                                                                                                                 | **Model Role**                                                                                                                   |
| Saturated thickness     | WL_DPTH_FT − SWL_FT (or AQ_THK_FT if available)                                    | The actual productive column of saturated aquifer material. More meaningful than well depth alone. If AQ_THK_FT is populated, use it directly; otherwise estimate from well depth minus static water level. | Strong secondary predictor. Can substitute for AQ_THK_FT when that field is null.                                                |
| K from T                | T_FT2_D / AQ_THK_FT                                                                | Hydraulic conductivity derived from transmissivity and aquifer thickness. Cross-check against K_FT_D if both are populated. Inconsistencies flag data quality issues.                                       | Validation. Use K_FT_D when available; derive when only T and thickness are present.                                             |
| Depth suitability flag  | WL_DPTH_FT between 50–150 ft                                                       | Binary: is this well in the Darcy target drilling window? Wells outside this range are downweighted but not excluded (they still inform aquifer properties in their area).                                  | Hard filter applied before scoring. Wells outside range get WL_DPTH_FT score of 0.1–0.4 but remain in the interpolation dataset. |
| Data completeness index | Count of non-null fields among: PMPTST_GPM, SPC_GPM_FT, T_FT2_D, AQ_THK_FT, SWL_FT | Wells with 4–5 fields populated are high-confidence observations. Wells with 0–1 fields contribute less to the model (higher kriging uncertainty at those locations).                                       | Optional: downweight low-completeness wells in the variogram fitting or use as a reliability layer in the final output.          |

# 5. Geospatial Covariates

These layers guide interpolation between wells. They do not independently score suitability — they define zones of expected geological similarity and provide continuous/categorical covariates for the trend model component. Every dataset listed below is publicly accessible.

|                                         |                                                                                                                   |                                                           |                                                                                          |                              |                                                                                                                                                |
|-----------------------------------------|-------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------|------------------------------------------------------------------------------------------|------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| **Dataset**                             | **Content**                                                                                                       | **Provider**                                              | **Access URL**                                                                           | **Format**                   | **Role in Model**                                                                                                                              |
| ISU GLSI Surficial Geology (10m raster) | Parent material at surface to ~2m: glacial till, loess, alluvium, outwash, residuum, bedrock. Statewide coverage. | ISU Geospatial Lab for Soil Informatics (gSSURGO-derived) | agron.iastate.edu/glsi/physiography-gis-data/surficial-geology-of-iowa-gis/              | Raster download              | Primary interpolation zone layer. Alluvium/outwash = high aquifer potential. Till/loess = low. Replaces V2 partial-coverage surficial geology. |
| Bedrock Surface Topography              | Bedrock elevation (ft AMSL). Derive depth-to-bedrock = DEM minus bedrock elevation.                               | IGS / Iowa DNR MapServer                                  | programs.iowadnr.gov/geospatial/rest/services/Geology/BedrockGeology/MapServer (Layer 7) | ArcGIS MapServer query       | Continuous covariate. <50 ft = hard constraint (insufficient unconsolidated thickness). Also defines maximum possible aquifer column.         |
| Landform Regions of Iowa                | 10 named regions based on geomorphology and glacial history                                                       | IGS / Iowa DNR                                            | geodata.iowa.gov (Landforms of Iowa)                                                     | Shapefile download           | Coarse zonal prior. Alluvial plains, Des Moines Lobe = favorable. Secondary to surficial geology.                                              |
| ISU Physiographic Regions (2025)        | Detailed subregions integrating LiDAR terrain, soils, geomorphology. Higher resolution than Landform Regions.     | ISU Geospatial Lab for Soil Informatics                   | agron.iastate.edu/glsi/gis-data/physiographic-regions-of-iowa/                           | Shapefile from ISU DataShare | Potential replacement for Landform Regions. More granular zone definitions from LiDAR-derived terrain analysis.                                |
| Iowa Alluvial Aquifers Extent           | Mapped boundaries of alluvial aquifer systems                                                                     | USGS / Iowa GeoData                                       | geodata.iowa.gov + USGS alluvial aquifer coverage                                        | GIS coverage                 | Binary covariate: inside/outside alluvial aquifer. Also distance-to-boundary as continuous covariate.                                          |
| Iowa DNR Surficial Geology MapServer    | Detailed surficial geology at 1:24,000 and 1:100,000 where mapped (~20% at 24k)                                   | IGS / Iowa DNR                                            | programs.iowadnr.gov/geospatial/rest/services/Geology/SurficialGeology/MapServer         | ArcGIS MapServer             | Use where available as high-confidence override. Gaps filled by ISU GLSI raster.                                                               |
| NLCD 2021 Land Cover                    | 30m land cover classification                                                                                     | USGS MRLC                                                 | mrlc.gov                                                                                 | GeoTIFF download             | Constraint mask only. Wetlands/water = 0. Urban/suburban = buildability discount.                                                              |
| EPA Superfund (NPL) Sites               | National Priorities List locations                                                                                | EPA                                                       | epa.gov/superfund                                                                        | Shapefile / API              | Hard constraint: 500m exclusion buffer = 0.                                                                                                    |

# 6. Model Architecture

## 6a. Random Forest + Residual Kriging

This is the recommended approach. It handles the mix of categorical and continuous covariates naturally, captures nonlinear relationships, and provides explicit prediction uncertainty through kriging variance.

**Step 1 — Score wells.** Compute the composite productivity score (Section 3) for every well with at least one non-null productivity field (PMPTST_GPM, SPC_GPM_FT, or T_FT2_D). Wells with zero productivity fields can still contribute if AQUIFER and WL_DPTH_FT are populated, but with a low-confidence flag.

**Step 2 — Extract covariates at well locations.** For each well, sample: surficial geology class (ISU GLSI raster), depth to bedrock (derived from bedrock topography MapServer + DEM), landform/physiographic region, alluvial aquifer membership (binary: inside/outside mapped alluvial extent), distance to nearest alluvial aquifer boundary (continuous). The AQUIFER field from the well data itself also enters as a covariate.

**Step 3 — Train Random Forest.** Predict well productivity score from covariates using scikit-learn RandomForestRegressor. Use spatial cross-validation (leave-one-group-out by county or HUC8 watershed) to prevent spatial leakage. Evaluate with R², RMSE, and feature importance rankings.

**Step 4 — Krige residuals.** Compute residuals (observed score minus RF prediction) at each well location. Fit an empirical variogram (pykrige or scikit-gstat). Perform ordinary kriging to interpolate residuals onto the statewide grid. The kriging variance at each grid cell provides a built-in uncertainty estimate.

**Step 5 — Generate prediction surface.** Apply the trained RF to predict at every grid cell using covariate rasters. Add the kriged residual surface. Clip to 0–1. Scale to 0–100 for the final suitability index.

**Step 6 — Apply constraint mask.** Multiply by binary feasibility layer: NLCD wetlands/water = 0, EPA Superfund 500m buffer = 0. Optionally apply NLCD-based buildability discounts for urban/suburban areas.

## 6b. How the AQUIFER Field Defines Interpolation Zones

The AQUIFER field creates natural groupings. Within “Alluvial” wells along the Des Moines River, spatial autocorrelation is expected to be strong — nearby alluvial wells should have similar productivity. Across aquifer type boundaries (e.g., from alluvial to buried sand and gravel, or from surficial to bedrock), the correlation structure changes. The RF handles this through the AQUIFER covariate; the variogram can optionally be fit per-aquifer-type if sample density permits, or a single variogram with AQUIFER as a factor.

## 6c. Handling Null Fields

The well dataset will have extensive nulls. Many wells have depth and aquifer type but no pump test. The strategy:

- **Wells with ≥1 productivity field (GPM, SPC, T):** Full scoring with neutral values (0.4) for missing fields. These are primary observations.

- **Wells with 0 productivity fields but AQUIFER + WL_DPTH_FT:** Score is based on non-productivity fields only. Flag as low-confidence. Include in RF training but optionally downweight.

- **Wells with only location + depth:** Exclude from scoring. Can still contribute to well density metrics if used as a secondary layer.

# 7. Implementation Steps

1.  **Acquire and clean well data.** Pull the Iowa DNR Sourcewater wells with all 11 fields. Filter to wells with valid coordinates and at least AQUIFER + WL_DPTH_FT populated. Parse AQUIFER values into standardized categories. Convert numeric fields, handle nulls.

2.  **Compute per-well productivity scores.** Apply the scoring table (Section 3). For percentile-normalized fields (GPM, SPC, T), compute percentiles within the filtered well population. Generate the composite score and data completeness index for each well.

3.  **Acquire and prepare covariates.** Download ISU GLSI surficial geology raster (10m), Landform Regions shapefile, Physiographic Regions shapefile, alluvial aquifer extent. Derive depth-to-bedrock raster from bedrock topography MapServer and Iowa LiDAR DEM. Align all layers to a common grid (250m recommended).

4.  **Extract covariates at well locations.** For each scored well, sample: surficial geology class, depth to bedrock, landform region, physiographic subregion, alluvial aquifer membership (binary), distance to nearest alluvial polygon. Include the well’s own AQUIFER and CONF_UNCON fields as additional covariates.

5.  **Train Random Forest trend model.** Fit RandomForestRegressor (scikit-learn) predicting composite well score from covariates. Use spatial CV (leave-one-county-out or leave-one-HUC8-out). Tune hyperparameters. Evaluate R², RMSE, feature importances.

6.  **Krige residuals.** Compute residuals at well locations. Fit empirical variogram (spherical or exponential model). Ordinary krige residuals onto 250m statewide grid. Export kriging variance as prediction uncertainty layer.

7.  **Generate final suitability surface.** RF prediction raster + kriged residual raster = raw suitability. Clip to 0–1, scale to 0–100. Apply constraint mask (NLCD wetlands/water = 0, Superfund buffer = 0, land cover buildability discounts).

8.  **Validate.** Hold out 20% of wells (spatially stratified). Compare predicted vs. observed scores. Generate validation statistics by aquifer type and landform region. Cross-check against USGS NGWMN monitoring wells as independent dataset.

9.  **Deploy to web map.** Export as Cloud-Optimized GeoTIFF. Tile for MapLibre display. Update click-report to show: suitability score, prediction confidence (from kriging variance), nearest wells with their productivity data, covariate values at clicked location.

# 8. Python Stack

- **scikit-learn:** RandomForestRegressor for trend model, spatial cross-validation

- **pykrige:** Variogram fitting and ordinary kriging of residuals

- **geopandas + rasterio + rasterstats:** Spatial joins, raster sampling at well points, zonal statistics

- **pandas + numpy:** Well data cleaning, scoring, percentile normalization

- **dataretrieval:** USGS NWIS groundwater level download (supplemental validation data)

- **rio-cogeo:** Cloud-Optimized GeoTIFF export for web deployment

# 9. Key Advantages Over V2

**Wells as ground truth, not just another layer.** V2 treated well data as one of five equally-weighted inputs. V3 treats wells as the response variable that all other layers exist to support. This matches the hydrogeological reality that well data is the only dataset integrating depth, lithology, saturation, and yield.

**Actual aquifer properties in the scoring.** V2 used well density and basic GPM. V3 uses transmissivity (T_FT2_D), specific capacity (SPC_GPM_FT), aquifer thickness (AQ_THK_FT), and hydraulic conductivity (K_FT_D) — the actual hydrogeological parameters that determine whether a site can support the Darcy U-line system.

**Geological layers inform interpolation, not scoring.** V2 assigned arbitrary scores to landform regions (Des Moines Lobe = 0.65). V3 uses surficial geology and landform regions as covariates in a trained model that learns the actual relationship between geological setting and aquifer productivity from the well data itself.

**Built-in uncertainty.** V2 produced a single score with no confidence information. V3 produces both a prediction and a kriging variance surface, so users know where the model is confident (dense wells, well-characterized geology) and where it’s extrapolating (sparse wells, geological complexity).

**Statewide surficial geology.** V2 was limited to ~20% coverage from the IGS surficial geology mapping. V3 uses the ISU GLSI 10m statewide raster derived from gSSURGO, giving full Iowa coverage for the primary interpolation zone layer.

# 10. Risks and Mitigations

**Pump test data sparsity:** Many wells will have depth and aquifer type but no PMPTST_GPM, SPC_GPM_FT, or T_FT2_D. Mitigation: neutral scores for missing fields, data completeness index for transparency, and the RF + kriging architecture which gracefully degrades in data-sparse areas by reverting to covariate-driven predictions.

**AQUIFER field inconsistency:** The AQUIFER field may use inconsistent naming across records (e.g., "Alluvial", "alluvial", "Alluvium", "Sand and Gravel"). Mitigation: standardize AQUIFER values into a controlled vocabulary during data cleaning (Step 1).

**Spatial bias in well locations:** Wells cluster around population centers and along river valleys (where alluvial aquifers are). Rural upland areas have fewer wells. Mitigation: the RF covariates (surficial geology, depth to bedrock) provide predictions in data-sparse areas; kriging variance explicitly flags low-confidence zones.

**Model overfitting to well clusters:** Dense well clusters (e.g., Des Moines metro) could dominate the RF training. Mitigation: spatial cross-validation (leave-one-county-out) prevents the model from memorizing local clusters. Optionally, spatially thin training data to equalize density.
