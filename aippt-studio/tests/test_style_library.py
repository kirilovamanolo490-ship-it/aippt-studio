from __future__ import annotations
from pathlib import Path
import importlib.util
from zipfile import ZipFile

from aippt_studio import style_library as style


def _synthetic(path: Path) -> None:
    p, a, r, pkg = style.P, style.A, style.R, style.PKG
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as z:
        z.writestr("ppt/presentation.xml", f'<p:presentation xmlns:p="{p}" xmlns:r="{r}"><p:sldIdLst><p:sldId id="1" r:id="s1"/><p:sldId id="2" show="0" r:id="s2"/></p:sldIdLst><p:sldSz cx="12192000" cy="6858000"/></p:presentation>')
        z.writestr("ppt/_rels/presentation.xml.rels", f'<Relationships xmlns="{pkg}"><Relationship Id="s1" Type="slide" Target="slides/s1.xml"/><Relationship Id="s2" Type="slide" Target="slides/s2.xml"/></Relationships>')
        rel = f'<Relationships xmlns="{pkg}"><Relationship Id="lay" Type="slideLayout" Target="../slideLayouts/slideLayout1.xml"/><Relationship Id="img" Type="image" Target="../media/photo.png"/></Relationships>'
        z.writestr("ppt/slides/_rels/s1.xml.rels", rel)
        z.writestr("ppt/slides/_rels/s2.xml.rels", rel)
        z.writestr("ppt/slides/s1.xml", f'''<p:sld xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree>
        <p:grpSp><p:nvGrpSpPr><p:cNvPr id="10" name="Title group"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
        <p:sp><p:nvSpPr><p:cNvPr id="2" name="Title"/><p:nvSpPr/><p:nvPr><p:ph type="title"/></p:nvPr></p:nvSpPr>
        <p:spPr><a:solidFill><a:srgbClr val="123456"/></a:solidFill></p:spPr>
        <p:txBody><a:p><a:r><a:rPr sz="3000" b="1"><a:solidFill><a:srgbClr val="ABCDEF"/></a:solidFill><a:latin typeface="Example"/></a:rPr><a:t>创新项目愿景</a:t></a:r></a:p></p:txBody></p:sp>
        <p:sp><p:nvSpPr><p:cNvPr id="4" name="Filled avatar"/><p:nvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="40" y="50"/><a:ext cx="600" cy="700"/></a:xfrm><a:blipFill><a:blip r:embed="img"/></a:blipFill></p:spPr></p:sp>
        </p:grpSp>
        <p:pic><p:nvPicPr><p:cNvPr id="3" name="照片"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="img"/></p:blipFill><p:spPr><a:xfrm><a:off x="10" y="20"/><a:ext cx="300" cy="400"/></a:xfrm></p:spPr></p:pic>
        </p:spTree></p:cSld></p:sld>''')
        z.writestr("ppt/slides/s2.xml", f'<p:sld xmlns:p="{p}"><p:cSld><p:spTree/></p:cSld></p:sld>')
        z.writestr("ppt/slideLayouts/slideLayout1.xml", f'''<p:sldLayout xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree><p:sp><p:nvSpPr><p:cNvPr id="20" name="Layout emblem"/><p:nvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:blipFill><a:blip r:embed="layoutImg"/></a:blipFill></p:spPr></p:sp></p:spTree></p:cSld></p:sldLayout>''')
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", f'<Relationships xmlns="{pkg}"><Relationship Id="m" Type="slideMaster" Target="../slideMasters/slideMaster1.xml"/><Relationship Id="layoutImg" Type="image" Target="../media/photo.png"/></Relationships>')
        z.writestr("ppt/slideMasters/slideMaster1.xml", f'''<p:sldMaster xmlns:p="{p}" xmlns:a="{a}" xmlns:r="{r}"><p:cSld><p:spTree><p:pic><p:nvPicPr><p:cNvPr id="30" name="Master logo"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="masterImg"/></p:blipFill><p:spPr/></p:pic></p:spTree></p:cSld></p:sldMaster>''')
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", f'<Relationships xmlns="{pkg}"><Relationship Id="t" Type="theme" Target="../theme/theme1.xml"/><Relationship Id="masterImg" Type="image" Target="../media/photo.png"/></Relationships>')
        z.writestr("ppt/theme/theme1.xml", f'''<a:theme xmlns:a="{a}"><a:themeElements><a:clrScheme><a:dk1><a:srgbClr val="000000"/></a:dk1><a:accent1><a:srgbClr val="123456"/></a:accent1></a:clrScheme><a:fontScheme><a:majorFont><a:latin typeface="Theme Display"/></a:majorFont><a:minorFont><a:ea typeface="主题字体"/></a:minorFont></a:fontScheme></a:themeElements></a:theme>''')
        z.writestr("ppt/media/photo.png", b"x" * 100000)


def test_inspects_order_hidden_text_geometry_style_theme_and_media_metadata(tmp_path: Path):
    deck = tmp_path / "sample.pptx"
    _synthetic(deck)
    row = style.inspect_style_deck(deck, "sample.pptx")
    assert row["status"] == "parsed"
    assert len(row["source_sha256"]) == 64
    assert [s["hidden"] for s in row["slides"]] == [False, True]
    slide = row["slides"][0]
    group, obj = slide["objects"][0:2]
    assert group["type"] == "grpSp" and group["text"] == ""
    assert slide["content_sequence"].count("创新项目愿景") == 1
    assert slide["composition"]["text_object_count"] == 1
    assert obj["text"] == "创新项目愿景"
    assert obj["text_runs"][0]["font_size_pt"] == 30
    assert obj["text_runs"][0]["bold"] == "1"
    assert obj["shape_style"]["fill"]["color"]["val"] == "123456"
    picture = slide["objects"][3]["picture"]
    assert picture["media_part"] == "ppt/media/photo.png"
    assert picture["declared_bytes"] == 100000 and not picture["media_bytes_read"]
    assert slide["style_context"]["theme"]["palette"]["accent1"]["val"] == "123456"
    assert slide["style_context"]["theme"]["fonts"]["majorFont"]["latin"] == "Theme Display"
    assert slide["title"] == "创新项目愿景"


def test_indexes_shape_image_fills_and_deduplicated_shared_layout_master_layers(tmp_path: Path):
    deck = tmp_path / "layers.pptx"
    _synthetic(deck)
    row = style.inspect_style_deck(deck, "layers.pptx")
    slide = row["slides"][0]
    filled_shape = next(obj for obj in slide["objects"] if obj["name"] == "Filled avatar")
    assert filled_shape["type"] == "sp"
    assert filled_shape["image_fills"] == [{
        "relationship_id": "img", "relationship_type": "image", "media_part": "ppt/media/photo.png",
        "external": False, "declared_bytes": 100000, "media_bytes_read": False,
    }]
    assert row["parser_version"] == "style-library-3"
    assert set(row["shared_layers"]) == {"ppt/slideLayouts/slideLayout1.xml", "ppt/slideMasters/slideMaster1.xml"}
    layout = row["shared_layers"]["ppt/slideLayouts/slideLayout1.xml"]
    master = row["shared_layers"]["ppt/slideMasters/slideMaster1.xml"]
    assert layout["image_count"] == 1 and layout["objects"][0]["name"] == "Layout emblem"
    assert master["image_count"] == 1 and master["objects"][0]["picture"]["media_part"] == "ppt/media/photo.png"
    script = Path(__file__).resolve().parents[2] / "scripts/build-style-library-index.py"
    spec = importlib.util.spec_from_file_location("build_style_library_index", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    page = module._page_row(row, slide, "detail.json", "layers.pptx")
    assert page["image_count"] == 2  # one p:pic and one shape image fill; group does not duplicate descendants
    assert page["index_version"] == module.INDEX_VERSION
    layer_summary = page["shared_layer_summary"]["layers"]
    assert [(layer["kind"], layer["image_count"]) for layer in layer_summary] == [
        ("slide_layout", 1), ("slide_master", 1)]


def test_real_career_slide_4_counts_background_and_avatar_once():
    root = Path(__file__).resolve().parents[2]
    source = root / "模板库/BB0086 大学生职业生涯规划大赛 PPT/简洁版-大学生职业生涯规划PPT模板/120套职业规划ppt/PPT模板01/3.pptx"
    if not source.is_file():
        return
    deck = style.inspect_style_deck(source, source.relative_to(root).as_posix())
    assert deck["status"] == "parsed", deck.get("errors")
    slide = deck["slides"][3]
    by_id = {obj["id"]: obj for obj in slide["objects"]}
    assert by_id["2"]["picture"]["relationship_id"] == "rId4"  # full-page background
    assert by_id["10"]["type"] == "grpSp" and not by_id["10"].get("image_fills")
    assert by_id["11"]["image_fills"][0]["relationship_id"] == "rId5"  # avatar
    script = root / "scripts/build-style-library-index.py"
    spec = importlib.util.spec_from_file_location("build_style_library_index_real", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    page = module._page_row(deck, slide, "detail.json", "source.pptx")
    assert page["image_count"] == 2
    assert page["layout_pattern"]["background_image_count"] == 1
    assert page["layout_pattern"]["kind"] == "image_left_text_right"


def test_bad_and_oversized_xml_keep_error_status(tmp_path: Path, monkeypatch):
    corrupt = tmp_path / "bad.pptx"
    corrupt.write_bytes(b"not zip")
    assert style.inspect_style_deck(corrupt, "bad.pptx")["parse_status"] == "unsupported"
    deck = tmp_path / "large.pptx"
    _synthetic(deck)
    monkeypatch.setattr(style, "XML_PART_LIMIT", 20)
    result = style.inspect_style_deck(deck, "large.pptx")
    assert result["status"] == "unsupported"
    assert "oversized_xml_part" in result["errors"][0]


def test_reads_two_small_real_library_sources_if_available():
    root = Path(__file__).resolve().parents[2]
    candidates = [
        min((p for p in (root / "模板库/创赛").rglob("*.pptx")), key=lambda p:p.stat().st_size, default=None),
        min((p for p in (root / "模板库/BB0086 大学生职业生涯规划大赛 PPT").rglob("*.pptx")), key=lambda p:p.stat().st_size, default=None),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        record = style.inspect_style_deck(candidate, candidate.relative_to(root).as_posix())
        assert record["status"] == "parsed", record["errors"]
        assert record["slide_count"] > 0
        assert len(record["source_sha256"]) == 64
