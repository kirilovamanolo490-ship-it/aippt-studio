"""Source-bound learning and page discovery for Gaojie layout components."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .library_read_guard import require_library_available

GAOJIE = "高阶文化"
INDEX_VERSION = "gaojie-layout-learning-1"
COMPONENT_CATEGORIES = {"标题模板", "实用素材", "风格配色", "数据基座", "文本组件", "装饰形状"}

# These describe the requested page function, not a visual judgment about every
# source in a folder. Geometry and source-page observations supply that evidence.
CATEGORIES = {
    "封面模板": ("cover", "确立主题、场合和主视觉", "主题标题、定位句、署名或日期", "把主视觉与主题建立关系；检查主副标题的面积和对齐"),
    "目录模板": ("agenda", "展示章节结构与阅读路径", "章节名称、真实章节顺序", "比较列表、分栏、路径和节点式导航；保持章节名称层级"),
    "章节模板": ("section_divider", "切换到下一叙事阶段", "章节名称、必要的章节序号", "保留整稿主视觉语言，并通过尺度或留白形成停顿"),
    "标题模板": ("statement", "突出本页或本段的核心主张", "主张、限定词、必要的短说明", "判断标题本身还是正文是主角；避免将装饰字替代信息层级"),
    "结尾模板": ("closing_outlook", "收束结论或明确下一步", "结论、行动、必要的联系方式", "比较总结型、行动型与感谢型结尾，按场合选择"),
    "一段内容": ("single_message", "说明一个中心观点", "一个观点与支持证据", "区分纯文、文图和主张加证据；保持唯一视觉重心"),
    "二段内容": ("two_part_content", "表达两个信息单元的关系", "两个主张、对照项或阶段", "先确定并列、对照或因果，再选分栏或相连结构"),
    "三段内容": ("three_part_content", "组织三个相关信息单元", "三个要点及各自证据", "比较横向并列、中心辐射、步骤与三角关系；不默认三等分"),
    "四段内容": ("four_part_content", "组织四个信息单元", "四个要点、维度或阶段", "四象限只用于真实两维关系；区分矩阵、并列和顺序"),
    "五段内容": ("five_part_content", "组织五个信息单元", "五个要点或阶段", "检查横排、折行和路径布局的阅读顺序与标题长度"),
    "六段内容": ("six_part_content", "组织六个信息单元", "六个要点或阶段", "比较两行、三列、环形与路径结构；密度过高时分配到相邻页"),
    "多段内容": ("multi_part_content", "组织较密的信息并保持主次", "多级要点及分组标题", "按真实层级分组，用主次和顺序组织，不用缩字挤满"),
    "人物介绍": ("team_organization", "建立人物身份与可信经历", "姓名、角色、肖像、能力和经历", "判断个人特写与多人组织图；肖像、身份和证明必须对应"),
    "荣誉奖项": ("evidence_results", "呈现荣誉及其证明", "奖项名称、等级、年份、证书", "区分突出单项与多项陈列；事实、证书图和机构名称逐一对应"),
    "地图排版": ("geographic_distribution", "说明地理范围或区域差异", "地区、地点、数据和图例", "地图用于空间关系；核对区域、标记、统计口径与图例"),
    "时间轴图": ("timeline_plan", "说明时间顺序与阶段变化", "日期、阶段、事件和里程碑", "区分顺序与时长；不等间隔的时间不要伪装成比例轴"),
    "架构流程": ("process_architecture", "说明层级、步骤或流向", "节点、关系、方向和分支", "先确定层级还是流程；保留箭头端点、分组和连接含义"),
    "商业模型": ("budget_model", "说明价值、交易或运营机制", "角色、价值、资源、渠道和流向", "模型应表达机制，先写清关系再映射到画布"),
    "样机展示": ("product_demonstration", "在载体中展示产品或界面", "设备外框、界面图、功能说明", "替换真实界面；检查裁切、透视、比例与设备边缘"),
    "金句模板": ("quote", "强化一句关键表达", "核心句、必要的作者或来源", "看断行、留白和背景关系；引用归属须与当前内容一致"),
    "合作伙伴": ("partner_network", "说明协作关系或机构覆盖", "伙伴名称、标识、合作角色", "区分标识陈列与协作网络；关系不能只靠摆放暗示"),
    "图文排版": ("image_text_content", "用文字和图像共同支持主张", "主张、图片、说明、图注", "判断图片是证据、解释还是气氛；比较左右、上下与穿插关系"),
    "表格图表": ("data_comparison", "表达数量、比例、变化或比较", "指标、数值、单位、时间和来源", "区分原生图表、原生表格与形状模拟；选择与数据关系匹配的图式"),
    "素材图片": ("image_asset", "为页面选择有意义的图片", "当前项目图片及其语义", "本次没有PPTX来源，不能从空目录推导实页版式"),
    "实用素材": ("utility_component", "复用实用图形和页面组件", "可复用对象及其依赖", "按具体对象检索；损坏原件排除，确认组内对象与媒体关系"),
    "风格配色": ("color_system", "建立或协调视觉系统", "主色、辅色、强调色与背景色", "统计实际使用色与主题槽位，避免把未使用主题颜色当主色"),
    "专题模板": ("thematic_deck", "参考同一专题的整套视觉与页序", "整稿主题、页序、章节和典型内容页", "观察封面与内容页的一致性；专题事实必须替换"),
    "数据基座": ("metric_component", "突出关键数字及其口径", "数值、单位、标签、时间和基准", "数字组件仍须完整单位和比较基准，装饰不应压过指标含义"),
    "文本组件": ("text_component", "复用局部文字组织与强调方式", "标题、说明、强调词或短句", "先判断文字功能，再复用强调与对齐；不带入原样例文案"),
    "装饰形状": ("shape_component", "以图形帮助分组或强调", "有意义的形状、轮廓与连接", "检查形状的作用和组关系；不额外加入无意义编号或线条"),
    "发布会": ("launch_case", "参考发布场合的叙事与视觉", "发布主题、产品、证据和行动", "本次来源均为PDF，没有PPTX可分析；保留资料缺口"),
}


def analysis_root(library: Path) -> Path:
    return Path(library) / "分析索引" / GAOJIE


def style_analysis_root(library: Path, family: str) -> Path:
    return analysis_root(library) if family == GAOJIE else Path(library) / family / "分析索引"


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()] if path.is_file() else []


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def gaojie_curation(library: Path) -> dict[str, dict]:
    out = {}
    for row in _rows(Path(library) / "CONTENT-CURATION.jsonl"):
        path = row.get("path", "")
        if not isinstance(path, str) or not path.startswith(GAOJIE + "/"):
            continue
        evidence = row.get("evidence") or {}
        unavailable = evidence.get("availability_status") == "unavailable_corrupt_source"
        out[path] = {"source_sha256": row.get("sha256"), "asset_type": row.get("asset_type"),
                     "category": row.get("category"), "confidence": row.get("confidence"),
                     "off_topic": False, "flattened_page_structure": False,
                     "unavailable": unavailable, "curation_stale": unavailable,
                     "evidence": evidence, "profession_candidates": []}
    return out


def page_measurements(deck: dict, page: dict) -> dict:
    """Capture group-level geometry and object relationships without pixel claims."""
    size = deck.get("presentation_size") or {}
    width, height = size.get("width_emu") or 0, size.get("height_emu") or 0
    objects = page.get("objects") or []
    units, font_sizes, faces, colors = [], [], Counter(), Counter()
    for obj in objects:
        for run in obj.get("text_runs", []):
            if run.get("font_size_pt"):
                font_sizes.append(run["font_size_pt"])
            face = run.get("east_asian_typeface") or run.get("latin_typeface")
            if face:
                faces[face] += 1
            color = run.get("color") or {}
            if color.get("kind") == "srgbClr":
                colors[color["val"]] += 1
        if obj.get("group_path"):
            continue
        geo = obj.get("geometry") or {}
        normalized = None
        if width and height and all(isinstance(geo.get(key), (int, float)) for key in ("x_emu", "y_emu", "width_emu", "height_emu")):
            normalized = [round(geo["x_emu"] / width, 4), round(geo["y_emu"] / height, 4),
                          round(geo["width_emu"] / width, 4), round(geo["height_emu"] / height, 4)]
        children = [child for child in objects if any(parent.get("id") == obj.get("id") for parent in child.get("group_path", []))]
        members = children if obj.get("type") == "grpSp" else [obj]
        units.append({"object_id": obj.get("id"), "type": obj.get("type"), "box_fraction": normalized,
                      "text": "\n".join(member.get("text", "") for member in members if member.get("text")),
                      "text_objects": sum(bool(member.get("text", "").strip()) for member in members),
                      "image_objects": sum(bool(member.get("image_fills")) for member in members),
                      "group_member_ids": [member.get("id") for member in children],
                      "rotation": geo.get("rotation_60000deg"),
                      "geometry_basis": "explicit_top_level_box; child_coordinates_remain_in_group_space"})
    signature_items = sorted((unit["type"], tuple(round(value * 10) for value in unit["box_fraction"]) if unit["box_fraction"] else (),
                              min(unit["text_objects"], 8), min(unit["image_objects"], 8)) for unit in units)
    signature = hashlib.sha256(json.dumps(signature_items, ensure_ascii=False).encode()).hexdigest()[:16]
    sizes = sorted(font_sizes)
    def quantile(q: float) -> float | None:
        return sizes[round((len(sizes) - 1) * q)] if sizes else None
    return {"basis": "source_ooxml_measurements_not_visual_judgment", "structure_signature": signature,
            "top_level_units": units, "group_count": sum(obj.get("type") == "grpSp" for obj in objects),
            "connector_count": sum(obj.get("type") == "cxnSp" for obj in objects),
            "font_sizes_pt": {"p10": quantile(.1), "p50": quantile(.5), "p90": quantile(.9)},
            "font_faces_top": faces.most_common(8), "text_colors_top": colors.most_common(8),
            "replaceable_text_ids": [obj.get("id") for obj in objects if obj.get("text", "").strip()],
            "image_ids": [obj.get("id") for obj in objects if obj.get("image_fills")],
            "native_graphics": [dict(object_id=obj.get("id"), **obj["graphic"]) for obj in objects if obj.get("graphic")],
            "visual_status": "not_reviewed"}


def search_gaojie_layouts(library: Path, query: str, *, category: str | None = None,
                         roles: list[str] | None = None, limit: int = 8) -> dict:
    """Return function-matched source pages plus persistent category guidance."""
    from .style_search import _safe_source
    library = Path(library).resolve()
    require_library_available(library)
    if not query.strip() or not 1 <= limit <= 30 or (category is not None and category not in CATEGORIES):
        raise ValueError("query, a known optional category and limit 1..30 are required")
    base = analysis_root(library)
    catalog = _rows(base / "STYLE-CATALOG.jsonl")
    by_sha = {}
    for deck in catalog:
        if deck.get("status") == "parsed":
            by_sha.setdefault(deck["source_sha256"], []).append(deck)
    curations = gaojie_curation(library)
    explicit_categories = [category] if category else [name for name in CATEGORIES if name in query]
    aliases = {"两段": "二段内容", "双栏": "二段内容", "时间轴": "时间轴图", "流程": "架构流程",
               "图表": "表格图表", "数据组件": "数据基座", "图文": "图文排版", "人物": "人物介绍",
               "封面": "封面模板", "目录": "目录模板", "结尾": "结尾模板", "地图": "地图排版"}
    if not explicit_categories:
        explicit_categories = list(dict.fromkeys(name for cue, name in aliases.items() if cue in query))
    wanted_roles = {role for role in (roles or []) if role}
    if wanted_roles and not explicit_categories:
        explicit_categories = [name for name, info in CATEGORIES.items() if info[0] in wanted_roles]
    learned = {str(row.get("source_sha256")) + ":" + str(row.get("source_slide")): row
               for row in _rows(base / "VISUAL-LEARNING.jsonl")}
    from .style_page_match import _terms
    terms = _terms(query)
    wanted_patterns = {kind for cue, kind in {
        "左图右文": "image_left_text_right", "图片在左": "image_left_text_right",
        "右图左文": "text_left_image_right", "图片在右": "text_left_image_right",
        "多图": "multi_image_gallery", "图集": "multi_image_gallery",
        "原生图表": "structured_data_frame", "数据比较": "structured_data_frame",
    }.items() if cue in query}
    usage = {}
    for row in _rows(base / "APPLICATION-LEARNING.jsonl"):
        usage.setdefault(str(row.get("source_sha256")) + ":" + str(row.get("source_slide")), []).append(row)
    candidates = []
    for page in _rows(base / "STYLE-PAGES.jsonl"):
        if page.get("hidden") or page.get("page_status") not in {None, "parsed"}:
            continue
        for deck in by_sha.get(page["source_sha256"], []):
            cat = deck.get("category")
            if explicit_categories and cat not in explicit_categories:
                continue
            if wanted_roles and CATEGORIES.get(cat, ("",))[0] not in wanted_roles and not wanted_roles.intersection(
                    item.get("role") for item in page.get("role_evidence", [])):
                continue
            path = deck["path"]
            meta = curations.get(path, {})
            if meta.get("unavailable") or meta.get("asset_type") != "template" or meta.get("source_sha256") != deck["source_sha256"]:
                continue
            text = (str(page.get("title", "")) + " " + str(page.get("text_excerpt", ""))).lower()
            observed = learned.get(deck["source_sha256"] + ":" + str(page["slide"]))
            if observed and observed.get("decision") == "avoid":
                continue
            if observed and wanted_roles and observed.get("actual_function") not in wanted_roles:
                continue
            if observed:
                text += " " + observed.get("finding", "").lower()
            overlap = [term for term in terms if term in text]
            measure = page.get("layout_measurements") or {}
            pattern_match = (page.get("layout_pattern") or {}).get("kind") in wanted_patterns
            if not explicit_categories and not wanted_roles and not overlap and not pattern_match:
                continue
            score = 5 * bool(explicit_categories) + 3 * bool(wanted_roles) + 2 * len(overlap) + 4 * pattern_match
            # Observation refines an otherwise equally relevant structural match.
            candidates.append({"source_path": path, "source_sha256": deck["source_sha256"],
                               "source_slide": page["slide"], "category": cat, "score": score,
                               "title": page.get("title"), "detail_ref": deck["detail_ref"],
                               "detail_sha256": deck["detail_sha256"],
                               "layout_pattern": page.get("layout_pattern"), "layout_measurements": measure,
                               "category_function": CATEGORIES[cat][1], "category_guidance": CATEGORIES[cat][3],
                               "reuse_granularity": "select_component_objects_or_group" if cat in COMPONENT_CATEGORIES else "page_layout",
                               "native_editability": "text_and_native_objects" if page.get("composition", {}).get("text_object_count", 0) else
                                   "native_vector_asset" if page.get("composition", {}).get("object_counts", {}).get("sp", 0) else "image_asset_without_native_text",
                               "learning_ref": f"分析索引/{GAOJIE}/分类学习/{cat}.md",
                               "content_term_overlap": overlap, "design_observation": observed,
                               "requested_layout_patterns": sorted(wanted_patterns), "layout_pattern_match": pattern_match,
                               "application_lessons": usage.get(deck["source_sha256"] + ":" + str(page["slide"]), [])[-8:],
                               "visual_status": "observed_approximate_source_page" if observed else "not_reviewed",
                               "reuse_status": "source_page_candidate_requires_current_content_mapping",
                               "collection": "gaojie", "asset_type": "template",
                               "media_rights": "unverified"})
    candidates.sort(key=lambda row: (-row["score"], row["design_observation"] is None, row["source_path"], row["source_slide"]))
    shown, seen, checked, stale = [], set(), {}, []
    for row in candidates:
        key = (row["source_sha256"], row["source_slide"])
        if key in seen:
            continue
        path = _safe_source(library, row["source_path"])
        if path is None:
            stale.append(row["source_path"])
            continue
        digest = checked.setdefault(path, _sha(path) if path not in checked else checked[path])
        if digest != row["source_sha256"]:
            stale.append(row["source_path"])
            continue
        detail = _safe_source(library, row["detail_ref"])
        if detail is None or _sha(detail) != row["detail_sha256"]:
            stale.append(row["source_path"])
            continue
        observation = row.get("design_observation")
        if observation:
            preview = Path(str(observation.get("preview", "")))
            if not preview.is_file() or _sha(preview) != observation.get("preview_sha256"):
                row.update(design_observation=None, visual_status="stale_observation")
        shown.append(row)
        seen.add(key)
        if len(shown) == limit:
            break
    return {"schema": "aippt.gaojie-layout-search.v1", "query": query, "collection_intent": "gaojie",
            "status": "ready" if shown else "no_match", "candidate_count": len(shown), "candidates": shown,
            "requested_categories": explicit_categories, "stale_source_paths": sorted(set(stale)),
            "boundary": "Source page measurements and observed sample judgments guide adaptation; replace source facts and review the current whole page."}


def record_layout_application(library: Path, request: dict) -> dict:
    """Retain an observed actual adaptation; successful rendering alone is insufficient."""
    from datetime import datetime
    import os
    from zoneinfo import ZoneInfo
    from .style_search import _safe_source
    library = Path(library).resolve()
    require_library_available(library)
    if not isinstance(request, dict):
        raise ValueError("learning request must be an object")
    for field in ("finding", "what_reused", "input_constraints", "outcome_issues"):
        if not isinstance(request.get(field), str) or len(request[field].strip()) < 8:
            raise ValueError(f"actual adaptation needs a specific {field}")
    if request.get("decision") not in {"use_with_constraints", "revise_before_reuse", "avoid_this_mapping"}:
        raise ValueError("decision must describe the observed mapping outcome")
    source = _safe_source(library, request.get("source_path", ""))
    if source is None or not str(request["source_path"]).startswith(GAOJIE + "/"):
        raise ValueError("learning source must be an existing Gaojie page")
    digest = _sha(source)
    if request.get("source_sha256") != digest or type(request.get("source_slide")) is not int:
        raise ValueError("learning source identity must match current source bytes and page")
    row = next((item for item in _rows(analysis_root(library) / "STYLE-CATALOG.jsonl") if item.get("path") == request["source_path"]), None)
    if row is None or row.get("status") != "parsed" or not 1 <= request["source_slide"] <= row["slide_count"]:
        raise ValueError("learning requires a parsed, in-range source page")
    output = Path(str(request.get("output_pptx", ""))).expanduser().resolve()
    if not output.is_file() or output.suffix.lower() != ".pptx" or output.is_relative_to(library):
        raise ValueError("actual adaptation must be an existing PPTX outside the source library")
    output_hash = _sha(output)
    if request.get("output_sha256") != output_hash:
        raise ValueError("actual adaptation output hash changed")
    if type(request.get("output_slide")) is not int or request["output_slide"] < 1:
        raise ValueError("actual adaptation needs a positive output page number")
    previews = []
    for field, expected_hash, expected_slide in (("source_preview_receipt", digest, request["source_slide"]),
                                                ("output_preview_receipt", output_hash, request.get("output_slide"))):
        receipt_path = Path(str(request.get(field, ""))).expanduser()
        receipt = json.loads(receipt_path.read_text())
        preview = Path(str(receipt.get("output_png", "")))
        if (receipt.get("status") != "rendered_advisory" or receipt.get("source_sha256") != expected_hash or receipt.get("slide_number") != expected_slide
                or not preview.is_file() or _sha(preview) != receipt.get("output_sha256")):
            raise ValueError("observed preview does not bind the current source/output page")
        previews.append({"receipt": str(receipt_path.resolve()), "receipt_sha256": _sha(receipt_path),
                         "preview": str(preview), "preview_sha256": receipt["output_sha256"]})
    result = {key: request[key] for key in ("source_path", "source_sha256", "source_slide", "output_slide", "finding", "what_reused", "input_constraints", "outcome_issues", "decision")}
    result.update(schema="aippt.gaojie-application-learning.v1", category=row["category"],
                  recorded_at=datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
                  output_pptx=str(output), output_sha256=output_hash, preview_evidence=previews,
                  review_basis="assistant_actual_whole_page_observation", user_acceptance="not_inferred")
    path = analysis_root(library) / "APPLICATION-LEARNING.jsonl"
    identity = (digest, request["source_slide"], output_hash, request["output_slide"], request["decision"])
    from filelock import FileLock
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path.with_suffix(".lock")), timeout=10):
        prior = _rows(path)
        if not any((entry.get("source_sha256"), entry.get("source_slide"), entry.get("output_sha256"), entry.get("output_slide"), entry.get("decision")) == identity for entry in prior):
            prior.append(result)
            temporary = path.with_suffix(".jsonl.tmp")
            temporary.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in prior), encoding="utf-8")
            os.replace(temporary, path)
    return {"status": "application_learning_recorded", "path": str(path), "events": len(prior), "source_path": request["source_path"], "decision": request["decision"]}


def build_gaojie_layout_candidate(library: Path, request: dict, output: Path) -> dict:
    """Reuse one reviewed page or selected native component, preserving source bytes."""
    import os
    import tempfile
    from zipfile import ZipFile
    from lxml import etree as ET
    from .motion_clone import clone_motion_deck, _read_xml, _safe_entries, _slide_objects, _slide_order, text_sha256
    from .style_hybrid_workflow import _source, _reviewed_preview
    from .template_content_replace import replace_template_content
    library = Path(library).resolve()
    output = Path(output).expanduser().absolute()
    if not isinstance(request, dict) or output.exists() or output.is_symlink() or output.resolve().is_relative_to(library):
        raise ValueError("layout build needs a request and a new output outside the source library")
    source = _source(library, request.get("source_path"), request.get("source_sha256"), require_template=True)
    if source["family"] != GAOJIE:
        raise ValueError("layout build selects a Gaojie source")
    slide = request.get("source_slide")
    if type(slide) is not int or slide not in source["pages"]:
        raise ValueError("selected source page is not indexed")
    review = _reviewed_preview(request.get("reviewed_source_page"), source_sha=source["sha256"], slide=slide)
    if not isinstance(request.get("selection_note"), str) or len(request["selection_note"].strip()) < 10:
        raise ValueError("explain the current content relationship and why this structure fits")
    ids = request.get("selected_object_ids")
    component = source["catalog"].get("category") in COMPONENT_CATEGORIES
    if component and not ids:
        raise ValueError("component sheets require explicit top-level object/group IDs; do not copy the entire gallery")
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    ns = {"p": p, "a": a}
    with ZipFile(source["source"]) as archive:
        entries = _safe_entries(archive)
        _, order = _slide_order(archive, entries)
        root = _read_xml(archive, entries, order[slide - 1])
        tree = root.find(f"{{{p}}}cSld/{{{p}}}spTree")
        all_objects = _slide_objects(root)
        top = {}
        for element in tree:
            if ET.QName(element).localname not in {"sp", "pic", "grpSp", "cxnSp", "graphicFrame"}:
                continue
            identity = element.find(f".//{{{p}}}cNvPr")
            if identity is not None:
                top[identity.get("id")] = element
        if ids is not None and (not isinstance(ids, list) or not 1 <= len(ids) <= 30 or
                                len(set(map(str, ids))) != len(ids) or any(str(i) not in top for i in ids)):
            raise ValueError("selected objects must be distinct existing top-level objects/groups")
        kept_ids = set(map(str, ids)) if ids else set(top)
        retained = set()
        for oid in kept_ids:
            retained.update(node.get("id") for node in top[oid].iter(f"{{{p}}}cNvPr"))
        if any(all_objects[oid].find(f".//{{{a}}}graphicData") is not None for oid in retained if oid in all_objects):
            raise ValueError("native chart/table/SmartArt data requires a dedicated current-data rebuild; text-only copying is insufficient")
        text_ids = {oid for oid in retained if oid in all_objects and
                    ET.QName(all_objects[oid]).localname != "grpSp" and
                    any((node.text or "").strip() for node in all_objects[oid].iter(f"{{{a}}}t"))}
        texts = request.get("text_replacements") or []
        if not isinstance(texts, list) or any(not isinstance(item, dict) for item in texts):
            raise ValueError("text replacements must be explicit object records")
        mapped = {str(item.get("shape_id")) for item in texts}
        if mapped != text_ids or len(mapped) != len(texts):
            raise ValueError("map every retained text object once, including source example and instruction copy")
        for item in texts:
            original = "".join(node.text or "" for node in all_objects[str(item["shape_id"])].iter(f"{{{a}}}t"))
            if item.get("original_text_sha256") != text_sha256(original):
                raise ValueError("text mapping is stale for the selected source object")
        if ids and root.find(f"{{{p}}}timing") is not None:
            raise ValueError("component selection with source timing needs a separately scoped static/dynamic adaptation")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gaojie-layout-build-", dir=output.parent) as temporary:
        temporary = Path(temporary)
        clone = temporary / "selected-page.pptx"
        clone_motion_deck(source["source"], clone, source_sha256=source["sha256"], slides=[slide])
        filtered = clone
        if ids:
            filtered = temporary / "selected-component.pptx"
            with ZipFile(clone) as archive:
                entries = _safe_entries(archive)
                _, order = _slide_order(archive, entries)
                root = _read_xml(archive, entries, order[0])
                tree = root.find(f"{{{p}}}cSld/{{{p}}}spTree")
                for element in list(tree):
                    if ET.QName(element).localname not in {"sp", "pic", "grpSp", "cxnSp", "graphicFrame"}:
                        continue
                    identity = element.find(f".//{{{p}}}cNvPr")
                    if identity is None or identity.get("id") not in kept_ids:
                        tree.remove(element)
                position = request.get("component_position_fraction")
                if position is not None:
                    if len(kept_ids) != 1 or not isinstance(position, list) or len(position) != 2 or any(
                            not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1 for value in position):
                        raise ValueError("component position needs one selected group/object and [x,y] fractions")
                    element = next(node for node in tree if node.find(f".//{{{p}}}cNvPr") is not None and node.find(f".//{{{p}}}cNvPr").get("id") in kept_ids)
                    transform = element.find(f"{{{p}}}grpSpPr/{{{a}}}xfrm") if ET.QName(element).localname == "grpSp" else element.find(f"{{{p}}}spPr/{{{a}}}xfrm")
                    if transform is None:
                        raise ValueError("selected component has no explicit moveable transform")
                    off = transform.find(f"{{{a}}}off")
                    presentation = _read_xml(archive, entries, "ppt/presentation.xml")
                    size = presentation.find(f"{{{p}}}sldSz")
                    off.set("x", str(round(position[0] * int(size.get("cx")))))
                    off.set("y", str(round(position[1] * int(size.get("cy")))))
                widths = request.get("component_text_widths") or []
                if not isinstance(widths, list) or len(widths) > 20:
                    raise ValueError("component text widths must be a bounded explicit list")
                if widths:
                    selected_objects = _slide_objects(root)
                    presentation = _read_xml(archive, entries, "ppt/presentation.xml")
                    canvas_width = int(presentation.find(f"{{{p}}}sldSz").get("cx"))
                    touched_groups = {}
                    for edit in widths:
                        oid = str(edit.get("shape_id", "")) if isinstance(edit, dict) else ""
                        fraction = edit.get("width_fraction") if isinstance(edit, dict) else None
                        shape = selected_objects.get(oid)
                        parent = shape.getparent() if shape is not None else None
                        if (oid not in text_ids or parent is None or ET.QName(parent).localname != "grpSp"
                                or parent.getparent() is not tree or not isinstance(fraction, (int, float))
                                or isinstance(fraction, bool) or not 0 < fraction <= .9):
                            raise ValueError("width adjustment requires retained direct text children of a selected top-level group")
                        transform = parent.find(f"{{{p}}}grpSpPr/{{{a}}}xfrm")
                        if transform is None or transform.get("rot", "0") != "0" or any(transform.get(key, "0") not in {"0", "false"} for key in ("flipH", "flipV")):
                            raise ValueError("rotated/flipped group needs a dedicated geometry adaptation")
                        ext, child_ext = transform.find(f"{{{a}}}ext"), transform.find(f"{{{a}}}chExt")
                        if ext is None or child_ext is None or int(child_ext.get("cx", "0")) <= 0 or int(ext.get("cx", "0")) <= 0:
                            raise ValueError("group lacks a resolved child coordinate space")
                        scale = int(ext.get("cx")) / int(child_ext.get("cx"))
                        shape_transform = shape.find(f"{{{p}}}spPr/{{{a}}}xfrm")
                        if shape_transform is None or shape_transform.get("rot", "0") != "0":
                            raise ValueError("text frame lacks an unrotated explicit transform")
                        shape_transform.find(f"{{{a}}}ext").set("cx", str(round(canvas_width * fraction / scale)))
                        touched_groups[parent] = (transform, scale)
                    # Expand group bounds while preserving ext/chExt scale,
                    # so font size and other child objects are not stretched.
                    for group, (transform, scale) in touched_groups.items():
                        child_off = transform.find(f"{{{a}}}chOff")
                        start = int(child_off.get("x"))
                        right = start + int(transform.find(f"{{{a}}}chExt").get("cx"))
                        for child in group:
                            child_transform = child.find(f"{{{p}}}spPr/{{{a}}}xfrm")
                            if child_transform is not None:
                                off, ext = child_transform.find(f"{{{a}}}off"), child_transform.find(f"{{{a}}}ext")
                                if off is not None and ext is not None:
                                    right = max(right, int(off.get("x")) + int(ext.get("cx")))
                        span = right - start
                        transform.find(f"{{{a}}}chExt").set("cx", str(span))
                        transform.find(f"{{{a}}}ext").set("cx", str(round(span * scale)))
                changed = ET.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                with ZipFile(filtered, "w") as destination:
                    for info in archive.infolist():
                        destination.writestr(info, changed if info.filename == order[0] else archive.read(info.filename))
        edits = [dict(item, slide=1) for item in texts]
        image_edits = [dict(item, slide=1) for item in request.get("image_replacements", [])]
        candidate = temporary / "adapted.pptx" if edits or image_edits else filtered
        if edits or image_edits:
            replace_template_content(filtered, candidate, source_sha256=_sha(filtered),
                                     text_replacements=edits, image_replacements=image_edits)
        with ZipFile(candidate) as archive:
            if archive.testzip() is not None:
                raise ValueError("adapted candidate failed ZIP integrity")
        if _sha(source["source"]) != source["sha256"]:
            raise ValueError("source changed during layout adaptation")
        os.link(candidate, output)
    return {"status": "layout_candidate_built", "source_path": source["path"], "source_sha256": source["sha256"],
            "source_slide": slide, "source_page_review": review, "selected_object_ids": sorted(kept_ids) if ids else None,
            "mapped_text_objects": len(texts), "output": str(output), "output_sha256": _sha(output),
            "visual_status": "requires_actual_current_page_observation", "inherited_layers": "preserved_needs_whole_page_review"}
