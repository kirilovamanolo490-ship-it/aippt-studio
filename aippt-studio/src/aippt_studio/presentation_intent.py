"""Turn a Work request into a reviewable presentation-intent contract.

Technical production routing deliberately remains in ``routing.py``.  This
module answers a different question: whether the work is a bounded revision,
template-derived re-layout, new composition, reconstruction, or visual
alternate, and what kind of presentation occasion it serves.  The request is
stored separately from evidence sources so assistant/user instructions cannot
become visible slide copy by accident.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Mapping, Sequence

from .models import (
    ChangeMode,
    InputRole,
    JobBrief,
    PresentationForm,
    PresentationIntent,
    PreservationContract,
    ProductionRoute,
    SourceRecord,
    VisualSystemMode,
    WorkRequestBinding,
    utc_now,
)
from .config import jobs_root
from .storage import job_dir, load_job, locked_job, save_job


MAX_WORK_REQUEST_BYTES = 256 * 1024
_SNAPSHOT_DIRECTORY = "planning/intake-requests"
# ``不`` must be a standalone alternative.  Some intent markers begin with a
# verb (``使用模板`` / ``保留模板`` / ``竞赛``), so the prefix available to the
# parser is only the single character in phrases such as ``不使用模板``.
_NEGATION_PREFIX = r"(?:不要|不(?:用|需|要|应|采取|做|走|必|推荐|想要|希望)|不|禁止|无需|避免|不是|非|别|not|no|without)"
_PAGE_NUMBER_FRAGMENT = r"\d{1,3}(?:\s*[、,，和及]\s*\d{1,3})*"
_PAGE_RANGE_FRAGMENT = r"\d{1,3}\s*(?:-|至|到)\s*\d{1,3}"
_PAGE_REFERENCE_FRAGMENT = rf"第\s*(?:{_PAGE_RANGE_FRAGMENT}|{_PAGE_NUMBER_FRAGMENT})\s*页"
_CLAUSE_BOUNDARY = r"[，。；,;.!！?？\n]"
_REVISION_MARKERS = ("修改", "改动", "优化", "调整", "替换", "保留", "不动", "微调")

# These labels are deliberately editorial rather than technical.  A person
# reviewing a task needs to know what the Studio believes it is making, not
# whether a particular renderer happens to be selected underneath it.
_CHANGE_MODE_LABELS: dict[ChangeMode, str] = {
    ChangeMode.UNCLASSIFIED: "尚不能安全开工",
    ChangeMode.LOCAL_PATCH: "局部精确修改",
    ChangeMode.TEMPLATE_RELAYOUT: "基于既有模板重排",
    ChangeMode.NEW_COMPOSITION: "新建表达方案",
    ChangeMode.FAITHFUL_RECONSTRUCTION: "忠实可编辑重建",
    ChangeMode.VISUAL_ALTERNATE: "视觉替代方案",
}
_PRESENTATION_FORM_LABELS: dict[PresentationForm, str] = {
    PresentationForm.GENERAL: "通用演示",
    PresentationForm.GOVERNMENT_BRIEFING: "正式政务／机构汇报",
    PresentationForm.BUSINESS_REPORT: "业务／管理汇报",
    PresentationForm.COMPETITION_DEFENSE: "竞赛／答辩",
    PresentationForm.ACADEMIC_DEFENSE: "学术答辩",
    PresentationForm.TEACHING: "教学／培训",
    PresentationForm.TECHNICAL_EXPLANATION: "技术说明",
    PresentationForm.CEREMONIAL_STORY: "仪式／叙事表达",
    PresentationForm.PREMIUM_BRAND_PORTFOLIO: "品牌／作品集表达",
}
_VISUAL_SYSTEM_LABELS: dict[VisualSystemMode, str] = {
    VisualSystemMode.PRESERVE_EXISTING: "保留已有视觉系统",
    VisualSystemMode.DERIVE_FROM_REFERENCE: "从现有参考中提炼",
    VisualSystemMode.CREATE_NEW: "按本轮内容建立新视觉系统",
}


class PresentationIntentError(ValueError):
    """A Work request cannot be safely bound or interpreted."""


class WorkBindingEnvironmentError(PresentationIntentError):
    """The caller is using the wrong Work wrapper or a stale Work environment."""


def presentation_intent_summary(intent: PresentationIntent) -> dict[str, object]:
    """Return a stable, user-facing explanation of an intake decision.

    This is intentionally separate from ``ProductionRoute``.  It keeps a
    reviewer from having to infer "what will be made" from implementation
    names such as ``native`` or ``template`` and gives the review UI a concise
    way to surface an unresolved request before any generic deck is produced.
    """

    needs_attention = intent.change_mode == ChangeMode.UNCLASSIFIED
    return {
        "status": "needs_attention" if needs_attention else "ready",
        "change_mode": {
            "value": intent.change_mode.value,
            "label": _CHANGE_MODE_LABELS[intent.change_mode],
        },
        "presentation_form": {
            "value": intent.presentation_form.value,
            "label": _PRESENTATION_FORM_LABELS[intent.presentation_form],
        },
        "visual_system": {
            "value": intent.visual_system.value,
            "label": _VISUAL_SYSTEM_LABELS[intent.visual_system],
        },
        "confidence": intent.confidence,
        "reason": intent.reason,
        "needs_attention": needs_attention,
        "attention": (
            "当前需求无法可靠区分局部修改对象；补充唯一的原稿或模板后再继续。"
            if needs_attention else None
        ),
        "rule": "表达场景决定信息组织和审阅重点，不预设通用颜色、字号或版式。",
    }


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_write_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_bytes(value)
    temporary.replace(path)


def _safe_relative(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    if candidate == root.resolve() or root.resolve() not in candidate.parents:
        raise PresentationIntentError("intake request snapshot escapes the job directory")
    return candidate


def _work_dir(value: Path | str | None = None) -> Path | None:
    raw = str(value) if value is not None else os.environ.get("AIPPT_WORK_DIR", "")
    if not raw.strip():
        return None
    work = Path(raw).expanduser().resolve()
    if not work.is_dir() or not (work / "workspace").is_dir():
        raise WorkBindingEnvironmentError("AIPPT_WORK_DIR must name an existing Work with workspace/")
    canonical_work_id(work)
    return work


def _read_work_request(work: Path) -> tuple[bytes, str]:
    source = (work / "input" / "request.md").resolve()
    if work not in source.parents or not source.is_file():
        raise PresentationIntentError("Work is missing input/request.md")
    payload = source.read_bytes()
    if len(payload) > MAX_WORK_REQUEST_BYTES:
        raise PresentationIntentError(
            f"Work request exceeds {MAX_WORK_REQUEST_BYTES} bytes; keep task instructions in input/request.md concise"
        )
    return payload, _sha256_bytes(payload)


def canonical_work_id(work: Path) -> str:
    """Return the physical Work identity, refusing copied metadata.

    A Work request is a provenance boundary.  Trusting a copied ``work.json``
    with a different ID lets a job snapshot from one Work claim another Work's
    archive.  The Work wrapper only accepts the canonical ``work/<N>`` package
    and requires its declared ID to agree.
    """

    work = work.resolve()
    if work.parent.name != "work" or not work.name.isdigit():
        raise WorkBindingEnvironmentError("AIPPT_WORK_DIR must be the canonical work/<N> directory")
    record = work / "work.json"
    if not record.is_file():
        raise WorkBindingEnvironmentError("AIPPT_WORK_DIR is missing its canonical work.json identity")
    try:
        parsed = json.loads(record.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkBindingEnvironmentError("AIPPT_WORK_DIR has unreadable work.json identity") from error
    declared = parsed.get("work_id") if isinstance(parsed, dict) else None
    if not isinstance(declared, str) or declared != work.name:
        raise WorkBindingEnvironmentError("work.json work_id must match the physical work/<N> directory")
    return work.name


def _work_id(work: Path) -> str:
    """Compatibility-private alias for the canonical Work identity."""

    return canonical_work_id(work)


def _require_work_job_root(job_id: str, work: Path) -> None:
    """Ensure a Work request cannot be attached to an unrelated jobs root.

    The Work wrapper is a provenance boundary: it keeps Studio's mutable job
    state underneath ``work/<N>/workspace/studio-jobs``.  A stale shell
    environment must not let ``aippt new`` write the job somewhere else while
    consuming this Work's request as if it belonged there.
    """

    expected_root = (work / "workspace" / "studio-jobs").resolve()
    actual_root = jobs_root().resolve()
    root = job_dir(job_id).resolve()
    if actual_root != expected_root or root.parent != expected_root:
        raise WorkBindingEnvironmentError(
            "Work-bound Studio jobs must use this Work's workspace/studio-jobs wrapper; "
            "the active AIPPT_JOBS_DIR belongs to a different location"
        )


@locked_job
def bind_work_request(job_id: str, work_dir: Path | str | None = None) -> tuple[WorkRequestBinding | None, bool]:
    """Copy the current Work request into a hash-addressed job snapshot.

    ``input/request.md`` remains immutable input.  Earlier snapshots are never
    overwritten, which lets a later correction invalidate a draft without
    rewriting its provenance.
    """

    work = _work_dir(work_dir)
    if work is None:
        return None, False
    job = load_job(job_id)
    root = job_dir(job_id)
    _require_work_job_root(job_id, work)
    work_id = _work_id(work)
    payload, source_sha256 = _read_work_request(work)
    snapshot_relative = f"{_SNAPSHOT_DIRECTORY}/{source_sha256}.md"
    snapshot = _safe_relative(root, snapshot_relative)
    if not snapshot.is_file():
        _atomic_write_bytes(snapshot, payload)
    snapshot_sha256 = _sha256_bytes(snapshot.read_bytes())
    if snapshot_sha256 != source_sha256:
        raise PresentationIntentError("Work request snapshot hash does not match its source")
    existing = job.work_request
    if existing is not None and existing.work_id != work_id:
        raise WorkBindingEnvironmentError(
            f"Studio job is already bound to Work {existing.work_id}; "
            f"it cannot be rebound to Work {work_id}"
        )
    # ``bound_at`` is provenance, not a change signal.  Reconstructing a model
    # with a fresh timestamp on every run would make an unchanged request look
    # like a correction and continually invalidate an otherwise reviewable job.
    if (
        existing is not None
        and existing.work_id == work_id
        and existing.source_relative_path == "input/request.md"
        and existing.source_sha256 == source_sha256
        and existing.snapshot_path == snapshot_relative
        and existing.snapshot_sha256 == snapshot_sha256
    ):
        return existing, False
    binding = WorkRequestBinding(
        work_id=work_id,
        source_sha256=source_sha256,
        snapshot_path=snapshot_relative,
        snapshot_sha256=snapshot_sha256,
    )
    job.work_request = binding
    save_job(job)
    return binding, True


def read_bound_work_request(job_id: str) -> tuple[str, WorkRequestBinding | None]:
    """Return the immutable snapshot text attached to a Studio job."""

    job = load_job(job_id)
    binding = job.work_request
    if binding is None:
        return "", None
    snapshot = _safe_relative(job_dir(job_id), binding.snapshot_path)
    if not snapshot.is_file():
        raise PresentationIntentError("bound Work request snapshot is missing")
    payload = snapshot.read_bytes()
    if _sha256_bytes(payload) != binding.snapshot_sha256:
        raise PresentationIntentError("bound Work request snapshot changed after binding")
    return payload.decode("utf-8-sig", errors="replace"), binding


def _is_negated_prefix(prefix: str) -> bool:
    return bool(
        re.search(
            rf"{_NEGATION_PREFIX}[^，。；,;.!！?？\n]{{0,12}}$",
            prefix,
            flags=re.IGNORECASE,
        )
    )


def _positive_mentions(text: str, markers: Sequence[str]) -> bool:
    lowered = text.lower()
    for marker in markers:
        for match in re.finditer(re.escape(marker.lower()), lowered):
            prefix = re.split(r"[，。；,;.!！?？\n]", lowered[max(0, match.start() - 24):match.start()])[-1]
            # Chinese negation commonly carries a modifier between the negator
            # and the matched word (for example, ``不要华丽品牌风``).  Treat a
            # negator anywhere in the current clause as negation for this
            # occurrence, while keeping markers such as ``不要增加目录`` valid
            # when they themselves are the requested constraint.
            if _is_negated_prefix(prefix):
                continue
            return True
    return False


def _positive_source(parts: Sequence[tuple[str, str]], markers: Sequence[str]) -> str | None:
    return next((source for source, text in parts if _positive_mentions(text, markers)), None)


def _target_slide_numbers(text: str) -> list[int]:
    values: set[int] = set()
    for match in re.finditer(r"第\s*((?:\d{1,3}\s*[、,，和及]\s*)*\d{1,3})\s*页", text):
        values.update(int(value) for value in re.findall(r"\d{1,3}", match.group(1)))
    for match in re.finditer(r"第\s*(\d{1,3})\s*(?:-|至|到)\s*(\d{1,3})\s*页", text):
        start, end = int(match.group(1)), int(match.group(2))
        if end >= start and end - start <= 40:
            values.update(range(start, end + 1))
    return sorted(value for value in values if value > 0)


def requests_bounded_revision(text: str) -> bool:
    """Whether an instruction explicitly asks for a limited existing-deck edit.

    This predicate belongs next to intent parsing so Work intake and the
    classifier cannot gradually diverge over what counts as a local revision.
    It is intentionally conservative: a topic mentioning a page number is not
    enough unless it uses the explicit ``第 N 页`` form.
    """

    explicit_local = _positive_mentions(
        text,
        ("仅改", "只改", "局部", "微调", "其余不动", "其他页不动", "原稿不变", "保留其他页", "指定页"),
    )
    if explicit_local:
        return True
    # A page-by-page outline for a new deck is not a local modification.  A
    # scope becomes a preservation contract only when an affirmative edit verb
    # occurs in the *same clause* ("第4页改" / "修改第4至6页").  Splitting by
    # clauses also prevents a later "其余内容" sentence from reclassifying an
    # otherwise normal new-deck outline.
    for clause in re.split(_CLAUSE_BOUNDARY, text):
        if not re.search(_PAGE_REFERENCE_FRAGMENT, clause):
            continue
        if _positive_mentions(clause, _REVISION_MARKERS):
            return True
        # ``第4页改、其余不动`` is common shorthand.  Keep this narrowly bound
        # to a page reference so ordinary prose such as "改进方案" is not a
        # local-edit signal.
        shorthand = re.search(rf"{_PAGE_REFERENCE_FRAGMENT}\s*(?:改|调)(?:\s|$)", clause)
        if shorthand and not _is_negated_prefix(clause[: shorthand.start()]):
            return True
    return False


def requests_template_adaptation(text: str) -> bool:
    """Whether a request makes a template/master an execution requirement."""

    if _positive_mentions(
        text,
        ("沿用模板", "保持模板", "保留母版", "保留模板", "按模板", "使用模板", "基于模板", "延续模板"),
    ):
        return True
    # "根据模板库里面合适的模板进行套用" expresses the same requirement, but
    # its noun and verb are separated.  Require both within one clause so a
    # passing mention of a library does not turn a loose reference into a
    # mandatory template, and keep negated requests such as "不要套用模板" out.
    return any(
        "模板" in clause and _positive_mentions(clause, ("套用",))
        for clause in re.split(_CLAUSE_BOUNDARY, text)
    )


def source_document_identity(
    job_root: Path,
    records: Sequence[SourceRecord],
    roles: Mapping[str, InputRole],
) -> list[tuple[str, str]]:
    """Read a document filename and leading title lines as occasion context."""

    identity: list[tuple[str, str]] = []
    for record in records:
        if record.kind not in {"word", "pdf"} or roles.get(record.id, InputRole.CONTENT) != InputRole.CONTENT:
            continue
        identity.append((f"source:{record.id}:filename", Path(record.original).name))
        relative = Path(record.normalized_path or "")
        if not record.normalized_path or relative.is_absolute() or ".." in relative.parts:
            continue
        path = job_root / relative
        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                beginning = stream.read(4000)
        except OSError:
            continue
        for index, line in enumerate((line.strip() for line in beginning.splitlines() if line.strip()), start=1):
            if index > 3:
                break
            identity.append((f"source:{record.id}:title:{index}", line[:160]))
    return identity


def _page_delta(text: str) -> int | None:
    matches = []
    for match in re.finditer(r"(?:前插|后插|新增|增加)\s*(\d{1,3})\s*页", text):
        prefix = re.split(r"[，。；,;.!！?？\n]", text[max(0, match.start() - 24):match.start()].lower())[-1]
        if not _is_negated_prefix(prefix):
            matches.append(match)
    return int(matches[-1].group(1)) if matches else None


def _form_from_parts(parts: Sequence[tuple[str, str]]) -> tuple[PresentationForm, str | None, str]:
    correction = re.compile(r"(?:改为|改成|改做|调整为|换成)\s*(工作汇报|业务汇报|经营汇报|政务汇报|政府汇报|教学课件|培训课程|技术说明|架构说明|论文答辩|学术答辩|毕业答辩|职业规划大赛|创业竞赛|品牌作品集|庆典演示)")
    for source, text in parts:
        for match in reversed(list(correction.finditer(text))):
            prefix = re.split(_CLAUSE_BOUNDARY, text[:match.start()])[-1]
            suffix = text[match.end():match.end() + 4]
            if (_is_negated_prefix(prefix) or any(word in prefix[-16:] for word in ("标题", "图表", "配色", "样式", "文字", "文案", "风格"))
                    or suffix.startswith(("风格", "样式", "模板", "配色"))):
                continue
            form, _, reason = _form_from_parts([(source, match.group(1))])
            return form, source, "按当前明确更改的演示性质判断。" + reason
    academic_defense = _positive_source(parts, ("学术答辩", "论文答辩", "毕业答辩"))
    contest = _positive_source(parts, ("竞赛", "大赛", "比赛", "参赛", "竞聘", "路演"))
    if academic_defense and not contest:
        return PresentationForm.ACADEMIC_DEFENSE, academic_defense, "按研究问题、方法、证据、贡献和边界组织学术答辩。"
    # Explicit occasion phrases outrank style adjectives, a department name,
    # or the subject being discussed. E.g. a premium-looking work report is
    # still a report; training government staff is still teaching.
    specific = (
        (PresentationForm.COMPETITION_DEFENSE, ("竞赛答辩", "职业规划大赛", "创新创业大赛", "竞聘演讲"), "按当前评审目的组织论证与证据。"),
        (PresentationForm.BUSINESS_REPORT, ("工作汇报", "经营汇报", "经营复盘", "年度述职", "进度汇报", "进展汇报", "业务汇报"), "以本次汇报的现状、证据和后续行动组织，不把题材或风格当作场合。"),
        (PresentationForm.TEACHING, ("教学", "培训", "课程", "课堂", "授课", "科普课"), "以当前学习者的理解路径、示例和练习组织，题材不改变教学目的。"),
        (PresentationForm.TECHNICAL_EXPLANATION, ("技术说明", "技术架构说明", "架构说明", "系统说明", "方案说明"), "解释机制、接口、流程与边界；视觉风格不能覆盖技术说明目的。"),
    )
    for form, markers, reason in specific:
        if source := _positive_source(parts, markers):
            if form == PresentationForm.BUSINESS_REPORT:
                if institution := _positive_source(parts, ("党政", "政务", "政府", "机关", "调度会", "工作专班")):
                    return PresentationForm.GOVERNMENT_BRIEFING, institution, "正式机构工作汇报，按事实、责任与时序组织。"
            return form, source, reason
    families: tuple[tuple[PresentationForm, tuple[str, ...], str], ...] = (
        (
            PresentationForm.GOVERNMENT_BRIEFING,
            ("党政", "政务", "政府", "政府领导", "机关", "调度会", "工作专班", "领导小组"),
            "正式机构汇报的事实、责任和时序关系优先，不套用销售或咨询叙事。",
        ),
        (
            PresentationForm.COMPETITION_DEFENSE,
            ("竞赛", "大赛", "比赛", "参赛", "竞聘", "路演", "答辩"),
            "围绕评审问题、方法、证据和可行性组织，不用泛化企业汇报替代。",
        ),
        (
            PresentationForm.TEACHING,
            ("教学", "培训", "课程", "课堂", "科普", "授课", "学生", "家属"),
            "以理解路径、示例和认知负荷组织信息。",
        ),
        (
            PresentationForm.PREMIUM_BRAND_PORTFOLIO,
            ("作品集", "高端", "华丽", "奢华", "时尚", "品牌", "精品", "电影感"),
            "以真实作品或主视觉为重心，使用编辑式层次和克制留白，不用装饰性卡片伪造高级感。",
        ),
        (
            PresentationForm.CEREMONIAL_STORY,
            ("盛典", "庆典", "仪式", "典礼", "发布会", "开场"),
            "以身份、情绪与节奏组织页面关系。",
        ),
        (
            PresentationForm.ACADEMIC_DEFENSE,
            ("学术", "论文", "研究", "课题", "病例"),
            "问题、方法、证据、贡献和边界必须清楚对应。",
        ),
        (
            PresentationForm.BUSINESS_REPORT,
            ("经营", "复盘", "述职", "管理层", "董事会", "进度汇报", "工作汇报", "决策"),
            "结论、证据、行动与责任关系清楚，避免套成通用咨询卡片。",
        ),
        (
            PresentationForm.TECHNICAL_EXPLANATION,
            ("技术", "架构", "系统", "方案说明", "操作流程"),
            "概念、机制、证据和使用边界清楚对应。",
        ),
    )
    for form, markers, reason in families:
        if source := _positive_source(parts, markers):
            return form, source, reason
    return PresentationForm.GENERAL, None, "未发现足以把普通演示强行归入特定场景的明确要求。"


def _intent_from_mode(
    text: str,
    roles: Mapping[str, InputRole],
    route: ProductionRoute | None,
) -> tuple[ChangeMode, VisualSystemMode, str, list[str]]:
    role_values = set(roles.values())
    has_template = bool(role_values & {InputRole.TEMPLATE, InputRole.EDIT_TARGET})
    target_slides = _target_slide_numbers(text)
    new_system = _positive_mentions(text, ("新建模板", "全新模板", "全新风格", "完全重新设计", "不沿用原", "从零设计", "新视觉体系"))
    local_language = requests_bounded_revision(text)
    preserve_language = requests_template_adaptation(text) or _positive_mentions(text, ("保留原有", "延续", "沿用", "不改变"))
    if route == ProductionRoute.REBUILD or InputRole.FLATTENED_INPUT in role_values:
        return ChangeMode.FAITHFUL_RECONSTRUCTION, VisualSystemMode.DERIVE_FROM_REFERENCE, "输入是扁平化材料，需要以可编辑重建为目标。", ["input_role:flattened_input"]
    if route == ProductionRoute.VISUAL:
        return ChangeMode.VISUAL_ALTERNATE, VisualSystemMode.DERIVE_FROM_REFERENCE, "当前技术路线已被明确接受为图片型替代稿。", ["production_route:visual"]
    if new_system:
        return ChangeMode.NEW_COMPOSITION, VisualSystemMode.CREATE_NEW, "要求建立新的视觉系统或完整重新设计，不把既有模板误当成必须保留。", ["request:new_visual_system"]
    if has_template and local_language:
        return ChangeMode.LOCAL_PATCH, VisualSystemMode.PRESERVE_EXISTING, "现有演示与明确局部范围同时存在，必须受控修改而非重新拼装整份模板。", ["input_role:edit_target_or_template", "request:bounded_revision"]
    if local_language:
        return ChangeMode.UNCLASSIFIED, VisualSystemMode.PRESERVE_EXISTING, "要求只修改既有演示的局部范围，但尚未指定可编辑原稿；不能把局部修订静默改成新建演示。", ["request:bounded_revision", "input_role:edit_target_missing"]
    if has_template and preserve_language:
        return ChangeMode.TEMPLATE_RELAYOUT, VisualSystemMode.PRESERVE_EXISTING, "需要沿用已确认的模板或母版系统，同时重新组织目标内容。", ["input_role:edit_target_or_template", "request:preserve_visual_system"]
    if has_template:
        return ChangeMode.TEMPLATE_RELAYOUT, VisualSystemMode.DERIVE_FROM_REFERENCE, "存在模板或编辑目标，但尚未声称它等同于全部页面都必须逐页保留。", ["input_role:edit_target_or_template"]
    return ChangeMode.NEW_COMPOSITION, VisualSystemMode.CREATE_NEW, "没有必须继承的模板或编辑目标，按内容和本轮场景建立新稿。", ["input_role:content"]


def select_presentation_intent(
    brief: JobBrief,
    roles: Mapping[str, InputRole],
    *,
    request_text: str = "",
    request_sha256: str | None = None,
    editorial_evidence: Sequence[tuple[str, str]] = (),
    source_identity: Sequence[tuple[str, str]] = (),
    route: ProductionRoute | None = None,
) -> PresentationIntent:
    """Classify only explicit instruction channels, never business-body keywords.

    ``editorial_evidence`` holds ``(source_ref, text)`` pairs that were already
    separated from source facts by the planner.  The current Work request,
    direct style direction and customer constraints are also valid instruction
    channels.  A historical automatic decision is recomputed; a user override
    remains authoritative only for the dimension it explicitly overrides.
    """

    parts: list[tuple[str, str]] = []
    if request_text.strip():
        parts.append(("work_request", request_text))
    parts.extend((source, text) for source, text in editorial_evidence if text.strip())
    if brief.style_direction.strip():
        parts.append(("brief:style_direction", brief.style_direction))
    if brief.customer_constraints:
        parts.append(("brief:customer_constraints", "\n".join(brief.customer_constraints)))
    decision_text = "\n".join(text for _, text in parts)
    mode, visual_system, mode_reason, mode_evidence = _intent_from_mode(decision_text, roles, route)
    form, form_source, form_reason = _form_from_parts(parts)
    # A contest application is source identity, not a production instruction.
    # Use it only when the request and explicit editorial channels did not
    # select an occasion; never scan ordinary body paragraphs for this.
    if form == PresentationForm.GENERAL:
        contest_identity = [
            (source, title) for source, title in source_identity
            if re.search(r"(?:竞赛|大赛|比赛|参赛).{0,12}(?:申报书|项目书|参赛作品)|(?:答辩|路演)", title)
        ]
        if contest_identity:
            form = PresentationForm.COMPETITION_DEFENSE
            form_source = contest_identity[0][0]
            form_reason = "来源文档的标题明确为竞赛申报或答辩材料；按评审问题、证据与可行性组织。"
    target_slides = _target_slide_numbers(decision_text) if (mode == ChangeMode.LOCAL_PATCH or requests_bounded_revision(decision_text)) else []
    scoped = mode == ChangeMode.LOCAL_PATCH or _positive_mentions(
        decision_text, ("其余不动", "其他页不动", "非目标页", "原稿不变", "保留其他页")
    )
    preservation = PreservationContract(
        target_slide_numbers=target_slides,
        preserve_non_target_slides=scoped,
        preserve_master_theme=_positive_mentions(decision_text, ("母版", "主题", "配色", "视觉体系", "模板")),
        preserve_brand_objects=_positive_mentions(decision_text, ("logo", "标识", "品牌", "页眉", "页脚")),
        preserve_media=_positive_mentions(decision_text, ("视频", "gif", "动图", "动画", "音频", "媒体")),
        insert_position=(
            "before_first_slide" if _positive_mentions(decision_text, ("前插", "插在第一页前", "放在开头"))
            else "after_last_slide" if _positive_mentions(decision_text, ("后插", "追加", "放在最后"))
            else "replace_target_slides" if target_slides else "unspecified"
        ),
        page_delta=_page_delta(decision_text),
        allow_auto_cover=not scoped and not _positive_mentions(decision_text, ("不要封面", "不加封面")),
        allow_auto_outline=not scoped and not _positive_mentions(
            decision_text,
            ("不要目录", "不要增加目录", "不增加目录", "不加目录", "不需要目录", "无需目录"),
        ),
        allow_auto_conclusion=not scoped and not _positive_mentions(decision_text, ("不要结尾", "不加结尾", "无需结论页")),
    )
    evidence = [*mode_evidence]
    if form_source:
        evidence.append(f"{form_source}:presentation_form")
    if target_slides:
        evidence.append("request:target_slide_scope")
    if preservation.allow_auto_outline is False:
        evidence.append("request:no_auto_outline")
    if not parts:
        confidence = "unresolved"
    elif request_text.strip() or editorial_evidence or brief.style_direction.strip() or brief.customer_constraints:
        confidence = "explicit" if bool(request_text.strip() or editorial_evidence) else "inferred"
    else:
        confidence = "inferred"
    prior = brief.presentation_intent
    if prior.change_mode_source == "user_override":
        mode = prior.change_mode
        mode_reason = prior.reason or mode_reason
        evidence.append("brief:user_override_change_mode")
    if prior.presentation_form_source == "user_override":
        form = prior.presentation_form
        form_reason = prior.reason or form_reason
        evidence.append("brief:user_override_presentation_form")
    if prior.visual_system_source == "user_override":
        visual_system = prior.visual_system
        evidence.append("brief:user_override_visual_system")
    source = "user_override" if any(
        value == "user_override"
        for value in (prior.change_mode_source, prior.presentation_form_source, prior.visual_system_source)
    ) else "automatic"
    return PresentationIntent(
        change_mode=mode,
        visual_system=visual_system,
        presentation_form=form,
        preservation=preservation,
        source=source,
        change_mode_source="user_override" if prior.change_mode_source == "user_override" else "automatic",
        presentation_form_source="user_override" if prior.presentation_form_source == "user_override" else "automatic",
        visual_system_source="user_override" if prior.visual_system_source == "user_override" else "automatic",
        confidence=confidence,
        reason=f"{mode_reason} {form_reason}".strip(),
        evidence=list(dict.fromkeys(evidence)),
        request_sha256=request_sha256,
    )


def apply_intent_overrides(
    intent: PresentationIntent,
    *,
    change_mode: ChangeMode | None = None,
    presentation_form: PresentationForm | None = None,
    visual_system: VisualSystemMode | None = None,
    reason: str = "",
) -> PresentationIntent:
    """Apply only supplied override dimensions; automatic dimensions stay live."""

    changed = any(item is not None for item in (change_mode, presentation_form, visual_system))
    if not changed:
        raise PresentationIntentError("set at least one presentation-intent dimension")
    next_intent = intent.model_copy(deep=True)
    if change_mode is not None:
        scope = next_intent.preservation.target_slide_numbers
        is_scoped = bool(scope) or next_intent.preservation.preserve_non_target_slides
        if change_mode == ChangeMode.LOCAL_PATCH and not scope:
            raise PresentationIntentError(
                "local_patch requires an explicit target-page scope from the Work request or a refreshed edit_target classification"
            )
        if change_mode != ChangeMode.LOCAL_PATCH and is_scoped:
            raise PresentationIntentError(
                "the current preservation contract is scoped to an existing deck; update the Work request and reset automatic classification before changing it to a non-scoped mode"
            )
        next_intent.change_mode = change_mode
        next_intent.change_mode_source = "user_override"
        if change_mode == ChangeMode.LOCAL_PATCH:
            # A direct local-patch override must never inherit a generic
            # cover/agenda/closing scaffold while its object-level contract is
            # being prepared.
            next_intent.preservation.allow_auto_cover = False
            next_intent.preservation.allow_auto_outline = False
            next_intent.preservation.allow_auto_conclusion = False
            next_intent.evidence = list(dict.fromkeys([*next_intent.evidence, "manual:local_patch_scope_required"]))
    if presentation_form is not None:
        next_intent.presentation_form = presentation_form
        next_intent.presentation_form_source = "user_override"
    if visual_system is not None:
        next_intent.visual_system = visual_system
        next_intent.visual_system_source = "user_override"
    next_intent.source = "user_override"
    next_intent.confidence = "explicit"
    next_intent.decided_at = utc_now()
    next_intent.reason = reason.strip() or "当前助手根据本轮明确要求覆盖了指定的任务意图维度。"
    next_intent.evidence = list(dict.fromkeys([*next_intent.evidence, "manual:presentation_intent_override"]))
    return next_intent
