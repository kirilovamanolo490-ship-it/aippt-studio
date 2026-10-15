"""Index every PPTX in the new dynamic-template collection without changing it.

The detailed JSON retains the full OOXML object/timing projection. The two
JSONL files are small, hash-bound discovery layers. No record asserts native
PowerPoint playback or media reuse rights.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import argparse
import csv
import json
from pathlib import Path
import re

from aippt_studio.motion_library import MotionLibraryError, inspect_motion_library


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
COLLECTION = LIBRARY / "镶金边高端动态ppt"
OUTPUT = COLLECTION / "分析索引"


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _transition_kind(transition: dict) -> str:
    children = transition.get("transition", {}).get("children", [])
    return "+".join(child.get("tag", "unknown") for child in children) or "timing_only"


def _effective_transitions(slide: dict) -> list[dict]:
    choices = [item for item in slide.get("transitions", []) if item.get("branch") == "choice"]
    return choices or [item for item in slide.get("transitions", []) if item.get("branch") == "direct"]


def _slide_summary(deck: dict, slide: dict, detail_ref: str, relative: str) -> dict:
    behaviors = slide.get("behaviors", [])
    transitions = _effective_transitions(slide)
    inherited = slide.get("inherited_motion") or {}
    layer_behaviors = {level: (inherited.get(level) or {}).get("behaviors", [])
                       for level in ("layout", "master")}
    effect_counts = Counter(item.get("type", "unknown") for item in behaviors)
    for items in layer_behaviors.values():
        effect_counts.update(item.get("type", "unknown") for item in items)
    effects_by_level = {"slide": dict(Counter(item.get("type", "unknown") for item in behaviors)),
                        **{level: dict(Counter(item.get("type", "unknown") for item in items))
                           for level, items in layer_behaviors.items()}}
    trans_types = list(dict.fromkeys(_transition_kind(item) for item in transitions))
    objects = slide.get("objects", [])
    texts = [item.get("text", "").strip() for item in objects if item.get("text", "").strip()]
    targets = []
    for level, items, layer_objects in [("slide", behaviors, objects),
                                         *((level, layer_behaviors[level], (inherited.get(level) or {}).get("objects", []))
                                           for level in ("layout", "master"))]:
        names = {item.get("id"): item.get("name", "") for item in layer_objects}
        ids = sorted({target.get("id") for item in items for target in item.get("targets", [])
                      if target.get("kind") == "shape" and target.get("id")})
        targets.extend({"id": shape_id, "name": names.get(shape_id, ""), "source_level": level}
                       for shape_id in ids)
    media = slide.get("media_relationships", [])
    inherited_media = [item for item in (slide.get("package_context") or {}).get("dependencies", [])
                       if item.get("type") in {"image", "audio", "video", "media"}]
    modes = []
    if behaviors:
        modes.append("object_timeline")
    if layer_behaviors["layout"]:
        modes.extend(("object_timeline", "layout_timeline") if "object_timeline" not in modes else ("layout_timeline",))
    if layer_behaviors["master"]:
        modes.extend(("object_timeline", "master_timeline") if "object_timeline" not in modes else ("master_timeline",))
    if "morph" in trans_types:
        modes.append("morph_transition")
    if trans_types and "morph" not in trans_types:
        modes.append("slide_transition")
    if effect_counts["audio"] or effect_counts["video"] or any(item.get("type") in {"audio", "video"} for item in [*media, *inherited_media]):
        modes.append("media_playback")
    if not modes:
        modes.append("no_motion_detected_in_ooxml")
    return {
        "schema": "aippt.motion-slide-discovery.v1",
        "path": relative,
        "source_sha256": deck["source_sha256"],
        "detail_ref": detail_ref,
        "presentation_order": slide["presentation_order"],
        "visible_order": slide.get("visible_order"),
        "hidden": slide.get("hidden", False),
        "slide_part": slide["part"],
        "object_count": len(objects),
        "all_visible_text": texts,
        "source_notes_present": bool(slide.get("notes_text")),
        "source_notes_characters": len(slide.get("notes_text", "")),
        "motion_modes": modes,
        "behavior_counts": dict(effect_counts),
        "behavior_counts_by_source_level": effects_by_level,
        "transition_types": trans_types,
        "effect_target_shapes": targets,
        "media_types": dict(Counter(item.get("type", "unknown") for item in [*media, *inherited_media])),
        "morph_pair_candidate_count": len((slide.get("morph_relationship") or {}).get("candidates", [])),
        "warnings": slide.get("warnings", []),
        "parse_error": slide.get("parse_error"),
        "playback_status": "not_verified",
    }


def _deck_summary(deck: dict, relative: str, detail_ref: str, detail_sha: str, source_bytes: int) -> dict:
    slides = deck["slides"]
    all_behaviors = Counter(item.get("type", "unknown") for slide in slides
                            for item in slide.get("behaviors", []))
    for slide in slides:
        for layer in (slide.get("inherited_motion") or {}).values():
            all_behaviors.update(item.get("type", "unknown") for item in layer.get("behaviors", []))
    all_transitions = Counter(_transition_kind(item) for slide in slides for item in _effective_transitions(slide))
    warnings = Counter(w for slide in slides for w in slide.get("warnings", []))
    modes = Counter(mode for slide in slides for mode in _slide_summary(deck, slide, detail_ref, relative)["motion_modes"])
    return {
        "schema": "aippt.motion-deck-discovery.v1",
        "path": relative,
        "source_sha256": deck["source_sha256"],
        "source_bytes": source_bytes,
        "slide_count": len(slides),
        "visible_slide_count": sum(slide.get("visible_order") is not None for slide in slides),
        "detail_ref": detail_ref,
        "detail_sha256": detail_sha,
        "motion_modes": dict(modes),
        "behavior_counts": dict(all_behaviors),
        "behavior_count_basis": "applied_per_slide_including_repeated_layout_master_inheritance",
        "transition_counts": dict(all_transitions),
        "slide_parse_errors": sum(bool(slide.get("parse_error")) for slide in slides),
        "warning_counts": dict(warnings),
        "review": {"ooxml": "all_resolvable_slides", "visual": "not_reviewed", "native_playback": "not_verified"},
    }


_BEHAVIOR_TAGS = {"anim", "animEffect", "animMotion", "animScale", "animRot", "animClr", "set", "cmd", "audio", "video"}


def _descendants(node: dict):
    for child in node.get("children", []):
        yield child
        yield from _descendants(child)


def _effect_lines(timing_tree: dict | None) -> list[str]:
    if not timing_tree:
        return []
    lines = []
    for node in _descendants(timing_tree):
        if node.get("tag") != "cTn" or "presetClass" not in node.get("attributes", {}):
            continue
        attrs = node["attributes"]
        identifiers = ", ".join(f"{key}={attrs[key]}" for key in
                                ("id", "presetClass", "presetID", "presetSubtype", "nodeType", "dur", "repeatCount", "fill")
                                if key in attrs)
        conditions = [item.get("attributes", {}) for child in node.get("children", [])
                      if child.get("tag") == "stCondLst" for item in _descendants(child)
                      if item.get("tag") == "cond"]
        behaviors = []
        for item in _descendants(node):
            if item.get("tag") not in _BEHAVIOR_TAGS:
                continue
            details = item.get("attributes", {})
            targets = [part.get("attributes", {}).get("spid") for part in _descendants(item)
                       if part.get("tag") == "spTgt"]
            behavior_timing = [part.get("attributes", {}).get("dur") for part in _descendants(item)
                               if part.get("tag") == "cTn"]
            features = ", ".join(f"{key}={details[key]}" for key in
                                 ("filter", "transition", "path", "origin", "from", "to", "by", "calcMode")
                                 if key in details)
            behaviors.append(f"{item['tag']}(目标={','.join(x for x in targets if x) or '非shape'},"
                             f"时长={','.join(x for x in behavior_timing if x) or '未显式'}, {features})")
        lines.append(f"  {len(lines) + 1}. `{identifiers}`；起始条件={conditions or '隐式'}；行为={'；'.join(behaviors) or '未识别'}")
    return lines


def _transition_lines(slide: dict) -> list[str]:
    lines = []
    for item in slide.get("transitions", []):
        transition = item.get("transition", {})
        child = [(node.get("tag"), node.get("attributes", {})) for node in transition.get("children", [])]
        lines.append(f"- `{item.get('branch', 'direct')}` Requires={item.get('requires') or '无'}；"
                     f"外层={transition.get('attributes', {})}；效果={child or '默认切换'}")
    return lines


def _page_markdown(page: dict, detail_slide: dict) -> str:
    text = "；".join(re.sub(r"\s+", " ", item).strip() for item in page["all_visible_text"])
    text = text[:500] + ("…" if len(text) > 500 else "")
    behavior = "、".join(f"{key}×{value}" for key, value in page["behavior_counts"].items()) or "无对象动画"
    transitions = "、".join(page["transition_types"]) or "无页面切换"
    targets = "、".join(f"{item['id']}:{item['name']}" for item in page["effect_target_shapes"][:12]) or "未见 shape 目标"
    if len(page["effect_target_shapes"]) > 12:
        targets += f" 等 {len(page['effect_target_shapes'])} 个"
    marker = "（隐藏页）" if page["hidden"] else ""
    effects = _effect_lines(detail_slide.get("timing_tree"))
    transitions_full = _transition_lines(detail_slide)
    inherited_blocks = []
    for level in ("layout", "master"):
        layer = (detail_slide.get("inherited_motion") or {}).get(level)
        if not layer or not layer.get("timing_tree"):
            continue
        inherited_effects = _effect_lines(layer["timing_tree"])
        resolved = sum(item.get("resolution") == "resolved" for item in layer.get("object_target_map", []))
        inherited_blocks.append(f"**{level} 继承动画**：`{layer['part']}`；行为 {len(layer.get('behaviors', []))} 项，"
                                f"目标映射 {resolved}/{len(layer.get('object_target_map', []))} 个已解析。\n\n"
                                + ("\n".join(inherited_effects) + "\n\n" if inherited_effects else ""))
    return (f"### 第 {page['presentation_order']} 页{marker}\n\n"
            f"- 内容：{text or '无可提取文字；需查看画面及母版'}\n"
            f"- 对象：{page['object_count']} 个；动画目标：{targets}\n"
            f"- 动画：{behavior}；切换：{transitions}；媒体关系：{page['media_types'] or '无'}\n"
            f"- 机制标记：{'、'.join(page['motion_modes'])}；Morph 对象配对候选 {page['morph_pair_candidate_count']} 个\n"
            f"- 来源备注：{'有，' + str(page['source_notes_characters']) + ' 字；完整内容见结构 JSON' if page['source_notes_present'] else '无可提取文字'}\n"
            f"- 待核：{('；'.join(page['warnings']) if page['warnings'] else '实际放映与字体/素材效果')}\n\n"
            + ("**逐项对象动画（源 OOXML 顺序）**\n\n" + "\n".join(effects) + "\n\n" if effects else "")
            + "".join(inherited_blocks)
            + ("**页面切换的 Choice/Fallback**\n\n" + "\n".join(transitions_full) + "\n\n" if transitions_full else ""))


def build(source_root: Path = COLLECTION, output_root: Path = OUTPUT) -> dict:
    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if not source_root.is_dir() or source_root == output_root:
        raise ValueError("source collection must be an existing directory distinct from output")
    files = sorted(path for path in source_root.rglob("*.pptx") if path.is_file())
    if not files:
        raise ValueError("collection has no PPTX files")
    details = output_root / "逐份结构"
    readable = output_root / "逐份拆解"
    details.mkdir(parents=True, exist_ok=True)
    readable.mkdir(parents=True, exist_ok=True)
    deck_rows, slide_rows, failures = [], [], []
    content_by_sha: dict[str, tuple[str, str, dict]] = {}
    for index, path in enumerate(files, 1):
        relative = path.relative_to(LIBRARY).as_posix() if path.is_relative_to(LIBRARY) else path.relative_to(source_root).as_posix()
        try:
            deck = inspect_motion_library(path)
            current_sha = deck["source_sha256"]
            if current_sha in content_by_sha:
                detail_ref, detail_sha, canonical = content_by_sha[current_sha]
                deck = {**canonical, "source_sha256": current_sha}
            else:
                deck["source"] = relative
                detail_ref = (details / f"{current_sha}.json").relative_to(LIBRARY).as_posix() if details.is_relative_to(LIBRARY) else str(details / f"{current_sha}.json")
                detail_path = details / f"{current_sha}.json"
                detail_path.write_text(json.dumps(deck, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                detail_sha = _sha(detail_path)
                content_by_sha[current_sha] = (detail_ref, detail_sha, deck)
            row = _deck_summary(deck, relative, detail_ref, detail_sha, path.stat().st_size)
            deck_rows.append(row)
            pages = [_slide_summary(deck, slide, detail_ref, relative) for slide in deck["slides"]]
            slide_rows.extend(pages)
            analysis = readable / f"{current_sha[:16]}-{index:03d}.md"
            analysis.write_text(
                f"# {path.stem}\n\n来源：`{relative}`  \nSHA-256：`{current_sha}`  \n"
                f"页数：{row['slide_count']}（可见 {row['visible_slide_count']}）  \n"
                f"对象行为：{row['behavior_counts']}  \n切换类型：{row['transition_counts']}\n\n"
                "本文件逐页记录了可提取文字、对象、效果目标和切换。完整时序树、触发条件、持续时间、路径、关键帧、媒体及母版关系在同 SHA 详细 JSON 中。"
                "下文的机制标记来自 OOXML；静态结构不能证明 PowerPoint 实际播放，原有文字与图片也不是新任务事实。\n\n"
                + "".join(_page_markdown(page, detail_slide)
                          for page, detail_slide in zip(pages, deck["slides"])), encoding="utf-8")
            row["readable_ref"] = analysis.relative_to(LIBRARY).as_posix() if analysis.is_relative_to(LIBRARY) else str(analysis)
            if index % 20 == 0:
                print(f"indexed {index}/{len(files)} PPTX", flush=True)
        except (MotionLibraryError, OSError, ValueError, KeyError, TypeError) as error:
            failures.append({"path": relative, "error": f"{type(error).__name__}:{error}"})
    for name, rows in (("MOTION-CATALOG.jsonl", deck_rows), ("MOTION-INDEX.jsonl", slide_rows)):
        target = output_root / name
        target.write_text("".join(_json(row) + "\n" for row in rows), encoding="utf-8")
    report = {
        "schema": "aippt.motion-library-build.v1",
        "collection": str(source_root),
        "pptx_files": len(files),
        "indexed_decks": len(deck_rows),
        "indexed_slides": len(slide_rows),
        "unique_source_hashes": len(content_by_sha),
        "failures": failures,
        "verification": "OOXML structure only; native playback unverified",
    }
    (output_root / "BUILD-REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_browse_index(output_root, deck_rows)
    return report


def _write_browse_index(output_root: Path, deck_rows: list[dict]) -> None:
    """Link every exact-source report; optional static previews stay labeled."""
    catalog_path = LIBRARY / "CATALOG.jsonl"
    catalog = {}
    if catalog_path.is_file():
        with catalog_path.open(encoding="utf-8-sig") as stream:
            for line in stream:
                if line.strip():
                    item = json.loads(line)
                    catalog[item["path"]] = item
    preview_path = output_root / "分析预览/PREVIEW-MANIFEST.json"
    full = set()
    if preview_path.is_file():
        packet = json.loads(preview_path.read_text(encoding="utf-8"))
        full = {row["relative_path"] for row in packet.get("rendered_decks", [])}
    fallback_path = output_root / "分析预览/fallback-single/FALLBACK-MANIFEST.json"
    first = set()
    if fallback_path.is_file():
        packet = json.loads(fallback_path.read_text(encoding="utf-8"))
        first = {row["source"] for row in packet.get("results", [])
                 if row.get("status") == "rendered_first_page"}
    mode_names = {"morph_transition": "平滑切换", "object_timeline": "对象时间轴",
                  "layout_timeline": "版式层动画", "master_timeline": "母版层动画",
                  "slide_transition": "普通转场", "media_playback": "媒体播放"}
    lines = ["# 镶金边高端动态 PPT：逐份分析入口", "",
             f"按当前来源 SHA-256 索引 {len(deck_rows)} 份 PPTX、{sum(row['slide_count'] for row in deck_rows)} 页。逐份拆解列出每页文字、对象、动画条件与转场，并链接到完整 OOXML 时间树。静态图和实际播放分级记录。", "",
             "| 序号 | 作品／素材 | 用途类别 | 页数 | 编码的动效结构 | 近似静态图 | 拆解 |",
             "| ---: | --- | --- | ---: | --- | --- | --- |"]
    for index, row in enumerate(deck_rows, 1):
        path = row["path"]
        relative = path.split("镶金边高端动态ppt/", 1)[-1]
        entry = catalog.get(path, {})
        title = str(entry.get("title") or Path(path).stem).replace("|", "／").replace("`", "")
        modes = "、".join(name for key, name in mode_names.items() if row["motion_modes"].get(key)) or "未检测到动效"
        preview = "首中末" if relative in full else "首页" if relative in first else "未渲染"
        detail = Path(row["readable_ref"]).name
        lines.append(f"| {index} | {title} | {entry.get('category', '待分类')}／{entry.get('asset_type', '?')} | "
                     f"{row['slide_count']} | {modes} | {preview} | [逐页分析](逐份拆解/{detail}) |")
    (output_root / "INDEX.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=COLLECTION)
    parser.add_argument("--output-root", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.source_root, args.output_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
