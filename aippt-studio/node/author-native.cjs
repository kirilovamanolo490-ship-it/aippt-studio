/* Studio-owned JSON bridge; upstream PptxGenJS and layout helpers stay untouched. */
const fs = require('node:fs');
const PptxGenJS = require('pptxgenjs');
const imageGeometry = require('./image-geometry.cjs');
const [requestPath, output, helperPath] = process.argv.slice(2);
const spec = JSON.parse(fs.readFileSync(requestPath, 'utf8'));
const defaultFont = process.platform === 'darwin' ? 'PingFang SC' : 'Microsoft YaHei';
const authoringProfile = spec.authoring_profile || {};
const bodyFontPt = Number.isFinite(authoringProfile.body_font_pt) ? authoringProfile.body_font_pt : 24;
const tableFontPt = Number.isFinite(authoringProfile.table_font_pt) ? authoringProfile.table_font_pt : bodyFontPt;
const chartLabelFontPt = Number.isFinite(authoringProfile.chart_label_font_pt) ? authoringProfile.chart_label_font_pt : 18;
const chartLegendFontPt = Number.isFinite(authoringProfile.chart_legend_font_pt) ? authoringProfile.chart_legend_font_pt : chartLabelFontPt;
const roleFontPt = role => ({
  cover_title: authoringProfile.cover_title_font_pt,
  title: authoringProfile.title_font_pt,
  body: bodyFontPt,
  caption: authoringProfile.caption_font_pt,
  statement: authoringProfile.statement_font_pt,
})[role] || bodyFontPt;
const roleFontFace = role => ({
  cover_title: authoringProfile.heading_font_face,
  title: authoringProfile.heading_font_face,
  body: authoringProfile.body_font_face,
  caption: authoringProfile.label_font_face,
  statement: authoringProfile.body_font_face,
})[role] || spec.font_family || defaultFont;
const pptx = new PptxGenJS();
function check(condition, message) { if (!condition) throw new Error(message); }
const pageSize = spec.page_size || {width: 40 / 3, height: 7.5};
for (const key of ['width', 'height']) {
  check(typeof pageSize[key] === 'number' && Number.isFinite(pageSize[key]) &&
    pageSize[key] > 0 && pageSize[key] <= 56, `page_size.${key} must be a finite inch value in (0, 56]`);
}
if (spec.page_size) {
  pptx.defineLayout({name: 'STUDIO_CUSTOM', width: pageSize.width, height: pageSize.height});
  pptx.layout = 'STUDIO_CUSTOM';
} else pptx.layout = 'LAYOUT_WIDE';
pptx.author = 'AI PPT Studio';
pptx.subject = 'Editable authoring candidate';
pptx.title = spec.title || 'Presentation';
pptx.lang = spec.language || 'zh-CN';
const {warnIfSlideHasOverlaps, warnIfSlideElementsOutOfBounds} = require(helperPath);
const warnings = [];
const imageRequests = [];
const log = console.log;
console.warn = (...args) => warnings.push(args.join(' '));
console.error = (...args) => warnings.push(args.join(' '));
console.log = (...args) => warnings.push(args.join(' '));
check(Array.isArray(spec.slides) && spec.slides.length > 0 && spec.slides.length <= 200, 'slides must contain 1-200 pages');
for (const [index, page] of spec.slides.entries()) {
  const slide = pptx.addSlide();
  slide.background = {color: page.background || 'FFFFFF'};
  check(Array.isArray(page.elements) && page.elements.length <= 550, 'elements must be an array of at most 550 entries');
  for (const [elementIndex, el] of page.elements.entries()) {
    const o = {...el.options};
    for (const key of ['x','y','w','h']) check(typeof o[key] === 'number' && Number.isFinite(o[key]), `element needs finite ${key}`);
    check(o.x >= 0 && o.y >= 0 && o.w > 0 && o.h > 0 &&
      o.x + o.w <= pageSize.width + 0.000001 && o.y + o.h <= pageSize.height + 0.000001,
      `page ${index+1}: element outside canvas`);
    if (el.type === 'text') {
      check(typeof el.text === 'string', 'text must be a string');
      check(el.style_role === undefined || ['cover_title','title','body','caption','statement'].includes(el.style_role), 'unsupported text style_role');
      slide.addText(el.text, {fontFace:roleFontFace(el.style_role || 'body'), fontSize:roleFontPt(el.style_role || 'body'), margin:0, breakLine:false, ...o});
    } else if (el.type === 'shape') {
      check([
        'rect','roundRect','ellipse','line','chevron','triangle','arc',
        'star5','wave','cloud','pieWedge','ribbon','wedgeRoundRectCallout','hexagon'
      ].includes(el.shape), 'unsupported shape');
      slide.addShape(pptx.ShapeType[el.shape], o);
    } else if (el.type === 'chart') {
      check(['bar','line','area','pie','doughnut','scatter','radar'].includes(el.chart), 'unsupported chart');
      check(Array.isArray(el.data) && el.data.length > 0, 'chart data is required');
      slide.addChart(pptx.ChartType[el.chart], el.data, {catAxisLabelFontSize:chartLabelFontPt, valAxisLabelFontSize:chartLabelFontPt, dataLabelFontSize:chartLabelFontPt, legendFontSize:chartLegendFontPt, ...o});
    } else if (el.type === 'table') {
      check(Array.isArray(el.rows) && el.rows.length > 0, 'table rows are required');
      slide.addTable(el.rows, {fontFace:authoringProfile.body_font_face || spec.font_family || defaultFont, fontSize:tableFontPt, margin:8, ...o});
    } else if (el.type === 'image') {
      check(typeof el.data === 'string' && /^data:image\/(png|jpeg|svg\+xml);base64,/.test(el.data), 'images require self-contained base64 image data');
      check(!o.sizing && !o.rounding && !o.line, 'image sizing/rounding/line must use the shared geometry fit/mask/border contract');
      const data = imageGeometry.imageData(el.data);
      const plan = imageGeometry.planGeometry(data, {x:o.x,y:o.y,w:o.w,h:o.h}, el.geometry);
      const objectName = `studio-image-${index+1}-${elementIndex+1}`;
      slide.addImage({...o, objectName, data:el.data});
      imageRequests.push({slide:index+1,object_name:objectName,geometry:plan.geometry,
        original_data:el.data,box:plan.box});
      for(const frame of page.elements.filter(item=>item.type==='shape' && ['roundRect','ellipse'].includes(item.shape))){
        if(['x','y','w','h'].every(key=>Math.abs((frame.options?.[key]??Infinity)-o[key])<0.02) && (!el.geometry || frame.shape!==plan.geometry.mask))
          warnings.push(`page ${index+1} image ${elementIndex+1}: independent ${frame.shape} frame and picture have unbound contours; apply geometry.mask/border on the picture itself or explicitly resolve this frame`);
      }
    } else throw new Error(`unsupported element type: ${el.type}`);
  }
  // Notes are added later through Studio's notes sanitization, not arbitrary JSON.
  // PptxGenJS 4 stores table geometry in EMU, unlike the other objects.
  // Supply a normalized inspection view; never mutate the actual slide.
  const inspection = Object.create(slide);
  const normalizedObjects = slide._slideObjects.map((obj, objectIndex) => {
    // PptxGenJS internally represents many AutoShapes as text containers.
    // Preserve the authored scene element kind so decorative layers are not
    // misreported as severe text overlaps by the geometry helper.
    const authoredType = page.elements[objectIndex]?.type;
    const normalized = {...obj, type:authoredType || obj.type || obj._type};
    if (obj._type === 'table') {
      normalized.options = {...obj.options};
      for (const k of ['x','y','w','h']) normalized.options[k] /= 914400;
    }
    return normalized;
  });
  inspection._slideObjects = normalizedObjects;
  const deckInspection = Object.create(pptx);
  deckInspection._slides = [...pptx._slides];
  // Only authored text/table objects participate in overlap QA. Layering text
  // over deliberate stage, ribbon, star, image and illustration shapes is an
  // intentional presentation technique, not a collision defect.
  const overlapInspection = Object.create(slide);
  overlapInspection._slideObjects = normalizedObjects.filter((_, objectIndex) =>
    ['text', 'table'].includes(page.elements[objectIndex]?.type)
  );
  deckInspection._slides[index] = overlapInspection;
  warnIfSlideHasOverlaps(overlapInspection, deckInspection);
  deckInspection._slides[index] = inspection;
  warnIfSlideElementsOutOfBounds(inspection, deckInspection);
}
console.log = log;
const qaPath = require('node:path').join(require('node:path').dirname(output), 'native-qa.json');
fs.writeFileSync(qaPath, JSON.stringify({status:warnings.length ? 'review_required' : 'passed', slides:spec.slides.length, warnings, pptx_editability:'native text/shapes/charts/tables; images remain images'}, null, 2));
// Geometry warnings need human review; do not claim a clean production build.
pptx.write({outputType:'nodebuffer'}).then(async buffer => {
  const result=await imageGeometry.patchPackage(buffer,imageRequests);
  fs.writeFileSync(require('node:path').join(require('node:path').dirname(output),'image-geometry.json'),JSON.stringify({schema_version:1,images:result.receipts},null,2));
  const temporary=`${output}.${process.pid}.tmp`;
  fs.writeFileSync(temporary,result.buffer);fs.renameSync(temporary,output);
  log(JSON.stringify({output,warnings:warnings.length}));
}).catch(e => {process.stderr.write(e.message); process.exitCode=1;});
