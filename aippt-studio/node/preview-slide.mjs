import { createHash } from 'node:crypto';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { loadPresentationFile, getSlides } from '@office-kit/pptx/node';
import { renderSlideToImage } from '@office-kit/pptx-preview/node';

const args = process.argv.slice(2);
const value = (name, fallback = undefined) => {
  const index = args.indexOf(name);
  return index < 0 ? fallback : args[index + 1];
};
const allValues = (name) => args.flatMap((arg, i) => arg === name ? [args[i + 1]] : []);
const sourceArg = value('--source');
const outputArg = value('--output');
const source = sourceArg ? resolve(sourceArg) : '';
const output = outputArg ? resolve(outputArg) : '';
const slideNumber = Number(value('--slide'));
const width = Number(value('--width', '1280'));
const fontFiles = allValues('--font-file').map(resolve);
const loadSystemFonts = value('--system-fonts', 'false') === 'true';

if (!source || !output || !Number.isInteger(slideNumber) || slideNumber < 1 || !Number.isInteger(width) || width < 320 || width > 4096) {
  throw new Error('Usage: --source FILE --slide N --output PNG [--width 320..4096] [--font-file FILE ...] [--system-fonts true|false]');
}

const sourceBytes = await readFile(source);
const sourceSha256 = createHash('sha256').update(sourceBytes).digest('hex');
const presentation = await loadPresentationFile(source);
const slides = getSlides(presentation);
if (slideNumber > slides.length) throw new Error(`Slide ${slideNumber} is outside the deck (1..${slides.length})`);
const png = renderSlideToImage(presentation, slides[slideNumber - 1], {
  width,
  fontFiles,
  loadSystemFonts,
});
await mkdir(dirname(output), { recursive: true });
await writeFile(output, png, { flag: 'wx' });
const outputSha256 = createHash('sha256').update(png).digest('hex');
console.log(JSON.stringify({
  status: 'rendered',
  source_sha256: sourceSha256,
  slide_number: slideNumber,
  slide_count: slides.length,
  width,
  output: output,
  output_sha256: outputSha256,
  output_bytes: png.byteLength,
  renderer: '@office-kit/pptx-preview/node',
  renderer_version: '0.11.0',
  approximate_renderer: true,
  generated_pdf: false,
  rendered_slide_count: 1,
  font_files: fontFiles,
  system_font_fallback: loadSystemFonts,
}, null, 2));
