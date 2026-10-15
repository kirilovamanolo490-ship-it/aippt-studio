/* Historical assignment reconstruction, not a generic Studio output route.
 * Its fixed brand names, English copy and chapter numerals reproduce that
 * assignment's reference pages. Do not reuse them as defaults for new work;
 * use the native scene or template adapter with the current audience copy.
 * Historical source decks and explicitly requested reference numbering stay
 * intact; this file is retained for reproducing its original assignment.
 */
import fs from "node:fs";
import path from "node:path";
import PptxGenJS from "pptxgenjs";

const specPath = process.argv[2];
if (!specPath) throw new Error("usage: node render-assignment.mjs <spec.json>");
const spec = JSON.parse(fs.readFileSync(specPath, "utf8"));
const outputPath = process.argv[3] || spec.output;
const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";
pptx.author = spec.author || "AI PPT Studio";
pptx.company = "AI PPT Studio";
pptx.subject = "Structured PowerPoint assignment";
pptx.title = spec.title;
pptx.lang = "zh-CN";
const F = {
  heavy: spec.typography?.heavy || "思源黑体 Heavy",
  normal: spec.typography?.normal || "思源黑体 Normal",
  light: spec.typography?.light || "思源黑体 Light",
  display: spec.typography?.display || "云峰飞云体",
};
pptx.theme = {
  headFontFace: F.heavy,
  bodyFontFace: F.normal,
  lang: "zh-CN",
};

const W = 13.333;
const H = 7.5;
const C = {
  ink: "142B3B",
  muted: "607889",
  paper: "F6F8FA",
  white: "FFFFFF",
  blue: "1C53CE",
  gold: "C8A766",
};

function image(slide, imagePath, x, y, w, h, type = "cover", transparency = 0) {
  slide.addImage({ path: imagePath, x, y, sizing: { type, w, h }, transparency });
}

function xinlaiMark(slide, x, y, dark = false) {
  const blue = dark ? C.white : "245AAA";
  slide.addShape(pptx.ShapeType.ellipse, { x, y, w: 0.48, h: 0.48, fill: { color: blue }, line: { color: blue } });
  slide.addText("LX", { x: x + 0.08, y: y + 0.07, w: 0.48, h: 0.3, fontFace: "Arial", fontSize: 18, bold: true, color: dark ? "245AAA" : C.white, margin: 0 });
  slide.addText("INLAI", { x: x + 0.54, y: y + 0.08, w: 1.15, h: 0.28, fontFace: "Arial", fontSize: 15, bold: true, color: dark ? C.white : C.gold, margin: 0 });
}

for (const master of spec.masters) {
  pptx.defineSlideMaster({
    title: master.name,
    background: { color: master.background },
    objects: [
      { rect: { x: 0, y: 0, w: 0.16, h: H, fill: { color: master.primary }, line: { color: master.primary } } },
      { line: { x: 0.7, y: 6.95, w: 11.95, h: 0, line: { color: "D6E1E8", width: 1 } } },
      { image: { path: master.logo, x: 10.75, y: 0.3, w: master.logoWidth, h: master.logoHeight, altText: `${master.name} LOGO` } },
      { text: { text: master.name, options: { x: 0.72, y: 7.02, w: 2.0, h: 0.22, fontFace: F.normal, fontSize: 9, color: "7A8E9D", margin: 0 } } },
    ],
    slideNumber: { x: 12.15, y: 7.0, w: 0.5, h: 0.22, fontFace: F.normal, fontSize: 9, color: "7A8E9D", align: "right", margin: 0 },
  });
}

for (const [index, master] of spec.masters.entries()) {
  const slide = pptx.addSlide(master.name);
  slide.background = { color: master.background };
  slide.addText(`BRAND SYSTEM / 0${index + 1}`, { x: 0.85, y: 0.62, w: 4.2, h: 0.3, fontFace: F.normal, fontSize: 13, color: master.primary, charSpacing: 1.5, margin: 0 });
  slide.addText(master.displayName, { x: 0.82, y: 1.28, w: 6.9, h: 0.78, fontFace: F.heavy, fontSize: 34, color: C.ink, margin: 0, breakLine: false });
  slide.addText("企业演示母版与配色方案", { x: 0.85, y: 2.18, w: 5.6, h: 0.42, fontFace: F.light, fontSize: 18, color: C.muted, margin: 0 });
  slide.addShape(pptx.ShapeType.rect, { x: 0.85, y: 3.05, w: 7.0, h: 2.55, fill: { color: C.white }, line: { color: "DCE5EA", width: 1 }, radius: 0.04 });
  slide.addText("建议色阶", { x: 1.18, y: 3.38, w: 1.4, h: 0.35, fontFace: F.heavy, fontSize: 15, color: C.ink, margin: 0 });
  master.palette.forEach((color, colorIndex) => {
    slide.addShape(pptx.ShapeType.rect, { x: 1.18 + colorIndex * 1.42, y: 4.02, w: 1.12, h: 0.74, fill: { color }, line: { color } });
    slide.addText(`#${color}`, { x: 1.18 + colorIndex * 1.42, y: 4.88, w: 1.12, h: 0.23, fontFace: F.normal, fontSize: 9, color: C.muted, align: "center", margin: 0 });
  });
  slide.addShape(pptx.ShapeType.rect, { x: 8.65, y: 1.48, w: 3.0, h: 4.18, fill: { color: master.primary }, line: { color: master.primary } });
  slide.addText("MASTER", { x: 8.98, y: 1.88, w: 2.2, h: 0.35, fontFace: F.heavy, fontSize: 13, color: master.onPrimary, charSpacing: 2, margin: 0 });
  slide.addText(master.name, { x: 8.95, y: 2.55, w: 2.25, h: 0.72, fontFace: F.heavy, fontSize: 30, color: master.onPrimary, margin: 0 });
  slide.addText("首版式右上角固定品牌 LOGO\n标题、正文与强调色均继承本母版", { x: 8.98, y: 3.6, w: 2.15, h: 1.05, fontFace: F.normal, fontSize: 14, color: master.onPrimary, breakLine: false, margin: 0, lineSpacingMultiple: 1.25 });
}

const a = spec.assignment;

{
  const slide = pptx.addSlide();
  slide.background = { color: "0B2B4A" };
  image(slide, a.building, 6.25, 0, 7.08, H, "cover");
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 7.12, h: H, fill: { color: "0B2B4A", transparency: 4 }, line: { color: "0B2B4A", transparency: 100 } });
  slide.addShape(pptx.ShapeType.line, { x: 0.88, y: 1.32, w: 1.2, h: 0, line: { color: C.gold, width: 4 } });
  xinlaiMark(slide, 0.88, 0.56, true);
  slide.addText("2023", { x: 0.86, y: 1.72, w: 2.2, h: 0.65, fontFace: F.normal, fontSize: 32, color: "9EC3E8", margin: 0 });
  slide.addText("设计部年终总结", { x: 0.82, y: 2.38, w: 5.25, h: 0.92, fontFace: F.heavy, fontSize: 41, color: C.white, margin: 0 });
  slide.addText("YEAR-END SUMMARY OF THE DESIGN DEPARTMENT", { x: 0.88, y: 3.48, w: 4.75, h: 0.35, fontFace: F.light, fontSize: 11, color: "9EC3E8", charSpacing: 1.4, margin: 0 });
  slide.addShape(pptx.ShapeType.line, { x: 0.88, y: 4.18, w: 4.6, h: 0, line: { color: "5F7E96", width: 1 } });
  slide.addText("汇报人：宋强", { x: 0.88, y: 5.45, w: 2.0, h: 0.38, fontFace: F.normal, fontSize: 15, color: C.white, margin: 0 });
  slide.addText("时间：202X.12.30", { x: 3.15, y: 5.45, w: 2.4, h: 0.38, fontFace: F.normal, fontSize: 15, color: C.gold, margin: 0 });
}

{
  const slide = pptx.addSlide();
  slide.background = { color: C.paper };
  image(slide, a.buildingCutout, 0, 0, 4.9, H, "cover");
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: 4.9, h: H, fill: { color: "143B63", transparency: 32 }, line: { color: "143B63", transparency: 100 } });
  slide.addText("CONTENTS", { x: 0.72, y: 5.95, w: 2.6, h: 0.36, fontFace: F.normal, fontSize: 15, color: C.white, charSpacing: 2.2, margin: 0 });
  xinlaiMark(slide, 10.82, 0.46, false);
  slide.addText("目录", { x: 5.62, y: 0.82, w: 2.4, h: 0.75, fontFace: F.heavy, fontSize: 35, color: C.ink, margin: 0 });
  slide.addText("围绕成果、方法与下一阶段计划展开", { x: 5.65, y: 1.65, w: 5.8, h: 0.38, fontFace: F.light, fontSize: 16, color: C.muted, margin: 0 });
  const items = ["工作概述", "完成情况", "经验收获", "未来计划"];
  items.forEach((item, i) => {
    const y = 2.35 + i * 0.93;
    slide.addText(`0${i + 1}`, { x: 5.62, y, w: 0.75, h: 0.43, fontFace: F.normal, fontSize: 18, color: C.gold, margin: 0 });
    slide.addText(item, { x: 6.5, y: y - 0.02, w: 3.5, h: 0.48, fontFace: F.heavy, fontSize: 22, color: C.ink, margin: 0 });
    slide.addShape(pptx.ShapeType.line, { x: 5.62, y: y + 0.58, w: 6.0, h: 0, line: { color: "D6E1E8", width: 1 } });
  });
}

{
  const slide = pptx.addSlide();
  image(slide, a.city, 0, 0, W, H, "cover");
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: H, fill: { color: "0F3D68", transparency: 18 }, line: { color: "0F3D68", transparency: 100 } });
  slide.addShape(pptx.ShapeType.rect, { x: 7.05, y: 0, w: 6.28, h: H, fill: { color: C.white, transparency: 5 }, line: { color: C.white, transparency: 100 } });
  slide.addText("PART", { x: 0.88, y: 1.22, w: 2.2, h: 0.55, fontFace: F.light, fontSize: 23, color: C.white, charSpacing: 2, margin: 0 });
  slide.addText("01", { x: 0.78, y: 2.0, w: 2.7, h: 1.35, fontFace: F.heavy, fontSize: 66, color: C.white, margin: 0 });
  slide.addShape(pptx.ShapeType.line, { x: 0.88, y: 3.72, w: 4.75, h: 0, line: { color: C.gold, width: 3 } });
  xinlaiMark(slide, 10.82, 0.48, false);
  slide.addText("工作概述", { x: 7.78, y: 2.55, w: 4.65, h: 0.9, fontFace: F.heavy, fontSize: 42, color: C.blue, align: "center", margin: 0 });
  slide.addText("OVERVIEW OF THE WORK", { x: 8.1, y: 3.58, w: 4.0, h: 0.32, fontFace: F.light, fontSize: 12, color: C.muted, charSpacing: 2.2, align: "center", margin: 0 });
}

{
  const slide = pptx.addSlide();
  image(slide, a.mountain, 0, 0, W, H, "cover");
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: H, fill: { color: "065E91", transparency: 30 }, line: { color: "065E91", transparency: 100 } });
  slide.addShape(pptx.ShapeType.rect, { x: 0, y: 0, w: W, h: 1.15, fill: { color: "082E50", transparency: 14 }, line: { color: "082E50", transparency: 100 } });
  xinlaiMark(slide, 0.82, 0.36, true);
  slide.addText("跨越巅峰  再创辉煌", { x: 1.15, y: 2.25, w: 11.0, h: 1.2, fontFace: F.display, fontSize: 47, color: C.white, align: "center", margin: 0, shadow: { type: "outer", color: "073455", blur: 3, angle: 45, distance: 2, opacity: 0.35 } });
  slide.addText("LEAPFROGGING THE PINNACLE · CREATE BRILLIANCE AGAIN", { x: 2.05, y: 3.62, w: 9.2, h: 0.34, fontFace: F.light, fontSize: 12, color: "D9F3FF", charSpacing: 1.8, align: "center", margin: 0 });
  slide.addShape(pptx.ShapeType.line, { x: 5.35, y: 4.35, w: 2.65, h: 0, line: { color: C.gold, width: 3 } });
  slide.addText("THANK YOU", { x: 5.15, y: 5.62, w: 3.0, h: 0.42, fontFace: F.normal, fontSize: 16, color: C.white, charSpacing: 3, align: "center", margin: 0 });
}

fs.mkdirSync(path.dirname(outputPath), { recursive: true });
await pptx.writeFile({ fileName: outputPath });
