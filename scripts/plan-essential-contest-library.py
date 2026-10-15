#!/usr/bin/env python3
"""Plan source-hash-bound relocation of the two competition PPT collections.

This only writes a reviewable plan. The live library is changed by Studio's
transactional library_reorganization.apply after independent validation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import unicodedata

PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
CREATIVE = "创赛"
CAREER = "BB0086 大学生职业生涯规划大赛 PPT"
PRESENTATIONS = {".ppt", ".pptx", ".ppsx", ".pptm"}
FONTS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}
KEPT = PRESENTATIONS | FONTS | {".svg"}

CREATIVE_TOPICS = {
    "电商/商业服务": "商业服务与电商",
    "教育/医疗健康": "教育与医疗健康",
    "综合/待判": "综合主题待核",
    "科技创新/智能制造": "科技与智能制造",
    "生态/环保": "生态与环保",
    "乡村振兴": "农业与乡村振兴",
    "非遗/文化传承": "非遗与文化传承",
    "社会调查/社会治理": "社会调查与治理",
    "红色文旅/红旅": "红色文旅",
}


def records(path: Path):
    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def norm(value: str):
    return unicodedata.normalize("NFKC", value).casefold()


def clean_name(value: str, fallback: str, max_bytes: int = 138):
    value = unicodedata.normalize("NFKC", value or "")
    value = re.sub(r"^(\d+页)】", r"\1-", value)
    structure = re.match(r"^(.*?)\(结构(.*?)(?:\s*[|｜]\s*配色线索|\))", value)
    if structure:
        base, details = structure.groups()
        parts = [part.strip() for part in re.split(r"[、，,;；]", details)]
        noise = ("姓名", "出生年月", "代用名", "请输入", "添加标题", "分标题", "学校", "性别", "前言")
        parts = [part for part in parts if 2 <= len(part) <= 20 and not any(word in part for word in noise)]
        details = "、".join(part[:13].strip() for part in parts[:2]) or "通用章节框架"
        value = base.strip() + "-" + details
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .-_")
    if not value:
        value = fallback
    result = ""
    for char in value:
        if len((result + char).encode("utf-8")) > max_bytes:
            break
        result += char
    result = result.rstrip(" .-_#、|｜")
    missing_closes = result.count("(") - result.count(")")
    if missing_closes > 0:
        while len((result + ")" * missing_closes).encode("utf-8")) > max_bytes:
            result = result[:-1].rstrip(" .-_#、|｜")
        result += ")" * missing_closes
    return result or fallback


def family(font_name: str):
    lower = font_name.casefold()
    if "思源宋体" in font_name or "source han serif" in lower:
        return "思源宋体"
    if "思源黑体" in font_name or "source han sans" in lower:
        return "思源黑体"
    if "alibaba" in lower or "阿里巴巴普惠" in font_name:
        return "阿里巴巴普惠体"
    if "方正" in font_name or lower.startswith("fz"):
        return "方正系列"
    if "汉仪" in font_name:
        return "汉仪系列"
    if "microsoft yahei" in lower or "微软雅黑" in font_name:
        return "微软雅黑"
    return "其他字体"


def career_profession(planned: str, display: str):
    parts = Path(planned).parts
    if len(parts) >= 4 and parts[1].startswith("行业专业"):
        candidate = parts[2]
        if not re.search(r"-[0-9a-f]{8}$", candidate) and candidate not in {
            "专业主题待核", "专业主题待细分", "其他专业", "主题待核"
        }:
            return clean_name(candidate, "专业待核", 70)
    matches = re.findall(r"[\u4e00-\u9fffA-Za-z]{2,18}专业", display)
    if matches:
        return clean_name(matches[0], "专业待核", 70)
    return "通用与待核"


def destination_for_deck(row, curation, creative_taxonomy, career_legacy, styles):
    old = row["old_path"]
    sha = row["sha256"]
    ext = Path(old).suffix.lower()
    label = curation[old]
    title = clean_name(label["display_name"], sha[:12])
    if old.startswith(CAREER + "/") and label["asset_type"] == "template":
        if re.fullmatch(r"(?:红|橙|黄|绿|蓝|紫|黑|白|灰|棕|粉)(?:色)?\s*PPT", title, re.I):
            title = "职业规划通用模板"
        elif title == "职业规划":
            title = "职业生涯规划模板"
    if old.startswith(CREATIVE + "/"):
        taxonomy = creative_taxonomy[old]
        if label["asset_type"] == "guide":
            parts = [CREATIVE, "备赛指南"]
        else:
            topic = CREATIVE_TOPICS.get(taxonomy["category"], "综合主题待核")
            role = {
                "template": "演示模板",
                "portfolio": "参赛案例",
                "reference": "参考案例",
            }.get(label["asset_type"], "性质待核")
            parts = [CREATIVE, topic, role]
        basis = "creativecup-curation.category + curated display_name/asset_type"
    else:
        planned = career_legacy[old]["new_path"]
        profession = career_profession(planned, title)
        typ = label["asset_type"]
        sub = label["subcategory"]
        if label["category"] == "工作汇报":
            parts = [CAREER, "偏题参考", "工作汇报"]
        elif typ == "guide":
            parts = [CAREER, "备赛指南"]
        elif sub == "职业访谈与体验":
            parts = [CAREER, "人物访谈"]
        elif sub == "整页图片案例参考":
            parts = [CAREER, "图片化案例参考"]
        elif typ == "template":
            if "/就业赛道/" in planned:
                parts = [CAREER, "职业规划模板", "就业赛道"]
            elif "/成长赛道/" in planned:
                parts = [CAREER, "职业规划模板", "成长赛道"]
            else:
                parts = [CAREER, "职业规划模板", profession]
        elif typ == "portfolio":
            parts = [CAREER, "职业规划案例", profession]
        else:
            parts = [CAREER, "职业规划参考", profession]
        basis = "SOURCE-TAXONOMY + curated display_name/asset_type + profession plan"
    style = styles.get(old, {})
    profile = style.get("style_profile") or {}
    palette = profile.get("theme_palette") or {}
    accent = palette.get("accent1") or ""
    slide_count = style.get("slide_count") or 0
    style_hint = f"{slide_count}页" if slide_count > 0 else ""
    if re.fullmatch(r"[0-9A-Fa-f]{6}", accent):
        style_hint += ("-" if style_hint else "") + f"主题色{accent.upper()}"
    return {"old_path": old, "new_path": "/".join(parts + [title + ext]),
            "sha256": sha, "source_kind": "presentation", "classification_basis": basis,
            "display_name": title, "asset_type": label["asset_type"],
            "confidence": label["confidence"], "style_hint": style_hint}


def destination_for_font(row):
    old = row["old_path"]
    collection = CREATIVE if old.startswith(CREATIVE + "/") else CAREER
    source = "来源创赛" if collection == CREATIVE else "来源职规赛"
    stem = clean_name(Path(old).stem, row["sha256"][:12], 115)
    ext = Path(old).suffix.lower()
    return {"old_path": old,
            "new_path": f"字体包/{source}/授权待核/{family(stem)}/{stem}{ext}",
            "sha256": row["sha256"], "source_kind": "font",
            "classification_basis": "source collection + font basename family; license not verified",
            "display_name": stem, "asset_type": "font", "confidence": "name_only"}


def distinguish(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[norm(row["new_path"])].append(row)
    for group in groups.values():
        if len(group) <= 1:
            continue
        for row in group:
            p = Path(row["new_path"])
            hint = row.get("style_hint") or row["sha256"][:8]
            row["new_path"] = str(p.with_name(f"{p.stem}-{hint}{p.suffix}"))
    groups = defaultdict(list)
    for row in rows:
        groups[norm(row["new_path"])].append(row)
    for group in groups.values():
        if len(group) <= 1:
            continue
        for row in group:
            p = Path(row["new_path"])
            row["new_path"] = str(p.with_name(f"{p.stem}--{row['sha256'][:8]}{p.suffix}"))
    used = set()
    for row in sorted(rows, key=lambda x: x["old_path"]):
        candidate = row["new_path"]
        number = 2
        while norm(candidate) in used:
            p = Path(row["new_path"])
            candidate = str(p.with_name(f"{p.stem}-副本{number}{p.suffix}"))
            number += 1
        row["new_path"] = candidate
        used.add(norm(candidate))
        if len(Path(candidate).name.encode("utf-8")) > 255:
            raise ValueError("target component too long: " + candidate)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--creative-plan", type=Path, required=True)
    ap.add_argument("--career-plan", type=Path, required=True)
    ap.add_argument("--curation", type=Path, required=True)
    ap.add_argument("--current-plan", type=Path,
                    help="already applied first-stage plan, to resolve moved analysis source paths")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    plans = [*records(args.creative_plan), *records(args.career_plan)]
    curation = {r["path"]: r for r in records(args.curation)}
    creative_taxonomy = {}
    for r in records(LIBRARY / CREATIVE / "分析索引/creativecup-curation.jsonl"):
        creative_taxonomy[r["path"].removeprefix("模板库/")] = r
    career_legacy = {r["old_path"]: r for r in records(args.career_plan)}
    styles = {}
    for collection in [CREATIVE, CAREER]:
        for r in records(LIBRARY / collection / "分析索引/STYLE-CATALOG.jsonl"):
            styles[r["path"]] = r
    if args.current_plan:
        for item in records(args.current_plan):
            old, current = item["old_path"], item["new_path"]
            if old not in styles and current in styles:
                styles[old] = styles[current]
            if old not in creative_taxonomy and current in creative_taxonomy:
                creative_taxonomy[old] = creative_taxonomy[current]
    result = []
    for row in plans:
        old = row["old_path"]
        ext = Path(old).suffix.lower()
        if ext in PRESENTATIONS:
            if old not in curation:
                raise ValueError("presentation missing curation: " + old)
            if old.startswith(CREATIVE + "/") and old not in creative_taxonomy:
                raise ValueError("presentation missing taxonomy: " + old)
            item = destination_for_deck(row, curation, creative_taxonomy, career_legacy, styles)
        elif ext in FONTS:
            item = destination_for_font(row)
        elif ext == ".svg":
            collection = CREATIVE if old.startswith(CREATIVE + "/") else CAREER
            item = {"old_path": old, "new_path": f"{collection}/矢量素材/{clean_name(Path(old).stem, row['sha256'][:12])}.svg",
                    "sha256": row["sha256"], "source_kind": "svg",
                    "classification_basis": "source vector asset", "confidence": "source_only"}
        else:
            continue
        result.append(item)
    distinguish(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as stream:
        for row in sorted(result, key=lambda x: x["old_path"]):
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    counts = Counter(r["source_kind"] for r in result)
    print(json.dumps({"rows": len(result), "kinds": counts,
                      "collisions": len(result) - len({norm(r['new_path']) for r in result}),
                      "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
