from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from .models import (
    DEFAULT_TARGET_PAGES, MAX_TARGET_PAGES, MIN_TARGET_PAGES, DeckPlan,
    EvidenceLedger, InputRole, JobBrief, PageCountError, SlideSpec, SourceDeckManifest, SourceInspectionIndex,
    SourceRecord, has_explicit_page_target, validate_plan_page_count,
)
from .planning import planning_decision
from .presentation_intent import read_bound_work_request, select_presentation_intent, source_document_identity
from .storage import job_dir, load_job, read_model, write_json
from .visual_system import visual_system_contract


def _source_records(root: Path, source_ids: list[str]) -> list[SourceRecord]:
    return [read_model(root / "normalized" / "sources" / f"{source_id}.json", SourceRecord) for source_id in source_ids]


def _normalized_text(root: Path, record: SourceRecord) -> str:
    if not record.normalized_path:
        return ""
    return (root / record.normalized_path).read_text(encoding="utf-8", errors="replace")


def _record_role(record: SourceRecord, input_roles: Mapping[str, InputRole]) -> InputRole:
    value = input_roles.get(record.id, record.metadata.get("input_role", InputRole.CONTENT))
    try:
        return value if isinstance(value, InputRole) else InputRole(str(value))
    except ValueError:
        return InputRole.CONTENT


def _content_bearing_records(
    records: list[SourceRecord],
    input_roles: Mapping[str, InputRole],
    *,
    include_assets: bool = False,
) -> list[SourceRecord]:
    """Return sources allowed to contribute audience-facing facts and copy.

    A visual reference can guide geometry and style, and a template can guide
    an adapter, but neither is a factual source for new slide text.  Keeping
    this boundary here prevents reference captions, placeholder text, and
    implementation notes from becoming titles, bullets or speaker notes.
    """

    excluded = {InputRole.VISUAL_REFERENCE, InputRole.TEMPLATE}
    if not include_assets:
        excluded.add(InputRole.ASSET)
    return [record for record in records if _record_role(record, input_roles) not in excluded]


def _clean_lines(text: str) -> list[str]:
    return [line for line, _ in _clean_lines_with_locators(text)]


def _is_content_line(line: str) -> bool:
    # Length is a layout concern, not permission to lose facts. Even a short
    # amount or a long paragraph must survive into the plan and its fit review.
    if len(line) < 2:
        return False
    return not bool(re.fullmatch(r"(?:制作|生成|做成|做|需要|仅需|只要)\s*(?:一份|一套)?\s*[\d零〇一二两三四五六七八九十百千]+\s*页\s*(?:PPTX?|演示文稿|幻灯片)?[。.!！]?", line, re.I))


# These signals deliberately require presentation-production language.  A
# source can truthfully say that a customer "要求两周内交付" or that a project
# has a "下一步"; those are audience-facing facts.  In contrast, an unlabelled
# line that tells an author how to make a slide is not a fact to print on that
# slide.  Keep it intact in the editorial-input report for a human/assistant
# to resolve rather than dropping it or leaking it into copy/notes.
_UNLABELED_PRESENTATION_INSTRUCTIONS = (
    re.compile(
        r"^(?:请|需要|务必|不要|记得|注意|建议).{0,56}"
        r"(?:PPTX?|演示文稿|幻灯片|页面|排版|版式|字号|字体|小字|大字|留白|配图|图片|图文|遮挡|封面|目录|模板|配色|视觉).*$",
        re.I,
    ),
    re.compile(
        r"^(?:这[一张]?页|本页|该页|此页).{0,40}"
        r"(?:需要|应当|应该|建议|注意|记得|待|不要).{0,56}"
        r"(?:排版|版式|字号|字体|小字|大字|留白|配图|图片|图文|遮挡|前[一]?页|后[一]?页|其他页面|衔接|关联|调整|补充).*$",
        re.S,
    ),
    re.compile(
        r"^(?:我会|我将|我可以|我帮你|为您|为你).{0,56}"
        r"(?:制作|生成|调整|修改|优化|检查|排版).{0,32}"
        r"(?:PPTX?|演示文稿|幻灯片|页面|这[一张]?页|本页).*$",
        re.I | re.S,
    ),
)


def _is_unlabeled_presentation_instruction(line: str) -> bool:
    return any(pattern.search(line) for pattern in _UNLABELED_PRESENTATION_INSTRUCTIONS)


# A bare number extracted from a reference often comes from a corner ordinal,
# slide number, or a detached decorative metric.  It has no audience-facing
# meaning until linked to a chapter, unit, comparison or nearby evidence.  Do
# not erase it; hold it for contextual editorial review.  Numbers with words,
# units or percent signs remain normal source content.
_ISOLATED_NUMERIC_FRAGMENT = re.compile(r"0*\d{1,4}")


def _is_isolated_numeric_fragment(line: str) -> bool:
    return bool(_ISOLATED_NUMERIC_FRAGMENT.fullmatch(line.strip()))


# A closing page can be useful when the source itself ends with a conclusion,
# proposed action or next-stage arrangement.  Do not manufacture one merely
# because the requested page count leaves room for a familiar deck scaffold.
# Restrict the match to a terminal run so a mid-deck discussion of future work
# is not silently moved out of its original context.
_TERMINAL_CLOSING_SIGNAL = re.compile(
    r"(?:结论(?:是|为|[:：])?|下一(?:阶段|步)|后续(?:安排|工作|计划|将|需|应)|"
    r"(?:提出|给出)?建议(?:是|为|[:：])?|行动(?:清单|计划)|展望|收束)",
    re.I,
)


def _split_terminal_closing_content(
    content: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """Separate only an explicit closing suffix from source-grounded content."""

    split_at = len(content)
    while split_at and _TERMINAL_CLOSING_SIGNAL.search(content[split_at - 1][1]):
        split_at -= 1
    return content[:split_at], content[split_at:]


# These are explicit input channels, not a blacklist of words in business copy.
# A mixed instruction/fact remains intact in editorial-inputs for an editor to
# resolve; recognizing a label never gives us permission to delete its facts.
_EDITORIAL_LABELS = {
    "purpose", "revision", "revision_notes", "style", "style_direction",
    "visual_direction", "planning_strategy", "制作说明", "制作要求", "排版要求",
    "修改要求", "修改意见", "修订说明", "设计说明", "风格要求", "助手提醒",
    "助手回复", "assistant", "客户要求", "汇报对象",
}


def _partition_source_lines(text: str) -> tuple[list[tuple[str, str]], list[dict[str, str]]]:
    located: list[tuple[str, str]] = []
    editorial: list[dict[str, str]] = []
    seen: set[str] = set()
    editorial_section: tuple[int, str] | None = None
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = re.sub(r"^\s*(?:[#>*-]+|\d+[.、)])\s*", "", raw).strip()
        line = re.sub(r"\s+", " ", line)
        locator = f"line:{line_number}"
        heading = re.match(r"^\s*(#{1,6})\s+(.+?)\s*#*\s*$", raw)
        if editorial_section and editorial_section[0] == 0 and (not line or heading):
            editorial_section = None
        if heading and editorial_section and len(heading.group(1)) <= editorial_section[0]:
            editorial_section = None
        label, separator, value = re.sub(r"：", ":", line, count=1).partition(":")
        label = label.strip().strip('"\'').lower()
        explicit_label = label in _EDITORIAL_LABELS and (bool(separator) or bool(heading))
        if heading and explicit_label:
            editorial_section = (len(heading.group(1)), label)
        elif explicit_label and not value.strip():
            # A plain "制作说明：" introduces a paragraph-sized channel. An
            # empty line or a Markdown heading explicitly ends that paragraph.
            editorial_section = (0, label)
        if line and (explicit_label or editorial_section):
            editorial.append({"text": raw, "locator": locator,
                              "input_channel": label if explicit_label else editorial_section[1],
                              "status": "pending_editorial_review"})
            continue
        if line and _is_unlabeled_presentation_instruction(line):
            editorial.append({"text": raw, "locator": locator,
                              "input_channel": "unlabeled_presentation_instruction",
                              "status": "pending_editorial_review"})
            continue
        if line and _is_isolated_numeric_fragment(line):
            editorial.append({"text": raw, "locator": locator,
                              "input_channel": "isolated_numeric_fragment",
                              "status": "pending_editorial_review"})
            continue
        if line and len(line) >= 2 and not _is_content_line(line):
            editorial.append({"text": raw, "locator": locator, "input_channel": "page_count",
                              "status": "planning_constraint"})
            continue
        if _is_content_line(line) and line not in seen:
            located.append((line, locator))
            seen.add(line)
    return located, editorial


def _clean_lines_with_locators(text: str) -> list[tuple[str, str]]:
    return _partition_source_lines(text)[0]


def _planning_inputs(
    root: Path,
    records: list[SourceRecord],
    input_roles: Mapping[str, InputRole] | None = None,
) -> tuple[list[tuple[SourceRecord, list[tuple[str, str]]]], dict]:
    text_records = []
    editorial = []
    roles = input_roles or {}
    for record in _content_bearing_records(records, roles):
        lines, held = _partition_source_lines(_normalized_text(root, record))
        text_records.append((record, lines))
        editorial.extend({**item, "source_id": record.id,
                          "source_ref": f"{record.id}#{item['locator']}",
                          "normalized_path": record.normalized_path} for item in held)
    pending = sum(item["status"] == "pending_editorial_review" for item in editorial)
    report = {"schema_version": 1, "status": "pending_editorial_review" if pending else "resolved",
              "pending_count": pending, "items": editorial,
              "review_requirement": "Review each held source line in context. Preserve its valid business facts when preparing audience-facing copy; no source text was deleted."}
    write_json(root / "planning" / "editorial-inputs.json", report)
    return text_records, report


def _explicit_target_pages(text: str) -> int | None:
    number = r"([+-]?\d+(?:\.\d+)?|[零〇一二两三四五六七八九十百千]+)"
    patterns = (
        rf"(?:PPT\s*)?(?:总页数|页数)\s*(?:必须|需要|需|应)?\s*(?:调整为|改为|设为|控制在|为|是)?\s*[:：]?\s*{number}\s*页",
        rf"(?:交付|制作|生成|做成|做|只要|仅需|需要)\s*(?:一份|一个|一套)?\s*{number}\s*页\s*(?:PPTX?|演示文稿|幻灯片)?",
    )
    for pattern in patterns:
        matches = list(re.finditer(pattern, text, flags=re.IGNORECASE))
        if not matches:
            continue
        # An explicit total outranks a prototype count; the newest correction
        # within the same kind of instruction outranks an earlier instruction.
        raw = matches[-1].group(1)
        if re.fullmatch(r"[+-]?\d+", raw):
            pages = int(raw)
        elif re.fullmatch(r"[零〇一二两三四五六七八九十百千]+", raw):
            digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
                      "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
            units = {"十": 10, "百": 100, "千": 1000}
            pages, digit = 0, 0
            for character in raw:
                if character in digits:
                    digit = digits[character]
                else:
                    pages += (digit or 1) * units[character]
                    digit = 0
            pages += digit
        else:
            raise PageCountError("explicit target page count must be an integer")
        if not MIN_TARGET_PAGES <= pages <= MAX_TARGET_PAGES:
            raise PageCountError(f"explicit target page count must be between {MIN_TARGET_PAGES} and {MAX_TARGET_PAGES}")
        return pages
    return None


_AUDIENCE_LABELS = {"audience", "target audience", "听众", "受众", "汇报对象", "演示听众", "演讲听众", "授课对象", "教学对象"}
_CURRENT_AUDIENCE_LABELS = {"本次汇报对象", "本次演示听众", "本次演讲听众", "本次授课对象", "本次受众", "本次听众"}
_AUDIENCE_METADATA_SECTIONS = {"制作说明", "制作要求", "设计说明", "演示要求", "汇报要求", "任务要求", "brief", "presentation brief"}


_FORM_PAGE_LANGUAGE: dict[str, dict[str, str]] = {
    "government_briefing": {
        "overview_title": "工作重点与推进主线", "overview_role": "briefing-overview",
        "body_role": "briefing-fact", "evidence_role": "briefing-evidence",
        "conclusion_title": "后续工作与责任衔接", "conclusion_role": "briefing-close",
    },
    "business_report": {
        "overview_title": "经营结论与行动主线", "overview_role": "decision-overview",
        "body_role": "decision-fact", "evidence_role": "decision-evidence",
        "conclusion_title": "关键结论与下一步", "conclusion_role": "decision-close",
    },
    "competition_defense": {
        "overview_title": "核心命题与评审路径", "overview_role": "argument-map",
        "body_role": "argument-case", "evidence_role": "argument-evidence",
        "conclusion_title": "可行性与答辩要点", "conclusion_role": "argument-close",
    },
    "academic_defense": {
        "overview_title": "研究问题与论证路径", "overview_role": "research-map",
        "body_role": "research-claim", "evidence_role": "research-evidence",
        "conclusion_title": "研究结论与边界", "conclusion_role": "research-close",
    },
    "teaching": {
        "overview_title": "学习路径与关键步骤", "overview_role": "learning-map",
        "body_role": "learning-step", "evidence_role": "learning-example",
        "conclusion_title": "回顾与下一步练习", "conclusion_role": "learning-close",
    },
    "technical_explanation": {
        "overview_title": "问题、机制与使用边界", "overview_role": "mechanism-map",
        "body_role": "mechanism-explanation", "evidence_role": "mechanism-evidence",
        "conclusion_title": "结论与实施边界", "conclusion_role": "mechanism-close",
    },
    "ceremonial_story": {
        "overview_title": "叙事主线", "overview_role": "story-map",
        "body_role": "story-beat", "evidence_role": "story-moment",
        "conclusion_title": "现场收束与致意", "conclusion_role": "story-close",
    },
    "premium_brand_portfolio": {
        "overview_title": "作品叙事与案例脉络", "overview_role": "portfolio-map",
        "body_role": "portfolio-case", "evidence_role": "portfolio-evidence",
        "conclusion_title": "作品价值与后续延展", "conclusion_role": "portfolio-close",
    },
}


def _form_page_language(form: str) -> dict[str, str]:
    return _FORM_PAGE_LANGUAGE.get(
        form,
        {
            "overview_title": "本次汇报内容", "overview_role": "overview",
            "body_role": "message", "evidence_role": "evidence",
            "conclusion_title": "关键结论", "conclusion_role": "close",
        },
    )


def _audience_candidates(root: Path, records: list[SourceRecord], input_roles: dict) -> list[dict[str, str]]:
    """Retain explicit metadata as candidates, never infer audiences from facts.

    Generic labels are accepted only in a document's leading metadata block or
    an explicitly marked task/production metadata section. An unquoted label
    identifying *this presentation* can occur later. Names, research subjects,
    marketing prose, examples, references and quoted code are not task metadata.
    """
    candidates = []
    for record in records:
        role = input_roles.get(record.id, record.metadata.get("input_role", InputRole.CONTENT))
        if role in {InputRole.TEMPLATE, InputRole.VISUAL_REFERENCE, InputRole.ASSET}:
            continue
        frontmatter, metadata_level, fence = True, None, None
        for number, raw in enumerate(_normalized_text(root, record).splitlines(), start=1):
            line = raw.strip()
            code = re.match(r"^(`{3,}|~{3,})", line)
            if code:
                marker = code.group(1)[0]
                if fence is None:
                    fence = marker
                elif fence == marker:
                    fence = None
                frontmatter = False
                continue
            if fence or line.startswith(">"):
                continue
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                title, level = heading.group(2).strip().lower(), len(heading.group(1))
                if title in _AUDIENCE_METADATA_SECTIONS:
                    metadata_level = level
                elif metadata_level is not None and level > metadata_level:
                    pass
                else:
                    metadata_level = None
                # One document title may precede leading key/value metadata.
                frontmatter = frontmatter and level == 1
                continue
            if not line:
                if metadata_level == 0:
                    metadata_level = None
                continue
            field = re.match(r"^(?:[-*+]\s+)?([^:：]{1,40})[:：]\s*(.*?)\s*$", line)
            if not field:
                frontmatter = False
                continue
            label = field.group(1).strip().strip("*").strip().lower()
            value = field.group(2).strip()
            if label in _AUDIENCE_METADATA_SECTIONS:
                if not value:
                    metadata_level = 0
                continue
            if value and (label in _CURRENT_AUDIENCE_LABELS or (
                    label in _AUDIENCE_LABELS and (frontmatter or metadata_level is not None))):
                candidates.append({"text": value, "source_text": raw, "source_ref": f"{record.id}#line:{number}",
                                   "normalized_path": record.normalized_path or "", "status": "source_candidate"})
    return candidates


def _intent_evidence(editorial: dict) -> list[tuple[str, str]]:
    """Use only already-separated instruction channels for task classification.

    Business paragraphs can mention a government, a competition or a brand
    without saying that this presentation serves that occasion.  The planner
    therefore supplies only explicit editorial channels to the classifier.
    """

    allowed = {
        "purpose", "revision", "revision_notes", "style", "style_direction", "visual_direction",
        "planning_strategy", "制作说明", "制作要求", "排版要求", "修改要求", "修改意见",
        "修订说明", "设计说明", "风格要求", "客户要求", "汇报对象",
    }
    return [
        (str(item["source_ref"]), str(item["text"]))
        for item in editorial.get("items", [])
        if item.get("input_channel") in allowed and str(item.get("text", "")).strip()
    ]


def _goal_for_intent(name: str, intent) -> str:
    descriptions = {
        "local_patch": "在既有演示中完成受控局部修订",
        "template_relayout": "在既有模板或母版体系内重新组织目标内容",
        "new_composition": "基于本轮材料建立新的演示叙事与视觉表达",
        "faithful_reconstruction": "将现有扁平材料忠实重建为可编辑演示",
        "visual_alternate": "制作经明确接受的图片型视觉替代稿",
        "unclassified": "根据本轮材料建立可审阅的演示方案",
    }
    return f"对“{name}”{descriptions[intent.change_mode.value]}"


def create_brief(job_id: str) -> JobBrief:
    job = load_job(job_id)
    root = job_dir(job_id)
    ledger = read_model(root / "evidence-ledger.json", EvidenceLedger)
    records = _source_records(root, job.source_ids)
    content_records = _content_bearing_records(records, job.input_roles)
    visual_content_records = _content_bearing_records(records, job.input_roles, include_assets=True)
    text = "\n".join(_normalized_text(root, record) for record in content_records)
    text_records, editorial = _planning_inputs(root, content_records, job.input_roles)
    lines = list(dict.fromkeys(line for _, items in text_records for line, _ in items))
    images = [record for record in visual_content_records if record.kind == "image"]
    # A reference-only job can still be reviewed and explicitly routed; it
    # simply cannot silently produce a factual deck plan until content arrives.
    if content_records and len(text.strip()) < 80 and not images:
        raise ValueError("材料内容不足：请补充可读取文字或至少一张参考图片")
    must_include = lines[:5] or [f"参考图片：{Path(record.stored_path).name}" for record in images[:5]]
    request_text, binding = read_bound_work_request(job_id)
    request_pages = _explicit_target_pages(request_text) if request_text.strip() else None
    explicit_pages = request_pages if request_pages is not None else _explicit_target_pages(text)
    brief_path = root / "planning" / "brief.json"
    # Re-intake may add sources. It must not replace already authored audience
    # or design constraints with new defaults or extracted source candidates.
    explicit_context = {}
    existing: JobBrief | None = None
    if brief_path.is_file():
        existing = read_model(brief_path, JobBrief)
        explicit_context = {key: getattr(existing, key) for key in (
            "audience", "audience_profile", "style_direction", "customer_constraints", "presentation_intent")}
    if explicit_pages is None and existing is not None and existing.target_pages_explicit:
        explicit_pages = existing.target_pages
    provisional = JobBrief(
        goal=job.name,
        audience_candidates=_audience_candidates(root, content_records, job.input_roles),
        target_pages=explicit_pages if explicit_pages is not None else DEFAULT_TARGET_PAGES,
        target_pages_explicit=explicit_pages is not None,
        must_include=must_include,
        locked_fact_ids=list(range(len(ledger.locked_facts))),
        **explicit_context,
    )
    intent = select_presentation_intent(
        provisional,
        job.input_roles,
        request_text=request_text,
        request_sha256=binding.source_sha256 if binding else None,
        editorial_evidence=_intent_evidence(editorial),
        source_identity=source_document_identity(root, content_records, job.input_roles),
        route=job.route,
    )
    brief = provisional.model_copy(update={
        "goal": _goal_for_intent(job.name, intent),
        "presentation_intent": intent,
    })
    write_json(root / "planning" / "brief.json", brief)
    write_json(root / "planning" / "intake-decision.json", {
        "schema_version": 1,
        "brief_path": "planning/brief.json",
        "work_request": binding.model_dump(mode="json") if binding else None,
        "presentation_intent": intent.model_dump(mode="json"),
        "rule": "Work requests and editorial instructions guide planning but never enter the evidence ledger, must_include, slide copy, or speaker notes.",
    })
    return brief


def _title_from_line(line: str, fallback: str) -> str:
    sentence = re.split(r"[。！？!?；;]", line, maxsplit=1)[0].strip(" ，,：:")
    if not sentence:
        return fallback
    clauses = [part.strip() for part in re.split(r"[，,]", sentence) if part.strip()]
    if len(sentence) <= 36 or len(clauses) < 2:
        return sentence

    first = clauses[0]
    metric_clause = next((part for part in clauses[1:] if re.search(r"\d|%|％|同比|环比|提升|下降|缩短|贡献", part)), None)
    if metric_clause:
        combined = f"{first}，{metric_clause}"
        if len(combined) <= 44:
            return combined
    return first


def _asset_kind(asset: object) -> str:
    kind = getattr(asset, "kind", "unknown")
    if kind != "unknown":
        return str(kind)
    content_type = str(getattr(asset, "content_type", "") or "").lower()
    stored_path = str(getattr(asset, "stored_path", "") or "").lower()
    if content_type.startswith("image/") or stored_path.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp")):
        return "image"
    return "unknown"


def _image_asset_refs(root: Path, records: list[SourceRecord]) -> list[str]:
    refs = [record.id for record in records if record.kind == "image"]
    index_path = root / "extraction" / "index.json"
    if not index_path.is_file():
        return refs
    index = read_model(index_path, SourceInspectionIndex)
    for relative_manifest in index.manifests:
        manifest_path = root / relative_manifest
        if not manifest_path.is_file():
            continue
        manifest = read_model(manifest_path, SourceDeckManifest)
        refs.extend(
            f"extracted:{manifest.source_id}:{asset.id}"
            for asset in manifest.assets
            if asset.extraction_status == "extracted" and asset.stored_path and _asset_kind(asset) == "image"
        )
    return list(dict.fromkeys(refs))


def create_deck_plan(job_id: str) -> DeckPlan:
    job = load_job(job_id)
    root = job_dir(job_id)
    brief = read_model(root / "planning" / "brief.json", JobBrief)
    if brief.presentation_intent.change_mode.value == "local_patch":
        raise ValueError(
            "局部修订不能生成通用新稿 deck plan；请使用 hash-bound pptx-patch 或 apply-edits，并验证非目标页保持不变"
        )
    if brief.presentation_intent.change_mode.value == "unclassified":
        raise ValueError(
            "任务意图仍未分类，不能生成通用 deck plan；请先确认局部编辑、模板重排、新建、重建或视觉替代的实际方式"
        )
    records = _source_records(root, job.source_ids)
    content_records = _content_bearing_records(records, job.input_roles)
    visual_content_records = _content_bearing_records(records, job.input_roles, include_assets=True)
    text_records, editorial = _planning_inputs(root, content_records, job.input_roles)
    content: list[tuple[str, str, str]] = []
    seen_content: set[str] = set()
    for record, lines in text_records:
        for line, locator in lines:
            if line == job.name or line in seen_content:
                continue
            content.append((record.id, line, locator))
            seen_content.add(line)
    image_ids = _image_asset_refs(root, visual_content_records)
    if not content and not image_ids:
        raise ValueError("没有可用于生成大纲的正文内容；请检查 planning/editorial-inputs.json 中待编辑的完整来源材料")

    decision = planning_decision(brief)
    page_language = _form_page_language(decision.presentation_form.value)
    target = brief.target_pages
    # A one/two-page task consists of actual content.  Cover and navigation are
    # optional structure; a closing page needs source-grounded terminal
    # material, otherwise it becomes the familiar but meaningless "结论/下一步"
    # page the user has repeatedly rejected.
    preservation = brief.presentation_intent.preservation
    include_cover = target >= 3 and preservation.allow_auto_cover
    body_content, terminal_closing_content = _split_terminal_closing_content(content)
    include_conclusion = bool(
        target >= 3
        and preservation.allow_auto_conclusion
        and body_content
        and terminal_closing_content
    )
    # A closing suffix only changes narrative placement when it can occupy an
    # actual closing page.  Retain it in normal source order otherwise.
    if not include_conclusion:
        body_content = content
        terminal_closing_content = []
    # A one-item or image-only outline communicates no route to the audience;
    # leave that space for material rather than generating a blank/generic map.
    include_summary = target >= 4 and preservation.allow_auto_outline and len(body_content) >= 2
    body_target = target - int(include_cover) - int(include_summary) - int(include_conclusion)
    available = len(body_content) if body_content else len(image_ids)
    if has_explicit_page_target(brief) and available < body_target:
        raise PageCountError(
            f"明确要求 {target} 页，需要 {body_target} 个正文信息单元；"
            f"当前只有 {available} 个，缺少 {body_target - available} 个。"
            "请补充独立事实、图片或由当前助手完成分页规划；不能静默减页、复制内容或用空页凑数。",
            needs_material=True,
        )
    slides: list[SlideSpec] = []
    if include_cover:
        slides.append(
            SlideSpec(
                id="slide-01",
                title=job.name,
                purpose="建立主题与价值预期",
                layout="cover",
                points=[],
                evidence_refs=job.source_ids[:3],
                asset_refs=image_ids[:1],
                speaker_notes=job.name,
                motion="fade",
                page_role="opening",
            )
        )
    if include_summary:
        slides.append(
            SlideSpec(
                id="slide-02",
                title=page_language["overview_title"],
                purpose="说明叙事结构",
                layout="summary",
                page_role=page_language["overview_role"],
                points=[_title_from_line(item[1], job.name) for item in body_content[:3]],
                evidence_refs=job.source_ids[:3],
                speaker_notes="本次汇报包括：" + "；".join(_title_from_line(item[1], job.name) for item in body_content[:3]) + "。" if body_content else "",
            )
        )

    body_count = min(body_target, available)
    for index in range(body_count):
        start = round(index * len(body_content) / body_count) if body_content else 0
        end = round((index + 1) * len(body_content) / body_count) if body_content else 0
        chunk = body_content[start:end] if body_content else [(image_ids[index], "", "")]
        lines = [item[1] for item in chunk]
        evidence_refs = list(dict.fromkeys(f"{item[0]}#{item[2]}" if item[2] else item[0] for item in chunk))
        slide_number = len(slides) + 1
        # Evidence is a communication role, not an every-third-page decoration.
        layout = "evidence" if re.search(r"数据|调查|实验|验证|结果|证据|合同|证书|[%％]", " ".join(lines)) else "content"
        slides.append(
            SlideSpec(
                id=f"slide-{slide_number:02d}",
                # An image without extracted copy does not establish a finding
                # or an ordinal; retain the known task topic for later review.
                title=_title_from_line(lines[0], job.name),
                purpose="用来源材料支撑一个明确结论",
                layout=layout,
                page_role=page_language["evidence_role"] if layout == "evidence" else page_language["body_role"],
                points=[line for line in lines if line],
                evidence_refs=evidence_refs,
                asset_refs=image_ids[index : index + 1],
                speaker_notes="；".join(lines),
            )
        )

    if include_conclusion:
        closing_title = _title_from_line(terminal_closing_content[0][1], page_language["conclusion_title"])
        slides.append(
            SlideSpec(
                id=f"slide-{len(slides) + 1:02d}",
                title=closing_title,
                purpose="收束来源材料中已经明确的结论或后续安排",
                layout="conclusion",
                page_role=page_language["conclusion_role"],
                points=[item[1] for item in terminal_closing_content],
                evidence_refs=[f"{item[0]}#{item[2]}" for item in terminal_closing_content],
                speaker_notes="；".join(item[1] for item in terminal_closing_content),
                motion="fade",
            )
        )
    for index, slide in enumerate(slides, start=1):
        slide.id = f"slide-{index:02d}"

    from .participation import design_context
    context = design_context(root, brief)
    strategy = decision.model_dump(mode="json")
    strategy["presentation_intent"] = brief.presentation_intent.model_dump(mode="json")
    request_text, _ = read_bound_work_request(job_id)
    strategy["visual_system_mode"] = strategy.pop("visual_system", None)
    strategy["visual_system"] = visual_system_contract(
        root,
        brief,
        records,
        job.input_roles,
        request_text=request_text,
        editorial_text=[str(item.get("text", "")) for item in editorial.get("items", [])],
    )
    strategy["page_role_policy"] = {
        "presentation_form": decision.presentation_form.value,
        "roles": page_language,
        "rule": "Form-specific roles change page expression and narrative labels; they do not impose a fixed palette or template.",
    }
    # Concrete, form-specific tokens travel with the plan and are consumed by
    # the renderer. The profile remains a starting system; references and
    # explicit user directions can override its individual choices.
    from .models import ChangeMode, VisualSystemMode
    from .style_profiles import style_profile
    profile = style_profile(decision.presentation_form, brief.audience, brief.goal)
    profile["application"] = (
        "baseline_new_composition"
        if decision.change_mode == ChangeMode.NEW_COMPOSITION
        and decision.visual_system == VisualSystemMode.CREATE_NEW
        else "advisory_only"
    )
    strategy["visual_style_profile"] = profile
    if profile["application"] == "baseline_new_composition":
        from .expression_patterns import select_expression_plan
        from .html_deck import _asset_info
        from .config import WORKSPACE_ROOT
        from .portfolio_examples import select_portfolio_examples

        # The renderer resolves actual files and excludes icon/logo assets from
        # cinematic frames. Do not spend the sole hero slot on a stale ref or
        # a decorative symbol that the renderer will later reject.
        cinematic_eligible_refs = set()
        for ref in dict.fromkeys(ref for slide in slides for ref in slide.asset_refs):
            info = _asset_info(root, ref)
            if info is not None and info[1] not in {"icon", "logo"}:
                cinematic_eligible_refs.add(ref)
        strategy["expression_plan"] = select_expression_plan(
            slides, decision.presentation_form,
            style_text="\n".join((request_text, brief.style_direction, brief.goal)),
            cinematic_eligible_refs=cinematic_eligible_refs,
        )
        portfolio_examples = select_portfolio_examples(WORKSPACE_ROOT, strategy["expression_plan"])
        write_json(root / "planning/portfolio-expression-examples.json", portfolio_examples)
        strategy["portfolio_expression_examples"] = {
            "path": "planning/portfolio-expression-examples.json",
            "status": portfolio_examples["status"],
            "slide_candidates": portfolio_examples["slide_candidates"],
            "slide_recipes": portfolio_examples["slide_recipes"],
            "use": "Only explicitly eligible, source-verified recipes enter the first HTML draft. Review each rendered page; do not copy case facts or media.",
        }
        from .template_designs import (
            apply_template_design_tokens, select_template_design,
            template_design_candidates,
        )

        # Visually reviewed library pages can refine the new-composition
        # baseline. The broad OOXML inventory remains discovery evidence, not
        # permission to execute a design that nobody has viewed as a page.
        discovery = template_design_candidates(
            WORKSPACE_ROOT, decision.presentation_form.value, strategy["expression_plan"],
        )
        write_json(root / "planning/template-design-candidates.json", discovery)
        strategy["template_design_candidates"] = {
            "path": "planning/template-design-candidates.json",
            "status": discovery["status"],
            "candidate_paths": [row["path"] for row in discovery["candidates"]],
            "use": "Discovery only; inspect current whole pages before choosing a template or layout.",
        }
        design = select_template_design(
            WORKSPACE_ROOT, decision.presentation_form.value,
            strategy["expression_plan"], brief.style_direction,
        )
        design_packet = {
            **design, "path": "planning/template-design-language.json",
            "form": decision.presentation_form.value,
            "style_text": brief.style_direction,
            "base_profile": profile,
            "use": "Reuse measured geometry and hierarchy only. Current facts, copy, images and user references remain authoritative; inspect each rendered page.",
        }
        write_json(root / "planning/template-design-language.json", design_packet)
        strategy["template_design_language"] = design_packet
        if design["status"] == "ready":
            strategy["visual_style_profile"] = apply_template_design_tokens(profile, design)
        # Work-bound new decks also search actual page content, composition and
        # reusable library media. The deep index is persistent and incremental;
        # only selected sources are later hash-verified and bound into a build.
        if job.work_request is not None:
            from .library_depth import build_depth_index
            from .library_retrieval import retrieve_library_reuse
            from .library_reuse_gate import plan_message_signature

            library = WORKSPACE_ROOT / "模板库"
            library_catalog = library / "CATALOG.jsonl"
            library_index = library / "DEEP-INDEX.jsonl"
            if library_catalog.is_file():
                index_receipt = build_depth_index(library_catalog, library_index)
                reuse = retrieve_library_reuse(
                    library_catalog, library_index,
                    request_text="\n".join((request_text, brief.goal, brief.style_direction)),
                    form=decision.presentation_form.value,
                    slides=[{
                        "id": slide.id, "title": slide.title, "message": " ".join(slide.points),
                        "purpose": slide.purpose, "page_role": slide.page_role,
                        "composition": strategy["expression_plan"].get("slides", {}).get(slide.id, {}).get("pattern", slide.layout),
                    } for slide in slides],
                )
                reuse["plan_message_signature"] = plan_message_signature(slides)
                write_json(root / "planning/library-reuse-candidates.json", reuse)
                strategy["library_reuse"] = {
                    "path": "planning/library-reuse-candidates.json",
                    "status": reuse["status"],
                    "index_status": index_receipt["status"],
                    "candidate_paths": [item["path"] for item in reuse["candidates"]],
                        "asset_candidate_paths": list(dict.fromkeys(
                            item.get("source_pptx") or item.get("source_path")
                            for item in reuse.get("asset_candidates", [])
                            if isinstance(item, dict) and (item.get("source_pptx") or item.get("source_path"))
                        )),
                    "application": "candidate_selection_required",
                    "rule": "Inspect source pages and media meaning; bind a hash-verified template or selected library media before claiming reuse.",
                }
    strategy["narrative_scaffold"] = {
        "auto_cover": include_cover,
        "auto_outline": include_summary,
        "source_grounded_closing": {
            "enabled": include_conclusion,
            "source_refs": [f"{source_id}#{locator}" for source_id, _, locator in terminal_closing_content],
            "reason": (
                "Terminal source content explicitly states a conclusion, recommendation, next stage or action."
                if include_conclusion
                else "No terminal source-grounded closing content was available, so the planner did not invent a generic conclusion page."
            ),
        },
    }
    strategy["upstream_context"] = context
    from .authoring_policy import job_authoring_defaults
    strategy["authoring_defaults"] = job_authoring_defaults(root)
    strategy["editorial_inputs"] = {"path": "planning/editorial-inputs.json", "status": editorial["status"],
                                    "pending_count": editorial["pending_count"]}
    plan = DeckPlan(
        title=job.name,
        subtitle="",
        visual_direction=decision.visual_direction,
        planning_strategy=strategy,
        slides=slides,
    )
    from .lesson_application import project_lesson_context, plan_query
    project_lessons = project_lesson_context(root, plan_query(plan, {"planning/brief.json": brief.model_dump(mode="json")}))
    write_json(root / "planning/project-lessons.json", project_lessons)
    plan.planning_strategy["project_lessons"] = {
        "path": "planning/project-lessons.json", "status": project_lessons["status"],
        "lesson_ids": [row["lesson_id"] for row in project_lessons["lessons"]],
        "use": "Interpret conditions before composition; validate adoption on actual pages at expression-review."}
    validate_plan_page_count(brief, plan)
    write_json(root / "planning" / "deck-plan.json", plan)
    if decision.change_mode in {ChangeMode.NEW_COMPOSITION, ChangeMode.TEMPLATE_RELAYOUT, ChangeMode.VISUAL_ALTERNATE}:
        from .config import WORKSPACE_ROOT
        from .design_context import build_design_context
        from .renderer import sha256
        design_context = build_design_context(root, plan, brief, workspace_root=WORKSPACE_ROOT)
        design_context["plan_binding"] = {
            "plan_sha256": sha256(root / "planning/deck-plan.json"),
            "brief_sha256": sha256(root / "planning/brief.json"),
        }
        design_context["scene_sha256"] = None
        write_json(root / "planning/design-context.json", design_context)
    return plan
