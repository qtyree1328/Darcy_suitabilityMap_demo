// GEE Proxy Server — authenticates with Google Earth Engine using a service account
// and proxies thumbnail requests from the frontend.

import { createServer } from 'http';
import { existsSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import { GoogleAuth } from 'google-auth-library';

const __dirname = dirname(fileURLToPath(import.meta.url));
const SA_PATH = process.env.GEE_SERVICE_ACCOUNT_PATH
  ? resolve(process.env.GEE_SERVICE_ACCOUNT_PATH)
  : resolve(__dirname, '../../service-account.json');
const GEE_PROJECT_PATH = 'projects/generalresearch-478019';

// GEE REST API base
const GEE_API_V1 = 'https://earthengine.googleapis.com/v1';
const GEE_API_V1ALPHA = 'https://earthengine.googleapis.com/v1alpha';

let authClient = null;

async function getAuthClient() {
  if (authClient) return authClient;
  const authOptions = {
    scopes: ['https://www.googleapis.com/auth/earthengine'],
  };
  if (existsSync(SA_PATH)) {
    authOptions.keyFile = SA_PATH;
  }
  const auth = new GoogleAuth(authOptions);
  authClient = await auth.getClient();
  return authClient;
}

async function getAccessToken() {
  const client = await getAuthClient();
  const tokenResponse = await client.getAccessToken();
  return tokenResponse.token;
}

// Build an Earth Engine expression to get a thumbnail
async function getThumbnail({ assetId, bbox, visParams, width, height, year, reducer, assetType = null, composite = null }) {
  const token = await getAccessToken();
  const [xmin, ymin, xmax, ymax] = bbox;

  // Construct the Earth Engine expression
  // We use the REST API's computePixels or getThumb equivalent
  const geometry = {
    type: 'Polygon',
    coordinates: [[[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]],
  };

  // Build the image expression based on asset type
  let expression;
  if (composite && composite.strategy) {
    console.log(`[GEE] Route: buildCompositeCollectionExpression (strategy=${composite.strategy})`);
    expression = await buildCompositeCollectionExpression(assetId, geometry, visParams, composite);
  } else if (assetType === 'image') {
    console.log(`[GEE] Route: buildImageExpression (assetType=image)`);
    expression = buildImageExpression(assetId, geometry, visParams);
  } else if (assetType === 'collection' || year || isLikelyCollection(assetId)) {
    console.log(`[GEE] Route: buildCollectionExpression (assetType=${assetType}, year=${year})`);
    expression = buildCollectionExpression(assetId, geometry, visParams, year, reducer);
  } else {
    console.log(`[GEE] Route: buildImageExpression (fallback)`);
    expression = buildImageExpression(assetId, geometry, visParams);
  }

  // Use the computePixels or thumbnail endpoint
  const thumbUrl = `${GEE_API_V1}/${GEE_PROJECT_PATH}/image:computePixels`;

  const requestBody = {
    expression: expression,
    fileFormat: 'PNG',
    grid: {
      dimensions: { width, height },
      affineTransform: {
        scaleX: (xmax - xmin) / width,
        shearX: 0,
        translateX: xmin,
        shearY: 0,
        scaleY: -(ymax - ymin) / height,
        translateY: ymax,
      },
      crsCode: 'EPSG:4326',
    },
  };

  const bodyJson = JSON.stringify(requestBody);
  console.log(`[GEE] Expression size: ${bodyJson.length} bytes`);

  const response = await fetch(thumbUrl, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: bodyJson,
  });

  if (!response.ok) {
    const errText = await response.text();
    console.error(`[GEE] API error ${response.status}:`, errText.slice(0, 500));
    throw new Error(`GEE API error ${response.status}: ${errText}`);
  }

  return Buffer.from(await response.arrayBuffer());
}

async function buildCompositeCollectionExpression(assetId, geometry, visParams, composite) {
  const { startDate, endDate } = resolveAdaptiveDateRange(composite);
  const boundsGeometryExpr = buildGeometryExpression(geometry);

  console.log(`[GEE] Composite: ${assetId} ${startDate} to ${endDate} visMode=${composite.visMode} ndviBands=${JSON.stringify(composite.ndviBands)}`);
  console.log(`[GEE] Input visParams:`, JSON.stringify(visParams));

  let collectionExpr = {
    functionInvocationValue: {
      functionName: 'ImageCollection.load',
      arguments: { id: { constantValue: assetId } },
    },
  };

  const startMillis = Date.parse(`${startDate}T00:00:00Z`);
  const endMillis = Date.parse(`${endDate}T00:00:00Z`);

  // Filter by date range
  collectionExpr = filterByDateRange(collectionExpr, startMillis, endMillis);

  // Filter by bounds
  collectionExpr = {
    functionInvocationValue: {
      functionName: 'Collection.filter',
      arguments: {
        collection: collectionExpr,
        filter: {
          functionInvocationValue: {
            functionName: 'Filter.intersects',
            arguments: {
              leftField: { constantValue: '.geo' },
              rightValue: boundsGeometryExpr,
            },
          },
        },
      },
    },
  };

  // Filter by cloud cover metadata (pre-filter, coarse)
  const cloudThreshold = parseOptionalNumber(composite.maxCloudCover);
  if (composite.cloudProperty && cloudThreshold !== null) {
    collectionExpr = {
      functionInvocationValue: {
        functionName: 'Collection.filter',
        arguments: {
          collection: collectionExpr,
          filter: {
            functionInvocationValue: {
              functionName: 'Filter.lessThanOrEquals',
              arguments: {
                leftField: { constantValue: composite.cloudProperty },
                rightValue: { constantValue: cloudThreshold },
              },
            },
          },
        },
      },
    };
  }

  // Mean reduce — averages all overlapping pixels, guaranteeing full spatial
  // coverage where any image has data. Mosaic only takes the "top" pixel and
  // leaves gaps when individual tiles don't cover the full bbox.
  let imageExpr = {
    functionInvocationValue: {
      functionName: 'ImageCollection.reduce',
      arguments: {
        collection: collectionExpr,
        reducer: {
          functionInvocationValue: {
            functionName: 'Reducer.mean',
            arguments: {},
          },
        },
      },
    },
  };
  // ImageCollection.reduce appends '_mean' to band names — strip it
  // so downstream band references (visParams.bands, NDVI) still work.
  imageExpr = {
    functionInvocationValue: {
      functionName: 'Image.regexpRename',
      arguments: {
        input: imageExpr,
        regex: { constantValue: '_mean$' },
        replacement: { constantValue: '' },
      },
    },
  };

  let visualizationParams = { ...(visParams || {}) };

  if (composite.visMode === 'ndvi' && Array.isArray(composite.ndviBands) && composite.ndviBands.length >= 2) {
    const [nirBand, redBand] = composite.ndviBands;
    console.log(`[GEE] Computing NDVI via band math: (${nirBand} - ${redBand}) / (${nirBand} + ${redBand})`);

    // Compute NDVI = (NIR - RED) / (NIR + RED) using explicit band math.
    // Image.normalizedDifference may not produce a true single-band result
    // in all REST API contexts, so we use subtract/add/divide instead.
    const nirExpr = {
      functionInvocationValue: {
        functionName: 'Image.select',
        arguments: { input: imageExpr, bandSelectors: { constantValue: [nirBand] } },
      },
    };
    const redExpr = {
      functionInvocationValue: {
        functionName: 'Image.select',
        arguments: { input: imageExpr, bandSelectors: { constantValue: [redBand] } },
      },
    };
    const diffExpr = {
      functionInvocationValue: {
        functionName: 'Image.subtract',
        arguments: { image1: nirExpr, image2: redExpr },
      },
    };
    const sumExpr = {
      functionInvocationValue: {
        functionName: 'Image.add',
        arguments: { image1: nirExpr, image2: redExpr },
      },
    };
    imageExpr = {
      functionInvocationValue: {
        functionName: 'Image.divide',
        arguments: { image1: diffExpr, image2: sumExpr },
      },
    };

    // NDVI is single-band — remove bands so palette can be applied
    delete visualizationParams.bands;
    if (visualizationParams.min === undefined) visualizationParams.min = -0.2;
    if (visualizationParams.max === undefined) visualizationParams.max = 0.8;
    console.log('[GEE] NDVI visParams:', JSON.stringify(visualizationParams));
  } else if (visualizationParams.palette && Array.isArray(visualizationParams.bands) && visualizationParams.bands.length > 1) {
    console.warn('[GEE] Removing bands from visParams — palette cannot be used with multiple bands');
    delete visualizationParams.bands;
  }

  imageExpr = {
    functionInvocationValue: {
      functionName: 'Image.clip',
      arguments: {
        input: imageExpr,
        geometry: boundsGeometryExpr,
      },
    },
  };

  return {
    result: '0',
    values: {
      '0': {
        functionInvocationValue: {
          functionName: 'Image.visualize',
          arguments: {
            image: imageExpr,
            ...visParamsToArgs(visualizationParams),
          },
        },
      },
    },
  };
}

function filterByDateRange(collectionExpr, startMillis, endMillis) {
  collectionExpr = {
    functionInvocationValue: {
      functionName: 'Collection.filter',
      arguments: {
        collection: collectionExpr,
        filter: {
          functionInvocationValue: {
            functionName: 'Filter.gte',
            arguments: {
              leftField: { constantValue: 'system:time_start' },
              rightValue: { constantValue: startMillis },
            },
          },
        },
      },
    },
  };

  return {
    functionInvocationValue: {
      functionName: 'Collection.filter',
      arguments: {
        collection: collectionExpr,
        filter: {
          functionInvocationValue: {
            functionName: 'Filter.lt',
            arguments: {
              leftField: { constantValue: 'system:time_start' },
              rightValue: { constantValue: endMillis },
            },
          },
        },
      },
    },
  };
}

// Compute the date range for compositing based on strategy and user params.
// For S2: uses composite.dateRangeDays (default 90) centered on anchorDate.
// For NAIP: uses the full year of the anchor date (NAIP is annual, summer flights).
function resolveAdaptiveDateRange(composite) {
  const anchor = parseISODateOrToday(composite.anchorDate);

  if (composite.strategy === 'naip-month-mean') {
    // NAIP is collected on a 2–3 year cycle per state. A single year may have
    // zero coverage for a given location. Use a 3-year window centered on the
    // anchor year to guarantee we capture the nearest available NAIP flight.
    const y = anchor.getUTCFullYear();
    const yearStart = new Date(Date.UTC(y - 1, 0, 1));
    const yearEnd = new Date(Date.UTC(y + 2, 0, 1));
    console.log(`[GEE] NAIP 3-year window: ${y - 1} to ${y + 1}`);
    return {
      startDate: formatISODate(yearStart),
      endDate: formatISODate(yearEnd),
    };
  }

  // S2 and other collections: use dateRangeDays (default 90) centered on anchor
  const halfDays = Math.max(3, Math.round((composite.dateRangeDays || 90) / 2));
  const start = new Date(anchor);
  start.setUTCDate(start.getUTCDate() - halfDays);
  const end = new Date(anchor);
  end.setUTCDate(end.getUTCDate() + halfDays);

  return {
    startDate: formatISODate(start),
    endDate: formatISODate(end),
  };
}

function geometryToBbox(geometry) {
  if (!geometry || geometry.type !== 'Polygon' || !Array.isArray(geometry.coordinates)) return null;
  const ring = geometry.coordinates[0];
  if (!Array.isArray(ring) || !ring.length) return null;
  let xmin = Infinity;
  let ymin = Infinity;
  let xmax = -Infinity;
  let ymax = -Infinity;
  for (const pair of ring) {
    if (!Array.isArray(pair) || pair.length < 2) continue;
    xmin = Math.min(xmin, pair[0]);
    ymin = Math.min(ymin, pair[1]);
    xmax = Math.max(xmax, pair[0]);
    ymax = Math.max(ymax, pair[1]);
  }
  if (!Number.isFinite(xmin) || !Number.isFinite(ymin) || !Number.isFinite(xmax) || !Number.isFinite(ymax)) {
    return null;
  }
  return [xmin, ymin, xmax, ymax];
}

function buildGeometryExpression(geometry) {
  const bbox = geometryToBbox(geometry);
  if (!Array.isArray(bbox)) {
    return { constantValue: geometry };
  }
  return {
    functionInvocationValue: {
      functionName: 'GeometryConstructors.BBox',
      arguments: {
        west: { constantValue: bbox[0] },
        south: { constantValue: bbox[1] },
        east: { constantValue: bbox[2] },
        north: { constantValue: bbox[3] },
      },
    },
  };
}

function parseISODateOrToday(dateText) {
  if (typeof dateText === 'string' && dateText.trim()) {
    const parsed = new Date(`${dateText}T12:00:00Z`);
    if (!Number.isNaN(parsed.getTime())) return parsed;
  }
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), 12, 0, 0));
}

function formatISODate(date) {
  const y = date.getUTCFullYear();
  const m = String(date.getUTCMonth() + 1).padStart(2, '0');
  const d = String(date.getUTCDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

async function getElevationPixels({ assetId, bbox, width, height, year, reducer = 'mosaic', band = null }) {
  const token = await getAccessToken();
  const [xmin, ymin, xmax, ymax] = bbox;

  const geometry = {
    type: 'Polygon',
    coordinates: [[[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]],
  };

  const expression = buildRawElevationExpression(assetId, year, reducer, band);
  const url = `${GEE_API_V1}/${GEE_PROJECT_PATH}/image:computePixels`;
  const requestBody = {
    expression,
    fileFormat: 'NPY',
    grid: {
      dimensions: { width, height },
      affineTransform: {
        scaleX: (xmax - xmin) / width,
        shearX: 0,
        translateX: xmin,
        shearY: 0,
        scaleY: -(ymax - ymin) / height,
        translateY: ymax,
      },
      crsCode: 'EPSG:4326',
    },
  };

  const response = await fetch(url, {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestBody),
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`GEE API error ${response.status}: ${errText}`);
  }

  return Buffer.from(await response.arrayBuffer());
}

async function listCollectionImages({
  collectionId,
  bbox,
  startDate,
  endDate,
  maxResults = 30,
  maxCloudCover = null,
  cloudProperty = null,
  sortBy = 'date_desc',
}) {
  if (!collectionId) {
    throw new Error('collectionId is required');
  }

  const token = await getAccessToken();
  const collectionPath = toAssetResourcePath(collectionId);
  const encodedCollectionPath = encodePathPreservingSlashes(collectionPath);

  const resultLimit = clampInt(maxResults, 1, 200, 30);
  const query = new URLSearchParams();
  query.set('pageSize', String(Math.min(resultLimit * 3, 250)));
  if (startDate) query.set('startTime', `${startDate}T00:00:00Z`);
  if (endDate) query.set('endTime', `${endDate}T23:59:59Z`);
  if (Array.isArray(bbox) && bbox.length === 4) {
    query.set('region', JSON.stringify(bboxToPolygon(bbox)));
  }

  const url = `${GEE_API_V1ALPHA}/${encodedCollectionPath}:listImages?${query.toString()}`;
  const response = await fetch(url, {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
  });

  if (!response.ok) {
    const errText = await response.text();
    throw new Error(`GEE listImages error ${response.status}: ${errText}`);
  }

  const payload = await response.json();
  const rawImages = Array.isArray(payload.images) ? payload.images : [];
  let images = rawImages.map(normalizeImageSummary);
  const effectiveCloudProperty = cloudProperty || inferCloudProperty(images);

  if (effectiveCloudProperty) {
    images = images.map((image) => ({
      ...image,
      cloudProperty: effectiveCloudProperty,
      cloudCover: image.cloudCover ?? parseOptionalNumber(image.properties[effectiveCloudProperty]),
    }));
  }

  const cloudThreshold = parseOptionalNumber(maxCloudCover);
  if (cloudThreshold !== null) {
    images = images.filter((image) => image.cloudCover === null || image.cloudCover <= cloudThreshold);
  }

  images = sortImageSummaries(images, sortBy).slice(0, resultLimit);

  return {
    collectionId,
    count: images.length,
    totalAvailable: rawImages.length,
    cloudProperty: effectiveCloudProperty,
    images,
  };
}

function normalizeImageSummary(image) {
  const safeImage = image || {};
  const properties = safeImage.properties || {};
  const assetId = normalizeAssetId(safeImage.id || safeImage.name || '');
  const cloudCover = parseOptionalNumber(
    properties.CLOUDY_PIXEL_PERCENTAGE ??
    properties.CLOUD_COVER ??
    properties.CLOUD_COVERAGE_ASSESSMENT ??
    properties.cloud_cover
  );

  return {
    id: safeImage.id || assetId,
    name: safeImage.name || null,
    assetId,
    startTime: normalizeStartTime(safeImage.startTime, properties),
    endTime: safeImage.endTime || null,
    cloudCover,
    properties,
    geometry: safeImage.geometry || null,
  };
}

function sortImageSummaries(images, sortBy) {
  const arr = [...images];
  if (sortBy === 'date_asc') {
    arr.sort((a, b) => compareDatesAsc(a.startTime, b.startTime));
    return arr;
  }
  if (sortBy === 'cloud_asc') {
    arr.sort((a, b) => {
      const cloudCmp = compareOptionalNumbersAsc(a.cloudCover, b.cloudCover);
      if (cloudCmp !== 0) return cloudCmp;
      return compareDatesDesc(a.startTime, b.startTime);
    });
    return arr;
  }
  arr.sort((a, b) => compareDatesDesc(a.startTime, b.startTime));
  return arr;
}

function compareDatesDesc(a, b) {
  return compareDatesAsc(b, a);
}

function compareDatesAsc(a, b) {
  const ta = a ? Date.parse(a) : NaN;
  const tb = b ? Date.parse(b) : NaN;
  const aValid = Number.isFinite(ta);
  const bValid = Number.isFinite(tb);
  if (aValid && bValid) return ta - tb;
  if (aValid) return -1;
  if (bValid) return 1;
  return 0;
}

function compareOptionalNumbersAsc(a, b) {
  const aValid = typeof a === 'number' && Number.isFinite(a);
  const bValid = typeof b === 'number' && Number.isFinite(b);
  if (aValid && bValid) return a - b;
  if (aValid) return -1;
  if (bValid) return 1;
  return 0;
}

function normalizeStartTime(startTime, properties) {
  if (startTime) return startTime;
  const millis = properties ? properties['system:time_start'] : null;
  const parsed = parseOptionalNumber(millis);
  if (parsed === null) return null;
  return new Date(parsed).toISOString();
}

function parseOptionalNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function inferCloudProperty(images) {
  const keys = ['CLOUDY_PIXEL_PERCENTAGE', 'CLOUD_COVER', 'CLOUD_COVERAGE_ASSESSMENT', 'cloud_cover'];
  for (const key of keys) {
    if (images.some((image) => image.properties && image.properties[key] !== undefined)) {
      return key;
    }
  }
  return null;
}

function clampInt(value, min, max, fallback) {
  const parsed = parseInt(value, 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

function bboxToPolygon(bbox) {
  const [xmin, ymin, xmax, ymax] = bbox;
  return {
    type: 'Polygon',
    coordinates: [[[xmin, ymin], [xmax, ymin], [xmax, ymax], [xmin, ymax], [xmin, ymin]]],
  };
}

function toAssetResourcePath(assetId) {
  if (assetId.startsWith('projects/')) {
    if (assetId.includes('/assets/')) return assetId;
    const parts = assetId.split('/');
    if (parts.length >= 3) {
      const projectId = parts[1];
      const rest = parts.slice(2).join('/');
      return `projects/${projectId}/assets/${rest}`;
    }
    return assetId;
  }
  if (assetId.startsWith('users/')) {
    return `projects/earthengine-legacy/assets/${assetId}`;
  }
  return `projects/earthengine-public/assets/${assetId}`;
}

function normalizeAssetId(idOrName) {
  if (!idOrName) return idOrName;
  const marker = '/assets/';
  const idx = idOrName.indexOf(marker);
  if (idx >= 0) return idOrName.slice(idx + marker.length);
  return idOrName;
}

function encodePathPreservingSlashes(path) {
  return path
    .split('/')
    .map((part) => encodeURIComponent(part))
    .join('/');
}

function buildImageExpression(assetId, geometry, visParams) {
  const geometryExpr = buildGeometryExpression(geometry);
  let expr = {
    result: '0',
    values: {
      '0': {
        functionInvocationValue: {
          functionName: 'Image.visualize',
          arguments: {
            image: {
              functionInvocationValue: {
                functionName: 'Image.clip',
                arguments: {
                  input: { functionInvocationValue: { functionName: 'Image.load', arguments: { id: { constantValue: assetId } } } },
                  geometry: geometryExpr,
                },
              },
            },
            ...visParamsToArgs(visParams),
          },
        },
      },
    },
  };
  return expr;
}

function buildCollectionExpression(assetId, geometry, visParams, year, reducer = 'mosaic') {
  const geometryExpr = buildGeometryExpression(geometry);
  // Load collection
  let collectionExpr = {
    functionInvocationValue: {
      functionName: 'ImageCollection.load',
      arguments: { id: { constantValue: assetId } },
    },
  };

  // Filter by date if year provided
  if (year) {
    const yearStartMillis = Date.parse(`${year}-01-01T00:00:00Z`);
    const yearEndMillis = Date.parse(`${Number(year) + 1}-01-01T00:00:00Z`);
    collectionExpr = {
      functionInvocationValue: {
        functionName: 'Collection.filter',
        arguments: {
          collection: collectionExpr,
          filter: {
            functionInvocationValue: {
              functionName: 'Filter.gte',
              arguments: {
                leftField: { constantValue: 'system:time_start' },
                rightValue: { constantValue: yearStartMillis },
              },
            },
          },
        },
      },
    };
    collectionExpr = {
      functionInvocationValue: {
        functionName: 'Collection.filter',
        arguments: {
          collection: collectionExpr,
          filter: {
            functionInvocationValue: {
              functionName: 'Filter.lt',
              arguments: {
                leftField: { constantValue: 'system:time_start' },
                rightValue: { constantValue: yearEndMillis },
              },
            },
          },
        },
      },
    };
  }

  // Reduce to single image
  let imageExpr;
  if (reducer === 'first') {
    imageExpr = {
      functionInvocationValue: {
        functionName: 'Collection.first',
        arguments: { collection: collectionExpr },
      },
    };
  } else if (reducer === 'mosaic') {
    imageExpr = {
      functionInvocationValue: {
        functionName: 'ImageCollection.mosaic',
        arguments: { collection: collectionExpr },
      },
    };
  } else {
    // Default: mean reduce
    imageExpr = {
      functionInvocationValue: {
        functionName: 'ImageCollection.reduce',
        arguments: {
          collection: collectionExpr,
          reducer: {
            functionInvocationValue: {
              functionName: 'Reducer.mean',
              arguments: {},
            },
          },
        },
      },
    };
    // Strip '_mean' suffix from band names
    imageExpr = {
      functionInvocationValue: {
        functionName: 'Image.regexpRename',
        arguments: {
          input: imageExpr,
          regex: { constantValue: '_mean$' },
          replacement: { constantValue: '' },
        },
      },
    };
  }

  // Clip to geometry
  imageExpr = {
    functionInvocationValue: {
      functionName: 'Image.clip',
      arguments: {
        input: imageExpr,
        geometry: geometryExpr,
      },
    },
  };

  // Visualize
  return {
    result: '0',
    values: {
      '0': {
        functionInvocationValue: {
          functionName: 'Image.visualize',
          arguments: {
            image: imageExpr,
            ...visParamsToArgs(visParams),
          },
        },
      },
    },
  };
}

function buildRawElevationExpression(assetId, year, reducer = 'mosaic', band = null) {
  const useCollection = year || isLikelyCollection(assetId);
  let imageExpr;

  if (useCollection) {
    let collectionExpr = {
      functionInvocationValue: {
        functionName: 'ImageCollection.load',
        arguments: { id: { constantValue: assetId } },
      },
    };

    if (year) {
      const yearStartMillis = Date.parse(`${year}-01-01T00:00:00Z`);
      const yearEndMillis = Date.parse(`${Number(year) + 1}-01-01T00:00:00Z`);
      collectionExpr = {
        functionInvocationValue: {
          functionName: 'Collection.filter',
          arguments: {
            collection: collectionExpr,
            filter: {
              functionInvocationValue: {
                functionName: 'Filter.gte',
                arguments: {
                  leftField: { constantValue: 'system:time_start' },
                  rightValue: { constantValue: yearStartMillis },
                },
              },
            },
          },
        },
      };
      collectionExpr = {
        functionInvocationValue: {
          functionName: 'Collection.filter',
          arguments: {
            collection: collectionExpr,
            filter: {
              functionInvocationValue: {
                functionName: 'Filter.lt',
                arguments: {
                  leftField: { constantValue: 'system:time_start' },
                  rightValue: { constantValue: yearEndMillis },
                },
              },
            },
          },
        },
      };
    }

    if (reducer === 'first') {
      imageExpr = {
        functionInvocationValue: {
          functionName: 'Collection.first',
          arguments: { collection: collectionExpr },
        },
      };
    } else if (reducer === 'mosaic') {
      imageExpr = {
        functionInvocationValue: {
          functionName: 'ImageCollection.mosaic',
          arguments: { collection: collectionExpr },
        },
      };
    } else {
      imageExpr = {
        functionInvocationValue: {
          functionName: 'ImageCollection.reduce',
          arguments: {
            collection: collectionExpr,
            reducer: {
              functionInvocationValue: {
                functionName: 'Reducer.mean',
                arguments: {},
              },
            },
          },
        },
      };
      imageExpr = {
        functionInvocationValue: {
          functionName: 'Image.regexpRename',
          arguments: {
            input: imageExpr,
            regex: { constantValue: '_mean$' },
            replacement: { constantValue: '' },
          },
        },
      };
    }
  } else {
    imageExpr = {
      functionInvocationValue: {
        functionName: 'Image.load',
        arguments: { id: { constantValue: assetId } },
      },
    };
  }

  if (band) {
    imageExpr = {
      functionInvocationValue: {
        functionName: 'Image.select',
        arguments: {
          input: imageExpr,
          bandSelectors: { constantValue: [band] },
        },
      },
    };
  }

  return {
    result: '0',
    values: {
      '0': imageExpr,
    },
  };
}

function visParamsToArgs(visParams) {
  const params = visParams || {};
  const args = {};
  if (params.bands) {
    args.bands = { constantValue: params.bands };
  }
  if (params.min !== undefined) {
    args.min = { constantValue: params.min };
  }
  if (params.max !== undefined) {
    args.max = { constantValue: params.max };
  }
  if (params.palette) {
    args.palette = { constantValue: params.palette };
  }
  return args;
}

function isLikelyCollection(assetId) {
  const collectionPatterns = [
    'COPERNICUS/', 'GOOGLE/', 'USGS/', 'MODIS/', 'LANDSAT/',
    'sat-io/open-datasets', 'ImageCollection', 'LULC_10m_TS',
    'DYNAMICWORLD',
  ];
  return collectionPatterns.some(p => assetId.includes(p));
}

// --- HTTP Server ---

export function createGEEProxy(port = 3001) {
  const server = createServer(async (req, res) => {
    const requestPath = (req.url || '').split('?')[0];

    // CORS
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

    if (req.method === 'OPTIONS') {
      res.writeHead(204);
      res.end();
      return;
    }

    try {
      if (req.method === 'POST' && requestPath === '/api/gee/thumbnail') {
        const params = await readJsonBody(req);
        console.log(`[GEE] Fetching: ${params.assetId} bbox=${params.bbox} year=${params.year}`);
        const pngBuffer = await getThumbnail(params);
        res.writeHead(200, { 'Content-Type': 'image/png' });
        res.end(pngBuffer);
        return;
      }

      if (req.method === 'POST' && requestPath === '/api/gee/elevation') {
        const params = await readJsonBody(req);
        console.log(`[GEE] Elevation: ${params.assetId} bbox=${params.bbox}`);
        const npyBuffer = await getElevationPixels(params);
        res.writeHead(200, { 'Content-Type': 'application/octet-stream' });
        res.end(npyBuffer);
        return;
      }

      if (req.method === 'POST' && requestPath === '/api/gee/list-images') {
        const params = await readJsonBody(req);
        console.log(
          `[GEE] List images: ${params.collectionId} bbox=${params.bbox} start=${params.startDate} end=${params.endDate}`
        );
        const result = await listCollectionImages(params);
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify(result));
        return;
      }

      res.writeHead(404, { 'Content-Type': 'text/plain' });
      res.end('Not found');
    } catch (err) {
      const routeLabel = requestPath.includes('/elevation')
        ? 'Elevation error'
        : requestPath.includes('/list-images')
          ? 'List images error'
          : 'Error';
      console.error(`[GEE] ${routeLabel}:`, err.message);
      res.writeHead(500, { 'Content-Type': 'text/plain' });
      res.end(err.message);
    }
  });

  server.listen(port, () => {
    console.log(`[GEE Proxy] Running on http://localhost:${port}`);
  });

  return server;
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', (chunk) => {
      body += chunk;
    });
    req.on('end', () => {
      try {
        resolve(body ? JSON.parse(body) : {});
      } catch (err) {
        reject(new Error('Invalid JSON body'));
      }
    });
    req.on('error', reject);
  });
}

// If run directly
if (process.argv[1] && process.argv[1].includes('gee-proxy')) {
  const port = Number.parseInt(process.env.PORT || '3001', 10);
  createGEEProxy(Number.isFinite(port) ? port : 3001);
}
