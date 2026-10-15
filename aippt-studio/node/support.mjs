import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';

const [runtimeRoot, input, output] = process.argv.slice(2);
const requireRuntime = createRequire(path.join(runtimeRoot, 'package.json'));
const echarts = requireRuntime('echarts');
const spec = JSON.parse(fs.readFileSync(input, 'utf8'));
const width = spec.width ?? 1280;
const height = spec.height ?? 720;
if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 || width > 8192 || height > 8192) {
  throw new Error('Chart width/height must be integers between 1 and 8192');
}
if (!spec.option || typeof spec.option !== 'object' || Array.isArray(spec.option)) {
  throw new Error('A chart input requires an ECharts option object');
}
const chart = echarts.init(null, null, {renderer: 'svg', ssr: true, width, height});
try {
  chart.setOption({...spec.option, animation: false});
  fs.writeFileSync(output, chart.renderToSVGString());
} finally {
  chart.dispose();
}
