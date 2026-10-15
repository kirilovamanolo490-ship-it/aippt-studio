from __future__ import annotations

from pathlib import Path
import zipfile
from xml.etree import ElementTree

import pytest

from aippt_studio.mastering import MasterPromotion, audit_master_layout_ids, promote_layouts_to_masters


PML = "http://schemas.openxmlformats.org/presentationml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _relationships(items: list[tuple[str, str, str]]) -> bytes:
    root = ElementTree.Element(f"{{{PKG_REL}}}Relationships")
    for rel_id, rel_type, target in items:
        ElementTree.SubElement(root, f"{{{PKG_REL}}}Relationship", {"Id": rel_id, "Type": rel_type, "Target": target})
    return ElementTree.tostring(root)


def test_promote_layouts_creates_three_real_masters_and_rebinds_slides(tmp_path: Path) -> None:
    source = tmp_path / "source.pptx"
    output = tmp_path / "output.pptx"
    slide_layout_rel = f"{REL}/slideLayout"
    slide_master_rel = f"{REL}/slideMaster"
    theme_rel = f"{REL}/theme"
    presentation = f'<p:presentation xmlns:p="{PML}" xmlns:r="{REL}"><p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst></p:presentation>'.encode()
    master = f'<p:sldMaster xmlns:p="{PML}" xmlns:r="{REL}"><p:cSld/><p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/><p:sldLayoutId id="2" r:id="rId2"/><p:sldLayoutId id="3" r:id="rId3"/></p:sldLayoutIdLst></p:sldMaster>'.encode()
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr("ppt/_rels/presentation.xml.rels", _relationships([("rId1", slide_master_rel, "slideMasters/slideMaster1.xml")]))
        archive.writestr("ppt/slideMasters/slideMaster1.xml", master)
        archive.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", _relationships([("rId1", slide_layout_rel, "../slideLayouts/slideLayout1.xml"), ("rId2", slide_layout_rel, "../slideLayouts/slideLayout2.xml"), ("rId3", slide_layout_rel, "../slideLayouts/slideLayout3.xml"), ("rId4", theme_rel, "../theme/theme1.xml")]))
        for index in range(1, 4):
            archive.writestr(f"ppt/slides/_rels/slide{index}.xml.rels", _relationships([("rId1", slide_layout_rel, f"../slideLayouts/slideLayout{index}.xml")]))
            archive.writestr(f"ppt/slideLayouts/slideLayout{index}.xml", f'<p:sldLayout xmlns:p="{PML}"><p:cSld name="layout-{index}"/></p:sldLayout>')
            archive.writestr(f"ppt/slideLayouts/_rels/slideLayout{index}.xml.rels", _relationships([("rId1", slide_master_rel, "../slideMasters/slideMaster1.xml")]))
        archive.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">' + ''.join(f'<Override PartName="/ppt/slideLayouts/slideLayout{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>' for i in range(1, 4)) + '</Types>')

    promote_layouts_to_masters(source, output, [MasterPromotion(1, "腾讯"), MasterPromotion(2, "携程"), MasterPromotion(3, "饿了么")])

    audit = audit_master_layout_ids(output)
    assert audit["master_count"] == 3
    assert audit["layout_count"] == 3
    assert len(audit["ids"]) == len(set(audit["ids"]))

    with zipfile.ZipFile(output) as archive:
        master_names = sorted(name for name in archive.namelist() if name.startswith("ppt/slideMasters/slideMaster") and name.endswith(".xml"))
        assert master_names == ["ppt/slideMasters/slideMaster1.xml", "ppt/slideMasters/slideMaster2.xml", "ppt/slideMasters/slideMaster3.xml"]
        slide2_rels = ElementTree.fromstring(archive.read("ppt/slides/_rels/slide2.xml.rels"))
        assert next(iter(slide2_rels)).get("Target") == "../slideLayouts/slideLayout4.xml"
        master2 = ElementTree.fromstring(archive.read("ppt/slideMasters/slideMaster2.xml"))
        assert master2.find(f"{{{PML}}}cSld").get("name") == "携程"
        assert b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"' in archive.read("[Content_Types].xml")


def test_audit_rejects_layout_ids_reused_across_masters(tmp_path: Path) -> None:
    deck = tmp_path / "duplicate-layout-id.pptx"
    presentation = f'''<p:presentation xmlns:p="{PML}" xmlns:r="{REL}">
        <p:sldMasterIdLst>
            <p:sldMasterId id="2147483648" r:id="rId1"/>
            <p:sldMasterId id="2147483650" r:id="rId2"/>
        </p:sldMasterIdLst>
    </p:presentation>'''
    with zipfile.ZipFile(deck, "w") as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        for index in (1, 2):
            archive.writestr(
                f"ppt/slideMasters/slideMaster{index}.xml",
                f'''<p:sldMaster xmlns:p="{PML}" xmlns:r="{REL}">
                    <p:cSld/><p:sldLayoutIdLst>
                        <p:sldLayoutId id="2147483649" r:id="rId1"/>
                    </p:sldLayoutIdLst>
                </p:sldMaster>''',
            )

    with pytest.raises(ValueError, match=r"globally unique: 2147483649"):
        audit_master_layout_ids(deck)
