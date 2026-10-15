from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .models import utc_now


_GENERIC_TERMS = {
    "ppt",
    "pptx",
    "任务",
    "工作",
    "完成",
    "用户",
    "制作",
    "页面",
    "内容",
    "要求",
    "进行",
    "这个",
    "需要",
    "最终",
    "使用",
    "经验",
    "studio", "aippt", "powerpoint", "修复", "修正", "完整", "保留", "建立", "工具",
    "自动", "验证", "原始", "历史", "参考", "优先", "本轮", "后续", "优化", "项目",
    "知识库", "任务知识库", "作品集", "可复用", "脚本", "问题", "过程", "记录", "归档",
    "需求", "结果", "以及", "同时", "其中", "进一步", "智能", "工作台", "整体",
}

# Explicit concepts make Chinese paraphrases and English tool names comparable.
# A concept is a retrieval hint, never a user preference or a production rule.
_CONCEPTS = {
    "mac_platform": ("macos", "mac", "mac os", "苹果电脑", "mac兼容", "跨平台", "windows", "迁移", "兼容性"),
    "upstream_integration": ("github", "上游", "无侵入", "仓库接入", "工具接入"),
    "code_maintenance": ("代码模板", "代码库", "源代码", "软件维护", "单元测试", "回归测试", "python", "pytest"),
    "template_preservation": ("模板", "母版", "版式继承", "template", "master"),
    "reference_match": ("参考匹配", "对照参考", "还原参考", "元素运用", "排版参考", "参考驱动"),
    "sample_first": ("样稿", "样张", "sample", "先做三页", "先看三页"),
    "native_chart": ("图表", "柱状图", "折线", "饼图", "数据图", "echarts", "chart"),
    "flow_diagram": ("流程图", "流程示意", "工艺流程", "技术路线", "mermaid", "diagram"),
    "native_motion": ("动画", "动效", "切换", "morph", "渐变过渡", "animations"),
    "image_layout": ("图片排版", "照片排版", "拼图", "多图", "照片丰富", "photo", "puzzle"),
    "child_friendly_teaching": ("儿童化表达", "儿童科普", "儿童教学", "儿童易懂", "小朋友友好", "孩子听得懂", "低龄受众", "小学课堂", "小学科普", "科学启蒙"),
    "image_text_relationship": ("图文关系", "图文分区", "图文分离", "图文遮挡", "图文重叠", "文字压图", "文字压在图片上", "文字直接在图片上", "叠字", "压字"),
    "revision_scope": ("反馈范围", "修改范围", "未改区域", "未改页面", "部分页面", "实际修改页", "指定页面", "局部改页", "微调图文", "前后页关联", "同类遗留设计", "保留已认可页面"),
    "font_typography": ("字体", "字号", "大字", "粗体", "字重", "font", "typography"),
    "icon_asset": ("图标", "语义图标", "icon", "iconify"),
    "editable_authoring": ("可编辑", "原生对象", "原生图表", "原生表格", "pptxgenjs", "editable"),
    "media_inspection": ("视频", "音轨", "音频", "时间戳", "mediabunny", "video", "audio"),
    "flood_response": ("防汛", "防洪", "洪涝", "内涝", "汛情", "抗洪"),
    "manufacturing_site": ("车间", "生产现场", "生产线", "产线", "厂房施工"),
    "character_identity": ("吉祥物", "角色设定", "角色形象", "角色原貌", "品牌角色", "mascot"),
}

# Independent role words can express the same intent without sharing a long
# Chinese ngram. Require both groups: "儿童票/亲子旅行" alone is not teaching,
# and a mention of a picture alone is not a text/image composition request.
_COMPOSED_CONCEPTS = {
    "child_friendly_teaching": (("儿童", "小朋友", "孩子", "低龄", "小学", "三年级"),
                                ("科普", "教学", "讲解", "易懂", "听得懂", "课堂", "受众")),
    "image_text_relationship": (("图片", "插画", "图像", "照片", "压图", "图文"),
                                ("文字", "叠字", "压字", "暗沉", "压暗", "分区", "遮挡")),
    "image_frame_geometry": (("图片", "插画", "图像", "照片", "角色图"),
                             ("圆角", "直角", "蒙版", "遮罩", "裁切边界", "裁切黑块")),
}

_FORMAT_TERMS = {"word", "docx", "doc", "pdf", "png", "jpg", "jpeg", "html", "mp4", "xlsx"}
_WORD_FORMAT = re.compile(r"(?<![a-z0-9])(?:word|docx?)(?![a-z0-9])", re.I)
_SLIDE_FORMAT = re.compile(r"(?<![a-z0-9])(?:pptx?|powerpoint)(?![a-z0-9])|幻灯片|演示文稿|课件", re.I)

# Version the retrospective parser independently of the PPTX structure reader.
# New aliases must invalidate old empty catalog rows even if sources are unchanged.
RETROSPECTIVE_READER_VERSION = 1
_RETROSPECTIVE_SECTIONS = {
    "summary": ("任务目标", "任务目标与输入", "任务目标与完成结果", "任务目标与最终结果",
                "任务目标与当前结果", "任务目标与当前交付"),
    "reusable_experience": ("可复用经验", "可复用经验与知识评估", "可复用经验与边界",
                            "可复用经验与当前边界", "可复用经验评估"),
    "failure_lessons": ("失败、诊断与恢复", "执行与失败恢复", "失败与恢复", "失败、反馈与恢复"),
    # This is original case narrative, not a set of verified reusable lessons.
    "case_history": ("用户反馈与修改", "用户反馈与对应修改", "用户要求与对应修改",
                     "最终验证与交付", "从需求到交付", "从需求到审阅", "板块演进与关键修改"),
}


def context_concepts(text: str) -> set[str]:
    folded = text.casefold()
    result = {key for key, phrases in _CONCEPTS.items() if any(
        (re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded) is not None)
        if phrase.isascii() else phrase in folded for phrase in phrases
    )}
    # Match within a sentence so independent topics in a long case description
    # cannot accidentally supply the two halves of a concept.
    sentences = re.split(r"[。！？!?\n]", folded)
    result.update(key for key, groups in _COMPOSED_CONCEPTS.items() if any(
        all(any(word in sentence for word in group) for group in groups) for sentence in sentences
    ))
    # IP is ambiguous: brand/character context is required, and network context
    # in the same sentence excludes the acronym interpretation.
    if any(re.search(r"(?<![a-z0-9])ip(?![a-z0-9])", sentence)
           and any(word in sentence for word in ("酒店", "品牌", "角色", "形象", "文旅", "吉祥物"))
           and not any(word in sentence for word in ("网络", "地址", "路由", "协议", "交换机"))
           for sentence in sentences):
        result.add("character_identity")
    return result


def _terms(text: str) -> set[str]:
    text = text.casefold()
    for generic in sorted(_GENERIC_TERMS, key=len, reverse=True):
        if not generic.isascii():
            text = text.replace(generic, " ")
    terms = {item.lower() for item in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]+", text)}
    for sequence in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for size in (2, 3, 4):
            if len(sequence) < size:
                continue
            terms.update(sequence[index : index + size] for index in range(len(sequence) - size + 1))
    return {term for term in terms if term not in _GENERIC_TERMS}


def match_context_text(query: str, title: str, body: str = "", *, scope_title: str | None = None) -> dict[str, object] | None:
    """Conservative lexical/concept recall; overlapping ngrams do not multiply scores."""
    query_terms = _terms(query)
    title_matches, body_matches = query_terms & _terms(title), query_terms & _terms(body)
    query_concepts = context_concepts(query)
    title_concepts = query_concepts & context_concepts(title)
    body_concepts = query_concepts & context_concepts(body)
    scope = scope_title if scope_title is not None else title
    # File extensions in input/export logs do not define a case's topic. A Word
    # pagination request cannot turn every PDF-exported PPT into a related case.
    scope_terms = _terms(scope)
    incidental_formats = _FORMAT_TERMS - scope_terms
    title_matches -= incidental_formats
    body_matches -= incidental_formats
    if (_WORD_FORMAT.search(query) and not _SLIDE_FORMAT.search(query)
            and _SLIDE_FORMAT.search(title) and not _WORD_FORMAT.search(scope)):
        return None
    if (_SLIDE_FORMAT.search(query) and not _WORD_FORMAT.search(query)
            and _WORD_FORMAT.search(scope) and not _SLIDE_FORMAT.search(scope)):
        return None
    # Operational refurbishment should not recall unrelated customer decks merely
    # because their failure log happens to mention Studio, a script, or Mac.
    operation_concepts = {"mac_platform", "upstream_integration", "code_maintenance"}
    operational = query_concepts & operation_concepts
    if operational and not (operational & context_concepts(scope_title if scope_title is not None else title)):
        # "修改范围" and "模板" also occur in software work. They cannot by
        # themselves override the operational-query boundary for client cases.
        presentation_intent = query_concepts - operation_concepts - {"revision_scope", "template_preservation", "sample_first"}
        if not presentation_intent:
            if "code_maintenance" in operational or not re.search(r"\bpptx?\b|幻灯片|演示文稿|母版|版式", query, re.I):
                return None
    strong_title = {term for term in title_matches if len(term) >= 3}
    strong_body = {term for term in body_matches if len(term) >= 4}
    specific_body = body_concepts & {"flood_response", "character_identity", "image_frame_geometry"}
    if not strong_title and not title_concepts and not strong_body and len(body_concepts) < 2 and not specific_body:
        return None
    # Keep longest phrases once, not every overlapping 2/3/4-character fragment.
    phrases = sorted(strong_title | strong_body, key=lambda item: (-len(item), item))
    distinct = [term for term in phrases if not any(term != longer and term in longer for longer in phrases)]
    score = sum(8 if term in title_matches else 2 for term in distinct[:8])
    score += len(title_concepts) * 12 + len(body_concepts - title_concepts) * 3
    # The actual case title outranks route/export keywords and incidental body
    # prose. This preserves useful cross-topic lessons without letting them
    # displace a direct subject match (e.g. a named product or factory case).
    score += sum(8 for term in distinct[:8] if term in scope_terms)
    score += len(title_concepts & context_concepts(scope)) * 6
    return {"score": score, "matched_terms": distinct[:12],
            "matched_concepts": sorted(title_concepts | body_concepts)}


def _section(text: str, heading: str, limit: int = 1400) -> str:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        text,
    )
    return match.group(1).strip()[:limit] if match else ""


def retrospective_sections(text: str) -> dict[str, str]:
    """Read known historical heading forms without inventing lesson status.

    Source/asset lists and undefined headings are deliberately not free-text
    retrieval material. Original qualifications and subheadings stay intact.
    """
    result = {}
    for field, headings in _RETROSPECTIVE_SECTIONS.items():
        limit = 10000 if field == "case_history" else (1400 if field == "summary" else 2500)
        sections = [(heading, _section(text, heading, limit)) for heading in headings]
        if field == "case_history":
            result[field] = "\n\n".join(f"## {heading}\n\n{value}" for heading, value in sections if value)[:limit]
        else:
            result[field] = next((value for _, value in sections if value), "")
    return result


def matched_case_history(query: str, history: str, *, source_path: str, source_sha256: str) -> dict | None:
    """Expose bounded verbatim matches, retaining heading and narrative authority."""
    headings: dict[int, str] = {}
    candidates = []
    for order, paragraph in enumerate(re.split(r"\n\s*\n", history)):
        paragraph = paragraph.strip()
        heading = re.fullmatch(r"(#{2,6})\s+([^\n]+)", paragraph)
        if heading:
            depth = len(heading[1])
            headings = {level: value for level, value in headings.items() if level < depth}
            headings[depth] = heading[2].strip()
            continue
        match = match_context_text(query, "", paragraph, scope_title="") if paragraph else None
        if match:
            candidates.append({"heading": " / ".join(headings.values()), "text": paragraph,
                               "order": order, **match})
    if not candidates:
        return None
    limits = {"max_paragraphs": 3, "max_paragraph_chars": 1200, "max_total_chars": 2400,
              "indexed_history_chars": 10000}
    selected, remaining = [], limits["max_total_chars"]
    for row in sorted(candidates, key=lambda value: (-value["score"], value["order"]))[:limits["max_paragraphs"]]:
        original = row["text"]
        excerpt = original[:min(limits["max_paragraph_chars"], remaining)]
        if not excerpt:
            break
        selected.append({"heading": row["heading"], "text": excerpt, "truncated": len(excerpt) < len(original),
                         "original_chars": len(original), "matched_terms": row["matched_terms"],
                         "matched_concepts": row["matched_concepts"]})
        remaining -= len(excerpt)
    return {"kind": "historical_case_narrative", "advisory_only": True,
            "source_path": source_path, "source_sha256": source_sha256,
            "selection": "matched_original_paragraphs", "limits": limits, "excerpts": selected,
            "reuse_boundary": "Original case history, including rejected/intermediate revisions; not a verified reusable rule or current-task evidence."}


def _indexed_completed_entries(root: Path) -> list[tuple[str, str, Path]]:
    index_path = root / "INDEX.md"
    if not index_path.is_file():
        return []
    entries: list[tuple[str, str, Path]] = []
    pattern = re.compile(
        r"^\|\s*(?P<work>\d+)\s*\|[^|]*\|\s*(?P<title>[^|]+?)\s*\|\s*completed\s*\|[^\n]*?\((?P<path>[^)]+)\)",
        re.IGNORECASE | re.MULTILINE,
    )
    root_resolved = root.resolve()
    for match in pattern.finditer(index_path.read_text(encoding="utf-8-sig")):
        entry = (root / match.group("path")).resolve()
        if root_resolved not in entry.parents or entry.suffix.lower() != ".md" or not entry.is_file():
            continue
        entries.append((match.group("work"), match.group("title").strip(), entry))
    return entries


def recall_task_knowledge(
    query: str,
    knowledge_root: Path,
    *,
    limit: int = 3,
    current_work_id: str | None = None,
) -> dict[str, object]:
    # Both public recall interfaces use the same final-review proof.  Import at
    # call time because context_intelligence imports this module's text matching
    # helpers during module initialization.
    from .context_intelligence import (_current_original_user_acceptance, _parse_document,
                                       _retrospective_content_trust)

    root = knowledge_root.resolve()
    query_text = query.strip()
    query_terms = _terms(query_text)
    index_available = (root / "INDEX.md").is_file()
    candidates: list[dict[str, object]] = []
    for work_id, index_title, entry in _indexed_completed_entries(root):
        if current_work_id and work_id == str(current_work_id):
            continue
        entry_bytes = entry.read_bytes()
        text = entry_bytes.decode("utf-8-sig")
        if not re.search(r"(?mi)^-\s*状态：\s*completed\s*$", text):
            continue
        source_sha256 = hashlib.sha256(entry_bytes).hexdigest()
        trust, content_approval = "legacy_status_only", "legacy_entry_without_work_metadata"
        workspace_root = root.parent
        work_record = workspace_root / "work" / work_id / "work.json"
        completed_metadata = False
        if work_record.is_file():
            try:
                work = _parse_document(work_record, "work")
                if (work.get("work_id") != work_id or work.get("status") != "completed"
                        or work.get("user_approved") is not True):
                    continue
                completed_metadata = True
                trust, content_approval = _retrospective_content_trust(
                    workspace_root,
                    {"source_path": entry.relative_to(workspace_root).as_posix(), "source_sha256": source_sha256},
                    work,
                )
            except (OSError, ValueError, TypeError, KeyError):
                # An unreadable associated Work never falls back to presumed approval.
                continue
        heading = re.search(r"(?m)^#\s+(.+)$", text)
        title = heading.group(1).strip() if heading else index_title
        task_type_match = re.search(r"(?mi)^-\s*任务类型：\s*(.+)$", text)
        task_type = task_type_match.group(1).strip() if task_type_match else ""
        metadata_terms = " ".join(
            match.group(2).strip()
            for match in re.finditer(
                r"(?mi)^-\s*(任务类型|生产路线|受众|检索关键词)：\s*(.+)$",
                text,
            )
        )
        sections = retrospective_sections(text)
        experience, failures = sections["reusable_experience"], sections["failure_lessons"]
        match = match_context_text(query_text, f"{index_title} {title} {task_type} {metadata_terms}",
                                   "\n".join(sections.values()), scope_title=f"{index_title} {title} {task_type}")
        if match is None:
            continue
        original_acceptance = _current_original_user_acceptance(workspace_root, work_id)
        explicit_acceptance = original_acceptance["state"] == "explicit_original_user_acceptance"
        # The original-user proof and current entry hash authenticate different
        # facts.  Neither a completed row nor user_approved=true can substitute
        # for the former, and a final review cannot safely return changed entry
        # bytes without the latter.
        eligible_for_reuse = completed_metadata and trust == "confirmed_completed" and explicit_acceptance
        if eligible_for_reuse:
            reuse_status = "accepted_lesson"
        elif completed_metadata and not explicit_acceptance:
            reuse_status = "completion_metadata_history"
        else:
            reuse_status = "not_reusable"
        candidate = {
                "work_id": work_id,
                "title": index_title,
                "task_type": task_type,
                "entry_path": entry.relative_to(root).as_posix(),
                **match,
                "source_sha256": source_sha256,
                "trust": trust,
                "retrospective_content_approval": content_approval,
                "original_user_acceptance": original_acceptance,
                "reuse_status": reuse_status,
            }
        if eligible_for_reuse:
            candidate.update({"reusable_experience": experience, "failure_lessons": failures})
            history = matched_case_history(query_text, sections["case_history"],
                                           source_path=entry.relative_to(root).as_posix(), source_sha256=source_sha256)
            if history:
                candidate["case_history_context"] = history
            candidate["reuse_boundary"] = (
                "Current final project review verified an original user acceptance; the hash-bound retrospective remains "
                "advisory and scoped to a comparable future case."
            )
        elif completed_metadata and not explicit_acceptance:
            candidate["reuse_boundary"] = (
                "Case navigation only; completed/user_approved metadata and a hash-bound retrospective do not establish "
                "original user acceptance. Automatic lessons require a current final project review with verified original acceptance."
            )
        elif completed_metadata:
            candidate["reuse_boundary"] = (
                "Case navigation only; current final review proves original user acceptance, but this retrospective is no longer "
                "current and hash-bound, so its historical text cannot be reused."
            )
        else:
            candidate["reuse_boundary"] = (
                "Case navigation only; no current Work-bound final review proves original user acceptance for this legacy entry."
            )
        candidates.append(candidate)
    selected = sorted(candidates, key=lambda item: (-int(item["score"]), -int(item["work_id"])))[: max(1, min(limit, 5))]
    return {
        "schema_version": 2,
        "generated_at": utc_now(),
        "status": "matched" if selected else ("no_match" if index_available else "unavailable"),
        "query_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        "query_terms": sorted(query_terms, key=lambda item: (-len(item), item))[:30],
        "advisory_only": True,
        "precedence": [
            "current_user_request",
            "current_inputs_and_references",
            "approved_studio_rules",
            "historical_task_examples",
        ],
        "matches": selected,
        "guidance": (
            "Only entries with a current final project review that verifies original user acceptance may expose reusable lessons. "
            "Other completed entries are navigation metadata only; never copy client facts, assets, style choices, or conclusions "
            "into the current task without current evidence."
        ),
    }
