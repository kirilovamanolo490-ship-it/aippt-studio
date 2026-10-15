"""Guard a Work request that explicitly requires applying a library template.

The request is the instruction channel.  An assistant-selected route, source
role, or planning note cannot downgrade "套用模板" into a loose reference.
"""
from __future__ import annotations

from pathlib import Path

from .models import ChangeMode, InputRole, JobBrief, JobRecord, PresentationForm, ProductionRoute, SourceRecord
from .presentation_intent import requests_bounded_revision, requests_template_adaptation, select_presentation_intent, source_document_identity
from .template_library import cataloged_template_by_sha256, occasion_compatible


def requested_presentation_form(
    request_text: str,
    brief: JobBrief,
    job: JobRecord,
    sources: dict[str, SourceRecord],
    job_root: Path | None = None,
) -> PresentationForm:
    """Use instruction channels first, then a content document's identity."""

    return select_presentation_intent(
        JobBrief(goal=brief.goal), {}, request_text=request_text,
        source_identity=source_document_identity(job_root, list(sources.values()), job.input_roles) if job_root else [
            (f"source:{source_id}:filename", Path(source.original).name)
            for source_id, source in sources.items()
            if source.kind in {"word", "pdf"}
            and job.input_roles.get(source_id, InputRole.CONTENT) == InputRole.CONTENT
        ],
    ).presentation_form


def template_request_block_reason(
    request_text: str,
    job: JobRecord,
    brief: JobBrief,
    sources: dict[str, SourceRecord],
    *,
    library_catalog: Path,
    job_root: Path | None = None,
) -> str | None:
    """Return a concrete reason when a required template is only a reference.

    A library match is source-hash bound and its occasion is checked against
    the *request*, so an assistant override cannot silently turn a contest
    into a service report.  User-supplied templates outside the library still
    use the template route, but this catalog check does not judge their style.
    """

    if not requests_template_adaptation(request_text):
        return None
    # A scoped edit can genuinely retain an existing template by patching the
    # named slides. The whole-deck template adapter is not its execution path.
    if requests_bounded_revision(request_text) and brief.presentation_intent.change_mode == ChangeMode.LOCAL_PATCH:
        return None
    requested_form = requested_presentation_form(request_text, brief, job, sources, job_root)
    current_form = brief.presentation_intent.presentation_form
    if requested_form != PresentationForm.GENERAL and current_form != requested_form:
        return (
            f"本轮要求的演示场景是 {requested_form.value}，当前 brief 被改为 {current_form.value}；"
            "请恢复按用户场景分类的任务意图。"
        )
    if brief.presentation_intent.change_mode != ChangeMode.TEMPLATE_RELAYOUT:
        return "本轮要求套用模板，不能改成新建表达或仅参考模板；请按模板重排进行规划。"
    if job.route != ProductionRoute.TEMPLATE:
        return "本轮要求套用模板，必须使用 template 路线，不能只登记 visual_reference 后走 native。"
    selected = [source_id for source_id, role in job.input_roles.items() if role == InputRole.TEMPLATE]
    if len(selected) != 1:
        return "套用模板需要唯一的 PowerPoint 输入被指定为 template；visual_reference 不能证明真实套用。"
    source = sources.get(selected[0])
    if source is None or source.kind != "powerpoint":
        return "指定模板必须是当前任务中可追溯的 PowerPoint 来源。"
    if "模板库" in request_text:
        row = cataloged_template_by_sha256(library_catalog, source.sha256)
        if row is None:
            return "本轮要求从模板库选择模板，但指定文件未通过当前目录和来源哈希校验。"
        if not occasion_compatible(row, requested_form.value):
            return (
                f"所选模板 {row['path']} 的场景标签与 {requested_form.value} 不匹配；"
                "主题相近不能替代竞赛答辩类型，请重新选择场景相符的模板。"
            )
    return None
