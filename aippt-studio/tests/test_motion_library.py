from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

from aippt_studio.motion_library import inspect_motion_library

P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC = "http://schemas.openxmlformats.org/markup-compatibility/2006"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _deck(path: Path, *, slide_xml: str | None = None, rels: str | None = None,
          second_visible: bool = False, morph_second: bool = False) -> Path:
    second_visibility = "" if second_visible else 'show="0"'
    presentation = f'''<p:presentation xmlns:p="{P}" xmlns:r="{R}">
      <p:sldIdLst><p:sldId id="301" r:id="rId1"/><p:sldId id="302" r:id="rId2" {second_visibility}/></p:sldIdLst>
      <p:sldSz cx="12192000" cy="6858000"/></p:presentation>'''
    presentation_rels = f'''<Relationships xmlns="{PKG}">
      <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/>
      <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/></Relationships>'''
    slide = slide_xml or f'''<p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}" xmlns:mc="{MC}">
      <p:cSld><p:spTree><p:nvGrpSpPr/><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="12192000" cy="6858000"/></a:xfrm></p:grpSpPr>
        <p:grpSp><p:nvGrpSpPr><p:cNvPr id="10" name="Group"/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="100" y="200"/><a:ext cx="300" cy="400"/><a:chOff x="0" y="0"/><a:chExt cx="300" cy="400"/></a:xfrm></p:grpSpPr>
          <p:sp><p:nvSpPr><p:cNvPr id="11" name="Animated title"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="1000" y="2000"/><a:ext cx="3000" cy="800"/></a:xfrm></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr><a:latin typeface="Example Sans"/></a:rPr><a:t>Gold title</a:t></a:r><a:endParaRPr/></a:p></p:txBody></p:sp>
        </p:grpSp>
        <p:pic><p:nvPicPr><p:cNvPr id="12" name="Portrait"/><p:cNvPicPr/><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed="rIdImage"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x="500" y="600"/><a:ext cx="700" cy="800"/></a:xfrm></p:spPr></p:pic>
      </p:spTree></p:cSld>
      <mc:AlternateContent><mc:Choice Requires="p14"><p:transition spd="slow"><p:push dir="u"/></p:transition></mc:Choice><mc:Fallback><p:transition><p:fade/></p:transition></mc:Fallback></mc:AlternateContent>
      <p:timing><p:tnLst><p:par><p:cTn id="1" dur="indefinite" nodeType="tmRoot"><p:childTnLst><p:seq concurrent="1" nextAc="seek"><p:cTn id="2" nodeType="mainSeq"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
        <p:par><p:cTn id="3" presetClass="entr" presetID="10" presetSubtype="0" nodeType="clickEffect"><p:stCondLst><p:cond evt="onClick" delay="0"/></p:stCondLst><p:childTnLst>
          <p:animEffect transition="in" filter="wipe(left)"><p:cBhvr><p:cTn id="4" dur="500" fill="hold"/><p:tgtEl><p:spTgt spid="11"/></p:tgtEl><p:attrNameLst><p:attrName>style.opacity</p:attrName></p:attrNameLst></p:cBhvr></p:animEffect>
          <p:animMotion origin="layout" path="M 0 0 L 1 0" pathEditMode="relative" ptsTypes="AA"><p:cBhvr><p:cTn id="5" dur="900"><p:stCondLst><p:cond delay="250"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="999"/></p:tgtEl><p:attrNameLst><p:attrName>ppt_x</p:attrName></p:attrNameLst></p:cBhvr><p:rCtr x="0" y="0"/></p:animMotion>
          <p:anim calcmode="lin" valueType="num"><p:cBhvr><p:cTn id="6" dur="700"/><p:tgtEl><p:spTgt spid="11"/></p:tgtEl><p:attrNameLst><p:attrName>ppt_y</p:attrName></p:attrNameLst></p:cBhvr><p:tavLst><p:tav tm="0"><p:val><p:fltVal val="0"/></p:val></p:tav><p:tav tm="100000"><p:val><p:fltVal val="1"/></p:val></p:tav></p:tavLst></p:anim>
          <p:audio><p:cMediaNode vol="80000"><p:cTn id="7" dur="2000"/><p:tgtEl><p:sndTgt r:embed="rIdAudio"/></p:tgtEl></p:cMediaNode></p:audio>
        </p:childTnLst></p:cTn></p:par>
      </p:childTnLst></p:cTn></p:seq></p:childTnLst></p:cTn></p:par></p:tnLst></p:timing>
      </p:sld>'''
    slide_rels = rels or f'''<Relationships xmlns="{PKG}">
      <Relationship Id="rIdLayout" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
      <Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
      <Relationship Id="rIdAudio" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/audio" Target="../media/sound1.mp3"/></Relationships>'''
    second_slide = slide.replace('id="11"', 'id="21"')
    if morph_second:
        second_slide = second_slide.replace('<p:push dir="u"/>', '<p:morph option="byObject"/>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", presentation_rels)
        archive.writestr("ppt/slides/slide1.xml", slide)
        archive.writestr("ppt/slides/slide2.xml", second_slide)
        archive.writestr("ppt/slides/_rels/slide1.xml.rels", slide_rels)
        archive.writestr("ppt/slides/_rels/slide2.xml.rels", slide_rels)
        archive.writestr("ppt/slideLayouts/slideLayout1.xml", f'<p:sldLayout xmlns:p="{P}"/>')
        archive.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", f'''<Relationships xmlns="{PKG}"><Relationship Id="rIdMaster" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/></Relationships>''')
        archive.writestr("ppt/slideMasters/slideMaster1.xml", f'<p:sldMaster xmlns:p="{P}"/>')
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", f'''<Relationships xmlns="{PKG}"><Relationship Id="rIdTheme" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/></Relationships>''')
        archive.writestr("ppt/theme/theme1.xml", f'''<a:theme xmlns:a="{A}"><a:themeElements><a:fontScheme><a:majorFont><a:latin typeface="Theme Major"/><a:ea typeface="主题字体"/><a:cs typeface="Major CS"/></a:majorFont><a:minorFont><a:latin typeface="Theme Minor"/><a:ea typeface="次要字体"/><a:cs typeface="Minor CS"/></a:minorFont></a:fontScheme></a:themeElements></a:theme>''')
        archive.writestr("ppt/media/image1.png", b"not decoded by the motion reader")
        archive.writestr("ppt/media/sound1.mp3", b"media remains a zip member")
    return path


def test_stream_inspector_maps_objects_timeline_transitions_and_media(tmp_path: Path) -> None:
    deck = _deck(tmp_path / "motion.pptx")
    result = inspect_motion_library(deck)

    assert result["schema"] == "aippt.motion-library.v1"
    assert result["source_sha256"] == hashlib.sha256(deck.read_bytes()).hexdigest()
    assert result["verification"]["playback_status"] == "not_verified"
    assert [(s["presentation_order"], s["visible_order"], s["hidden"]) for s in result["slides"]] == [
        (1, 1, False), (2, None, True)
    ]
    first = result["slides"][0]
    objects = {obj["id"]: obj for obj in first["objects"]}
    assert objects["10"]["type"] == "grpSp"
    assert objects["11"]["group_path"] == [{"id": "10", "name": "Group"}]
    assert objects["11"]["text"] == "Gold title"
    assert objects["11"]["geometry"]["width_emu"] == 3000
    assert objects["12"]["image_relationship_ids"] == ["rIdImage"]
    assert objects["11"]["font_references"][0]["typeface"] == "Example Sans"
    assert objects["11"]["font_references"][0]["resolution"] == "explicit"
    assert [t["branch"] for t in first["transitions"]] == ["choice", "fallback"]
    assert first["transitions"][0]["transition"]["children"][0]["tag"] == "push"
    assert {b["type"] for b in first["behaviors"]} == {"animEffect", "animMotion", "anim", "audio"}
    motion = next(b for b in first["behaviors"] if b["type"] == "animMotion")
    assert motion["attributes"]["path"] == "M 0 0 L 1 0"
    assert "unresolved_animation_target:999" in first["warnings"]
    timing_text = str(first["timing_tree"])
    assert "onClick" in timing_text and "tav" in timing_text and "nextAc" in timing_text
    media = {rel["type"]: rel for rel in first["media_relationships"]}
    assert media["image"]["part_exists"] is True
    assert media["audio"]["part_exists"] is True
    assert media["audio"]["referenced_by_shape"] is False
    context = first["package_context"]
    assert context["layout"]["part"] == "ppt/slideLayouts/slideLayout1.xml"
    assert context["master"]["part"] == "ppt/slideMasters/slideMaster1.xml"
    assert context["theme_fonts"]["major"]["ea"] == "主题字体"
    assert context["theme_font_resolution"] == "resolved"
    inherited = first["inherited_motion"]
    assert inherited["layout"]["source_level"] == "layout"
    assert inherited["master"]["source_level"] == "master"
    assert inherited["layout"]["applies_to_slide"] is True
    assert inherited["master"]["applies_to_slide"] is True


def test_malformed_media_relationship_is_reported_without_reading_media(tmp_path: Path) -> None:
    bad_rels = f'''<Relationships xmlns="{PKG}">
      <Relationship Id="rIdImage" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/not-there.png"/>
      <Relationship Id="rIdAudio" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/audio" Target="../media/no-sound.mp3"/></Relationships>'''
    result = inspect_motion_library(_deck(tmp_path / "broken-targets.pptx", rels=bad_rels))
    first = result["slides"][0]
    assert {rel["type"]: rel["part_exists"] for rel in first["media_relationships"]} == {
        "image": False, "audio": False
    }
    assert not any(w.startswith("unresolved_relationship") for w in first["warnings"])


def test_notes_are_source_content_not_animation_instructions(tmp_path: Path) -> None:
    deck = _deck(tmp_path / "with-notes.pptx")
    with zipfile.ZipFile(deck) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    rel_name = "ppt/slides/_rels/slide1.xml.rels"
    parts[rel_name] = parts[rel_name].replace(
        b"</Relationships>",
        b'<Relationship Id="rIdNotes" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide" Target="../notesSlides/notesSlide1.xml"/></Relationships>',
    )
    parts["ppt/notesSlides/notesSlide1.xml"] = (
        f'<p:notes xmlns:p="{P}" xmlns:a="{A}"><p:cSld><p:spTree><p:sp><p:txBody>'
        '<a:p><a:r><a:t>Presenter context</a:t></a:r></a:p>'
        '</p:txBody></p:sp></p:spTree></p:cSld></p:notes>'
    ).encode()
    with zipfile.ZipFile(deck, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in parts.items():
            archive.writestr(name, payload)
    result = inspect_motion_library(deck)
    assert result["slides"][0]["notes_status"] == "parsed"
    assert result["slides"][0]["notes_text"] == "Presenter context"
    assert result["slides"][1]["notes_status"] == "absent"


def test_morph_has_neighbor_object_match_hypotheses_not_playback_claim(tmp_path: Path) -> None:
    result = inspect_motion_library(_deck(tmp_path / "morph.pptx", second_visible=True, morph_second=True))
    second = result["slides"][1]
    relation = second["morph_relationship"]
    assert relation["direction"] == "previous_visible_slide_to_this_slide"
    assert relation["previous_slide_part"] == "ppt/slides/slide1.xml"
    assert any("same_name" in pair["match_basis"] for pair in relation["candidates"])
    assert all(pair["status"] == "heuristic_candidate_only" for pair in relation["candidates"])
    assert "does not prove" in relation["verification"]


def test_real_library_effects_have_structural_records_and_unverified_playback() -> None:
    project = Path(__file__).resolve().parents[2]
    examples = [
        project / "模板库/镶金边高端动态ppt/更新23套/SS51 PPT影视封面-扬帆起航风正劲/SS51 PPT影视封面-扬帆起航风正劲 墨迹开场.pptx",
        project / "模板库/镶金边高端动态ppt/更新23套/SS44  PPT结尾-闭幕式视频/SS44  PPT结尾-闭幕式视频.pptx",
        project / "模板库/镶金边高端动态ppt/SS 屎盆子镶金边ppt合集40套/SS34 夯大力（完整版）/SS34 夯大力.pptx",
    ]
    assert all(path.is_file() for path in examples), "expected checked-in dynamic-library source decks"
    results = [inspect_motion_library(path) for path in examples]
    behavior_types = {b["type"] for result in results for slide in result["slides"] for b in slide["behaviors"]}
    assert {"animMotion", "animScale", "animEffect", "anim", "set", "audio", "video"} <= behavior_types
    assert all(result["verification"]["playback_status"] == "not_verified" for result in results)
    assert all(len(result["source_sha256"]) == 64 for result in results)


def test_inherited_layout_motion_is_attributed_per_source_level() -> None:
    project = Path(__file__).resolve().parents[2]
    root = project / "模板库/镶金边高端动态ppt/更新23套"
    ss57 = root / "SS57 图片循环轮播动态相册(照片数量8+12)/SS57 图片循环轮播动态相册(照片数量8+12).pptx"
    ss62 = root / "SS62 照片摩天轮动画PPT相册(必需8张照片)/SS62 照片摩天轮动画PPT相册(必需8张照片).pptx"
    assert ss57.is_file() and ss62.is_file()

    for path, expected_motion_count in ((ss57, 15), (ss62, 16)):
        result = inspect_motion_library(path)
        for slide in result["slides"]:
            inherited = slide["inherited_motion"]
            layout = inherited["layout"]
            assert layout["source_level"] == "layout"
            assert layout["applies_to_slide"] is True
            motions = [behavior for behavior in layout["behaviors"] if behavior["type"] == "animMotion"]
            if layout["part"] == "ppt/slideLayouts/slideLayout2.xml":
                assert len(motions) == expected_motion_count
                assert all(behavior["targets"] for behavior in motions)
                assert any(row["resolution"] == "resolved" for row in layout["object_target_map"])
            else:
                assert not motions
            assert "timing_tree" in layout and "objects" in layout and "transitions" in layout


def test_slide_only_ss43_ss51_do_not_gain_false_inherited_motion() -> None:
    project = Path(__file__).resolve().parents[2]
    root = project / "模板库/镶金边高端动态ppt/更新23套"
    examples = [
        root / "SS43 花瓣展开动态PPT/SS43 花瓣展开动态PPT.pptx",
        root / "SS51 PPT影视封面-扬帆起航风正劲/SS51 PPT影视封面-扬帆起航风正劲 墨迹开场.pptx",
    ]
    assert all(path.is_file() for path in examples)
    results = [inspect_motion_library(path) for path in examples]
    for result in results:
        for slide in result["slides"]:
            assert not [behavior for behavior in slide["behaviors"] if behavior["type"] == "animMotion"]
            inherited = slide["inherited_motion"]
            assert not any(layer["behaviors"] for layer in inherited.values())
