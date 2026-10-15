#!/usr/bin/env python3
"""Build real short/long source adaptations for whole-page observation."""
from __future__ import annotations
import json
import argparse
from pathlib import Path
from zipfile import ZipFile

from lxml import etree as ET
from aippt_studio.gaojie_layouts import analysis_root, build_gaojie_layout_candidate, _rows, _sha
from aippt_studio.motion_clone import _read_xml, _safe_entries, _slide_objects, _slide_order, text_sha256
from aippt_studio.local_preview import render_slide_preview

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ANALYSIS = analysis_root(LIBRARY)
OUTPUT = ROOT / "aippt-studio/.maintenance/gaojie-learning/usage"

CASES = {
    "three-part": {"id": "1144", "selection": "三项行动是同级要点，分别由环保、协作和记录图形辅助；完整替换原咖啡产品文案。",
                   "short": {"6": "社区共创三项行动", "7": "共同参与绿色活动，以协作推进服务，用记录支持复盘。", "12": "绿色共创", "11": "组织社区绿化活动，邀请居民共同参与。",
                             "18": "社区协作", "17": "明确参与角色，形成清楚的分工与沟通方式。", "24": "跟踪复盘", "23": "记录实施过程，整理居民反馈，持续改进服务。"},
                   "long": {"6": "社区共创的三项关键行动", "7": "围绕公共空间、居民参与和服务反馈，建立彼此支持的协作机制，推动社区行动持续改进。",
                            "12": "绿色活动共同参与", "11": "从居民实际需要出发，选择适合社区公共空间的绿色活动。将准备、实施和维护安排清楚，让不同年龄的居民都能找到可参与的工作。",
                            "18": "社区服务协作机制", "17": "明确居民、志愿者和服务人员的角色，建立可追踪的沟通与分工机制。通过共同确认任务、反馈进展和处理问题，形成稳定的协作方式。",
                            "24": "记录反馈持续改进", "23": "把实施过程、参与者反馈和待解决的问题整理为可复盘记录。区分观察到的结果与预期效果，据此调整下一轮活动，而不是只记录活动数量。"}},
    "timeline": {"id": "1659", "selection": "借日期轴与上下交替说明表达五个制作阶段，只表达顺序，不将等距布局视为真实耗时比例。",
                 "short": {"43": "页面制作的五个阶段", "57": "阶段一", "55": "明确听众\n确认页面要解决的问题。", "49": "阶段二", "50": "整理证据\n选出能支持主张的材料。",
                           "64": "阶段三", "62": "选择结构\n按信息关系确定页面组织。", "45": "阶段四", "46": "完成映射\n替换当前文字与材料。", "71": "阶段五", "69": "整页复核\n检查表达并保存可编辑稿。"},
                 "long": {"43": "从内容判断到可编辑页面的五个阶段", "57": "明确听众", "55": "先了解听众需要什么，以及演示场合。把本页想表达的核心结论写清楚，再决定需要哪些信息。",
                          "49": "整理证据", "50": "从当前材料中选出能够支持结论的图片、数字和事实。保留时间、单位与比较基准，避免用来源示例替代真实依据。",
                          "64": "选择结构", "62": "判断信息是并列、对比、层级还是先后。选择能表达这种关系的来源页或组件，阅读其对象组和真实整页。",
                          "45": "内容映射", "46": "把当前主张、解释与证据映射到具体对象，处理来源品牌、人物、数据和占位说明，保留必要的图文关系。",
                          "71": "整页复核", "69": "查看改造后的完整页面与前后页，检查层级、比例、断行和图像意义。保存可编辑文件及值得复用的具体发现。"}},
    "architecture": {"id": "2027", "selection": "原页是一个主节点、五个能力组与组内子项；复用层级关系，全部销售管理字段改为版式应用能力。",
                     "short": {"5": "版式应用五项能力", "41": "页面适配", "29": "资料核对", "31": "结构选择", "33": "内容映射", "35": "视觉复核", "37": "证据保存",
                               "9": "来源材料", "10": "页面主张", "11": "图像关系", "12": "信息单元", "13": "单元关系", "14": "阅读顺序", "15": "对象分组",
                               "16": "标题改写", "17": "正文改写", "18": "图像替换", "19": "单位口径", "20": "旧文清理",
                               "21": "全页层级", "22": "文字断行", "23": "对象重叠", "24": "相邻页面", "25": "来源身份", "26": "应用发现"},
                     "long": {"5": "从资料到成稿的版式应用能力", "41": "当前页面适配", "29": "真实资料核对", "31": "内容关系选型", "33": "当前内容映射", "35": "整页表达复核", "37": "具体经验保存",
                              "9": "核对材料来源", "10": "明确页面主张", "11": "确认图像意义", "12": "划分信息单元", "13": "分析单元关系", "14": "设计阅读顺序", "15": "检查原生分组",
                              "16": "映射当前标题", "17": "重写说明正文", "18": "替换对应图像", "19": "确认统计口径", "20": "清理来源占位",
                              "21": "判断视觉层级", "22": "检查文字断行", "23": "处理对象重叠", "24": "协调相邻页面", "25": "绑定来源身份", "26": "保存真实发现"}},
    "title-component": {"id": "6195", "groups": ["21"], "position": [.10, .12],
                        "selection": "源页是十款标题组件的集合，仅选取含主标题与副标题的顶层组21，保持组内原生关系并移动到当前标题区。",
                        "short": {"22": "社区共创", "24": "协作、行动与反馈"},
                        "long": {"22": "从社区共创到居民协作的行动机制", "24": "围绕共同参与、清楚分工和持续复盘组织页面内容"}},
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--adjust", action="store_true", help="Build only revised mappings for the observed title-wrap failures")
    args = parser.parse_args()
    catalog = _rows(ANALYSIS / "STYLE-CATALOG.jsonl")
    observed = _rows(ANALYSIS / "VISUAL-LEARNING.jsonl")
    previous_file = OUTPUT / "USAGE-VALIDATION.json"
    results = json.loads(previous_file.read_text())["cases"] if args.adjust else []
    for name, case in CASES.items():
        deck = next(row for row in catalog if row.get("resource_id") == case["id"])
        observation = next(row for row in observed if row["resource_id"] == case["id"] and row["source_slide"] == 1)
        source = LIBRARY / deck["path"]
        with ZipFile(source) as archive:
            entries = _safe_entries(archive)
            _, parts = _slide_order(archive, entries)
            objects = _slide_objects(_read_xml(archive, entries, parts[0]))
            original_texts = {oid: "".join(node.text or "" for node in obj.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t"))
                              for oid, obj in objects.items() if ET.QName(obj).localname == "sp"}
        if args.adjust and name == "timeline":
            continue
        variants = (("long-width-adjusted",) if name == "title-component" else ("long-adjusted",)) if args.adjust else ("short", "long")
        for variant in variants:
            mapping = dict(case["long"] if args.adjust else case[variant])
            if args.adjust and name == "three-part":
                mapping.update({"6": "社区共创三项行动", "12": "绿色活动共创", "18": "社区服务协作", "24": "持续记录反馈"})
            if args.adjust and name == "architecture":
                mapping.update({"5": "版式应用能力", "41": "页面适配"})
            base = OUTPUT / name / variant
            base.mkdir(parents=True, exist_ok=True)
            request = {"source_path": deck["path"], "source_sha256": deck["source_sha256"], "source_slide": 1,
                       "selection_note": case["selection"],
                       "reviewed_source_page": {"slide": 1, "finding": observation["finding"], "preview_receipt": observation["preview_receipt"]},
                       "text_replacements": [{"shape_id": oid, "original_text_sha256": text_sha256(original_texts[oid]), "new_text": text}
                                             for oid, text in mapping.items()]}
            if case.get("groups"):
                request.update(selected_object_ids=case["groups"], component_position_fraction=case["position"])
            if args.adjust and name == "title-component":
                request["component_text_widths"] = [{"shape_id": "22", "width_fraction": .70}, {"shape_id": "24", "width_fraction": .70}]
            request_file = base / "mapping.json"
            request_file.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
            pptx = base / "candidate.pptx"
            if args.adjust and pptx.is_file():
                # Resume only our exact mapping output after a later operation
                # failed; do not rebuild successful probes or overwrite files.
                result = {"status": "layout_candidate_built", "source_path": deck["path"], "source_sha256": deck["source_sha256"],
                          "source_slide": 1, "mapped_text_objects": len(mapping), "output": str(pptx), "output_sha256": _sha(pptx)}
            else:
                result = build_gaojie_layout_candidate(LIBRARY, request, pptx)
            png = base / "preview.png"
            preview_receipt = base / "preview.preview.json"
            if not png.is_file() or not preview_receipt.is_file():
                render_slide_preview(pptx, 1, png, receipt_path=preview_receipt, width=1280, load_system_fonts=True)
            result.update(case=name, variant=variant, preview=str(png), preview_sha256=_sha(png), preview_receipt=str(preview_receipt),
                          source_preview_receipt=observation["preview_receipt"], internal_test_content=True,
                          source_content_replaced=True, visual_review="awaiting_actual_whole_page_observation")
            results.append(result)
            print(json.dumps({"case": name, "variant": variant, "mapped_text_objects": result["mapped_text_objects"], "output": str(pptx)}, ensure_ascii=False), flush=True)
    (OUTPUT / "USAGE-VALIDATION.json").write_text(json.dumps({"schema": "aippt.gaojie-layout-usage-validation.v1", "cases": results,
                                                           "native_powerpoint_qa": False, "source_files_changed": False}, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
