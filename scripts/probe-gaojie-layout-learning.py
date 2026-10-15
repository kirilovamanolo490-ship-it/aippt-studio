#!/usr/bin/env python3
"""Small real-source integration probes for the Gaojie learning workflow."""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from aippt_studio.gaojie_layouts import analysis_root, _rows, _sha

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ANALYSIS = analysis_root(LIBRARY)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--extras", action="store_true")
    parser.add_argument("--objects", action="store_true")
    parser.add_argument("--tests", action="store_true")
    args = parser.parse_args()
    if args.tests:
        raise SystemExit(subprocess.call([sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
                                         "tests/test_style_library.py", "tests/test_style_search.py",
                                         "tests/test_style_page_match.py", "tests/test_style_hybrid_workflow.py",
                                         "tests/test_library_read_guard.py", "tests/test_gaojie_layouts.py"], cwd=ROOT / "aippt-studio"))
    if args.extras:
        spec = importlib.util.spec_from_file_location("gaojie_render_samples", ROOT / "scripts/render-gaojie-learning-samples.py")
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        catalog = _rows(ANALYSIS / "STYLE-CATALOG.jsonl")
        pages = _rows(ANALYSIS / "STYLE-PAGES.jsonl")
        packet = json.loads((ANALYSIS / "VISUAL-SAMPLE.json").read_text())
        for resource_id, slide_numbers in {"3871": [1, 98], "3893": [45]}.items():
            deck = next(row for row in catalog if row.get("resource_id") == resource_id)
            for slide in slide_numbers:
                if any(row["resource_id"] == resource_id and row["source_slide"] == slide for row in packet["samples"]):
                    continue
                page = next(row for row in pages if row["source_sha256"] == deck["source_sha256"] and row["slide"] == slide)
                item = {"category": deck["category"], "source_path": deck["path"], "source_sha256": deck["source_sha256"],
                        "source_slide": slide, "resource_id": resource_id, "structure_signature": page["layout_measurements"]["structure_signature"],
                        "pattern": page["layout_pattern"]["kind"], "text_characters": page["text_characters"],
                        "image_count": page["image_count"], "title": page["title"], "detail_ref": deck["detail_ref"]}
                result = helper.render(item)
                packet["samples"].append(result)
                print(json.dumps(result, ensure_ascii=False), flush=True)
        unique = {(row["source_sha256"], row["source_slide"]): row for row in packet["samples"]}
        packet.update(selected_source_pages=len(unique), rendered_source_pages=sum(row["status"] == "rendered_advisory" for row in unique.values()),
                      category_sample_entries=len(packet["samples"]))
        packet["contact_sheets"] = helper.sheets(packet["samples"])
        (ANALYSIS / "VISUAL-SAMPLE.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    if args.objects:
        for resource_id in ("1144", "1659", "2027", "6195", "7936"):
            deck = next(row for row in _rows(ANALYSIS / "STYLE-CATALOG.jsonl") if row.get("resource_id") == resource_id)
            data = json.loads((LIBRARY / deck["detail_ref"]).read_text())
            slide = data["slides"][0]
            print(json.dumps({"id": resource_id, "source_sha256": deck["source_sha256"], "path": deck["path"],
                              "objects": [{"id": obj["id"], "type": obj["type"], "group_ids": [g["id"] for g in obj["group_path"]], "text": obj["text"]}
                                          for obj in slide["objects"] if obj["text"] or (obj["type"] == "grpSp" and not obj["group_path"])]}, ensure_ascii=False))

if __name__ == "__main__":
    main()
