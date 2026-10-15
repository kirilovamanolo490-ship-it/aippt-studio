import copy
import hashlib
import json
from pathlib import Path

import pytest

from aippt_studio import evolution
from aippt_studio.authoring_policy import authoring_defaults
from aippt_studio.learning import read_json, write_json


def test_codex_path_uses_bundled_cli_when_old_config_path_is_stale(tmp_path, monkeypatch):
    cli = tmp_path / "CodexCLI.app/Contents/MacOS/codex"
    cli.parent.mkdir(parents=True)
    cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cli.chmod(0o755)
    monkeypatch.setattr(evolution, "DEFAULT_CODEX_CANDIDATES", (str(cli),))
    monkeypatch.setattr(evolution.shutil, "which", lambda _name: None)
    assert evolution._codex_path({"codex_path": str(tmp_path / "old-missing-codex")}) == str(cli.resolve())


def event(tmp_path, identifier='event-a', thread='thread-a', work='1', text='以后每次PPT都不要无意义的装饰编号。', line=1):
    data = (json.dumps({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}}, ensure_ascii=False) + '\n').encode()
    raw = tmp_path / (identifier + '.jsonl')
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    return {'event_id': identifier, 'thread_id': thread, 'work_ids': [work], 'text': text,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'sources': [{'raw_path': str(raw), 'record_sha256': hashlib.sha256(data).hexdigest(),
                         'byte_offset': 0, 'byte_length': len(data), 'line': line}]}


def response(events, *, rule=True):
    return {'summary': 'Review user scope and page meaning.',
            'dispositions': [{'event_id': e['event_id'], 'classification': 'feedback', 'reason': 'Explicit direction for future PPT tasks.', 'implementation_gap': ''} for e in events],
            'rules': [{'id': 'meaningful-navigation', 'action': 'Remove decorative ordinals.',
                       'applies_when': 'Future PPT page composition.', 'exceptions': 'Meaningful quantities or explicitly requested navigation remain.',
                       'scope_basis': 'explicit_workspace_direction', 'scope_reason': 'The user explicitly says every future PPT.',
                       'evidence': [{'event_id': events[0]['event_id'], 'quote': events[0]['text']}], 'supersedes': []}] if rule else [],
            'repair_candidates': []}


def test_all_messages_need_exactly_one_disposition_even_without_keywords(tmp_path):
    a = event(tmp_path)
    b = event(tmp_path, 'b', text='嗯，可以的。')
    result = response([a])
    with pytest.raises(ValueError, match='Every input'):
        evolution.validate_response(result, [a, b], {})
    result = response([a, b], rule=False)
    result['dispositions'][1]['classification'] = 'not_feedback'
    assert evolution.validate_response(result, [a, b], {}) == result
    result['dispositions'].append(result['dispositions'][0])
    with pytest.raises(ValueError, match='Every input'):
        evolution.validate_response(result, [a, b], {})


def test_project_instruction_cannot_become_global_rule(tmp_path):
    a = event(tmp_path)
    result = response([a])
    result['dispositions'][0]['classification'] = 'project_instruction'
    with pytest.raises(ValueError, match='Project-only'):
        evolution.validate_response(result, [a], {})


def test_recurrence_requires_independent_threads_and_can_cross_batches(tmp_path):
    old = event(tmp_path)
    new = event(tmp_path, 'b', thread='thread-b', work='2', text='这个编号没有意义，这次也不要角落的编号。')
    old['prior_classification'] = 'feedback'
    result = response([new])
    result['rules'][0]['scope_basis'] = 'recurring_across_works'
    result['rules'][0]['evidence'].append({'event_id': old['event_id'], 'quote': old['text']})
    assert evolution.validate_response(result, [new], {}, [old])
    old['thread_id'] = new['thread_id']
    with pytest.raises(ValueError, match='independent Work'):
        evolution.validate_response(result, [new], {}, [old])


def test_exact_quotes_and_strict_fields(tmp_path):
    a = event(tmp_path)
    result = response([a])
    result['rules'][0]['evidence'][0]['quote'] = 'The user never actually said this'
    with pytest.raises(ValueError, match='verbatim'):
        evolution.validate_response(result, [a], {})
    result = response([a])
    result['execute'] = 'unrequested shell command'
    with pytest.raises(ValueError, match='unknown fields'):
        evolution.validate_response(result, [a], {})


def test_active_guidance_is_consumed_and_missing_source_is_quarantined(tmp_path):
    a = event(tmp_path)
    run = evolution.directory(tmp_path) / 'runs/one'
    state = {}
    accepted = evolution.accept_response(tmp_path, run, [a], response([a]), state)
    assert accepted['status'] == 'learned'
    assert any(r['id'] == 'learned:meaningful-navigation' for r in authoring_defaults(tmp_path)['rules'])
    assert read_json(run / 'accepted.json')['implementation_verified'] is False
    (tmp_path / 'event-a.jsonl').unlink()
    guidance = evolution.load_guidance(tmp_path)
    assert guidance['status'] == 'partial' and guidance['rules'] == []


def test_cached_text_cannot_forge_original_user_preference(tmp_path):
    a = event(tmp_path, text='这个客户这页的编号先保留。')
    a['text'] = '我要求以后所有项目都不要编号。'
    a['text_sha256'] = hashlib.sha256(a['text'].encode()).hexdigest()
    with pytest.raises(ValueError, match='Derived user text'):
        evolution.accept_response(tmp_path, evolution.directory(tmp_path) / 'runs/one', [a], response([a]), {})
    assert not (evolution.directory(tmp_path) / 'state.json').exists()


def test_explicitly_purged_snapshot_verifies_against_manifest_bound_original_rollout(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    a = event(tmp_path)
    source = a['sources'][0]
    raw = Path(source['raw_path'])
    store = tmp_path / 'work/1/workspace/conversations'
    raw = store / 'snapshots/thread-a/pending.jsonl'
    raw.parent.mkdir(parents=True)
    record = Path(source['raw_path']).read_bytes()
    header = (json.dumps({'type': 'session_meta', 'payload': {'id': 'thread-a'}}) + '\n').encode()
    raw.write_bytes(header + record)
    source['byte_offset'] = len(header)
    rollout = tmp_path / '.codex/sessions/thread-a.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_bytes(raw.read_bytes() + b'{"type":"other"}\n')
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    raw = raw.with_name(digest + '.jsonl')
    raw.with_name('pending.jsonl').replace(raw)
    source.update({'raw_path': str(raw), 'original_rollout_path': str(rollout), 'snapshot_sha256': digest})
    manifest = store / 'manifest.json'
    write_json(store / 'retention-policy.json', {'mode': 'metadata_only'})
    write_json(manifest, {'threads': {'thread-a': {'snapshots': [{
        'source_path': str(rollout), 'sha256': digest, 'bytes': raw.stat().st_size,
        'raw_path': f'snapshots/thread-a/{digest}.jsonl', 'local_copy_deleted_at': '2026-09-30T00:00:00Z',
        'byte_preserved': False}]}}})
    raw.unlink()
    assert evolution.verify_event(a) == a['text']


def test_active_guidance_remains_verifiable_after_explicit_snapshot_purge(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    a = event(tmp_path)
    source = a['sources'][0]
    store = tmp_path / 'work/1/workspace/conversations'
    raw = store / 'snapshots/thread-a/pending.jsonl'
    raw.parent.mkdir(parents=True)
    record = Path(source['raw_path']).read_bytes()
    header = (json.dumps({'type': 'session_meta', 'payload': {'id': 'thread-a'}}) + '\n').encode()
    raw.write_bytes(header + record)
    source['byte_offset'] = len(header)
    rollout = tmp_path / '.codex/sessions/thread-a.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_bytes(raw.read_bytes())
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    raw = raw.with_name(digest + '.jsonl')
    raw.with_name('pending.jsonl').replace(raw)
    source.update({'raw_path': str(raw), 'original_rollout_path': str(rollout), 'snapshot_sha256': digest})
    write_json(store / 'retention-policy.json', {'mode': 'metadata_only'})
    write_json(store / 'manifest.json', {'threads': {'thread-a': {'snapshots': [{
        'raw_path': f'snapshots/thread-a/{digest}.jsonl', 'source_path': str(rollout),
        'sha256': digest, 'bytes': raw.stat().st_size,
        'local_copy_deleted_at': '2026-09-30T00:00:00Z', 'byte_preserved': False}]}}})
    evolution.accept_response(tmp_path, evolution.directory(tmp_path) / 'runs/one', [a], response([a]), {})
    raw.unlink()
    guidance = evolution.load_guidance(tmp_path)
    assert guidance['status'] == 'ready'
    assert [rule['id'] for rule in guidance['rules']] == ['meaningful-navigation']


def test_manifest_fallback_rejects_changed_rollout_or_mismatched_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    a = event(tmp_path)
    source = a['sources'][0]
    original = Path(source['raw_path'])
    store = tmp_path / 'work/1/workspace/conversations'
    raw = store / 'snapshots/thread-a/pending.jsonl'
    raw.parent.mkdir(parents=True)
    record = original.read_bytes()
    header = (json.dumps({'type': 'session_meta', 'payload': {'id': 'thread-a'}}) + '\n').encode()
    raw.write_bytes(header + record)
    source['byte_offset'] = len(header)
    rollout = tmp_path / '.codex/sessions/thread-a.jsonl'
    rollout.parent.mkdir(parents=True)
    rollout.write_bytes(raw.read_bytes())
    digest = hashlib.sha256(rollout.read_bytes()).hexdigest()
    raw = raw.with_name(digest + '.jsonl')
    raw.with_name('pending.jsonl').replace(raw)
    source.update({'raw_path': str(raw), 'original_rollout_path': str(rollout), 'snapshot_sha256': digest})
    manifest = store / 'manifest.json'
    write_json(store / 'retention-policy.json', {'mode': 'metadata_only'})
    write_json(manifest, {'threads': {'thread-a': {'snapshots': [{
        'source_path': str(rollout), 'sha256': digest, 'bytes': rollout.stat().st_size,
        'raw_path': f'snapshots/thread-a/{digest}.jsonl', 'local_copy_deleted_at': '2026-09-30T00:00:00Z',
        'byte_preserved': False}]}}})
    raw.unlink()
    changed = bytearray(rollout.read_bytes())
    changed[0] ^= 1
    rollout.write_bytes(changed)
    with pytest.raises(ValueError, match='rollout|snapshot|metadata'):
        evolution.verify_event(a)


def test_moved_rollout_is_resolved_only_by_exact_name_and_metadata_thread_id(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    a = event(tmp_path)
    source = a['sources'][0]
    store = tmp_path / 'work/1/workspace/conversations'
    local = store / 'snapshots/thread-a/pending.jsonl'
    local.parent.mkdir(parents=True)
    record = Path(source['raw_path']).read_bytes()
    header = (json.dumps({'type': 'session_meta', 'payload': {'id': 'thread-a'}}) + '\n').encode()
    snapshot_bytes = header + record
    local.write_bytes(snapshot_bytes)
    source['byte_offset'] = len(header)
    missing_original = tmp_path / '.codex/sessions/thread-a.rollout.jsonl'
    archived = tmp_path / '.codex/archived_sessions' / missing_original.name
    archived.parent.mkdir(parents=True)
    archived.write_bytes(snapshot_bytes)
    digest = hashlib.sha256(snapshot_bytes).hexdigest()
    local = local.with_name(digest + '.jsonl')
    local.with_name('pending.jsonl').replace(local)
    source.update({'raw_path': str(local), 'original_rollout_path': str(missing_original), 'snapshot_sha256': digest})
    write_json(store / 'retention-policy.json', {'mode': 'metadata_only'})
    write_json(store / 'manifest.json', {'threads': {'thread-a': {'snapshots': [{
        'raw_path': f'snapshots/thread-a/{digest}.jsonl', 'source_path': str(missing_original),
        'sha256': digest, 'bytes': len(snapshot_bytes),
        'local_copy_deleted_at': '2026-09-30T00:00:00Z', 'byte_preserved': False}]}}})
    local.unlink()
    assert evolution.verify_event(a) == a['text']
    archived.write_bytes((json.dumps({'type': 'session_meta', 'payload': {'id': 'other-thread'}}) + '\n').encode() + record)
    with pytest.raises(ValueError, match='unique archived-session match'):
        evolution.verify_event(a)


def test_purged_snapshot_requires_canonical_work_path_and_hash_filename(tmp_path):
    a = event(tmp_path)
    source = a['sources'][0]
    source.update({'original_rollout_path': str(tmp_path / '.codex/sessions/thread-a.jsonl'),
                   'snapshot_sha256': 'a' * 64})
    source['raw_path'] = str(tmp_path / 'fake/1/workspace/conversations/snapshots/thread-a' / (('a' * 64) + '.jsonl'))
    with pytest.raises(ValueError, match='outside a Work conversation store'):
        evolution.verify_event(a)


def test_snapshot_prefix_hash_is_reused_only_for_unchanged_file_identity(tmp_path):
    path = tmp_path / 'rollout.jsonl'
    path.write_bytes(b'first-prefix-and-tail')
    evolution._PREFIX_SHA_CACHE.clear()
    first = evolution._sha256_prefix(path, 13)
    size = len(evolution._PREFIX_SHA_CACHE)
    assert evolution._sha256_prefix(path, 13) == first
    assert len(evolution._PREFIX_SHA_CACHE) == size
    path.write_bytes(b'second-prefix-and-tail')
    assert evolution._sha256_prefix(path, 13) != first
    assert len(evolution._PREFIX_SHA_CACHE) == size + 1
    evolution._PREFIX_SHA_CACHE.clear()


def test_receipt_tampering_does_not_change_active_guidance(tmp_path):
    a = event(tmp_path)
    run = evolution.directory(tmp_path) / 'runs/one'
    evolution.accept_response(tmp_path, run, [a], response([a]), {})
    receipt = read_json(run / 'accepted.json')
    receipt['rules'][0]['action'] = 'different behavior'
    write_json(run / 'accepted.json', receipt)
    assert evolution.load_guidance(tmp_path)['rules'] == []


def test_uncertain_feedback_is_revisited_only_when_thread_context_changes(tmp_path):
    a = event(tmp_path)
    response_a = response([a], rule=False)
    response_a['dispositions'][0]['classification'] = 'needs_context'
    state = {}
    evolution.accept_response(tmp_path, evolution.directory(tmp_path) / 'runs/one', [a], response_a, state)
    assert evolution.select_batch({a['event_id']: a}, state['reviewed'], deferred=state['deferred']) == []
    b = event(tmp_path, 'b', line=2, text='我是说所有项目，不只是这个客户。')
    events = {e['event_id']: e for e in [a, b]}
    assert {e['event_id'] for e in evolution.select_batch(events, state['reviewed'], deferred=state['deferred'])} == set(events)


def test_rule_replacement_requires_explicit_supersession_and_retains_history(tmp_path):
    a = event(tmp_path)
    state = {}
    first = evolution.directory(tmp_path) / 'runs/one'
    evolution.accept_response(tmp_path, first, [a], response([a]), state)
    b = event(tmp_path, 'b', text='以后明确要求的章节页码可以保留，取消此前全局不编号的理解。')
    changed = response([b])
    with pytest.raises(ValueError, match='explicit supersession'):
        evolution.accept_response(tmp_path, evolution.directory(tmp_path) / 'runs/two', [b], changed, state)
    changed['rules'][0]['supersedes'] = ['meaningful-navigation']
    evolution.accept_response(tmp_path, evolution.directory(tmp_path) / 'runs/two', [b], changed, state)
    assert (first / 'accepted.json').is_file()
    assert state['rules']['meaningful-navigation']['run_id'] == 'two'


def test_no_silent_truncation_of_large_user_message(tmp_path):
    a = event(tmp_path, text='重要上下文' * 100)
    with pytest.raises(ValueError, match='split review'):
        evolution.select_batch({a['event_id']: a}, {}, char_limit=50)


def test_archive_storage_does_not_mark_work_accepted(tmp_path):
    work = tmp_path / 'work/1'
    write_json(work / 'work.json', {'status': 'review'})
    write_json(work / 'workspace/conversations/manifest.json', {'threads': {'a': {
        'source_state': 'archived_snapshot', 'conversation_role': 'user_conversation'}}})
    assert evolution.eligible_works(tmp_path) == [work]
    assert read_json(work / 'work.json')['status'] == 'review'


def test_worker_skips_unchanged_and_isolates_missing_archives(tmp_path, monkeypatch):
    from aippt_studio import feedback_audit
    a = event(tmp_path)
    root = evolution.directory(tmp_path)
    write_json(root / 'config.json', {'enabled': True})
    def audit(works, output):
        write_json(output / 'feedback-audit.json', {'events': {a['event_id']: a}})
        return {'warnings': [{'work_id': '8', 'kind': 'no_archive_manifest'}]}
    monkeypatch.setattr(feedback_audit, 'audit_archived_feedback', audit)
    monkeypatch.setattr(feedback_audit, 'record_feedback_review', lambda *a, **k: None)
    calls = []
    def reviewer(*args):
        calls.append(1)
        return response([a])
    assert evolution.run_evolution(tmp_path, reviewer=reviewer)['status'] == 'learned'
    assert evolution.run_evolution(tmp_path, reviewer=reviewer)['status'] == 'unchanged'
    assert len(calls) == 1


def test_unchanged_model_failure_is_not_retried_every_worker_tick(tmp_path, monkeypatch):
    from aippt_studio import feedback_audit
    a = event(tmp_path)
    root = evolution.directory(tmp_path)
    write_json(root / 'config.json', {'enabled': True})
    def audit(works, output):
        write_json(output / 'feedback-audit.json', {'events': {a['event_id']: a}})
        return {'warnings': []}
    monkeypatch.setattr(feedback_audit, 'audit_archived_feedback', audit)
    calls = []
    def fail(*args):
        calls.append(1)
        raise RuntimeError('offline')
    assert evolution.run_evolution(tmp_path, reviewer=fail)['status'] == 'needs_attention'
    assert evolution.run_evolution(tmp_path, reviewer=fail)['status'] == 'needs_attention'
    assert len(calls) == 1


def test_corrupt_learning_state_does_not_break_base_authoring(tmp_path):
    root = evolution.directory(tmp_path)
    root.mkdir(parents=True)
    (root / 'state.json').write_text('{broken')
    assert evolution.load_guidance(tmp_path)['status'] == 'partial'
    assert any(r['id'] == 'audience_message' for r in authoring_defaults(tmp_path)['rules'])


def test_failed_batch_does_not_starve_later_independent_archive(tmp_path, monkeypatch):
    from aippt_studio import feedback_audit
    a = event(tmp_path)
    events = {a['event_id']: a}
    root = evolution.directory(tmp_path)
    write_json(root / 'config.json', {'enabled': True, 'batch_events': 1})
    def audit(works, output):
        write_json(output / 'feedback-audit.json', {'events': events})
        return {'warnings': []}
    monkeypatch.setattr(feedback_audit, 'audit_archived_feedback', audit)
    monkeypatch.setattr(feedback_audit, 'record_feedback_review', lambda *a, **k: None)
    def fail(*args):
        raise RuntimeError('offline')
    assert evolution.run_evolution(tmp_path, reviewer=fail)['status'] == 'needs_attention'
    b = event(tmp_path, 'event-z', thread='thread-z', work='2')
    events[b['event_id']] = b
    observed = []
    def succeed(repo, run, batch, *args):
        observed.extend(e['event_id'] for e in batch)
        return response(batch, rule=False)
    assert evolution.run_evolution(tmp_path, reviewer=succeed)['status'] == 'reviewed'
    assert observed == ['event-z']
    assert a['event_id'] not in read_json(root / 'state.json')['reviewed']


def test_projection_failure_recovers_without_repeating_model(tmp_path, monkeypatch):
    from aippt_studio import feedback_audit
    a = event(tmp_path)
    root = evolution.directory(tmp_path)
    write_json(root / 'config.json', {'enabled': True})
    def audit(works, output):
        write_json(output / 'feedback-audit.json', {'events': {a['event_id']: a}})
        return {'warnings': []}
    monkeypatch.setattr(feedback_audit, 'audit_archived_feedback', audit)
    calls = []
    attempts = []
    def project(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError('temporary projection failure')
    monkeypatch.setattr(feedback_audit, 'record_feedback_review', project)
    def reviewer(*args):
        calls.append(1)
        return response([a])
    first = evolution.run_evolution(tmp_path, reviewer=reviewer)
    assert first['status'] == 'learned' and first['projection_errors']
    assert evolution.run_evolution(tmp_path, reviewer=reviewer)['status'] == 'unchanged'
    assert len(calls) == 1 and len(attempts) == 2
    assert read_json(root / 'state.json')['projection_pending'] == []


def test_system_proxy_is_forwarded_without_overriding_explicit_process_proxy(monkeypatch):
    import urllib.request
    for key in ('HTTP_PROXY', 'http_proxy', 'HTTPS_PROXY', 'https_proxy'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'http': 'http://127.0.0.1:7897', 'https': 'http://127.0.0.1:7897'})
    env = evolution.codex_environment()
    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:7897'
    monkeypatch.setenv('https_proxy', 'http://explicit:1234')
    env = evolution.codex_environment()
    assert env['https_proxy'] == 'http://explicit:1234' and 'HTTPS_PROXY' not in env


def test_semantic_worker_preserves_user_model_choice_without_loading_tools(tmp_path, monkeypatch):
    (tmp_path / 'config.toml').write_text('model = "configured-model"\nmodel_reasoning_effort = "high"\n')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path))
    args = evolution.configured_model_arguments({})
    assert args[:2] == ['--model', 'configured-model']
    assert 'model_reasoning_effort="high"' in args


def test_unsupported_global_rule_is_held_without_losing_valid_independent_rule(tmp_path):
    a = event(tmp_path)
    b = event(tmp_path, 'event-b', text='这个客户的主题都用绿色，暂时保留右侧编号。')
    raw = response([a, b])
    raw['dispositions'][1]['classification'] = 'project_instruction'
    invalid = copy.deepcopy(raw['rules'][0])
    invalid.update(id='all-projects-green', action='Make every presentation green.', evidence=[{'event_id': b['event_id'], 'quote': b['text']}])
    raw['rules'].append(invalid)
    clean, held = evolution.partition_response(raw, [a, b], {})
    assert [r['id'] for r in clean['rules']] == ['meaningful-navigation']
    assert held[0]['proposal'] == invalid and held[0]['status'] == 'held_not_activated'
    assert len(clean['dispositions']) == 2
    assert len(raw['rules']) == 2


def test_uncertain_project_bug_can_be_retained_for_reproduction_not_global_learning(tmp_path):
    a = event(tmp_path)
    raw = response([a], rule=False)
    raw['dispositions'][0]['classification'] = 'needs_context'
    raw['repair_candidates'] = [{'title': 'Verify a reported failure', 'observed_problem': 'The conversation alone does not establish the cause.',
                                  'next_verification': 'Inspect the actual affected file and reproduce the failure.',
                                  'evidence': [{'event_id': a['event_id'], 'quote': a['text']}]}]
    assert evolution.validate_response(raw, [a], {}) == raw


def test_short_complete_chinese_direction_is_valid_evidence(tmp_path):
    a = event(tmp_path, text='封面不用动')
    result = response([a], rule=False)
    result['dispositions'][0]['classification'] = 'project_instruction'
    result['repair_candidates'] = [{'title': 'Preserve the accepted cover', 'observed_problem': 'Verify the reported scope in the actual file.',
                                    'next_verification': 'Compare the cover before and after the named changes.',
                                    'evidence': [{'event_id': a['event_id'], 'quote': '封面不用动'}]}]
    assert evolution.validate_response(result, [a], {}) == result
