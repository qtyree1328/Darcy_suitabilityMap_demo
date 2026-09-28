# Iowa Darcy-Style Geothermal Site Suitability
## Single Handoff Document for Coding Agent

## 1) Objective

Build a **statewide Iowa proof-of-concept suitability map** for **Darcy-style shallow geothermal screening**.

This is **not** a general deep geothermal or bedrock-driven geothermal model. It is a screening model for siting areas where a Darcy-style system is more plausible based on:
- shallow unconsolidated materials
- likely shallow groundwater access
- manageable drilling depth
- minimal environmental/development conflicts

Darcy’s public description emphasizes **surficial aquifers shallower than about 150 ft** to reduce drilling cost and avoid interfering with deeper drinking-water aquifers. Their system uses a **closed-loop U-line with heat exchangers submerged in groundwater**, so the model should prioritize **shallow hydrogeologic conditions**, not bedrock favorability. :contentReference[oaicite:0]{index=0}

---

## 2) Core Modeling Principle

### Do not use bedrock geology as a major driver.

For this Darcy-style proof-of-concept, **bedrock lithology should not be a primary factor**. The model should instead focus on:
1. **surficial material favorability**
2. **depth to bedrock / shallow drilling window**
3. **shallow well / groundwater proxy**
4. **developability**
5. **environmental constraints**

Bedrock geology can be dropped entirely or included only as a minor optional background factor.

---

## 3) Feasible Iowa Data Stack

These are the layers that are actually practical to obtain statewide for Iowa.

### A. Depth to Bedrock
Use Iowa’s statewide **Depth to Bedrock** layer. The ArcGIS item describes it as approximate depth from current land surface to bedrock in feet. This is directly relevant to Darcy’s shallow-aquifer targeting and is much more useful than a standalone bedrock lithology ranking. :contentReference[oaicite:1]{index=1}

### B. Surficial Geology
Use Iowa DNR / Iowa Geological Survey statewide **Surficial Geology** service. This is one of the most important Darcy-relevant inputs because it distinguishes coarse permeable materials from fine-grained drift. Iowa also has a newer high-resolution parent-material raster from Iowa State GLSI derived from gSSURGO, which is promising for a later refinement pass. :contentReference[oaicite:2]{index=2}

### C. Wells / Shallow-Well Proxy
Use **USGS NWIS** site inventory and, if feasible, Iowa DNR / IWFoS well information. Iowa DNR states that IWFoS provides well information including **aquifer depths**, with historical data drawing from publicly available DNR data, IWIS water quality, and GeoSAM well geology. USGS NWIS is also available for site inventory and wells. This is feasible, but more data-engineering-heavy than the geology layers. :contentReference[oaicite:3]{index=3}

### D. Land Cover
Use **NLCD 2021** for a basic developability proxy. NLCD is easy to obtain and works well for excluding water and downweighting dense urban land. :contentReference[oaicite:4]{index=4}

### E. Environmental Constraints
Use wetlands/water plus **EPA Superfund / NPL** geospatial data as a first-pass exclusion set. EPA provides geospatial information for NPL and Superfund site footprints. :contentReference[oaicite:5]{index=5}

### F. Principal Aquifers
USGS principal aquifers are accessible and easy to use, but this is a **coarse regional layer** and should not be the lead variable in a Darcy-style model. It is acceptable as an optional context layer, not a dominant scoring input. :contentReference[oaicite:6]{index=6}

---

## 4) Recommended Final Factor Stack

This is the recommended **feasible** and **Darcy-aligned** statewide Iowa model.

| Factor | Weight | Purpose |
|---|---:|---|
| Surficial Material Favorability | 30% | Identify coarse, permeable unconsolidated materials |
| Depth to Bedrock / Shallow Drilling Window | 25% | Favor areas with enough unconsolidated thickness for shallow aquifer targeting |
| Shallow Well / Well Density Proxy | 20% | Indirect evidence of usable shallow groundwater conditions |
| Land Cover / Developability | 15% | Favor buildable land and reduce heavily constrained urban/water areas |
| Environmental Constraints | 10% | Hard exclusions and conflict avoidance |

### Why this stack
- It is **buildable statewide**
- It is **closer to Darcy’s concept**
- It avoids over-weighting deep/regional aquifer geology
- It reduces reliance on hard-to-source statewide hydraulic conductivity or water table rasters

---

## 5) Recommended Scoring

## 5.1 Surficial Material Favorability (30%)

### Source
- Iowa DNR Surficial Geology service
- Optional refinement: Iowa State GLSI parent material raster

### Concept
Darcy-style systems should prefer **coarse unconsolidated materials** that are more likely to support groundwater flow and practical installation.

### Suggested scoring
Map surficial units into generalized favorability classes:

- **sand and gravel / coarse alluvium / outwash** = `1.0`
- **mixed alluvium / mixed coarse drift** = `0.8`
- **till / diamicton / mixed glacial drift** = `0.5`
- **loess / silty cover / fine overburden** = `0.3`
- **clay-rich lacustrine / very fine-grained deposits** = `0.1`
- **water** = `0.0`
- **unknown** = `0.5`

### Notes
- Exact Iowa unit names will need a lookup table.
- First implementation can use a hand-built crosswalk from unit names/descriptions to coarse/fine classes.
- Later version can refine by parsing parent material or lithologic descriptors.

---

## 5.2 Depth to Bedrock / Shallow Drilling Window (25%)

### Source
- Iowa statewide Depth to Bedrock layer

### Concept
Favor areas where unconsolidated material is **thick enough** to host shallow aquifer conditions, but **not so deep** that the drilling-cost advantage is lost.

### Suggested scoring
Use approximate piecewise bins:

- `0–20 ft` = `0.2`
- `20–50 ft` = `0.6`
- `50–150 ft` = `1.0`
- `150–250 ft` = `0.6`
- `>250 ft` = `0.3`

### Notes
- The `50–150 ft` peak aligns with Darcy’s stated shallow targeting.
- This factor replaces the previous “Bedrock Geology” factor.
- Do **not** simply score “deeper is better.”

---

## 5.3 Shallow Well / Well Density Proxy (20%)

### Source
- USGS NWIS wells
- Iowa DNR / IWFoS / IWIS / GeoSAM-derived well information if accessible

### Concept
Use wells as an indirect proxy for:
- productive shallow groundwater conditions
- known hydrogeologic use
- real-world drilling feasibility

### Implementation options

#### Option A: Fast statewide proof-of-concept
Use **kernel density of wells** after filtering to likely relevant groundwater wells.

#### Option B: Better version
Use **only shallow wells** where depth/aquifer data are available.

### Suggested scoring
If using kernel density:
- generate density raster with a radius around `3–5 km`
- normalize with min-max or percentile clipping
- output to `0–1`

### Recommended filtering
Prefer:
- groundwater wells
- wells with usable depth or aquifer fields
- shallow wells where possible

Exclude where possible:
- very deep municipal supply wells
- non-groundwater sites
- duplicate or low-quality records

### Notes
- This remains a proxy, not direct aquifer transmissivity.
- It is acceptable for proof-of-concept.

---

## 5.4 Land Cover / Developability (15%)

### Source
- NLCD 2021

### Concept
Favor locations that are easier to develop and less likely to be unusable due to water, dense urbanization, or heavy forest cover.

### Suggested scoring
- grassland / open space = `1.0`
- agriculture = `0.9`
- forest = `0.7`
- suburban / developed open to low intensity = `0.5`
- medium/high intensity urban = `0.2`
- wetlands = `0.0`
- open water = `0.0`

### Notes
- This is not a hydrogeologic factor.
- It is a practical siting factor.

---

## 5.5 Environmental Constraints (10%)

### Source
- wetlands / open water
- EPA Superfund / NPL site polygons

### Concept
Hard exclusion / penalty layer for locations that are environmentally problematic or unsuitable.

### Suggested scoring
- wetlands = `0`
- open water = `0`
- within `500 m` of Superfund/NPL site = `0`
- elsewhere = `1`

### Notes
- Keep this simple in V1.
- Additional constraints can be added later.

---

## 6) Remove or Reduce These Previous Factors

## Remove: Bedrock Geology (12%)
Do not use this as a major factor. It is not the right conceptual driver for Darcy-style shallow systems.

## Remove: Hydrologic Proximity (11%)
Do not use distance to mapped water/wetlands as a strong proxy for shallow groundwater suitability. This can create false positives in floodplains, wetlands, and otherwise unsuitable areas.

## Reduce or demote: Principal Aquifer Quality (35%)
Do not let regional principal aquifer classes dominate the model. Principal aquifers are accessible and useful for context, but they are too coarse and not tightly aligned with Darcy’s shallow surficial targeting. :contentReference[oaicite:7]{index=7}

---

## 7) Final Recommended Equation

### Suitability Equation
```text
Score = 0.30 * SurficialMaterial
      + 0.25 * DepthToBedrock
      + 0.20 * ShallowWellProxy
      + 0.15 * LandCover
      + 0.10 * Constraints
Output

Each factor normalized to 0–1

Final score multiplied by 100

Output raster range: 0–100

8) Implementation Guidance for Coding Agent
8.1 Target Deliverables

Produce:

statewide Iowa suitability raster

statewide tiled web map layer

optional vector summary polygons for top classes

click-based site report for any map point

8.2 Preferred Build Order
Phase 1: Fast proof-of-concept

Build first using:

Surficial Geology

Depth to Bedrock

NLCD

Wetlands / Water

EPA NPL / Superfund

NWIS well density

This is the fastest defensible statewide product.

Phase 2: Better hydrogeologic screening

Refine with:

Iowa DNR / IWFoS well depths

shallow-well filtering

improved surficial material lookup

parcel/building overlays if needed

8.3 Processing Workflow
Step 1: Download / access source layers

ArcGIS REST for Iowa geology layers

USGS / NWIS for wells

NLCD for land cover

EPA NPL polygons

wetlands/water source of choice

Step 2: Reproject everything

Use a single Iowa-friendly projected CRS for raster processing.

Step 3: Build factor rasters

Rasterize/reclassify each factor to common cell size and extent.

Step 4: Normalize

Ensure every factor is 0–1.

Step 5: Weighted overlay

Apply final equation.

Step 6: Export outputs

GeoTIFF / COG

PMTiles or XYZ tiles for web

optional contours / vector classes

8.4 Suggested Technical Stack
Good Python stack

geopandas

rasterio

rioxarray

xarray

numpy

scipy

pyproj

shapely

requests

pandas

Optional

geocube for rasterizing vector inputs

whitebox or gdal for additional raster utilities

PostGIS if building a persistent spatial pipeline

Web map output

COG + dynamic serving

or pre-rendered tiles / PMTiles

MapLibre or Mapbox GL JS on frontend

9) Point-Click Site Report Logic

For any clicked location, return:

final suitability score

surficial material class

depth to bedrock

shallow well density / proxy value

land cover class

whether environmental constraints are triggered

Suggested text fields in report

Overall suitability

Why this location scores this way

Key positives

Key limitations

Data confidence

Example interpretation logic

High surficial favorability + ideal depth window + moderate/high well proxy = strong candidate

Fine-grained surficial material + shallow bedrock + low well proxy = weak candidate

Any hard environmental exclusion = unsuitable

10) Minimal Lookup Tables to Build

The coding agent should explicitly create these lookup tables:

A. Surficial unit crosswalk

Map Iowa surficial units to:

coarse permeable

mixed

till

loess/silt

clay-rich

water

unknown

B. NLCD crosswalk

Map NLCD classes to developability scores.

C. Constraint crosswalk

Map wetlands, open water, and NPL buffers to binary exclusion.

11) Known Limitations

This proof-of-concept will not directly model:

hydraulic conductivity

transmissivity

actual groundwater flux

parcel-specific constructability

building thermal load

permitting nuances

exact Darcy engineering performance

It is a regional screening model, not a final engineering siting tool.

That is acceptable for a proof-of-concept, especially because the clean statewide versions of water table depth, transmissivity, and hydraulic conductivity are much harder to obtain than the Iowa geology and well datasets above.

12) Final Recommendation

For Iowa, the most feasible and defensible Darcy-aligned statewide proof-of-concept is:

drop bedrock geology as a major factor

replace it with depth to bedrock

make surficial geology the top geology input

use wells as a practical shallow groundwater proxy

keep land cover and environmental constraints simple

Final production model
Surficial Material Favorability   30%
Depth to Bedrock                  25%
Shallow Well / Well Density       20%
Land Cover / Developability       15%
Environmental Constraints         10%
Final production equation
Score = 0.30 * SurficialMaterial
      + 0.25 * DepthToBedrock
      + 0.20 * ShallowWellProxy
      + 0.15 * LandCover
      + 0.10 * Constraints

This is the version the coding agent should implement first.

## 13) Source Notes for Agent

Key accessible sources confirmed:

- **Iowa statewide depth-to-bedrock ArcGIS item**  
  https://www.arcgis.com/home/item.html?id=96156935df5b4851bf0f57be26fda08e

- **Iowa DNR Surficial Geology MapServer**  
  https://programs.iowadnr.gov/geospatial/rest/services/Geology/SurficialGeology/MapServer

- **Iowa DNR Bedrock Geology MapServer**  
  https://programs.iowadnr.gov/geospatial/rest/services/Geology/BedrockGeology/MapServer

- **Iowa State GLSI surficial / parent-material raster option**  
  https://www.agron.iastate.edu/glsi/physiography-gis-data/surficial-geology-of-iowa-gis/

- **USGS principal aquifers access**  
  https://www.usgs.gov/mission-areas/water-resources/science/principal-aquifers-united-states

- **Iowa DNR well logs / IWFoS aquifer depth availability**  
  https://www.iowadnr.gov/environmental-protection/water-quality/private-well-program/well-logs-reports

- **USGS NWIS site inventory / mapper**  
  https://waterdata.usgs.gov/nwis/si

- **EPA Superfund / NPL geospatial data**  
  https://www.epa.gov/superfund/superfund-data-and-reports

- **NLCD 2021 availability**  
  https://www.usgs.gov/centers/eros/news/nlcd-2021-now-available