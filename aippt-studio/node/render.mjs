import fs from 'node:fs';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {chromiumExecutable} from './browser.mjs';
import imageGeometry from './image-geometry.cjs';
import {prepareImageGeometry} from './html-image-geometry.mjs';

// Explicit dimensions avoid dom-to-pptx 2.0.3's rounded 13.3-inch WIDE scale.
export const SLIDE_INCHES = {width: 40 / 3, height: 7.5};
// Reference sizes inform review; they are never a default export veto.
export const MINIMUM_PT = {title: 30, body: 24, caption: 18, source: 16};

export async function auditLayout(page) {
  await page.evaluate(async () => {
    await document.fonts.ready;
    await Promise.all([...document.images].map(image => image.complete ? Promise.resolve() : new Promise(resolve => {
      image.addEventListener('load', resolve, {once: true});
      image.addEventListener('error', resolve, {once: true});
    })));
  });
  return page.evaluate(({slideInches, minimumPt}) => {
    const issues = [];
    const advisories = [];
    const slides = [...document.querySelectorAll('.slide')];
    const allSlides = [];
    const layoutObservations = [];
    const rect = box => ({x: box.left, y: box.top, width: box.width, height: box.height});
    const outside = (box, bounds, tolerance = 2) => box.left < bounds.left - tolerance || box.top < bounds.top - tolerance || box.right > bounds.right + tolerance || box.bottom > bounds.bottom + tolerance;
    const intersects = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 2 && Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 2;
    const visible = element => {
      for (let item = element; item; item = item.parentElement) {
        const style = getComputedStyle(item);
        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) return false;
      }
      return true;
    };
    if (!slides.length) issues.push({kind: 'no_slides', message: 'No slide containers were found'});
    for (const [index, slide] of slides.entries()) {
      const id = slide.dataset.slideId || `slide-${String(index + 1).padStart(2, '0')}`;
      const bounds = slide.getBoundingClientRect();
      // Record the rendered layout tree and measured zone geometry. Text,
      // colours and planning labels are deliberately excluded.
      const layoutZones = [...slide.querySelectorAll('[data-layout-zone]')].filter(visible);
      const q = value => Math.round(value * 20) / 20;
      const layoutSkeleton = layoutZones.map(zone => {
        const box = zone.getBoundingClientRect();
        const children = [...zone.children].filter(visible).map(child => {
          const childBox = child.getBoundingClientRect();
          return {
            tag: child.tagName.toLowerCase(),
            classes: [...child.classList].sort(),
            rect: [q((childBox.left - bounds.left) / bounds.width), q((childBox.top - bounds.top) / bounds.height),
              q(childBox.width / bounds.width), q(childBox.height / bounds.height)],
            descendants: [...child.children].filter(visible).map(item => ({tag: item.tagName.toLowerCase(), classes: [...item.classList].sort()})),
          };
        });
        return {tag: zone.tagName.toLowerCase(), classes: [...zone.classList].sort(),
          rect: [q((box.left - bounds.left) / bounds.width), q((box.top - bounds.top) / bounds.height),
            q(box.width / bounds.width), q(box.height / bounds.height)], children};
      });
      layoutObservations.push({slide_id: slide.dataset.slideId || `slide-${String(index + 1).padStart(2, '0')}`,
        eligible: document.body.dataset.compositionBaseline === 'new_composition',
        // Layout names are planner labels: content and evidence can still
        // render the same measured skeleton. Do not let a label hide that.
        fingerprint: layoutSkeleton.length ? JSON.stringify(layoutSkeleton) : ''});
      const ptPerPx = Math.min(slideInches.width * 72 / bounds.width, slideInches.height * 72 / bounds.height);
      const texts = [];
      const add = (kind, detail) => issues.push({slide_id: id, kind, ...detail});
      const advise = (kind, detail) => advisories.push({slide_id: id, kind, severity: 'advisory', ...detail});
      if (!bounds.width || !bounds.height || Math.abs(bounds.width / bounds.height - 16 / 9) > 0.005) add('invalid_slide_geometry', {bounds: rect(bounds)});
      if (Number(slide.dataset.unresolvedAssets || 0)) add('unresolved_assets', {count: Number(slide.dataset.unresolvedAssets)});
      const walker = document.createTreeWalker(slide, NodeFilter.SHOW_TEXT);
      for (let node = walker.nextNode(); node; node = walker.nextNode()) {
        const text = node.textContent.trim();
        const element = node.parentElement;
        if (!text || !element || ['STYLE', 'SCRIPT', 'NOSCRIPT'].includes(element.tagName) || !visible(element)) continue;
        const style = getComputedStyle(element);
        const role = element.closest('[data-text-role]')?.dataset.textRole || (/^H[12]$/.test(element.tagName) ? 'title' : 'body');
        const fontPt = parseFloat(style.fontSize) * ptPerPx;
        const minimum = minimumPt[role] ?? minimumPt.body;
        const range = document.createRange();
        range.selectNodeContents(node);
        const boxes = [...range.getClientRects()].filter(box => box.width > 0 && box.height > 0);
        const description = {text: text.slice(0, 140), role, font_px: parseFloat(style.fontSize), font_pt: Number(fontPt.toFixed(3)), reference_pt: minimum};
        if (fontPt + 0.01 < minimum) advise('font_below_reference', description);
        if (boxes.some(box => outside(box, bounds))) add('text_outside_slide', {...description, bounds: boxes.map(rect)});
        const content = element.closest('[data-content-region],.content');
        if (content && boxes.some(box => outside(box, content.getBoundingClientRect()))) add('text_exceeds_content_region', description);
        for (let ancestor = element; ancestor && ancestor !== slide.parentElement; ancestor = ancestor.parentElement) {
          const ancestorStyle = getComputedStyle(ancestor);
          if (/(hidden|clip)/.test(`${ancestorStyle.overflowX} ${ancestorStyle.overflowY}`) && boxes.some(box => outside(box, ancestor.getBoundingClientRect()))) {
            add('text_clipped_by_container', {...description, container: ancestor.className || ancestor.tagName});
            break;
          }
        }
        const elementBounds = element.getBoundingClientRect();
        if (text.length >= 70 && elementBounds.width < bounds.width * 0.2 && elementBounds.height > elementBounds.width * 1.3) {
          advise('narrow_dense_text', {...description, bounds: rect(elementBounds), message: 'Review the meaning and reading effort of this narrow text region in the whole slide.'});
        }
        texts.push({element, boxes, length: text.length, ...description});
      }
      const largestBodyFont = Math.max(0, ...texts.filter(text => text.role === 'body').map(text => text.font_pt));
      for (const text of texts) {
        if (text.role === 'body' && text.length >= 40 && largestBodyFont >= text.font_pt * 2.4) {
          advise('body_scale_contrast', {text: text.text, font_pt: text.font_pt, largest_body_font_pt: largestBodyFont,
            message: 'Review whether this size contrast reflects the message hierarchy or leaves essential explanation subordinate to decoration.'});
        }
      }
      const images = [...slide.querySelectorAll('img')].filter(visible);
      for (const image of images) {
        if (!image.complete || image.naturalWidth === 0) add('image_failed_to_load', {source: image.getAttribute('src')?.slice(0, 180)});
        if (outside(image.getBoundingClientRect(), bounds)) add('image_outside_slide', {bounds: rect(image.getBoundingClientRect())});
        if (!image.hasAttribute('data-image-geometry')) {
          const ownStyle=getComputedStyle(image), parent=image.parentElement, parentStyle=parent&&getComputedStyle(parent);
          const ownRadius=parseFloat(ownStyle.borderRadius)||0, parentRadius=parseFloat(parentStyle?.borderRadius)||0;
          const a=image.getBoundingClientRect(), b=parent?.getBoundingClientRect();
          const sameFrame=b&&Math.abs(a.width-b.width)<5&&Math.abs(a.height-b.height)<5;
          if (ownRadius>0 || ownStyle.clipPath!=='none' || (parentRadius>0&&sameFrame))
            add('image_frame_contour_unbound', {bounds:rect(a),message:'Declare data-image-geometry on this picture so fit, source crop, mask and border share one contour. A separate rounded parent does not establish a matching image mask.'});
        }
      }
      for (const [index, text] of texts.entries()) {
        for (const image of images) {
          if (text.boxes.some(box => intersects(box, image.getBoundingClientRect()))) add('text_image_overlap', {text: text.text, image_bounds: rect(image.getBoundingClientRect())});
        }
        for (const other of texts.slice(index + 1)) {
          if (text.boxes.some(a => other.boxes.some(b => intersects(a, b)))) add('text_text_overlap', {text: text.text, other_text: other.text});
        }
      }
      const zones = [...slide.querySelectorAll('[data-layout-zone]')].filter(visible);
      for (const [index, zone] of zones.entries()) {
        for (const other of zones.slice(index + 1)) {
          if (zone.contains(other) || other.contains(zone)) continue;
          if (intersects(zone.getBoundingClientRect(), other.getBoundingClientRect())) add('layout_zone_overlap', {zone: zone.dataset.layoutZone, other_zone: other.dataset.layoutZone});
        }
      }
      allSlides.push({slide_id: id, width_px: bounds.width, height_px: bounds.height, points_per_css_px: ptPerPx,
        text_count: texts.length, image_count: images.length,
        measured_fonts: texts.map(({text, role, font_px, font_pt, reference_pt}) => ({text, role, font_px, font_pt, reference_pt})),
        issue_count: issues.filter(issue => issue.slide_id === id).length,
        advisory_count: advisories.filter(item => item.slide_id === id).length});
    }
    const repetitionRuns = [];
    let run = [];
    const flushRun = () => {
      if (run.length >= 3) repetitionRuns.push(run.map(item => item.slide_id));
      run = [];
    };
    for (const observation of layoutObservations) {
      if (!observation.eligible || !observation.fingerprint) { flushRun(); continue; }
      if (run.length && run[run.length - 1].fingerprint !== observation.fingerprint) flushRun();
      run.push(observation);
    }
    flushRun();
    for (const slide_ids of repetitionRuns) {
      advisories.push({kind: 'repeated_layout_skeleton', severity: 'advisory', slide_ids,
        message: 'Three or more consecutive new-composition slides share the same measured DOM layout skeleton. Review page-to-page composition against the changing message.'});
      for (const observed of allSlides) if (slide_ids.includes(observed.slide_id)) observed.advisory_count += 1;
    }
    return {schema_version: 1, status: issues.length ? 'needs_review' : 'passed', check: 'browser_dom_preflight',
      visual_reviewed: false, expression_reviewed: false, typography_mode: 'advisory',
      acceptance_scope: 'Geometry and image loading only; this result does not approve wording, hierarchy, aesthetics or whole-deck expression.',
      physical_slide_inches: slideInches, font_reference_pt: minimumPt,
      remedy: issues.length ? 'Resolve clipping, overflow or collisions by editing and reflowing the whole slide. Preserve facts for review; do not silently crop or drop them.' : null,
      slides: allSlides, issues, advisories,
      layout_repetition: {scope: 'new_composition_baseline_only', consecutive_minimum: 3, runs: repetitionRuns}};
  }, {slideInches: SLIDE_INCHES, minimumPt: MINIMUM_PT});
}

export async function render(htmlInput, pptxOutput, previewOutput, {auditOnly = false} = {}) {
  const executablePath = chromiumExecutable();
  process.env.PUPPETEER_EXECUTABLE_PATH = executablePath;
  const {default: puppeteer} = await import('puppeteer');
  const htmlPath = path.resolve(htmlInput);
  const pptxPath = path.resolve(pptxOutput);
  const previewDir = path.resolve(previewOutput);
  fs.mkdirSync(previewDir, {recursive: true});
  const browser = await puppeteer.launch({headless: true, executablePath, args: ['--no-sandbox', '--allow-file-access-from-files']});
  try {
    const page = await browser.newPage();
    await page.setViewport({width: 2016, height: 1176, deviceScaleFactor: 1});
    await page.goto(pathToFileURL(htmlPath).href, {waitUntil: 'networkidle0'});
    let imageRequests;
    try { imageRequests=await prepareImageGeometry(page); }
    catch(error){fs.writeFileSync(path.join(previewDir,'image-geometry.json'),JSON.stringify({status:'needs_review',error:error.message},null,2));throw error;}
    const report = await auditLayout(page);
    const reportPath = path.join(previewDir, 'layout-review.json');
    const previewStage = path.join(previewDir, report.status === 'passed' ? 'preflight' : 'needs-review');
    fs.mkdirSync(previewStage, {recursive: true});
    // A shorter retry must not republish obsolete slide PNGs from this staging
    // directory. Existing published previews are kept until export succeeds.
    for (const name of fs.readdirSync(previewStage)) if (name.toLowerCase().endsWith('.png')) fs.unlinkSync(path.join(previewStage, name));
    const slides = await page.$$('.slide');
    for (let index = 0; index < slides.length; index += 1) {
      const declaredId = await slides[index].evaluate(element => element.getAttribute('data-slide-id'));
      const slideId = declaredId && /^[A-Za-z0-9_-]+$/.test(declaredId) ? declaredId : `slide-${String(index + 1).padStart(2, '0')}`;
      await slides[index].screenshot({path: path.join(previewStage, `${slideId}.png`)});
    }
    report.preview_directory = previewStage;
    fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
    if (report.status !== 'passed') return {status: 'needs_review', report: reportPath, previews: previewStage, issue_count: report.issues.length, advisory_count: report.advisories.length, pptx_written: false};
    if (auditOnly) return {status: 'passed', check: 'browser_dom_preflight_only', report: reportPath, previews: previewStage, advisory_count: report.advisories.length, pptx_written: false};
    // No original output or published preview is changed before this gate passes.
    const {exportHtmlToPptx} = await import('dom-to-pptx/node');
    let exportPath=htmlPath;
    if(imageRequests.length){
      exportPath=path.join(previewDir,'image-geometry-export.html');
      const prepared=await page.evaluate(()=>{const copy=document.documentElement.cloneNode(true);const base=document.createElement('base');base.href=document.baseURI;copy.querySelector('head').prepend(base);return '<!doctype html>'+copy.outerHTML;});
      fs.writeFileSync(exportPath,prepared);
    }
    const buffer = await exportHtmlToPptx(exportPath, {
      selector: '.slide', injectBundle: true, browserWidth: 2016, browserHeight: 1176,
      pptxOptions: {width: SLIDE_INCHES.width, height: SLIDE_INCHES.height, autoEmbedFonts: false},
    });
    fs.mkdirSync(path.dirname(pptxPath), {recursive: true});
    const temporaryPptx = `${pptxPath}.${process.pid}.tmp`;
    const patched=await imageGeometry.patchPackage(buffer,imageRequests);
    fs.writeFileSync(path.join(previewDir,'image-geometry.json'),JSON.stringify({schema_version:1,status:'passed',images:patched.receipts},null,2));
    fs.writeFileSync(temporaryPptx, patched.buffer);
    fs.renameSync(temporaryPptx, pptxPath);
    for (const name of fs.readdirSync(previewDir)) if (name.toLowerCase().endsWith('.png')) fs.unlinkSync(path.join(previewDir, name));
    for (const name of fs.readdirSync(previewStage)) if (name.toLowerCase().endsWith('.png')) fs.copyFileSync(path.join(previewStage, name), path.join(previewDir, name));
    return {status: 'ok', slides: slides.length, pptx: pptxPath, previews: previewDir, layout_review: reportPath, advisory_count: report.advisories.length};
  } finally {
    await browser.close();
  }
}

if (process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url) {
  const [htmlInput, pptxOutput, previewOutput, ...flags] = process.argv.slice(2);
  if (!htmlInput || !pptxOutput || !previewOutput) {
    console.error('Usage: node render.mjs <deck.html> <output.pptx> <preview-dir> [--audit-only]');
    process.exitCode = 2;
  } else {
    try {
      const result = await render(htmlInput, pptxOutput, previewOutput, {auditOnly: flags.includes('--audit-only')});
      console.log(JSON.stringify(result));
      if (result.status === 'needs_review') process.exitCode = 3;
    } catch (error) {
      console.error(JSON.stringify({status: 'error', error: error.message}));
      process.exitCode = 1;
    }
  }
}
