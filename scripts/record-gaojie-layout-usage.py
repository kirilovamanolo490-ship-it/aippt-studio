#!/usr/bin/env python3
"""Record actually observed positive and failed layout adaptations."""
from __future__ import annotations
import json
from pathlib import Path
from aippt_studio.gaojie_layouts import record_layout_application, _sha

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
OUTPUT = ROOT / "aippt-studio/.maintenance/gaojie-learning/usage"

REVIEWS = {
    ("three-part", "short"): ("use_with_constraints", "主标题、三个要点和正文彼此分离，三枚图标与绿色活动/协作/记录的语义对应；实际整页未见文字重叠。",
                               "适用于三个同级观点、短标题和简明正文；源页标题框偏窄，内容变长需查看整页。"),
    ("three-part", "long"): ("revise_before_reuse", "主标题换为两行后第二行与引导句重叠；中间要点标题换行并接近下划线，长正文仍在各列内。",
                              "保持三列结构时应精炼主标题/单元标题或改变标题框关系，不能只把长文字填入原框。"),
    ("three-part", "long-adjusted"): ("use_with_constraints", "精炼主标题与三项标题后，保留的长正文仍有清楚列间隔，标题、说明、图标各有独立区域，重叠已消除。",
                                       "长正文可在当前三列结构内使用，但标题需短而明确；这次文字不代表任何字数均安全。"),
    ("timeline", "short"): ("use_with_constraints", "五个阶段在横轴上按顺序排列，解释上下交替且不互相遮挡；当前轴只表示流程次序，不表示真实时长。",
                            "适用于五个阶段和短说明；日期/时长有比例需求时需另用对应尺度的时间轴。"),
    ("timeline", "long"): ("use_with_constraints", "标题为两行但与上方事件说明保持间隔，较长说明在上下区域内可读，最右下说明接近原节点末端仍未越出画布。",
                           "五节点长说明需逐栏查看；本例底部说明已较满，再增加内容时应扩大区域、分阶段或换版。"),
    ("architecture", "short"): ("revise_before_reuse", "五组能力与层级连线保持正确，组内竖向短标签可辨；页标题出现单字换行，当前短文案也超出原标题框的单行容量。",
                                "层级关系可借，原页主标题区窄，不能按所谓短文案自动判定可用；必须看实际断行。"),
    ("architecture", "long"): ("revise_before_reuse", "长标题在左侧形成两行，主节点内六字标题换成两行并落出蓝色节点，五组分支仍保持关系。",
                               "主节点标题应采用更短概念或重做节点尺寸，不缩小所有分支字体来弥补一个节点。"),
    ("architecture", "long-adjusted"): ("use_with_constraints", "主标题与主节点改为清晰短概念后均为单行；五组名称和竖向子项未见截断，主节点-能力组-子项层级保留。",
                                        "适用于短名称的功能层级树；长句或说明应放到其他布局，不直接塞入窄竖条。"),
    ("title-component", "short"): ("use_with_constraints", "只保留标题+副标题组，组被移到左上标题区；源页其他九种备选组件没有出现在目标页，短文字没有重叠。",
                                    "该组用于局部标题，不是整页内容版式；短主副标题保持原文字区可用。"),
    ("title-component", "long"): ("revise_before_reuse", "长主标题在原组件文字区换行并与副标题重叠；仅移动组不能解决组内文字容量问题。",
                                   "需要扩宽两个文本框、换用更宽组件或精炼主副标题；不能只缩放整个组来改变字体。"),
    ("title-component", "long-width-adjusted"): ("use_with_constraints", "明确扩宽组内主、副标题的文字区并扩大组界，保持ext/chExt比例后原字号未变；长标题和副标题各自一行，重叠已消除。",
                                               "适用于当前页有足够横向留白且组无旋转/翻转的条件；宽度按当前画布设置，不作为固定模板尺寸。"),
}


def main():
    path = OUTPUT / "USAGE-VALIDATION.json"
    packet = json.loads(path.read_text())
    assert len(packet["cases"]) == 11
    results = []
    for case in packet["cases"]:
        decision, finding, constraints = REVIEWS[(case["case"], case["variant"])]
        what = {"three-part": "复用三列同级要点、主标题和下方语义图标的对应关系",
                "timeline": "复用横轴五节点与说明上下交替的顺序关系",
                "architecture": "复用主节点、五个能力组和组内子项的三级层级关系",
                "title-component": "从十款组件集合中选择组21，复用主副标题与左色条关系"}[case["case"]]
        request = {"source_path": case["source_path"], "source_sha256": case["source_sha256"], "source_slide": case["source_slide"],
                   "source_preview_receipt": case["source_preview_receipt"], "output_pptx": case["output"], "output_sha256": case["output_sha256"],
                   "output_slide": 1, "output_preview_receipt": case["preview_receipt"], "finding": finding, "what_reused": what,
                   "input_constraints": constraints, "outcome_issues": finding if decision == "revise_before_reuse" else "在本次指定文案和结构条件下未观察到文字重叠；仍需按新输入重新检查整页。",
                   "decision": decision}
        request_path = Path(case["output"]).parent / "learning-request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n")
        results.append(record_layout_application(LIBRARY, request))
        case.update(visual_review="assistant_observed_actual_whole_page", finding=finding,
                    input_constraints=constraints, decision=decision, learning_request=str(request_path))
    packet.update(source_files_changed=False, actual_observed_candidates=11, usable_with_conditions=7, needs_revision=4,
                  source_observation_scope="four_page/component structures, not full-library visual or native PowerPoint QA")
    path.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"recorded_cases": len(results), "usable_with_conditions": 7, "needs_revision": 4}, ensure_ascii=False))


if __name__ == "__main__":
    main()
