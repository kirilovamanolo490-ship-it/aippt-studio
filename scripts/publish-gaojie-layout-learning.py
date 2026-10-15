#!/usr/bin/env python3
"""Persist observed category learning and expose it as a scoped Studio skill."""
from __future__ import annotations
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from aippt_studio.gaojie_layouts import CATEGORIES, COMPONENT_CATEGORIES, analysis_root, _rows, _sha

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ANALYSIS = analysis_root(LIBRARY)
OBSERVATIONS = ROOT / "aippt-studio/.maintenance/gaojie-learning/source-observations.json"
SKILL = ROOT / ".agents/skills/gaojie-layouts"


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def bullets(items: list[str]) -> str:
    return "\n".join("- " + item for item in items)


def main():
    observed = json.loads(OBSERVATIONS.read_text())
    assert set(observed["categories"]) == set(CATEGORIES)
    build = json.loads((ANALYSIS / "STYLE-BUILD-REPORT.json").read_text())
    samples = json.loads((ANALYSIS / "VISUAL-SAMPLE.json").read_text())
    catalog = _rows(ANALYSIS / "STYLE-CATALOG.jsonl")
    pages = _rows(ANALYSIS / "STYLE-PAGES.jsonl")
    by_page = {(row["source_sha256"], row["slide"]): row for row in pages}
    per_category = {row["category"]: row for row in build["categories"]}
    applications = _rows(ANALYSIS / "APPLICATION-LEARNING.jsonl")
    seen, visual = set(), []
    for item in samples["samples"]:
        if item["status"] != "rendered_advisory":
            continue
        key = (item["category"], item["source_sha256"], item["source_slide"])
        if key in seen:
            continue
        seen.add(key)
        note = observed["categories"][item["category"]]
        override_key = item["resource_id"] + ":" + str(item["source_slide"])
        finding = note["examples"].get(override_key) or note["examples"].get(item["resource_id"])
        if not finding:
            raise ValueError("Missing actual sample finding: " + str(key))
        receipt = json.loads(Path(item["preview_receipt"]).read_text())
        if (receipt.get("source_sha256") != item["source_sha256"] or receipt.get("slide_number") != item["source_slide"]
                or receipt.get("output_sha256") != item["preview_sha256"] or _sha(Path(item["preview"])) != item["preview_sha256"]):
            raise ValueError("Observed sample identity changed: " + str(key))
        override = note.get("overrides", {}).get(override_key) or note.get("overrides", {}).get(item["resource_id"], {})
        page = by_page[(item["source_sha256"], item["source_slide"])]
        visual.append({"schema": "aippt.gaojie-source-learning.v1", "category": item["category"],
                       "resource_id": item["resource_id"], "source_path": item["source_path"], "source_sha256": item["source_sha256"],
                       "source_slide": item["source_slide"], "preview": item["preview"], "preview_sha256": item["preview_sha256"],
                       "preview_receipt": item["preview_receipt"], "preview_receipt_sha256": _sha(Path(item["preview_receipt"])),
                       "finding": finding, "rules": note["rules"], "pitfalls": note["pitfalls"],
                       "actual_function": override.get("actual_function", CATEGORIES[item["category"]][0]),
                       "decision": override.get("decision", "use_with_constraints"),
                       "decision_reason": override.get("reason", "只借经观察的结构，按当前内容映射与复核"),
                       "measured_text_capacity_basis": page["text_characters"],
                       "native_object_counts": page["composition"]["object_counts"],
                       "review_basis": "assistant_observed_actual_approximate_source_page_and_ooxml",
                       "native_powerpoint_qa": False, "full_source_deck_visual_review": False})
    write(ANALYSIS / "VISUAL-LEARNING.jsonl", "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in visual))
    summary_rows = []
    for category, info in CATEGORIES.items():
        category_info = per_category[category]
        notes = observed["categories"][category]
        category_samples = [row for row in visual if row["category"] == category]
        decks = [row for row in catalog if row.get("category") == category and row.get("status") == "parsed"]
        hashes = {row["source_sha256"] for row in decks}
        category_pages = [row for row in pages if row["source_sha256"] in hashes]
        counts = Counter()
        for page in category_pages:
            counts.update(page.get("composition", {}).get("object_counts", {}))
        source_chars = sorted(page["text_characters"] for page in category_pages)
        def quantile(q):
            return source_chars[round((len(source_chars) - 1) * q)] if source_chars else 0
        common = ", ".join(f"{kind}={value}" for kind, value in counts.most_common(7)) or "无本次PPTX结构样本"
        patterns = ", ".join(f"{kind}={value}" for kind, value in category_info["layout_patterns"].items()) or "无"
        sheet_mode = category in COMPONENT_CATEGORIES
        rows = []
        for sample in category_samples:
            page = by_page[(sample["source_sha256"], sample["source_slide"])]
            measure = page["layout_measurements"]
            slots = ",".join(str(value) for value in measure["replaceable_text_ids"][:14]) or "无文字槽"
            groups = [unit["object_id"] for unit in measure["top_level_units"] if unit["type"] == "grpSp"]
            source_link = "../../../" + quote(sample["source_path"], safe="/")
            preview_link = "../静态预览/" + Path(sample["preview"]).name
            rows.append(f"### ID {sample['resource_id']} · 第{sample['source_slide']}页\n\n"
                        f"[源PPTX]({source_link}) · [已观察的页面图]({preview_link})\n\n"
                        f"{sample['finding']}\n\n"
                        f"实际功能：`{sample['actual_function']}`。结构对象：文字{page['composition']['text_object_count']}个，图片{page['image_count']}个，原生图表{page['chart_count']}个，原生表格{page['table_count']}个。"
                        f"文字对象ID：`{slots}`。顶层组ID：`{','.join(map(str, groups[:14])) or '无'}`。\n\n"
                        f"来源SHA-256：`{sample['source_sha256']}`；预览SHA-256：`{sample['preview_sha256']}`。\n")
        status = "完整结构分析与代表页观察已持久化" if category_samples else "无本次PPTX，持久保留用途与资料缺口"
        selection = ("这类常是多个方案的组件板。先读顶层组和组内对象，明确选择一个组件或一组相关对象；"
                     "调用时提供 selected_object_ids，不把整个集合页作为正文。" if sheet_mode else
                     "先根据本页主张、信息关系、证据类型和内容长度选择具体来源页；比较相邻页配色与字体后再应用。")
        applied = [row for row in applications if row.get("category") == category]
        usage_text = "\n\n".join(f"- 源页{Path(row['source_path']).name} / 第{row['source_slide']}页，结果`{row['decision']}`：{row['finding']} 条件：{row['input_constraints']}"
                                   for row in applied) if applied else "本分类本次完成结构分析和代表页观察；实际使用案例待在具体制作中逐次补充。"
        body = f"""# {category}：排版学习与调用

状态：{status}。记录日期：2026-10-09。

## 页面功能与材料要求

任务功能：{info[1]}。所需内容：{info[2]}。选择重点：{info[3]}。

{selection}

## 本次实际覆盖

- 目录内PPTX：{category_info['source_files']}份；成功解析：{category_info['parsed_source_files']}份；去重后的已解析来源：{category_info['unique_parsed_sources']}份。
- 来源页面记录：{category_info['page_records']}条；几何结构指纹：{category_info['unique_structure_signatures']}种；实际观察代表页：{len(category_samples)}页。
- 结构模式统计：{patterns}。
- 原生对象类型统计：{common}。
- 原生图表：{category_info['native_charts']}个；原生表格：{category_info['native_tables']}个。
- 来源页文字字符数p10/p50/p90：{quantile(.1)}/{quantile(.5)}/{quantile(.9)}，仅描述来源，不是替换字数上限。

{category_info['missing_source_reason'] or ('实用素材的3890网站原件损坏，已明确排除结构学习和应用。' if category == '实用素材' else '')}

## 已观察的结构族

{bullets(notes['families']) if notes['families'] else '没有PPTX实页样本，不推导具体结构族。'}

## 怎样使用这些结构

{bullets(notes['rules'])}

## 需要主动检查的限制

{bullets(notes['pitfalls'])}

## 当前内容的映射与容量

先把当前内容整理为中心主张、信息单元、单元间关系和证明材料，再比对真实文字/图片对象与顶层组。
把源页里的“请输入”“字号建议”“模板品牌介绍”等操作或样例文案移出受众页面，不能作为本项目正文保留。
长标题检查实际标题宽度；长正文检查内边距、行距、分栏和图像安全区。来源字体大小和文字数量只能作为观察线索，替换后查看完整页面。
图片明确是主视觉、证据、解释还是背景；需要数据的图式应使用当前指标、单位、时间和比较口径，所有内部数据与标签同步重建。
复用后协调当前整稿的主题、字体和画布，查看前后页，保留对应来源/成稿哈希和具体发现。

## 带来源的代表页判断

{chr(10).join(rows) if rows else '本分类没有本次可分析的PPTX附件；未来收录真实可用材料后再补充。'}

## Studio 调用

在当前Work的wrapper中运行 `library-layout-search --category '{category}' --query '当前页面主张与信息关系' --json`。
检索返回具体源路径、源页、对象组、分类指南和当前观察。用 `library-style-inspect` 读取完整对象信息，并读取来源页图片后再填写调用请求。
完整文字映射或所选组件组通过 `library-layout-build --request <映射.json> --output <当前Work内的候选.pptx> --json` 构建；模板改造中也可作为 `library-hybrid-build` 的已观察供体页。
原生图表/表格/SmartArt的旧内部事实需按当前数据重建，不能用仅替换可见文字的调用方式代替。
完成实际页面观察后，用 `library-layout-learn --request <本次使用记录.json> --json` 持久保存具体适用条件、发现与修正。

## 真实调用得到的经验

{usage_text}

## 证据边界

全量结构记录与代表页视觉判断分别保存；近似预览可帮助判断组织关系，未确认PowerPoint原生字体、动效或逐页验收。该分类指南并不表示目录内每页都经过视觉审阅。
"""
        write(ANALYSIS / "分类学习" / (category + ".md"), body)
        summary_rows.append({**category_info, "visual_observed_pages": len(category_samples),
                             "status": "structure_and_observed_category_learning" if category_samples else "no_pptx_source",
                             "guide": f"分析索引/高阶文化/分类学习/{category}.md"})
    table = "\n".join(f"| [{row['category']}](分类学习/{quote(row['category'])}.md) | {row['source_files']} | {row['page_records']} | {row['visual_observed_pages']} |"
                      for row in summary_rows)
    total_observed = len({(row["source_sha256"], row["source_slide"]) for row in visual})
    report = {"schema": "aippt.gaojie-persistent-learning.v1", "date": "2026-10-09", "categories": summary_rows,
              "source_pptx": build["source_files"], "parsed_pptx": build["parsed_source_files"],
              "unique_parsed_sources": build["unique_parsed_sources"], "unique_pages": build["unique_pages"],
              "observed_source_pages": total_observed, "category_guides": len(summary_rows),
              "categories_with_pptx": sum(row["source_files"] > 0 for row in summary_rows),
              "excluded_corrupt_sources": build["excluded_corrupt_sources"], "native_powerpoint_qa": False,
              "application_learning_events": len(applications), "source_observation_sha256": _sha(OBSERVATIONS),
              "application_usable_with_constraints": sum(row.get("decision") == "use_with_constraints" for row in applications),
              "application_needs_revision": sum(row.get("decision") == "revise_before_reuse" for row in applications),
              "source_manifest_sha256": _sha(ANALYSIS / "SOURCE-MANIFEST.jsonl"),
              "update_model": "incremental source hashes + explicit whole-page observation + concrete application feedback; no weight fine-tuning or unattended approval"}
    write(ANALYSIS / "LEARNING-COVERAGE.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    readme = f"""# 高阶文化：31类持久排版学习

本分析保持 `模板库/高阶文化` 原有31类目录和源PPTX不动。全部5,693份完整文件已解析，得到{build['unique_pages']:,}条去重页面记录；观察了{total_observed}个具体来源页，覆盖29个有PPTX的分类。素材图片和发布会没有本次PPTX附件，保留明确资料缺口。3890损坏原件不参与学习和调用。

学习保存为原生对象/母版/主题结构、页面测量、实际来源页观察、分类使用指南与真实应用记录。后续制作PPT时可按页面功能选择源页或组件，也可据结构规则重建当前内容。机器结构覆盖与视觉样本覆盖分开记录。

| 分类指南 | PPTX份数 | 页面记录 | 已观察代表页 |
| --- | ---: | ---: | ---: |
{table}

## 检索与应用入口

专用skill位于项目 `.agents/skills/gaojie-layouts/SKILL.md`，已接入 aippt-studio 主技能和作者入口。

- `library-layout-search`：按分类/功能查具体源页、可用对象组和持久指南。
- `library-style-search --collection gaojie`：在原Style检索入口查询高阶文化。
- `library-style-inspect`、`library-style-image-inspect`：检查对象、图像、母版及关系。
- `library-layout-build`：复用已观察的单页或明确选择的组件组，逐对象映射全部保留文字，支持多run正文。
- `library-style-page-search`：显式 `layout_collection=gaojie` 可在主稿局部换版时找供体。
- `library-hybrid-build`：沿用当前主模板流程调用已观察的高阶文化源页，并核对画布与主题。
- `library-layout-learn`：把本次实际使用的输入条件、发现和修正绑定源/成稿/页图哈希，写入应用学习。

图表、表格、SmartArt等内部数据应按当前材料重建；文字映射器不能把旧图表值当成新证据。源页的占位说明、无关品牌、人物与数据应清理，整页和相邻页须查看。

## 增量维护

`python3 scripts/with-studio-runtime.py --maintenance -- python scripts/build-gaojie-layout-learning.py --index` 重新核对当前源SHA，复用同SHA/同解析器缓存；来源发生变化时应先登记新的真实文件身份，再重建索引。旧页图观察以源SHA绑定，无法替换对新来源的观察。

`render-gaojie-learning-samples.py` 生成有明确来源/页号/哈希的近似预览。必须实际读取页面图后才能加入 `VISUAL-LEARNING.jsonl`，渲染成功不等于理解。

`publish-gaojie-layout-learning.py` 将已观察的证据与规则持久化；实际应用记录由 `library-layout-learn` 追加，保留问题案例及适用边界。重复相同应用不重复记录，写入使用独占锁。

本次验证案例与机器对账见项目维护区 `aippt-studio/.maintenance/gaojie-learning/`。这套持久学习依赖可读取的知识与来源证据，不表示修改模型权重，也不推定用户验收。
"""
    write(ANALYSIS / "README.md", readme)
    write(SKILL / "references/categories.md", "# 31类排版使用规则\n\n按当前页的实际功能选择分类，读取对应的详细分析。不同目录中的同功能页可以比较，但仍查看来源页。\n\n" +
          "\n".join(f"- [{name}](../../../../模板库/分析索引/高阶文化/分类学习/{quote(name)}.md)：{description[1]}；{description[3]}。" for name, description in CATEGORIES.items()) + "\n")
    write(ANALYSIS / "学习进度.json", json.dumps({"status": "category_learning_persisted", "updated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
          "source_files": build["source_files"], "parsed_source_files": build["parsed_source_files"], "unique_pages": build["unique_pages"],
          "category_guides": 31, "observed_source_pages": total_observed, "application_learning_events": len(applications)}, ensure_ascii=False, indent=2))
    print(json.dumps({key: report[key] for key in ("parsed_pptx", "unique_pages", "observed_source_pages", "category_guides", "categories_with_pptx", "application_learning_events")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
