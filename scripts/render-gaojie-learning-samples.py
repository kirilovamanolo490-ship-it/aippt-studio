#!/usr/bin/env python3
"""Render varied source-page samples with exact receipts for actual observation."""
from __future__ import annotations
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from aippt_studio.gaojie_layouts import CATEGORIES, GAOJIE, analysis_root, _rows, _sha
from aippt_studio.local_preview import render_slide_preview

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ANALYSIS = analysis_root(LIBRARY)


def select(per_category: int) -> list[dict]:
    catalog = _rows(ANALYSIS / "STYLE-CATALOG.jsonl")
    pages_by_sha = defaultdict(list)
    for row in _rows(ANALYSIS / "STYLE-PAGES.jsonl"):
        if not row.get("hidden") and row.get("page_status") == "parsed":
            pages_by_sha[row["source_sha256"]].append(row)
    chosen = []
    for category in CATEGORIES:
        decks = [row for row in catalog if row.get("category") == category and row.get("status") == "parsed"]
        eligible = []
        for deck in decks:
            for page in pages_by_sha[deck["source_sha256"]]:
                eligible.append({"category": category, "source_path": deck["path"], "source_sha256": deck["source_sha256"],
                                 "source_slide": page["slide"], "resource_id": deck["resource_id"],
                                 "structure_signature": page["layout_measurements"]["structure_signature"],
                                 "pattern": page.get("layout_pattern", {}).get("kind", "unknown"),
                                 "text_characters": page.get("text_characters", 0), "image_count": page.get("image_count", 0),
                                 "chart_count": page.get("chart_count", 0), "table_count": page.get("table_count", 0),
                                 "detail_ref": deck["detail_ref"], "title": page.get("title", "")})
        if category == "专题模板":
            # Inspect sequence and content for every actual thematic source.
            for deck in decks:
                available = pages_by_sha[deck["source_sha256"]]
                numbers = {1, max(2, round(len(available) / 2)), len(available)}
                chosen.extend(item for item in eligible if item["source_path"] == deck["path"] and item["source_slide"] in numbers)
            continue
        seen_pages, seen_sources, signatures, patterns = set(), set(), set(), set()
        for i in range(min(per_category, len(eligible))):
            candidates = [item for item in eligible if (item["source_sha256"], item["source_slide"]) not in seen_pages]
            if not candidates:
                break
            def score(item):
                return (7 * (item["source_sha256"] not in seen_sources) + 4 * (item["pattern"] not in patterns) +
                        3 * (item["structure_signature"] not in signatures) +
                        (item["chart_count"] + item["table_count"] > 0) +
                        min(item["text_characters"], 300) / 300 if i % 2 else
                        7 * (item["source_sha256"] not in seen_sources) + 4 * (item["pattern"] not in patterns) +
                        3 * (item["structure_signature"] not in signatures) +
                        (item["image_count"] > 0) + min(item["text_characters"], 100) / 100)
            item = sorted(candidates, key=lambda row: (-score(row), row["source_path"], row["source_slide"]))[0]
            chosen.append(item)
            seen_pages.add((item["source_sha256"], item["source_slide"]))
            seen_sources.add(item["source_sha256"])
            signatures.add(item["structure_signature"])
            patterns.add(item["pattern"])
    return chosen


def render(item: dict) -> dict:
    key = item["source_sha256"][:16] + "-" + str(item["source_slide"])
    preview = ANALYSIS / "静态预览" / (key + ".png")
    receipt = preview.with_suffix(".preview.json")
    result = dict(item, preview=str(preview), preview_receipt=str(receipt))
    try:
        source = LIBRARY / item["source_path"]
        if _sha(source) != item["source_sha256"]:
            raise ValueError("source_changed_before_render")
        if preview.is_file() and receipt.is_file():
            packet = json.loads(receipt.read_text())
            if packet.get("source_sha256") != item["source_sha256"] or packet.get("slide_number") != item["source_slide"] or packet.get("output_sha256") != _sha(preview):
                raise ValueError("stale_render_cache_requires_review")
        else:
            packet = render_slide_preview(source, item["source_slide"], preview, receipt_path=receipt,
                                          width=1024, load_system_fonts=True)
        result.update(status="rendered_advisory", preview_sha256=_sha(preview),
                      approximate=True, visual_status="needs_assistant_observation")
    except Exception as error:
        result.update(status="render_failed", error=f"{type(error).__name__}:{error}", visual_status="unavailable")
    return result


def sheets(results: list[dict]) -> list[dict]:
    output = []
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 22)
        small = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", 16)
    except OSError:
        font = small = ImageFont.load_default()
    for category in CATEGORIES:
        sample = [item for item in results if item["category"] == category and item["status"] == "rendered_advisory"]
        if not sample:
            continue
        for part in range(math.ceil(len(sample) / 6)):
            current = sample[part * 6:(part + 1) * 6]
            height = math.ceil(len(current) / 2) * 474 + 56
            image = Image.new("RGB", (1560, height), "#edf0f4")
            draw = ImageDraw.Draw(image)
            draw.text((20, 12), f"{category} · 来源页观察 · {part + 1}", fill="#132c43", font=font)
            for index, item in enumerate(current):
                x, y = (index % 2) * 780 + 6, (index // 2) * 474 + 52
                with Image.open(item["preview"]) as source:
                    thumbnail = source.convert("RGB")
                    thumbnail.thumbnail((768, 432))
                    image.paste(thumbnail, (x, y))
                    actual_dimensions = thumbnail.size
                label_y = y + 434
                draw.text((x, label_y), f"{index + 1}. ID {item['resource_id']} / 第{item['source_slide']}页 / {item['pattern']}", fill="#132c43", font=small)
            path = ANALYSIS / "观察联系表" / f"{category}-{part + 1}.png"
            path.parent.mkdir(exist_ok=True)
            image.save(path)
            output.append({"category": category, "path": str(path), "sha256": _sha(path), "samples": current})
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--per-category", type=int, default=6)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if not 1 <= args.per_category <= 12 or not 1 <= args.workers <= 4:
        parser.error("bounded per-category 1..12 and workers 1..4 required")
    selected = select(args.per_category)
    seen, unique = {}, []
    for item in selected:
        key = (item["source_sha256"], item["source_slide"])
        if key not in seen:
            seen[key] = item
            unique.append(item)
    done = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(render, item): item for item in unique}
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            done[(result["source_sha256"], result["source_slide"])] = result
            if i % 10 == 0 or i == len(unique):
                print(json.dumps({"rendered_tasks": i, "total_tasks": len(unique), "last_category": result["category"], "last_status": result["status"]}, ensure_ascii=False), flush=True)
    results = [{**item, **{key: value for key, value in done[(item["source_sha256"], item["source_slide"])].items()
                          if key not in ("category", "source_path", "resource_id")}} for item in selected]
    packet = {"schema": "aippt.gaojie-learning-samples.v1", "created_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
              "selection_basis": "diverse OOXML signatures, arrangement kinds and source identities; all three thematic decks at cover/middle/end",
              "selected_source_pages": len(unique), "rendered_source_pages": sum(result["status"] == "rendered_advisory" for result in done.values()),
              "category_sample_entries": len(results), "samples": results,
              "contact_sheets": sheets(results), "visual_status": "needs_actual_assistant_observation"}
    (ANALYSIS / "VISUAL-SAMPLE.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: packet[key] for key in ("selected_source_pages", "rendered_source_pages", "category_sample_entries")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
