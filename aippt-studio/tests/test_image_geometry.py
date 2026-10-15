"""Image semantics are verified with XML, ZIP fragments and tiny PNGs, not decks."""
import base64
import hashlib
import io
import json
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
from lxml import etree as E
from PIL import Image

from aippt_studio.config import PROJECT_ROOT
from aippt_studio.runtime import resolve_executable

MODULE = PROJECT_ROOT / "node/image-geometry.cjs"
NS = {"p": "http://schemas.openxmlformats.org/presentationml/2006/main", "a": "http://schemas.openxmlformats.org/drawingml/2006/main"}


def node(code, value):
    binary = resolve_executable("node")
    assert binary, "a compatible Node runtime is required"
    program = "const g=require(process.argv[1]);const v=JSON.parse(require('fs').readFileSync(0,'utf8'));(async()=>{" + code + "})().catch(e=>{console.error(e.message);process.exitCode=1});"
    result = subprocess.run([str(binary), "-e", program, str(MODULE)], input=json.dumps(value), text=True, capture_output=True, cwd=PROJECT_ROOT, timeout=30)
    if result.returncode:
        raise ValueError(result.stderr.strip())
    return json.loads(result.stdout)


def plan(geometry, source=None, box=None):
    return node("console.log(JSON.stringify(g.planGeometry(v.source,v.box,v.geometry)))", {
        "source": source or {"width": 200, "height": 100},
        "box": box or {"x": 1, "y": 1, "w": 2, "h": 2}, "geometry": geometry})


def png():
    image = Image.new("RGB", (80, 40), "red")
    for x in range(40, 80):
        for y in range(40):
            image.putpixel((x, y), (0, 80, 255))
    stream = io.BytesIO(); image.save(stream, format="PNG")
    return stream.getvalue()


def test_default_contain_retains_full_source_without_stretch():
    result = plan({})
    assert result["crop"] == {"left": 0, "top": 0, "right": 0, "bottom": 0}
    assert result["fill"] == {"left": 0, "top": 0.25, "right": 0, "bottom": 0.25}
    assert result["geometry"]["mask"] == "rect"


def test_cover_uses_explicit_focal_position_and_crops_only_expected_axis():
    result = plan({"fit": "cover", "position": {"x": 1, "y": 0.5}, "allow_crop": True, "crop_reason": "Keep the right-hand subject in this ordinary photo"})
    assert result["crop"] == {"left": 0.5, "right": 0, "top": 0, "bottom": 0}
    assert not any(result["fill"].values())


def test_existing_template_bleed_coordinates_are_preserved():
    result = plan({}, box={"x": -0.5, "y": -0.25, "w": 2, "h": 2})
    assert result["box"] == {"x": -0.5, "y": -0.25, "w": 2, "h": 2}


@pytest.mark.parametrize("mask", ["roundRect", "ellipse"])
def test_contained_nonrectangular_frame_cannot_leave_a_rectangular_photo_inside(mask):
    value = {"fit": "contain", "mask": mask, "allow_crop": True, "crop_reason": "ordinary photo mask"}
    with pytest.raises(ValueError, match="leaves rectangular image edges"):
        plan(value)
    result = plan(value, box={"x": 0, "y": 0, "w": 2, "h": 1})
    assert not any(result["fill"].values())


@pytest.mark.parametrize("value", [{"fit": "cover"}, {"mask": "ellipse"}, {"mask": "roundRect"}, {"crop": {"left": 0.1, "top": 0, "right": 0, "bottom": 0}}, {"fit": "cover", "allow_crop": True}])
def test_evidence_is_not_silently_cropped_or_masked(value):
    with pytest.raises(ValueError, match="allow_crop.*crop_reason"):
        plan(value)


@pytest.mark.parametrize("value", [{"fit": "fill"}, {"radius": 0.8}, {"unknown": True}, {"position": {"x": 2, "y": 0}}, {"crop": {"left": 0.6, "top": 0, "right": 0.6, "bottom": 0}}, {"border": {"color": "red", "width_pt": 2}}])
def test_invalid_or_conflicting_geometry_is_explicit(value):
    with pytest.raises(ValueError, match="image geometry"):
        plan(value)


def picture(shape_id, name):
    return f'''<p:pic><p:nvPicPr><p:cNvPr id="{shape_id}" name="{name}"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rId1"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="914400" y="914400"/><a:ext cx="1828800" cy="1828800"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr></p:pic>'''


def package():
    source = png()
    slide = f'''<p:sld xmlns:p="{NS['p']}" xmlns:a="{NS['a']}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:cSld><p:spTree>{picture(2,'chosen')}{picture(3,'unchanged')}</p:spTree></p:cSld></p:sld>'''
    entries = {"ppt/slides/slide1.xml": slide.encode(), "ppt/slides/slide2.xml": b"untouched slide XML sentinel", "ppt/media/image1.png": source,
               "ppt/slides/_rels/slide1.xml.rels": b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/></Relationships>',
               "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="png" ContentType="image/png"/></Types>'}
    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return stream.getvalue(), entries


def patch(requests):
    buffer, entries = package()
    result = node("const r=await g.patchPackage(Buffer.from(v.buffer,'base64'),v.requests);console.log(JSON.stringify({buffer:r.buffer.toString('base64'),receipts:r.receipts}))", {"buffer": base64.b64encode(buffer).decode(), "requests": requests})
    with ZipFile(io.BytesIO(base64.b64decode(result["buffer"]))) as archive:
        actual = {name: archive.read(name) for name in archive.namelist()}
    return actual, entries, result["receipts"]


@pytest.mark.parametrize("mask", ["roundRect", "ellipse"])
def test_native_xml_has_one_picture_contour_and_border_with_unchanged_media(mask):
    geometry = {"fit": "cover", "mask": mask, "allow_crop": True, "crop_reason": "ordinary photo crop", "border": {"color": "228844", "width_pt": 2}}
    actual, original, receipts = patch([{"slide": 1, "shape_id": 2, "source_sha256": hashlib.sha256(png()).hexdigest(), "geometry": geometry}])
    for name, content in original.items():
        if name != "ppt/slides/slide1.xml":
            assert actual[name] == content
    root = E.fromstring(actual["ppt/slides/slide1.xml"])
    selected, other = root.findall(".//p:pic", NS)
    assert selected.find("p:spPr/a:prstGeom", NS).get("prst") == mask
    assert selected.find("p:spPr/a:ln", NS).get("w") == "25400"
    assert selected.find("p:blipFill/a:srcRect", NS).get("l") == "25000"
    assert other.find("p:spPr/a:prstGeom", NS).get("prst") == "rect"
    assert other.find("p:blipFill/a:srcRect", NS) is None
    assert receipts[0]["source_sha256"] == hashlib.sha256(png()).hexdigest()


def test_contain_is_native_fill_rectangle_not_a_baked_image():
    actual, _, _ = patch([{"slide": 1, "object_name": "chosen", "geometry": {}}])
    selected = E.fromstring(actual["ppt/slides/slide1.xml"]).find(".//p:pic", NS)
    assert selected.find("p:blipFill/a:stretch/a:fillRect", NS).get("t") == "25000"
    assert selected.find("p:blipFill/a:srcRect", NS).get("l") == "0"


def test_native_rotation_and_flips_remain_attached_to_picture_geometry():
    raw = f'<root xmlns:p="{NS["p"]}" xmlns:a="{NS["a"]}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">{picture(2, "chosen")}</root>'
    raw = raw.replace("<a:xfrm>", '<a:xfrm rot="1800000" flipH="1">')
    result = node('''const doc=g.parseXml(v.xml),pic=doc.getElementsByTagNameNS('http://schemas.openxmlformats.org/presentationml/2006/main','pic')[0];
      g.applyPictureGeometry(pic,g.planGeometry({width:80,height:40},g.pictureBox(pic),{}));
      console.log(JSON.stringify({xml:doc.toString()}));''', {"xml": raw})
    xfrm = E.fromstring(result["xml"].encode()).find(".//a:xfrm", NS)
    assert xfrm.get("rot") == "1800000" and xfrm.get("flipH") == "1"


@pytest.mark.parametrize("operation,match", [
    ({"slide": 1, "shape_id": 999, "geometry": {}}, "matched 0"),
    ({"slide": 1, "match_box": {"x": 1, "y": 1, "w": 2, "h": 2}, "geometry": {}}, "matched 2"),
    ({"slide": 1, "shape_id": 2, "source_sha256": "0" * 64, "geometry": {}}, "hash changed"),
])
def test_template_target_is_exact_and_hash_bound(operation, match):
    with pytest.raises(ValueError, match=match):
        patch([operation])


def test_html_restores_original_bytes_without_replacing_shared_media():
    original = png()
    actual, before, receipts = patch([{"slide": 1, "shape_id": 2, "geometry": {}, "original_data": "data:image/png;base64," + base64.b64encode(original).decode()}])
    restored = [name for name in actual if name.startswith("ppt/media/studio-original-")]
    assert len(restored) == 1 and actual[restored[0]] == original
    assert actual["ppt/media/image1.png"] == before["ppt/media/image1.png"]
    root = E.fromstring(actual["ppt/slides/slide1.xml"])
    blips = root.findall(".//a:blip", NS)
    key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
    assert blips[0].get(key) == "rIdStudioImage2"
    assert blips[1].get(key) == "rId1"


def test_svg_restoration_keeps_png_fallback_and_adds_original_vector():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><rect width="80" height="40" fill="red"/></svg>'
    actual, _, _ = patch([{"slide": 1, "shape_id": 2, "geometry": {}, "original_data": "data:image/svg+xml;base64," + base64.b64encode(svg).decode()}])
    assert any(content == svg for name, content in actual.items() if name.startswith("ppt/media/"))
    root = E.fromstring(actual["ppt/slides/slide1.xml"])
    assert len(root.findall(".//{http://schemas.microsoft.com/office/drawing/2016/SVG/main}svgBlip")) == 1
    assert root.find(".//a:blip", NS).get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed") == "rId1"


def test_duplicate_operation_fails_without_mutating_input_package():
    buffer, _ = package()
    result = node('''const buffer=Buffer.from(v.buffer,'base64'),before=g.hash(buffer);
      let error;try{await g.patchPackage(buffer,[{slide:1,shape_id:2,geometry:{}},{slide:1,shape_id:2,geometry:{}}]);}catch(e){error=e.message;}
      console.log(JSON.stringify({error,unchanged:before===g.hash(buffer)}));''', {"buffer": base64.b64encode(buffer).decode()})
    assert "duplicate geometry operation" in result["error"]
    assert result["unchanged"] is True


def test_default_html_binds_geometry_to_exact_asset_and_preserves_copy(tmp_path, monkeypatch):
    from aippt_studio import html_deck
    from aippt_studio.models import SlideSpec
    from lxml import html
    source = tmp_path / "fixture.png"; source.write_bytes(png())
    monkeypatch.setattr(html_deck, "_asset_info", lambda root, ref: (source.as_uri(), "image"))
    spec = SlideSpec(id="technical", title="技术内容", purpose="metadata", layout="content", points=["事实完整保留"], asset_refs=["source-1"])
    fragment = html.fromstring(html_deck.render_slide(tmp_path, spec, 1, 1))
    image = fragment.xpath(".//img")[0]
    assert json.loads(image.get("data-image-geometry")) == {"fit": "contain", "mask": "rect"}
    assert source.read_bytes() == png()
    spec.asset_geometry = {"other-source": {"fit": "cover"}}
    with pytest.raises(ValueError, match="exact asset_ref"):
        html_deck.render_slide(tmp_path, spec, 1, 1)


def test_browser_contour_preview_matches_shared_geometry_without_building_a_deck(tmp_path):
    uri = "data:image/png;base64," + base64.b64encode(png()).decode()
    result = node('''
      const {default:puppeteer}=await import('puppeteer');
      const {chromiumExecutable}=await import('./node/browser.mjs');
      const {prepareImageGeometry}=await import('./node/html-image-geometry.mjs');
      const browser=await puppeteer.launch({headless:true,executablePath:chromiumExecutable(),args:['--no-sandbox']});
      try{
        const page=await browser.newPage();
        const contract={fit:'cover',mask:'ellipse',allow_crop:true,crop_reason:'synthetic geometry test',border:{color:'00FF00',width_pt:1}};
        await page.setContent(`<style>html,body{margin:0;background:transparent}.slide{width:200px;height:100px}img{width:100px;height:100px}</style><div class="slide"><img src="${v.uri}" data-image-geometry='${JSON.stringify(contract)}'></div>`);
        const requests=await prepareImageGeometry(page);await page.evaluate(async()=>{await Promise.all([...document.images].map(i=>i.decode()));});
        const image=await page.$('img');const bytes=await image.screenshot({omitBackground:true});
        await page.setContent(`<style>.slide{width:200px;height:100px}img{width:100px;height:100px}</style><div class="slide"><img src="${v.uri}" data-image-geometry='${JSON.stringify({...contract,fit:'contain'})}'></div>`);
        let containError;try{await prepareImageGeometry(page);}catch(error){containError=error.message;}
        await page.setContent(`<style>.slide{width:200px;height:100px}.frame{width:100px;height:100px;border-radius:20px;overflow:hidden}img{width:100px;height:100px}</style><div class="slide"><div class="frame"><img src="${v.uri}" data-image-geometry='{}'></div></div>`);
        let clipError;try{await prepareImageGeometry(page);}catch(error){clipError=error.message;}
        const {auditLayout}=await import('./node/render.mjs');
        await page.setContent(`<style>.slide{width:200px;height:112.5px}.frame{width:100px;height:100px;border-radius:20px}img{width:100px;height:100px}</style><div class="slide"><div class="frame"><img src="${v.uri}"></div></div>`);
        const audit=await auditLayout(page);
        console.log(JSON.stringify({png:Buffer.from(bytes).toString('base64'),requests,containError,clipError,issues:audit.issues}));
      }finally{await browser.close();}
    ''', {"uri": uri})
    image = Image.open(io.BytesIO(base64.b64decode(result["png"]))).convert("RGBA")
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((50, 50))[3] == 255
    assert result["requests"][0]["original_data"] == uri
    assert result["requests"][0]["geometry"]["mask"] == "ellipse"
    assert "parent clipping is independent" in result["clipError"]
    assert "leaves rectangular image edges" in result["containError"]
    assert any(issue["kind"] == "image_frame_contour_unbound" for issue in result["issues"])
    assert not list(tmp_path.rglob("*.pptx"))
