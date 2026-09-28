
// ============================================================
// IOWA GEOTHERMAL SUITABILITY MODEL
// Darcy Solutions Demo — Tyree Spatial
// Paste into https://code.earthengine.google.com/ to run
// ============================================================

var states = ee.FeatureCollection("TIGER/2018/States");
var iowa = states.filter(ee.Filter.eq('NAME', 'Iowa'));
var iowaGeom = iowa.geometry();

// Factor 1: Aquifer Presence (0.25)
var aquiferScore = ee.Image(0.5).clip(iowaGeom).rename('aquifer');

// Factor 2: Well Density (0.20)
var wellDensity = ee.Image(0.3).clip(iowaGeom).rename('wellDensity');

// Factor 3: Bedrock Geology (0.15)
var geologyScore = ee.Image(0.7).clip(iowaGeom).rename('geology');

// Factor 4: Land Cover (0.15)
var nlcd = ee.ImageCollection("USGS/NLCD_RELEASES/2021_REL/NLCD")
    .filter(ee.Filter.eq('system:index', '2021')).first()
    .select('landcover').clip(iowaGeom);
var landSuit = nlcd.remap(
    [11,12,21,22,23,24,31,41,42,43,52,71,81,82,90,95],
    [0,0,0.5,0.5,0.4,0.3,0.8,0.8,0.8,0.8,0.7,1.0,0.9,0.9,0.2,0]
).rename('landcover');

// Factor 5: Environmental Constraints (0.10)
var wetlandMask = nlcd.eq(90).or(nlcd.eq(95));
var waterMask = nlcd.eq(11);
var constraintScore = ee.Image(1.0).where(wetlandMask,0).where(waterMask,0)
    .clip(iowaGeom).rename('constraints');

// Factor 6: Hydrologic Proximity (0.10)
var hydroMask = wetlandMask.or(waterMask).unmask(0);
var hydroDist = hydroMask.Not().fastDistanceTransform(256,'pixels').sqrt().multiply(30);
var hydroScore = hydroDist.expression(
    "(d<=250)?1.0:(d<=1000)?0.8:(d<=2500)?0.5:(d<=5000)?0.2:0.0",{d:hydroDist}
).clip(iowaGeom).rename('hydroProximity');

// Factor 7: Remote Sensing (0.05)
var landsat = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2')
    .merge(ee.ImageCollection('LANDSAT/LC09/C02/T1_L2'))
    .filterBounds(iowaGeom).filterDate('2024-06-01','2024-08-31').select('ST_B10')
    .map(function(img){return img.multiply(0.00341802).add(149).subtract(273.15)
        .copyProperties(img,['system:time_start']);});
var lstMedian = landsat.median().clip(iowaGeom);
var lstMean = lstMedian.reduceRegion({reducer:ee.Reducer.mean(),geometry:iowaGeom,
    scale:1000,maxPixels:1e9}).get('ST_B10');
var lstScore = lstMedian.expression("0.5+clamp((mean-lst)*0.1,-0.5,0.5)",
    {lst:lstMedian,mean:ee.Number(lstMean)}).rename('lst_score');
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED').filterBounds(iowaGeom)
    .filterDate('2024-06-01','2024-08-31')
    .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE',20)).median().clip(iowaGeom);
var ndwi = s2.normalizedDifference(['B3','B8']).rename('NDWI');
var ndwiScore = ndwi.expression("clamp(ndwi+0.5,0,1)",{ndwi:ndwi}).rename('ndwi_score');
var rsScore = lstScore.add(ndwiScore).divide(2).clamp(0,1).rename('remoteSensing');

// Weighted Overlay
var suitability = aquiferScore.multiply(0.25).add(wellDensity.multiply(0.20))
    .add(geologyScore.multiply(0.15)).add(landSuit.multiply(0.15))
    .add(constraintScore.multiply(0.10)).add(hydroScore.multiply(0.10))
    .add(rsScore.multiply(0.05)).multiply(100).rename('suitability');

Map.centerObject(iowa, 7);
Map.addLayer(suitability, {min:0,max:100,palette:['dc2626','f97316','eab308','84cc16','22c55e']}, 'Suitability');
