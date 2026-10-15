from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/build-contest-design-structure.py"
SPEC = importlib.util.spec_from_file_location("build_contest_design_structure", SCRIPT)
assert SPEC and SPEC.loader
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def test_bridge_keeps_alias_path_and_rejects_changed_or_partial_detail(tmp_path: Path) -> None:
    library = tmp_path / "模板库"
    source = library / "创赛/另一份相同字节.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"same-deck-bytes")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    detail = library / "创赛/分析索引/逐份结构/detail.json"
    detail.parent.mkdir(parents=True)
    packet = {"status": "parsed", "source_sha256": digest, "slide_count": 1,
              "presentation_size": {"width_emu": 12192000, "height_emu": 6858000},
              "slides": [{"status": "parsed", "objects": [], "content_sequence": []}]}
    detail.write_text(json.dumps(packet), encoding="utf-8")
    catalog = {"schema": bridge.CATALOG_SCHEMA, "path": "创赛/另一份相同字节.pptx",
               "canonical_path": "创赛/原始路径.pptx", "source_format": "pptx", "status": "parsed",
               "source_sha256": digest, "slide_count": 1,
               "detail_ref": detail.relative_to(library).as_posix(),
               "detail_sha256": hashlib.sha256(detail.read_bytes()).hexdigest(),
               "style_profile": {}}
    row, reason = bridge.make_row(catalog, detail.parent / "STYLE-CATALOG.jsonl",
                                  tmp_path, detail.parent,
                                  {catalog["path"]: {"category": "竞赛与答辩", "subcategory": "创新创业路演"}})
    assert reason == "included" and row is not None
    assert row["source_path"] == catalog["path"]
    assert row["coverage"]["full_slide_coverage"] is True

    packet["slides"][0]["status"] = "unsupported"
    detail.write_text(json.dumps(packet), encoding="utf-8")
    assert bridge.make_row(catalog, detail.parent / "STYLE-CATALOG.jsonl", tmp_path,
                           detail.parent)[1] == "detail_sha_mismatch"
    catalog["detail_sha256"] = hashlib.sha256(detail.read_bytes()).hexdigest()
    assert bridge.make_row(catalog, detail.parent / "STYLE-CATALOG.jsonl", tmp_path,
                           detail.parent)[1] == "partial_detail"
