import path from 'node:path';
import fs from 'node:fs';
import AutomizerModule from 'pptx-automizer';
import imageGeometry from './image-geometry.cjs';

const Automizer = AutomizerModule.Automizer || AutomizerModule.default || AutomizerModule;
const modify = AutomizerModule.modify;

const [templatePath, mappingPath, outputPath] = process.argv.slice(2);
if (!templatePath || !mappingPath || !outputPath) {
  throw new Error('Usage: node template_adapter.mjs <template.pptx> <mapping.json> <output.pptx>');
}
const templateDir = path.dirname(templatePath);
const outputDir = path.dirname(outputPath);
fs.mkdirSync(outputDir, { recursive: true });
const mapping = JSON.parse(fs.readFileSync(mappingPath, 'utf8'));
const templateName = path.basename(templatePath);
const automizer = new Automizer({
  templateDir,
  outputDir,
  autoImportSlideMasters: true,
  removeExistingSlides: true,
  cleanup: false,
  cleanupPlaceholders: false,
  verbosity: 0,
});
const pres = automizer.loadRoot(templateName).load(templateName, 'source');
for (const slideSpec of mapping.slides || []) {
  pres.addSlide('source', Number(slideSpec.source_slide), async (slide) => {
    const replacements = Object.entries(slideSpec.replacements || {}).map(([replace, text]) => ({
      replace,
      by: { text: String(text ?? '') },
    }));
    if (replacements.length) {
      for (const elementId of await slide.getAllTextElementIds()) {
        slide.modifyElement(elementId, modify.replaceText(replacements, { openingTag: '{{', closingTag: '}}' }));
      }
    }
    // An explicit page map can target ordinary library text boxes that lack
    // {{...}} placeholders. Python verifies each source shape name and its
    // original text before this source-bound edit is allowed to execute.
    for (const [field, target] of Object.entries(slideSpec.shape_targets || {})) {
      slide.modifyElement(target.shape_name, modify.setText(String(slideSpec.replacements?.[field] ?? '')));
    }
  });
}
// Existing pictures remain untouched unless explicitly selected by shape ID
// and the hash of their embedded source image. Never infer a frame by position.
const imageRequests=[];
for(const [index,slideSpec] of (mapping.slides||[]).entries()){
  for(const operation of (slideSpec.image_geometry||[])){
    if(!Number.isInteger(operation.shape_id)||!/^([a-f0-9]{64})$/.test(operation.source_sha256||''))
      throw new Error('template image_geometry requires exact shape_id and source_sha256');
    if(!operation.geometry || typeof operation.geometry!=='object')
      throw new Error('template image_geometry requires an explicit geometry object');
    imageRequests.push({slide:index+1,shape_id:operation.shape_id,source_sha256:operation.source_sha256,
      geometry:operation.geometry,box:operation.box});
  }
}
const stagingName=`.template-${process.pid}-${Date.now()}.pptx`;
try{
  await pres.write(stagingName);
  const result=await imageGeometry.patchPackage(fs.readFileSync(path.join(outputDir,stagingName)),imageRequests);
  fs.writeFileSync(path.join(outputDir,'image-geometry.json'),JSON.stringify({schema_version:1,images:result.receipts},null,2));
  fs.writeFileSync(path.join(outputDir,stagingName),result.buffer);
  fs.renameSync(path.join(outputDir,stagingName),outputPath);
}finally{if(fs.existsSync(path.join(outputDir,stagingName)))fs.unlinkSync(path.join(outputDir,stagingName));}
