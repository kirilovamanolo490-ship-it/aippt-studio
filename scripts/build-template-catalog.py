"""Build a fast discovery catalog for the current root-level template library."""

from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import re


PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "模板库"
CATALOG = ROOT / "CATALOG.jsonl"
SUMMARY = ROOT / "CATEGORY-SUMMARY.csv"
REPORT = ROOT / "CATALOG-BUILD.json"
SAMPLES = ROOT / "REVIEWED-SAMPLES.json"
CLASSIFICATION = ROOT / "分类记录.csv"
CONTENT_CURATION = ROOT / "CONTENT-CURATION.jsonl"
ASSET_TYPE_ALIASES = {
    "整套演示模板": "template", "演示模板": "template", "模板": "template",
    "作品案例": "portfolio", "完成案例": "portfolio", "作品集案例": "portfolio",
    "图表素材": "diagram_asset", "图标素材": "diagram_asset", "矢量图素材": "diagram_asset",
}

ROOT_OCCASION_TAGS = {
    "竞赛与答辩": ("竞赛", "比赛"),
    "党政党建": ("党政", "党建", "政治"),
    "工作汇报": ("汇报",),
    "商业与经营": ("商业", "经营"),
    "海报与视觉设计": ("海报", "视觉设计"),
}

# These are discovery cues inferred from collection folder names.  They are not
# claims that a particular page, licensed image, or editable master is present.
CATEGORY_TAGS: dict[str, tuple[list[str], list[str]]] = {
    "C001": (["素材", "图表", "数据可视化"], ["图表", "图标", "流程图", "素材"]),
    "C002": (["图标素材"], ["矢量图标", "素材"]),
    "C003": (["学术答辩", "毕业答辩", "论文答辩", "研究汇报"], ["学术"]),
    "C004": ([], ["朋克"]),
    "C005": ([], ["仙系"]),
    "C006": ([], ["国潮", "中国风"]),
    "C007": ([], ["ins风"]),
    "C008": ([], ["猫咪", "卡通"]),
    "C009": ([], ["线条", "简洁"]),
    "C010": ([], ["孟菲斯", "几何"]),
    "C011": ([], ["晕染"]),
    "C012": ([], ["油画", "肌理"]),
    "C013": ([], ["渣渣灰"]),
    "C014": ([], ["古禅", "民风"]),
    "C015": ([], ["宫廷", "影视主题"]),
    "C016": ([], ["故宫", "文博"]),
    "C017": ([], ["森系", "北欧"]),
    "C018": ([], ["卡通"]),
    "C019": (["党政汇报", "党课", "政策学习"], ["党政", "红色"]),
    "C020": (["商业计划", "创业路演", "融资"], ["商业"]),
    "C021": (["招新", "招聘", "社团招新"], ["招新"]),
    "C022": ([], ["快闪", "动态"]),
    "C023": ([], ["和风", "日式"]),
    "C024": (["工作总结", "工作汇报", "述职", "年终总结"], ["商务"]),
    "C025": ([], ["古典", "民族风"]),
    "C026": (["科技"], ["科技"]),
    "C028": ([], ["日系"]),
    "C029": (["说课", "教学"], ["教学"]),
    "C030": ([], ["马卡龙", "柔和配色"]),
    "C031": ([], ["盐系", "低饱和"]),
    "C032": ([], ["中国风"]),
    "C033": ([], ["空灵", "留白"]),
    "C034": ([], ["欧美简洁", "极简"]),
    "C035": ([], ["古风"]),
    "C036": ([], ["莫兰迪", "低饱和"]),
    "C037": (["节庆"], ["节日庆典"]),
    "C038": (["医学", "护理"], ["医学"]),
    "C039": (["作品集"], ["作品展示"]),
    "C040": ([], ["人像", "欧美"]),
    "C041": ([], ["水墨", "中国风"]),
    "C042": ([], ["星空"]),
    "C043": ([], ["水彩", "手绘"]),
    "C044": ([], ["MBE", "插画"]),
    "C045": ([], ["柠檬黄"]),
    "C046": ([], ["手绘"]),
    "C047": ([], ["夏日", "清爽"]),
    "C048": ([], ["浮世绘", "日式"]),
    "C049": (["党政汇报", "政府汇报", "政策学习"], ["党政", "红色"]),
    "C050": ([], ["北欧", "极简"]),
    "C051": (["圣诞节", "节庆活动"], ["圣诞"]),
    "C052": ([], ["蒸汽波"]),
    "C053": ([], ["极简", "低饱和"]),
    "C054": (["环保", "公益"], ["环保"]),
    "C055": (["音乐"], ["音乐"]),
    "C056": ([], ["火烈鸟"]),
    "C057": ([], ["粉色", "低饱和"]),
    "C058": ([], ["女性主题"]),
    "C059": ([], ["人像", "美妆"]),
    "C060": (["信息可视化"], ["信息图", "图表"]),
    "人物介绍": (["人物介绍"], ["人物介绍"]),
    "作品集": (["作品集"], ["完成案例"]),
    "课程模板": ([], ["课程模板"]),
}

ROLE_RULES = (
    (r"封面|cover", "封面"),
    (r"目录|contents|agenda", "目录"),
    (r"背景|问题|痛点", "背景问题"),
    (r"方法|研究|技术路线", "方法路线"),
    (r"结果|成果|业绩", "结果证据"),
    (r"图表|折线|柱状|饼图|数据|信息可视化", "数据图表"),
    (r"流程|架构|鱼骨|时间轴", "流程架构"),
    (r"人物|个人|团队|简历", "人物团队"),
    (r"作品|案例|项目介绍", "案例作品"),
    (r"预算|财务|融资|投资|回报", "预算财务"),
    (r"总结|计划|展望", "总结计划"),
    (r"招聘|招新|报名", "招聘行动"),
)


def category_key(name: str) -> str:
    match = re.match(r"C0?(\d{1,2})(?:\D|$)", name, re.IGNORECASE)
    return f"C{int(match.group(1)):03d}" if match else name


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_content_curation(path: Path | None = None) -> dict[str, dict]:
    """Load source-bound semantic labels; absence keeps legacy behavior."""
    path = path or CONTENT_CURATION
    if not path.is_file():
        return {}
    entries: dict[str, dict] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise ValueError(f"invalid CONTENT-CURATION.jsonl line {line_number}: {exc}") from exc
        rel = row.get("path") if isinstance(row, dict) else None
        if (not isinstance(rel, str) or not rel or rel.startswith(("/", "~")) or "\\" in rel
                or ":" in rel or ".." in Path(rel).parts):
            raise ValueError(f"invalid curation path on line {line_number}")
        if any(not isinstance(row.get(key), str) or not row[key].strip()
               for key in ("category", "display_name", "asset_type")):
            raise ValueError(f"missing semantic fields on CONTENT-CURATION.jsonl line {line_number}")
        if rel in entries:
            raise ValueError(f"duplicate curation path on line {line_number}: {rel}")
        entries[rel] = row
    return entries


def main() -> None:
    assert ROOT.is_dir(), ROOT
    classified = {}
    if CLASSIFICATION.is_file():
        with CLASSIFICATION.open(encoding="utf-8-sig", newline="") as stream:
            classified = {row["final_path"]: row for row in csv.DictReader(stream)}
    sampled = {}
    if SAMPLES.is_file():
        data = json.loads(SAMPLES.read_text(encoding="utf-8"))
        sampled = {item["path"]: item for item in data.get("entries", [])}
    curated = load_content_curation()
    rows = []
    summary = defaultdict(lambda: Counter())
    summary_basis: dict[str, set[str]] = defaultdict(set)
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT)
        suffix = path.suffix.casefold()
        curation = curated.get(rel.as_posix())
        if suffix not in {".pptx", ".ppt", ".ppsx", ".pptm"} and not (suffix == ".pdf" and (rel.as_posix() in classified or curation)):
            continue
        if curation and curation.get("sha256"):
            expected = curation["sha256"]
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError(f"invalid curation SHA-256: {rel.as_posix()}")
            if sha256(path) != expected:
                raise RuntimeError(f"content curation source SHA-256 mismatch: {rel.as_posix()}")
        category = curation.get("category", rel.parts[0]) if curation else rel.parts[0]
        subcategory = (curation.get("subcategory", "") if curation else "/".join(rel.parts[1:-1]))
        detail = classified.get(rel.as_posix())
        occasions = [category, *ROOT_OCCASION_TAGS.get(category, ())]
        if curation:
            occasions.extend(curation.get("occasion_tags", []))
            if subcategory:
                occasions.append(subcategory)
        elif subcategory:
            occasions.extend(part for part in rel.parts[1:-1] if part not in occasions)
        styles = []
        search_text = str(rel) + (" " + detail["display_title"] if detail else "")
        if curation:
            search_text += " " + curation["display_name"]
            search_text += " " + " ".join(curation.get("search_terms", []))
        roles = [label for pattern, label in ROLE_RULES if re.search(pattern, search_text, re.IGNORECASE)]
        if category == "素材资源" and "图表" in search_text and "数据图表" not in roles:
            roles.append("数据图表")
        if category == "素材资源" and "图标" in search_text and "图标素材" not in roles:
            roles.append("图标素材")
        if curation:
            asset_type = ASSET_TYPE_ALIASES.get(curation["asset_type"], curation["asset_type"])
        elif category == "作品集":
            asset_type = "portfolio"
        elif category == "素材资源":
            asset_type = "diagram_asset"
        elif suffix == ".pdf":
            asset_type = "reference_pdf"
        else:
            asset_type = "template"
        row = {
            "path": rel.as_posix(),
            "title": curation["display_name"] if curation else (detail["display_title"] if detail else path.stem),
            "asset_type": asset_type,
            "category": category,
            "subcategory": subcategory,
            "occasion_tags": occasions,
            "page_role_tags": roles,
            "style_tags": styles,
            "review_status": (curation.get("review_status", "curated_evidence") if curation else
                              ("structure_sampled" if detail else "filename_only")),
            "tag_basis": (curation.get("evidence_basis", "content_curation_manifest") if curation else
                          ("first_pages_text_and_manual_corrections" if detail else "folder_and_filename_candidate")),
            "media_rights": "unverified",
            "file_format": suffix.lstrip("."),
            "bytes": path.stat().st_size,
        }
        if curation:
            for target, source in (("content_subcategory", "subcategory"), ("confidence", "confidence"),
                                   ("evidence", "evidence"), ("source_sha256", "sha256"),
                                   ("curation_ref", "evidence_ref")):
                if source in curation:
                    row[target] = curation[source]
        if detail:
            row["source_sha256"] = detail["sha256"]
            row["confidence"] = detail["confidence"]
            row["source_url"] = detail["source_url"]
            row["content_note"] = detail["reason"]
        reviewed = sampled.get(rel.as_posix())
        if reviewed:
            digest = sha256(path)
            if digest != reviewed["source_sha256"]:
                raise RuntimeError(f"sample hash mismatch: {path}")
            row["occasion_tags"] = reviewed["occasion_tags"]
            row["page_role_tags"] = reviewed["page_role_tags"]
            row["review_status"] = ("visual_sampled" if reviewed["visual_status"] in
                                    {"preview_contact_sheet", "rendered_pages_2_3"} else "structure_sampled")
            row["tag_basis"] = reviewed["inspection"]
            row["source_sha256"] = digest
            row["sample_review_ref"] = "REVIEWED-SAMPLES.json"
            row["page_count"] = reviewed["page_count"]
            if path.name == "商业案例模板15页.pptx":
                row["title"] = "202X年工作总结及明年思路"
                row["style_tags"] = ["党政", "工作汇报"]
                row["name_mismatch"] = True
                row["content_note"] = "原文件名写商业案例；实际前四页为党政工作总结"
        if curation:
            # Apply the semantic source last: legacy folder, title, and sampled
            # tags must not replace curated meaning. Keep actual sample receipts
            # alongside it; only those receipts can claim a visual sample.
            row["title"] = curation["display_name"]
            row["category"] = category
            row["subcategory"] = subcategory
            row["asset_type"] = ASSET_TYPE_ALIASES.get(curation["asset_type"], curation["asset_type"])
            row["occasion_tags"] = list(dict.fromkeys(occasions))
            row["page_role_tags"] = list(dict.fromkeys(curation.get("page_role_tags", roles)))
            row["style_tags"] = curation.get("style_tags", styles)
            if "confidence" in curation:
                row["confidence"] = curation["confidence"]
            if "evidence" in curation:
                row["evidence"] = curation["evidence"]
            if curation.get("sha256"):
                row["source_sha256"] = curation["sha256"]
            row["curation_review_status"] = curation.get("review_status", "curated_evidence")
            row["curation_evidence_basis"] = curation.get("evidence_basis", "content_curation_manifest")
            observation = curation.get("classification_observation")
            if isinstance(observation, dict) and observation.get("off_topic") is True:
                row["off_topic"] = True
            if (isinstance(observation, dict) and observation.get("source_collection") == "career"
                    and observation.get("off_topic") is not True and row["asset_type"] == "template"):
                # This is a candidate for a career-planning competition deck,
                # not a claim that its original slides were contest-ready.
                row["occasion_tags"] = list(dict.fromkeys([
                    *row["occasion_tags"], "职业规划竞赛答辩候选（待源页确认）",
                ]))
                row["occasion_candidate_basis"] = "SHA-bound career collection taxonomy; source-page review required"
            if reviewed:
                row["sample_inspection"] = reviewed["inspection"]
                row["sample_visual_status"] = reviewed["visual_status"]
                row["sample_review_ref"] = "REVIEWED-SAMPLES.json"
                row["page_count"] = reviewed["page_count"]
                # review_status already reflects whether a real sample was visual.
            else:
                row["review_status"] = "curated_evidence"
            row["tag_basis"] = row["curation_evidence_basis"]
        if suffix in {".ppsx", ".pptm"}:
            # These files are indexed for discovery and analysis, but the
            # source-preserving template copy path accepts parsed .pptx only.
            row["asset_type"] = "reference"
            row["reuse_scope"] = "inspect_only_non_pptx_source"
        rows.append(row)
        summary[category][suffix.lstrip(".")] += 1
        summary[category]["bytes"] += row["bytes"]
        summary_basis[category].add(row["tag_basis"])
    with CATALOG.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    with SUMMARY.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["category", "pptx", "ppt_legacy", "pdf", "deck_bytes", "label_basis", "ppsx", "pptm"])
        for category, counts in sorted(summary.items()):
            writer.writerow([category, counts["pptx"], counts["ppt"], counts["pdf"], counts["bytes"],
                             ";".join(sorted(summary_basis[category])), counts["ppsx"], counts["pptm"]])
    report = {"schema_version": 1, "deck_count": len(rows), "pptx": sum(r["file_format"] == "pptx" for r in rows),
              "ppt_legacy": sum(r["file_format"] == "ppt" for r in rows),
              "ppsx_reference_only": sum(r["file_format"] == "ppsx" for r in rows),
              "pptm_reference_only": sum(r["file_format"] == "pptm" for r in rows),
              "pdf": sum(r["file_format"] == "pdf" for r in rows),
              "first_pages_classified_count": sum(bool(classified.get(r["path"])) for r in rows),
              "content_curated_count": sum(bool(curated.get(r["path"])) for r in rows),
              "carried_reviewed_sample_count": sum(bool(r.get("sample_review_ref")) for r in rows),
              "non_filename_only_tagged_count": sum(r["review_status"] != "filename_only" for r in rows),
              "category_count": len(summary), "catalog": str(CATALOG), "summary": str(SUMMARY),
              "limitations": ["CONTENT-CURATION.jsonl semantic labels take precedence for exact current paths; unlisted files retain legacy folder/filename candidates.",
                              "The 196 downloads were classified from first pages' text and manually corrected where clear; no whole-deck visual review is claimed.",
                              "Legacy bundles remain folder and filename candidates unless a reviewed sample is recorded.",
                              "Inspect actual slides before reuse."]}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
