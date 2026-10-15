const fs = require("fs");
const path = require("path");

const root = path.resolve(__dirname, "..");
const sourcePath = path.join(root, ".firecrawl", "nbcharts-world-json.txt");
const outputPath = path.join(root, "outputs", "world.svg");
const map = JSON.parse(fs.readFileSync(sourcePath, "utf8"));

if (!map.center || !Array.isArray(map.children)) {
  throw new Error("Unexpected NB Map world data structure");
}

const width = map.center.x * 2;
const height = map.center.y * 2;
const color = (value) => `#${value.toString(16).padStart(6, "0")}`;
const escapeAttribute = (value) =>
  value.replaceAll("&", "&amp;").replaceAll('"', "&quot;");

const paths = map.children.map(
  (region, index) =>
    `  <path id="region-${String(index + 1).padStart(3, "0")}" d="${escapeAttribute(region.path)}"/>`,
);

const svg = [
  '<?xml version="1.0" encoding="UTF-8"?>',
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="World map">`,
  "  <title>World map</title>",
  "  <desc>Converted from NB Map World.json path data.</desc>",
  `  <g fill="${color(map.mapColor)}" fill-opacity="${map.mapOpacity}" stroke="${color(map.lineColor)}" stroke-width="0.65" stroke-linejoin="round">`,
  ...paths,
  "  </g>",
  "</svg>",
  "",
].join("\n");

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
fs.writeFileSync(outputPath, svg, "utf8");
console.log(`Created ${outputPath} with ${paths.length} paths.`);
