/** Audited pixelmatch adapter. The vendored algorithm is unchanged ISC code. */
import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import pixelmatch from './vendor/pixelmatch/index.js';

const MAX_PIXELS = 20_000_000;

function integer(value, name, minimum = 0) {
  if (!Number.isSafeInteger(value) || value < minimum) throw new Error(`${name} must be an integer >= ${minimum}`);
  return value;
}

function bounds(indices, width) {
  if (!indices.count) return null;
  return {x: indices.minX, y: indices.minY, width: indices.maxX - indices.minX + 1, height: indices.maxY - indices.minY + 1};
}

function add(indices, x, y) {
  indices.count++;
  indices.minX = Math.min(indices.minX, x); indices.maxX = Math.max(indices.maxX, x);
  indices.minY = Math.min(indices.minY, y); indices.maxY = Math.max(indices.maxY, y);
}

/** Buffers must be tightly packed RGBA. Regions use half-open integer pixel bounds. */
export function compareRgba(baseline, candidate, width, height, options = {}) {
  integer(width, 'width', 1); integer(height, 'height', 1);
  const pixels = width * height;
  if (!Number.isSafeInteger(pixels) || pixels > MAX_PIXELS) throw new Error(`image exceeds ${MAX_PIXELS} pixels`);
  if (!(baseline instanceof Uint8Array) || !(candidate instanceof Uint8Array) || baseline.length !== pixels * 4 || candidate.length !== pixels * 4) {
    throw new Error('RGBA buffer sizes must equal width * height * 4');
  }
  const threshold = options.threshold ?? 0.1;
  if (typeof threshold !== 'number' || !Number.isFinite(threshold) || threshold < 0 || threshold > 1) throw new Error('threshold must be between 0 and 1');
  const includeAA = options.includeAntialiasing ?? false;
  if (typeof includeAA !== 'boolean') throw new Error('includeAntialiasing must be boolean');
  const regions = options.allowedRegions ?? [];
  if (!Array.isArray(regions) || regions.length > 1000) throw new Error('allowedRegions must be an array of at most 1000 rectangles');
  const allowed = new Uint8Array(pixels);
  for (const region of regions) {
    if (!region || Object.keys(region).sort().join(',') !== 'height,width,x,y') throw new Error('region must contain exactly x, y, width, height');
    const x = integer(region.x, 'region.x'), y = integer(region.y, 'region.y');
    const w = integer(region.width, 'region.width', 1), h = integer(region.height, 'region.height', 1);
    if (x + w > width || y + h > height) throw new Error('allowed region is outside image bounds');
    for (let row = y; row < y + h; row++) allowed.fill(1, row * width + x, row * width + x + w);
  }
  // pixelmatch creates Uint32Array views; copied inputs guarantee four-byte alignment.
  const mask = new Uint8Array(pixels * 4);
  const changedPixels = pixelmatch(Uint8Array.from(baseline), Uint8Array.from(candidate), mask, width, height,
    {threshold, includeAA, checkerboard: true, diffMask: true});
  const changed = {count: 0, minX: width, minY: height, maxX: -1, maxY: -1};
  const outside = {...changed};
  let allowedPixels = 0;
  for (let i = 0; i < pixels; i++) {
    allowedPixels += allowed[i];
    if (mask[i * 4 + 3] === 0) continue;
    const x = i % width, y = Math.floor(i / width);
    add(changed, x, y);
    if (!allowed[i]) add(outside, x, y);
  }
  if (changedPixels !== changed.count) throw new Error('pixelmatch count and mask disagree');
  return {mask, summary: {
    width, height, total_pixels: pixels, changed_pixels: changedPixels,
    allowed_pixels: allowedPixels, inside_allowed_changed_pixels: changedPixels - outside.count,
    outside_allowed_changed_pixels: outside.count,
    changed_bounds: bounds(changed, width), outside_allowed_bounds: bounds(outside, width),
    threshold, include_antialiasing: includeAA, checkerboard: true,
  }};
}

if (process.argv[1] && fileURLToPath(import.meta.url) === fs.realpathSync(process.argv[1])) {
  try {
    if (process.argv.length !== 3) throw new Error('usage: node visual-diff.mjs request.json');
    const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
    const {mask, summary} = compareRgba(fs.readFileSync(request.baseline_rgba), fs.readFileSync(request.candidate_rgba),
      request.width, request.height, {threshold: request.threshold, includeAntialiasing: request.include_antialiasing, allowedRegions: request.allowed_regions});
    fs.writeFileSync(request.mask_rgba, mask, {flag: 'wx'});
    process.stdout.write(JSON.stringify(summary));
  } catch (error) {
    process.stderr.write(`visual-diff: ${error.message}\n`);
    process.exitCode = 1;
  }
}
