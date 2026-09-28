# GEE Proxy — Google Earth Engine Thumbnail Service

## Endpoint
```
POST https://gee-proxy-787413290356.us-east1.run.app/api/gee/thumbnail
```

No authentication required. CORS enabled. Returns raw PNG bytes.

## Request Body (JSON)

```json
{
  "assetId": "GOOGLE/DYNAMICWORLD/V1",
  "bbox": [-74.05, 40.68, -73.85, 40.82],
  "visParams": {
    "bands": ["label"],
    "min": 0,
    "max": 8,
    "palette": ["419BDF","397D49","88B053","7A87C6","E49635","DFC35A","C4281B","A59B8F","B39FE1"]
  },
  "width": 512,
  "height": 512,
  "year": 2023,
  "reducer": "mosaic"
}
```

### Parameters

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `assetId` | string | ✅ | Earth Engine asset ID (Image or ImageCollection) |
| `bbox` | number[4] | ✅ | Bounding box [xmin, ymin, xmax, ymax] in EPSG:4326 |
| `visParams` | object | ✅ | Visualization parameters (see below) |
| `width` | number | ✅ | Output image width in pixels |
| `height` | number | ✅ | Output image height in pixels |
| `year` | number | optional | Filter ImageCollection to this year |
| `reducer` | string | optional | How to reduce collection: `"mosaic"` (default), `"median"`, `"first"` |

### visParams

| Param | Type | Description |
|-------|------|-------------|
| `bands` | string[] | Band names to visualize |
| `min` | number | Min value for stretch |
| `max` | number | Max value for stretch |
| `palette` | string[] | Hex color palette for single-band visualization |

### Response
- **200**: Raw PNG image (`Content-Type: image/png`)
- **500**: Error message (plain text)

## Supported Datasets

| Asset ID | Type | Band | Min | Max | Years | Reducer |
|----------|------|------|-----|-----|-------|---------|
| `GOOGLE/DYNAMICWORLD/V1` | ImageCollection | `label` | 0 | 8 | 2015–2024 | mosaic |
| `projects/sat-io/open-datasets/landcover/ESRI_Global-LULC_10m_TS` | ImageCollection | `b1` | 1 | 11 | 2017–2023 | mosaic |
| `USGS/NLCD_RELEASES/2021_REL/NLCD` | ImageCollection | `landcover` | 11 | 95 | 2001–2021 | mosaic |
| `ESA/WorldCover/v200` | ImageCollection | `Map` | 10 | 100 | 2020–2021 | first |
| `users/nlang/ETH_GlobalCanopyHeight_2020_10m_v1` | Image | `b1` | 0 | 50 | 2020 | — |

Any public Earth Engine asset works — not limited to this list.

## Example: curl

```bash
curl -X POST https://gee-proxy-787413290356.us-east1.run.app/api/gee/thumbnail \
  -H "Content-Type: application/json" \
  -d '{"assetId":"ESA/WorldCover/v200","bbox":[-122.5,37.7,-122.3,37.85],"visParams":{"bands":["Map"],"min":10,"max":100,"palette":["006400","ffbb22","ffff4c","f096ff","fa0000","b4b4b4","f0f0f0","0064c8","0096a0","00cf75","fae6a0"]},"width":512,"height":512,"reducer":"first"}' \
  -o output.png
```

## Example: JavaScript fetch

```javascript
const response = await fetch('https://gee-proxy-787413290356.us-east1.run.app/api/gee/thumbnail', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    assetId: 'GOOGLE/DYNAMICWORLD/V1',
    bbox: [-74.05, 40.68, -73.85, 40.82],
    visParams: { bands: ['label'], min: 0, max: 8, palette: ['419BDF','397D49','88B053','7A87C6','E49635','DFC35A','C4281B','A59B8F','B39FE1'] },
    width: 512, height: 512, year: 2023, reducer: 'mosaic'
  })
});
const blob = await response.blob();
const url = URL.createObjectURL(blob);
```

## Example: Python requests

```python
import requests

r = requests.post('https://gee-proxy-787413290356.us-east1.run.app/api/gee/thumbnail', json={
    'assetId': 'GOOGLE/DYNAMICWORLD/V1',
    'bbox': [-74.05, 40.68, -73.85, 40.82],
    'visParams': {'bands': ['label'], 'min': 0, 'max': 8, 'palette': ['419BDF','397D49','88B053','7A87C6','E49635','DFC35A','C4281B','A59B8F','B39FE1']},
    'width': 512, 'height': 512, 'year': 2023, 'reducer': 'mosaic'
})
with open('output.png', 'wb') as f:
    f.write(r.content)
```

## Notes
- First request after cold start takes ~15-30s (Python initialization + EE auth)
- Subsequent requests are faster (~5-10s) due to expression caching
- Max dimensions: 32,768 x 32,768 pixels (GEE limit)
- Max uncompressed data: 48MB per request (GEE limit)
- Service auto-scales 0-3 instances, scales to zero when idle
