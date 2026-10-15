"""Persist task-specific authoring decisions for every newly initialized Work.

This is assistant planning inside the existing production lifecycle, not a new
user approval and not a visual-quality score. Older Works remain compatible.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .models import ChangeMode, DeckPlan, JobBrief, JobState
from .storage import job_dir, load_job, locked_job, read_json, read_model, save_job, write_json

PREFIX = "设计规划尚未完成："
FIELDS = ("narrative_intent", "whole_deck_reference_decision", "page_design")
NARRATIVE_FIELDS = ("task_specific_type", "audience_need", "core_message", "storyline", "evidence_boundary")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _work(root: Path) -> Path | None:
    for parent in root.resolve().parents:
        if parent.name.isdigit() and parent.parent.name == 'work' and (parent / 'work.json').is_file():
            return parent
    return None


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _signature(plan: DeckPlan) -> str:
    # Strategy changes are validated separately; titles, purpose, copy and
    # evidence may not drift while reusing an old authoring decision.
    data = {'title': plan.title, 'slides': [s.model_dump(mode='json') for s in plan.slides],
            'decisions': {key: plan.planning_strategy.get(key) for key in FIELDS}}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _validate_reference(decision: dict, workspace: Path, label: str, *, whole: bool, hashes: dict, allow_preserve: bool) -> list[str]:
    errors = []
    if not _nonempty(decision.get('reason')):
        errors.append(f'{label}: 缺少选用/自建/保留理由')
    mode = decision.get('mode')
    if mode == 'new_composition':
        return errors
    if mode == 'preserve_existing':
        return errors if allow_preserve else errors + [f'{label}: 未绑定可保留的模板或编辑目标']
    if mode != 'reference':
        return errors + [f'{label}: mode须为reference、new_composition或preserve_existing']
    for key in ('source_path', 'source_sha256', 'borrowed_relationship', 'observation', 'adaptation'):
        if not _nonempty(decision.get(key)):
            errors.append(f'{label}: 缺少{key}')
    raw = decision.get('source_path')
    if not _nonempty(raw):
        return errors
    from .style_search import _safe_source
    source = _safe_source(workspace / '模板库', raw)
    if source is None:
        return errors + [f'{label}: 来源不存在或不在模板库内']
    if source not in hashes:
        hashes[source] = _sha(source)
    if hashes[source] != decision.get('source_sha256'):
        errors.append(f'{label}: 来源身份已变化')
    pages = decision.get('source_pages') if whole else [decision.get('source_slide')]
    if not isinstance(pages, list) or not pages or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in pages):
        return errors + [f'{label}: 需要明确观察的源页码']
    try:
        from zipfile import ZipFile, BadZipFile
        from xml.etree import ElementTree as ET
        key = ('pages', source)
        if key not in hashes:
            with ZipFile(source) as archive:
                hashes[key] = len(ET.fromstring(archive.read('ppt/presentation.xml')).findall(
                    './/{http://schemas.openxmlformats.org/presentationml/2006/main}sldId'))
        if any(page > hashes[key] for page in pages):
            errors.append(f'{label}: 声明页码超出来源演示范围')
    except (OSError, ValueError, KeyError, BadZipFile, ET.ParseError):
        errors.append(f'{label}: 来源不是可读取的PPTX演示')
    previews = decision.get('previews', [])
    if not isinstance(previews, list) or not previews:
        return errors + [f'{label}: 缺少已观察的源页图身份']
    observed_pages = set()
    for preview in previews:
        if not isinstance(preview, dict) or not isinstance(preview.get('path'), str):
            errors.append(f'{label}: 源页图记录格式无效')
            continue
        if preview.get('source_sha256') != decision.get('source_sha256') or preview.get('source_slide') not in pages:
            errors.append(f'{label}: 源页图的来源/页码身份不对应')
        else:
            observed_pages.add(preview['source_slide'])
        path = Path(preview['path'])
        path = (workspace / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(workspace.resolve()) or not path.is_file():
            errors.append(f'{label}: 源页图须在当前工作区内且存在')
            continue
        if path not in hashes:
            hashes[path] = _sha(path)
        if hashes[path] != preview.get('sha256'):
            errors.append(f'{label}: 源页图已变化')
    if observed_pages != set(pages):
        errors.append(f'{label}: 源页图未覆盖声明观察的页面')
    return errors


def design_plan_status(root: Path, plan: DeckPlan | None = None, *, require_binding: bool = True) -> dict:
    work = _work(root)
    if work is None or read_json(work / 'work.json').get('authoring_workflow_version') != 1:
        return {'status': 'legacy_or_unmanaged', 'required': False, 'issues': []}
    brief_path = root / 'planning/brief.json'
    if not brief_path.is_file():
        return {'status': 'awaiting_brief', 'required': False, 'issues': []}
    brief = read_model(brief_path, JobBrief)
    if brief.presentation_intent.change_mode in {ChangeMode.LOCAL_PATCH, ChangeMode.FAITHFUL_RECONSTRUCTION}:
        return {'status': 'bounded_revision_or_reconstruction', 'required': False, 'issues': []}
    if plan is None:
        if not (root / 'planning/deck-plan.json').is_file():
            return {'status': 'needs_design_plan', 'required': True, 'issues': ['尚未生成内容计划']}
        plan = read_model(root / 'planning/deck-plan.json', DeckPlan)
    strategy = plan.planning_strategy
    narrative = strategy.get('narrative_intent', {})
    narrative = narrative if isinstance(narrative, dict) else {}
    issues = [f'叙事: 缺少{key}' for key in NARRATIVE_FIELDS if not _nonempty(narrative.get(key))]
    pages = strategy.get('page_design', {})
    pages = pages if isinstance(pages, dict) else {}
    hashes = {}
    record = read_json(root / 'job.json') if (root / 'job.json').is_file() else {}
    allow_preserve = any(value in {'template', 'edit_target'} for value in record.get('input_roles', {}).values())
    whole = strategy.get('whole_deck_reference_decision', {})
    issues += _validate_reference(whole if isinstance(whole, dict) else {}, work.parent.parent, '整稿', whole=True, hashes=hashes, allow_preserve=allow_preserve)
    unknown = set(pages) - {slide.id for slide in plan.slides}
    if unknown:
        issues.append('包含不属于当前计划的页面: ' + ', '.join(sorted(unknown)))
    for slide in plan.slides:
        meta = pages.get(slide.id, {})
        meta = meta if isinstance(meta, dict) else {}
        for key in ('page_job', 'content_relationship'):
            if not _nonempty(meta.get(key)):
                issues.append(f'{slide.id}: 缺少{key}')
        decision = meta.get('reference_decision', {})
        issues += _validate_reference(decision if isinstance(decision, dict) else {}, work.parent.parent, slide.id, whole=False, hashes=hashes, allow_preserve=allow_preserve)
    if require_binding:
        binding = strategy.get('design_plan_binding', {})
        request_path = work / 'input/request.md'
        expected = {'brief_sha256': _sha(brief_path), 'content_sha256': _signature(plan),
                    'request_sha256': _sha(request_path) if request_path.is_file() else None}
        if binding != expected:
            issues.append('规划未通过design-plan记录，或当前需求/brief/内容/选择已改变')
    return {'status': 'needs_design_plan' if issues else 'recorded_for_build_not_visual_approval',
            'required': True, 'issues': issues, 'next': 'design-plan <job> --request <作者规划.json>；由当前助手完成，不新增用户审批'}


def design_plan_block_reason(root: Path, plan: DeckPlan | None = None) -> str | None:
    status = design_plan_status(root, plan)
    return PREFIX + '；'.join(status['issues'][:6]) + '。' + status['next'] if status['issues'] else None


@locked_job
def author_design_plan(job_id: str, request: dict | None = None) -> dict:
    job = load_job(job_id)
    root = job_dir(job_id)
    eligibility = design_plan_status(root)
    if not eligibility['required']:
        if request is not None:
            raise ValueError('design-plan recording is limited to versioned new Work authoring; preserve this existing job')
        return eligibility
    state = job.resume_state if job.state == JobState.BLOCKED else job.state
    if state not in {JobState.PROTOTYPE_REVIEW, JobState.FULL_BUILD, JobState.FINAL_REVIEW}:
        raise ValueError('design-plan requires an existing reviewed brief before delivery')
    if job.state == JobState.BLOCKED and not (job.blocked_reason or '').startswith(PREFIX):
        raise ValueError('Resolve the existing non-design failure before changing authoring decisions')
    if not (root / 'planning/deck-plan.json').is_file():
        from .planner import create_deck_plan
        create_deck_plan(job_id)
    plan = read_model(root / 'planning/deck-plan.json', DeckPlan)
    if request is None:
        existing = plan.planning_strategy.get('page_design', {})
        existing = existing if isinstance(existing, dict) else {}
        return {'status': design_plan_status(root, plan), 'plan': str(root / 'planning/deck-plan.json'),
                'design_context': str(root / 'planning/design-context.json') if (root / 'planning/design-context.json').is_file() else None,
                'request_template': {**{key: plan.planning_strategy.get(key, {}) for key in FIELDS},
                    'page_design': {s.id: existing.get(s.id, {'page_job': '', 'content_relationship': '', 'layout_categories': [], 'reference_decision': {}}) for s in plan.slides}},
                'instruction': 'Read the shared workflow. Author this task’s narrative and page relationships, inspect suitable references, or explain a deliberate new composition. Do not ask the user to fill this technical record.'}
    if not isinstance(request, dict) or set(request) != set(FIELDS):
        raise ValueError('design-plan request must contain narrative_intent, whole_deck_reference_decision and page_design')
    if all(plan.planning_strategy.get(key) == request[key] for key in FIELDS) and not design_plan_status(root, plan)['issues']:
        resumed = job.state == JobState.BLOCKED
        if resumed:
            job.state, job.resume_state, job.blocked_reason, job.failure = state, None, None, None
            save_job(job)
        return {'job_id': job_id, **design_plan_status(root, plan), 'changed': False, 'resumed': resumed}
    plan.planning_strategy.update(request)
    status = design_plan_status(root, plan, require_binding=False)
    if status['issues']:
        raise ValueError(PREFIX + '；'.join(status['issues'][:12]))
    work = _work(root)
    plan.planning_strategy['design_plan_binding'] = {
        'brief_sha256': _sha(root / 'planning/brief.json'), 'content_sha256': _signature(plan),
        'request_sha256': _sha(work / 'input/request.md') if work and (work / 'input/request.md').is_file() else None}
    write_json(root / 'planning/deck-plan.json', plan)
    from .assets.acquisition import _invalidate_rendered_artifacts
    job.approvals.update(prototype=False, final=False)
    job.approval_hashes.pop('prototype', None)
    job.approval_hashes.pop('final', None)
    if job.state == JobState.BLOCKED:
        job.state, job.resume_state, job.blocked_reason, job.failure = state, None, None, None
    job.state = _invalidate_rendered_artifacts(root, job.state, reason='task-specific design plan changed')
    # A previous native scene is intentionally not rebound to a new plan.
    save_job(job)
    from .design_context import build_design_context
    brief = read_model(root / 'planning/brief.json', JobBrief)
    from .config import WORKSPACE_ROOT
    context = build_design_context(root, plan, brief, workspace_root=work.parent.parent if work else WORKSPACE_ROOT)
    context['plan_binding'] = {'plan_sha256': _sha(root / 'planning/deck-plan.json'), 'brief_sha256': _sha(root / 'planning/brief.json')}
    context['scene_sha256'] = None
    write_json(root / 'planning/design-context.json', context)
    return {'job_id': job_id, **design_plan_status(root, plan), 'design_context': str(root / 'planning/design-context.json'),
            'next': 'Author/rebind the scene or template mapping for this plan, build representative pages, then inspect actual pages through existing expression review.'}
