/* Studio image geometry contract shared by native, HTML and template routes.
 * Original media remain embedded; cropping/masks/borders are editable OOXML.
 */
const crypto = require('node:crypto');
const path = require('node:path');
const JSZip = require('jszip');
const {DOMParser, XMLSerializer} = require('@xmldom/xmldom');
const imageSize = require('image-size');
const A = 'http://schemas.openxmlformats.org/drawingml/2006/main';
const P = 'http://schemas.openxmlformats.org/presentationml/2006/main';
const R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships';
const EMU = 914400;
function check(ok, message) { if (!ok) throw new Error(`image geometry: ${message}`); }
function finite(value, name, min = 0, max = Infinity) {
  check(typeof value === 'number' && Number.isFinite(value) && value >= min && value <= max, `${name} must be finite in ${min}..${max}`);
  return value;
}
function normalizeGeometry(value = {}) {
  check(value && typeof value === 'object' && !Array.isArray(value), 'geometry must be an object');
  const allowed = ['fit', 'mask', 'radius', 'position', 'crop', 'allow_crop', 'crop_reason', 'border'];
  check(Object.keys(value).every(key => allowed.includes(key)), 'unknown geometry field');
  const fit = value.fit ?? 'contain', mask = value.mask ?? 'rect';
  check(['contain','cover'].includes(fit), 'fit must be contain or cover; stretching is not supported');
  check(['rect','roundRect','ellipse'].includes(mask), 'mask must be rect, roundRect or ellipse');
  const radius = value.radius ?? (mask === 'roundRect' ? 0.12 : 0);
  finite(radius, 'radius (fraction of shorter frame side)', 0, 0.5);
  check(mask === 'roundRect' || radius === 0, 'radius only applies to roundRect');
  const position = value.position ?? {x:0.5,y:0.5};
  check(position && Object.keys(position).sort().join(',') === 'x,y', 'position requires x and y fractions');
  finite(position.x, 'position.x', 0, 1); finite(position.y, 'position.y', 0, 1);
  const crop = value.crop ?? {left:0,top:0,right:0,bottom:0};
  check(crop && Object.keys(crop).sort().join(',') === 'bottom,left,right,top', 'crop requires left/top/right/bottom fractions');
  for (const side of Object.keys(crop)) finite(crop[side], `crop.${side}`, 0, 0.99999);
  check(crop.left + crop.right < 1 && crop.top + crop.bottom < 1, 'crop must retain positive image area');
  const hidesSource = fit === 'cover' || mask !== 'rect' || Object.values(crop).some(v => v > 0);
  check(!hidesSource || (value.allow_crop === true && typeof value.crop_reason === 'string' && value.crop_reason.trim()),
    'cover, source cropping and nonrectangular masks require allow_crop:true and a crop_reason; preserve evidence otherwise');
  let border = null;
  if (value.border != null) {
    check(Object.keys(value.border).sort().join(',') === 'color,width_pt', 'border requires color and width_pt');
    check(/^[0-9a-fA-F]{6}$/.test(value.border.color), 'border color must be six hex digits');
    finite(value.border.width_pt, 'border.width_pt', 0, 24);
    border = {color:value.border.color.toUpperCase(), width_pt:value.border.width_pt};
  }
  return {fit,mask,radius,position:{...position},crop:{...crop},allow_crop:hidesSource,
    crop_reason:hidesSource ? value.crop_reason.trim() : '',border};
}
function planGeometry(source, box, value = {}) {
  const geometry = normalizeGeometry(value);
  finite(source.width, 'source.width', 1); finite(source.height, 'source.height', 1);
  // Existing templates may deliberately bleed beyond the slide. Visibility and
  // audience context belong to layout QA, not this image-local fit operation.
  for (const key of ['x','y']) finite(box[key], `box.${key}`, -Infinity);
  for (const key of ['w','h']) finite(box[key], `box.${key}`, Number.EPSILON);
  const crop = {...geometry.crop};
  const sw = source.width * (1-crop.left-crop.right), sh = source.height * (1-crop.top-crop.bottom);
  const sourceAspect = sw / sh, targetAspect = box.w / box.h;
  check(geometry.fit!=='contain' || geometry.mask==='rect' || Math.abs(sourceAspect-targetAspect)/Math.max(sourceAspect,targetAspect)<1e-6,
    'contain with a nonrectangular mask and a different aspect ratio leaves rectangular image edges inside the frame; use cover for an authorized photo crop, or rect to preserve the full evidence');
  const fill = {left:0,top:0,right:0,bottom:0};
  if (geometry.fit === 'cover') {
    if (sourceAspect > targetAspect) {
      const extra = (1-targetAspect/sourceAspect)*(1-crop.left-crop.right);
      crop.left += extra*geometry.position.x; crop.right += extra*(1-geometry.position.x);
    } else {
      const extra = (1-sourceAspect/targetAspect)*(1-crop.top-crop.bottom);
      crop.top += extra*geometry.position.y; crop.bottom += extra*(1-geometry.position.y);
    }
  } else if (sourceAspect > targetAspect) {
    const extra = 1-targetAspect/sourceAspect;
    fill.top = extra*geometry.position.y; fill.bottom = extra*(1-geometry.position.y);
  } else {
    const extra = 1-sourceAspect/targetAspect;
    fill.left = extra*geometry.position.x; fill.right = extra*(1-geometry.position.x);
  }
  return {schema_version:1,geometry,source:{width:source.width,height:source.height,...(source.type?{type:source.type}:{})},box:{...box},crop,fill,
    media_policy:'original bytes retained; native picture crop/mask/border remain editable'};
}
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
function imageInfo(data) {
  const result = imageSize(data);
  check(['png','jpg','jpeg','svg'].includes(result.type), 'only PNG, JPEG and SVG are supported');
  check(!result.orientation || result.orientation === 1, 'EXIF-oriented image requires explicit orientation normalization before layout');
  return {width:result.width,height:result.height,type:result.type};
}
function imageData(uri) {
  check(typeof uri === 'string' && /^data:image\/(png|jpeg|svg\+xml);base64,/.test(uri), 'self-contained PNG/JPEG/SVG data is required');
  const data = Buffer.from(uri.split(',')[1], 'base64');
  check(data.length > 0 && data.length <= 100*1024*1024, 'image is empty or exceeds 100 MiB');
  return {data,...imageInfo(data)};
}
function parseXml(xml) {
  const errors = [];
  const doc = new DOMParser({errorHandler:{warning(){},error:m=>errors.push(m),fatalError:m=>errors.push(m)}}).parseFromString(xml,'application/xml');
  check(!errors.length && doc.documentElement, 'invalid XML'); return doc;
}
const children = (node, ns, name) => [...Array.from(node.childNodes || [])].filter(n=>n.nodeType===1 && n.namespaceURI===ns && n.localName===name);
function one(node, ns, name) { const found = children(node,ns,name); check(found.length===1,`expected one ${name}`); return found[0]; }
function make(doc, name, attrs={}) { const node=doc.createElementNS(A,`a:${name}`); for(const [key,val] of Object.entries(attrs))node.setAttribute(key,String(val)); return node; }
function replaceChildren(parent, names, replacements) {
  const old=Array.from(parent.childNodes).filter(n=>n.namespaceURI===A && names.includes(n.localName));
  const anchor=old[0] || null; for(const item of replacements)parent.insertBefore(item,anchor); for(const item of old)parent.removeChild(item);
}
function applyPictureGeometry(pic, plan) {
  const doc=pic.ownerDocument, sp=one(pic,P,'spPr'), blip=one(pic,P,'blipFill');
  const xfrm=one(sp,A,'xfrm');
  // Crop, fill and shape geometry use local coordinates. Preserve existing
  // native rotation and flips so the picture and its border rotate together.
  const off=one(xfrm,A,'off'), ext=one(xfrm,A,'ext');
  off.setAttribute('x',Math.round(plan.box.x*EMU));off.setAttribute('y',Math.round(plan.box.y*EMU));
  ext.setAttribute('cx',Math.round(plan.box.w*EMU));ext.setAttribute('cy',Math.round(plan.box.h*EMU));
  const percentages = object => Object.fromEntries(Object.entries(object).map(([key,v])=>[({left:'l',top:'t',right:'r',bottom:'b'})[key],Math.round(v*100000)]));
  const src=make(doc,'srcRect',percentages(plan.crop)), stretch=make(doc,'stretch');
  stretch.appendChild(make(doc,'fillRect',percentages(plan.fill)));
  replaceChildren(blip,['srcRect','stretch','tile'],[src,stretch]);
  const geom=make(doc,'prstGeom',{prst:plan.geometry.mask}), av=make(doc,'avLst');geom.appendChild(av);
  if(plan.geometry.mask==='roundRect')av.appendChild(make(doc,'gd',{name:'adj',fmla:`val ${Math.round(plan.geometry.radius*100000)}`}));
  replaceChildren(sp,['prstGeom','custGeom'],[geom]);
  const line=make(doc,'ln',{w:Math.round((plan.geometry.border?.width_pt||0)*12700)});
  if(plan.geometry.border?.width_pt){const fill=make(doc,'solidFill');fill.appendChild(make(doc,'srgbClr',{val:plan.geometry.border.color}));line.appendChild(fill);}
  else line.appendChild(make(doc,'noFill'));
  const oldLines=children(sp,A,'ln');for(const old of oldLines)sp.removeChild(old);
  const afterGeometry=geom.nextSibling;sp.insertBefore(line,afterGeometry);
}
function pictureBox(pic){const x=one(one(pic,P,'spPr'),A,'xfrm'),o=one(x,A,'off'),e=one(x,A,'ext');return{x:Number(o.getAttribute('x'))/EMU,y:Number(o.getAttribute('y'))/EMU,w:Number(e.getAttribute('cx'))/EMU,h:Number(e.getAttribute('cy'))/EMU};}
function targetPicture(doc,target) {
  const pictures=Array.from(doc.getElementsByTagNameNS(P,'pic'));
  const matches=pictures.filter(pic=>{
    const prop=one(one(pic,P,'nvPicPr'),P,'cNvPr');
    if(target.shape_id!=null)return prop.getAttribute('id')===String(target.shape_id);
    if(target.object_name)return prop.getAttribute('name')===target.object_name;
    if(target.match_box){const b=pictureBox(pic);return ['x','y','w','h'].every(k=>Math.abs(b[k]-target.match_box[k])<0.003);}
    return false;
  });
  check(matches.length===1,`picture target matched ${matches.length} objects; select an exact unambiguous picture`);
  const pic=matches[0];check(pic.parentNode.localName==='spTree','grouped pictures require a separate explicit group transform');return pic;
}
async function patchPackage(buffer, requests) {
  if(!requests.length)return{buffer,receipts:[]};
  const zip=await JSZip.loadAsync(buffer), receipts=[], seen=new Set();
  for(const request of requests){
    check(Number.isInteger(request.slide)&&request.slide>=1,'slide must be a positive number');
    const part=`ppt/slides/slide${request.slide}.xml`, file=zip.file(part);check(file,'slide not found');
    const doc=parseXml(await file.async('string')),pic=targetPicture(doc,request);
    const shapeId=one(one(pic,P,'nvPicPr'),P,'cNvPr').getAttribute('id');
    check(!seen.has(`${part}:${shapeId}`),'duplicate geometry operation on the same picture');seen.add(`${part}:${shapeId}`);
    const relPart=`ppt/slides/_rels/slide${request.slide}.xml.rels`, relFile=zip.file(relPart);check(relFile,'image relationship missing');
    const relDoc=parseXml(await relFile.async('string')), blip=one(one(pic,P,'blipFill'),A,'blip');
    const rels=Array.from(relDoc.documentElement.childNodes).filter(n=>n.nodeType===1);
    const vectors=Array.from(blip.getElementsByTagNameNS('http://schemas.microsoft.com/office/drawing/2016/SVG/main','svgBlip'));
    check(vectors.length<=1,'ambiguous SVG source');
    const sourceId=vectors.length?vectors[0].getAttributeNS(R,'embed'):blip.getAttributeNS(R,'embed');
    const rel=rels.find(n=>n.getAttribute('Id')===sourceId);
    check(rel && rel.getAttribute('TargetMode')!=='External','embedded image relationship required');
    const mediaPath=path.posix.normalize(path.posix.join('ppt/slides',rel.getAttribute('Target')));
    check(mediaPath.startsWith('ppt/media/'),'image relationship outside ppt/media');
    const media=zip.file(mediaPath);check(media,'image bytes missing');
    let data=await media.async('nodebuffer');
    // HTML exporters rasterize CSS clipping. Replace only this picture's media
    // relationship with its captured original, never a shared media member.
    if(request.original_data){
      data=imageData(request.original_data).data;
      const info=imageInfo(data), extension=info.type==='jpg'?'jpeg':info.type;
      const name=`studio-original-${hash(data)}.${extension}`, target=`../media/${name}`;
      zip.file(`ppt/media/${name}`,data);
      const id=`rIdStudioImage${shapeId}`;check(!rels.some(r=>r.getAttribute('Id')===id),'reserved image relationship already exists');
      const item=relDoc.createElementNS(relDoc.documentElement.namespaceURI,'Relationship');
      item.setAttribute('Id',id);item.setAttribute('Type',`${R}/image`);item.setAttribute('Target',target);relDoc.documentElement.appendChild(item);
      if(extension==='svg'){
        // Keep the exporter's PNG fallback; attach the untouched vector as the
        // Office SVG source. Never put an SVG directly in the raster blip slot.
        let list=children(blip,A,'extLst')[0];if(!list){list=make(doc,'extLst');blip.appendChild(list);}
        for(const old of Array.from(list.childNodes))if(old.nodeType===1&&old.getAttribute('uri')==='{96DAC541-7B7A-43D3-8B79-37D633B846F1}')list.removeChild(old);
        const ext=make(doc,'ext',{uri:'{96DAC541-7B7A-43D3-8B79-37D633B846F1}'});
        const svg=doc.createElementNS('http://schemas.microsoft.com/office/drawing/2016/SVG/main','asvg:svgBlip');svg.setAttributeNS(R,'r:embed',id);ext.appendChild(svg);list.appendChild(ext);
      }else blip.setAttributeNS(R,'r:embed',id);
      zip.file(relPart,new XMLSerializer().serializeToString(relDoc));
      const ctDoc=parseXml(await zip.file('[Content_Types].xml').async('string'));
      if(!Array.from(ctDoc.documentElement.childNodes).some(n=>n.nodeType===1&&n.getAttribute('Extension')===extension)){
        const type=ctDoc.createElementNS(ctDoc.documentElement.namespaceURI,'Default');type.setAttribute('Extension',extension);type.setAttribute('ContentType',extension==='svg'?'image/svg+xml':`image/${extension}`);ctDoc.documentElement.appendChild(type);zip.file('[Content_Types].xml',new XMLSerializer().serializeToString(ctDoc));
      }
    }
    const sourceHash=hash(data);if(request.source_sha256)check(sourceHash===request.source_sha256,'source image hash changed');
    const info=imageInfo(data),plan=planGeometry(info,request.box||pictureBox(pic),request.geometry);
    applyPictureGeometry(pic,plan);zip.file(part,new XMLSerializer().serializeToString(doc));
    receipts.push({slide:request.slide,shape_id:Number(shapeId),source_sha256:sourceHash,...plan});
  }
  return{buffer:await zip.generateAsync({type:'nodebuffer',compression:'DEFLATE'}),receipts};
}
function previewSvg(uri, plan, width, height, ptPerPx=0.5) {
  // Preview derives from the same crop/fill plan as OOXML. This derivative is
  // for browser display only; package export restores the original image.
  const c=plan.crop,f=plan.fill,g=plan.geometry;
  const w=width*(1-f.left-f.right)/(1-c.left-c.right),h=height*(1-f.top-f.bottom)/(1-c.top-c.bottom);
  const x=width*f.left-w*c.left,y=height*f.top-h*c.top;
  const stroke=g.border?.width_pt ? g.border.width_pt/ptPerPx : 0;
  const inset=stroke/2, fw=Math.max(0,width-stroke),fh=Math.max(0,height-stroke);
  const contour=(border=false)=>g.mask==='ellipse'
    ? `<ellipse cx="${width/2}" cy="${height/2}" rx="${(border?fw:width)/2}" ry="${(border?fh:height)/2}"/>`
    : `<rect x="${border?inset:0}" y="${border?inset:0}" width="${border?fw:width}" height="${border?fh:height}" rx="${g.mask==='roundRect'?g.radius*Math.min(width,height):0}"/>`;
  const border=stroke ? `<g fill="none" stroke="#${g.border.color}" stroke-width="${stroke}">${contour(true)}</g>`:'';
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><clipPath id="image-mask">${contour()}</clipPath></defs><g clip-path="url(#image-mask)"><image href="${uri}" x="${x}" y="${y}" width="${w}" height="${h}" preserveAspectRatio="none"/></g>${border}</svg>`;
}
module.exports={normalizeGeometry,planGeometry,imageInfo,imageData,parseXml,applyPictureGeometry,pictureBox,targetPicture,patchPackage,previewSvg,hash};
