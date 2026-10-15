import fs from 'node:fs';
import {fileURLToPath} from 'node:url';
import geometry from './image-geometry.cjs';

export async function prepareImageGeometry(page) {
  await page.evaluate(async()=>{
    await Promise.all([...document.images].map(image=>image.complete?Promise.resolve():new Promise(resolve=>{
      image.addEventListener('load',resolve,{once:true});image.addEventListener('error',resolve,{once:true});
    })));
  });
  const declared=await page.evaluate(()=>[...document.querySelectorAll('.slide')].flatMap((slide,index)=>{
    const bounds=slide.getBoundingClientRect();
    return [...slide.querySelectorAll('img[data-image-geometry]')].filter(image=>{
      const box=image.getBoundingClientRect();
      for(let item=image;item;item=item.parentElement){const style=getComputedStyle(item);if(style.display==='none'||style.visibility==='hidden')return false;}
      return box.width>0&&box.height>0;
    }).map((image,imageIndex)=>{
      const box=image.getBoundingClientRect(),style=getComputedStyle(image);
      let transformed=style.transform!=='none';
      const parentContours=[];
      for(let parent=image.parentElement;parent&&parent!==slide;parent=parent.parentElement){
        const ps=getComputedStyle(parent);
        const pb=parent.getBoundingClientRect();
        if(parseFloat(ps.borderRadius)>0&&Math.abs(pb.width-box.width)<5&&Math.abs(pb.height-box.height)<5)
          parentContours.push(ps.borderRadius);
        transformed ||= ps.transform!=='none';
        if(ps.clipPath!=='none'||(parseFloat(ps.borderRadius)>0&&/(hidden|clip)/.test(`${ps.overflowX} ${ps.overflowY}`)))
          throw new Error('image geometry: parent clipping is independent of the picture contract; put the contour on data-image-geometry and remove the competing clip');
      }
      image.dataset.studioImageKey=`${index+1}-${imageIndex+1}`;
      return{key:image.dataset.studioImageKey,slide:index+1,src:image.currentSrc||image.src,
        value:image.dataset.imageGeometry,width:box.width,height:box.height,
        box:{x:(box.left-bounds.left)/bounds.width*(40/3),y:(box.top-bounds.top)/bounds.height*7.5,
          w:box.width/bounds.width*(40/3),h:box.height/bounds.height*7.5},
        transformed,parentContours,
        ptPerPx:(40/3)*72/bounds.width};
    });
  }));
  const requests=[];
  for(const item of declared){
    if(item.transformed)throw new Error(`image geometry: transformed HTML image ${item.key} needs an explicit supported transform`);
    let original=item.src;
    if(original.startsWith('file:')){
      const data=fs.readFileSync(fileURLToPath(original));const info=geometry.imageInfo(data);
      const mime=info.type==='svg'?'svg+xml':info.type==='jpg'?'jpeg':info.type;
      original=`data:image/${mime};base64,${data.toString('base64')}`;
    }
    const source=geometry.imageData(original);
    const value=JSON.parse(item.value),plan=geometry.planGeometry(source,item.box,value);
    for(const radius of item.parentContours){
      const ellipse=radius.includes('%')&&parseFloat(radius)===50;
      const compatible=ellipse?plan.geometry.mask==='ellipse':plan.geometry.mask==='roundRect'&&Math.abs(parseFloat(radius)-plan.geometry.radius*Math.min(item.width,item.height))<0.5;
      if(!compatible)throw new Error(`image geometry: picture ${item.key} and its independent rounded frame have different contours; put mask and border on the picture contract`);
    }
    const svg=geometry.previewSvg(original,plan,item.width,item.height,item.ptPerPx);
    await page.evaluate(({key,src})=>{
      const image=document.querySelector(`[data-studio-image-key="${key}"]`);
      image.src=src;
      // The derivative contains the contour and aligned border itself. Neither
      // a parent radius nor a second CSS border may silently crop it again.
      image.style.objectFit='fill';image.style.border='0';image.style.borderRadius='0';
      image.style.clipPath='none';image.style.mask='none';
    },{key:item.key,src:`data:image/svg+xml;base64,${Buffer.from(svg).toString('base64')}`});
    requests.push({slide:item.slide,match_box:item.box,box:item.box,geometry:plan.geometry,original_data:original});
  }
  return requests;
}
