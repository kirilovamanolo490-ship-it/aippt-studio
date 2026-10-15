from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from aippt_studio.extraction import _extract_assets, _media_part_for_rel_id
from aippt_studio.models import SourceDeckElement, SourceSlideManifest


P = "http://schemas.openxmlformats.org/presentationml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _package(*, order=("rSecond", "rFirst"), slide_target="slides/slide9.xml",
             slide_mode="", image_mode="", image_target="../media/second.png",
             missing_part: str | None = None) -> BytesIO:
    """Minimal real OOXML relationship graph; no Office runtime or customer file."""
    parts = {
        "ppt/presentation.xml": f'<p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>'
        + "".join(f'<p:sldId id="{256+i}" r:id="{rid}"/>' for i, rid in enumerate(order))
        + "</p:sldIdLst></p:presentation>",
        "ppt/_rels/presentation.xml.rels": f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rFirst" Type="{R}/slide" Target="slides/slide1.xml"/>'
        f'<Relationship Id="rSecond" Type="{R}/slide" Target="{slide_target}" {slide_mode}/>'
        "</Relationships>",
        "ppt/slides/slide1.xml": f'<p:sld xmlns:p="{P}"/>',
        "ppt/slides/slide9.xml": f'<p:sld xmlns:p="{P}"/>',
        "ppt/slides/_rels/slide1.xml.rels": f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rImg" Type="{R}/image" Target="../media/first.png"/>'
        "</Relationships>",
        "ppt/slides/_rels/slide9.xml.rels": f'<Relationships xmlns="{PKG}">'
        f'<Relationship Id="rImg" Type="{R}/image" Target="{image_target}" {image_mode}/>'
        "</Relationships>",
        "ppt/media/first.png": b"image belonging to slide1.xml",
        "ppt/media/second.png": b"image belonging to slide9.xml",
    }
    data = BytesIO()
    with ZipFile(data, "w") as archive:
        for name, value in parts.items():
            if name != missing_part:
                archive.writestr(name, value)
    data.seek(0)
    return data


@pytest.mark.parametrize("order, expected", [
    (("rFirst", "rSecond"), ["first.png", "second.png"]),
    (("rSecond", "rFirst"), ["second.png", "first.png"]),
])
def test_media_relationship_follows_logical_slide_order(order, expected):
    with ZipFile(_package(order=order)) as archive:
        assert [_media_part_for_rel_id(archive, i, "rImg") for i in (1, 2)] == [
            "ppt/media/" + name for name in expected]


def test_absolute_package_relationship_targets_are_resolved():
    with ZipFile(_package(slide_target="/ppt/slides/slide9.xml", image_target="/ppt/media/second.png")) as archive:
        assert _media_part_for_rel_id(archive, 1, "rImg") == "ppt/media/second.png"


@pytest.mark.parametrize("options", [
    {"missing_part": "ppt/presentation.xml"},
    {"missing_part": "ppt/_rels/presentation.xml.rels"},
    {"missing_part": "ppt/slides/slide9.xml"},
    {"missing_part": "ppt/slides/_rels/slide9.xml.rels"},
    {"slide_mode": 'TargetMode="External"'},
    {"image_mode": 'TargetMode="External"'},
    {"order": ("rMissing", "rFirst")},
])
def test_unresolved_or_external_relationship_never_falls_back_to_slide_number(options):
    with ZipFile(_package(**options)) as archive:
        assert _media_part_for_rel_id(archive, 1, "rImg") is None


def test_missing_relationship_and_invalid_logical_index_return_none():
    with ZipFile(_package()) as archive:
        assert _media_part_for_rel_id(archive, 1, "rMissing") is None
        assert _media_part_for_rel_id(archive, 0, "rImg") is None
        assert _media_part_for_rel_id(archive, 3, "rImg") is None


def _slide(index: int, *, rel_id="rImg", image_source="") -> SourceSlideManifest:
    return SourceSlideManifest(id=f"slide-{index:03}", index=index, elements=[
        SourceDeckElement(path=f"/slide[{index}]/picture[@id=2]", type="picture",
                          format={"relId": rel_id, "image.src": image_source, "contentType": "image/png"})])


def test_asset_extraction_uses_owner_part_bytes_after_reorder(tmp_path: Path):
    source = tmp_path / "relationship-fixture.pptx"
    original = _package().getvalue()
    source.write_bytes(original)
    output = tmp_path / "inspection"
    slides = [_slide(1), _slide(2)]
    assets = _extract_assets(source, "source-1", slides, output)
    assert [asset.package_path for asset in assets] == ["ppt/media/second.png", "ppt/media/first.png"]
    assert [asset.extraction_status for asset in assets] == ["extracted", "extracted"]
    assert (tmp_path / assets[0].stored_path).read_bytes() == b"image belonging to slide9.xml"
    assert (tmp_path / assets[1].stored_path).read_bytes() == b"image belonging to slide1.xml"
    assert source.read_bytes() == original


@pytest.mark.parametrize("rel_id, options", [
    ("rMissing", {}), ("rImg", {"image_mode": 'TargetMode="External"'}),
])
def test_failed_explicit_relation_does_not_guess_matching_image_filename(tmp_path: Path, rel_id, options):
    source = tmp_path / "relationship-fixture.pptx"
    source.write_bytes(_package(**options).getvalue())
    assets = _extract_assets(source, "source-1", [_slide(1, rel_id=rel_id, image_source="second.png")], tmp_path / "inspection")
    assert assets[0].extraction_status == "failed"
    assert assets[0].package_path is None
    assert assets[0].stored_path is None


def test_image_fill_without_relationship_retains_explicit_filename_support(tmp_path: Path):
    source = tmp_path / "relationship-fixture.pptx"
    source.write_bytes(_package().getvalue())
    assets = _extract_assets(source, "source-1", [_slide(1, rel_id=None, image_source="second.png")], tmp_path / "inspection")
    assert assets[0].extraction_status == "extracted"
    assert assets[0].package_path == "ppt/media/second.png"
