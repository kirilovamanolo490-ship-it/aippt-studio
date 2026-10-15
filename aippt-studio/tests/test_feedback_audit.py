from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from aippt_studio.conversations import archive_conversation, refresh_work_conversations
from aippt_studio.feedback_audit import (
    audit_archived_feedback, audit_work_feedback, candidate_signals,
    record_feedback_review, user_authored_text,
)

THREAD = '11111111-1111-4111-8111-111111111111'


def message(role, text, *, timestamp='2026-09-15T00:00:00Z'):
    return {'type': 'response_item', 'timestamp': timestamp, 'payload': {'type': 'message', 'role': role,
            'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]}}


def setup_archive(tmp_path, records, *, work_id='1', source=None):
    work = tmp_path / 'work' / work_id
    (work / 'workspace').mkdir(parents=True)
    (work / 'work.json').write_text('{"status":"in_progress"}')
    if source is None:
        source = tmp_path / 'original.jsonl'
        rows = [{'type':'session_meta','payload':{'id':THREAD, 'cwd':str(tmp_path)}}] + records
        source.write_bytes(b'\xef\xbb\xbf' + b'\r\n'.join(json.dumps(r, ensure_ascii=False).encode() for r in rows) + b'\r\n')
    archive_conversation(work, rollout_path=source, codex_home=tmp_path/'codex', binding_reason='explicit test binding')
    return work, source


def state(path):
    return json.loads((path / 'feedback-audit.json').read_text())


def test_only_original_user_messages_are_feedback_and_hashes_resolve_exact_bytes(tmp_path):
    work, original = setup_archive(tmp_path, [message('user', '每一页右下角都不要数字编号'),
        message('assistant', '不要编号，不要小字'), {'type':'response_item','payload':{'type':'function_call_output','output':'用户说不要编号'}},
        message('user', '# AGENTS.md instructions for /tmp\n不要编号'), message('user','<skill>不要编号</skill>'),
        message('user','谢谢')])
    before = original.read_bytes()
    report = audit_work_feedback(work)
    directory = work/'workspace/feedback-audit'
    events = list(state(directory)['events'].values())
    assert report['coverage']['user_events'] == 2
    assert report['coverage']['candidate_events'] == 1
    assert report['coverage']['reviewed_events'] == 0
    assert not report['automatic_semantic_approval']
    event = next(e for e in events if e['signals'])
    assert event['semantic_status'] == 'unreviewed'
    source = event['sources'][0]
    snapshot = Path(source['raw_path']).read_bytes()
    assert source['line'] == 2
    assert hashlib.sha256(snapshot).hexdigest() == source['snapshot_sha256']
    assert hashlib.sha256(snapshot[source['byte_offset']:source['byte_offset']+source['byte_length']]).hexdigest() == source['record_sha256']
    assert original.read_bytes() == before


def test_transport_reply_does_not_treat_assistant_question_as_user_feedback():
    reply = '<send_user_message_question_reply>' + json.dumps([{'question':'不要编号还是不要小字？', 'answer':'按原稿保留。'}],ensure_ascii=False) + '</send_user_message_question_reply>'
    text, excluded = user_authored_text(reply)
    assert text == '按原稿保留。'
    assert 'assistant_question_in_structured_reply' in excluded
    assert 'decorative_numbering' not in {s['category'] for s in candidate_signals(text)}


def test_attachment_paths_and_image_tags_are_not_feedback_but_actual_request_survives():
    text, excluded = user_authored_text('# Files mentioned by the user:\n## 01 02.pptx: /tmp/不要编号/01 02.pptx\n## My request:\n请保留真实业务步骤。\n<image name="01 02" path="/tmp/不要编号.png"></image>')
    assert text == '请保留真实业务步骤。'
    assert 'attachment_path_preamble' in excluded and 'attachment_image_markup' in excluded
    assert not any(s['category'] == 'decorative_numbering' for s in candidate_signals(text))


def test_plugin_wrapper_followed_by_agents_is_injected_context_not_user_feedback():
    text, excluded = user_authored_text('<recommended_plugins>Some plugin</recommended_plugins>\n# AGENTS.md instructions for /tmp\n不要编号')
    assert text == '' and 'injected_workspace_instructions' in excluded


def test_goal_continuation_and_budget_are_not_a_repeated_user_preference():
    text, excluded = user_authored_text('<codex_internal_context source="goal">Continue working. <objective>不要编号</objective> Budget: Tokens used 60000</codex_internal_context>')
    assert text == '' and 'injected_codex_internal_context' in excluded


def test_ambient_browser_state_is_not_request_but_enclosed_actual_request_survives():
    text, excluded = user_authored_text('<in-app-browser-context source="ambient-ui-state">Current URL: /不要编号</in-app-browser-context>\n\n## My request:\n保留当前配色。')
    assert text == '保留当前配色。'
    assert 'injected_in-app-browser-context' in excluded
    assert 'decorative_numbering' not in {x['category'] for x in candidate_signals(text)}


def test_extractor_upgrade_removes_internal_events_and_invalidates_changed_review_basis(tmp_path):
    goal='<codex_internal_context source="goal"><objective>不要编号</objective>Budget: 600</codex_internal_context>'
    browser='<in-app-browser-context source="ambient-ui-state">Current URL: /old</in-app-browser-context>\n## My request:\n不要编号'
    work,_=setup_archive(tmp_path,[message('user',goal),message('user',browser)])
    output=tmp_path/'audit'
    with patch('aippt_studio.feedback_audit.USER_TEXT_VERSION','old'),patch('aippt_studio.feedback_audit.user_authored_text',side_effect=lambda text:(text,[])):
        audit_work_feedback(work,output_dir=output)
    original=state(output)['events'];browser_id=next(k for k,e in original.items() if 'ambient' in e['text'])
    goal_id=next(k for k,e in original.items() if 'objective' in e['text'])
    record_feedback_review(output,browser_id,classification='confirmed_feedback',reviewer='old-editor',reason='Old contaminated input.',implementation_status='gap')
    report=audit_work_feedback(work,output_dir=output)
    current=state(output)
    assert report['coverage']['user_events']==1 and report['coverage']['reviewed_events']==0
    assert goal_id in current['excluded_prior_events']
    assert list(current['events'])==[browser_id]
    event=current['events'][browser_id]
    assert event['text']=='不要编号' and event['reviews'][0]['reviewer']=='old-editor'
    assert event['semantic_status']=='needs_context' and not event['review_valid_for_current_text']
    report=audit_work_feedback(work,output_dir=output)
    assert report['coverage']['raw_bytes_read_this_run']==0


def test_changed_archive_permissions_reverify_bytes_once_and_cache(tmp_path):
    work,_=setup_archive(tmp_path,[message('user','不要编号')]);output=tmp_path/'audit'
    audit_work_feedback(work,output_dir=output)
    snapshot=next((work/'workspace/conversations').glob('snapshots/*/*.jsonl'))
    snapshot.chmod(0o640)
    report=audit_work_feedback(work,output_dir=output)
    assert report['warnings']==[]
    assert report['coverage']['raw_bytes_read_this_run'] >= snapshot.stat().st_size
    report=audit_work_feedback(work,output_dir=output)
    assert report['coverage']['raw_bytes_read_this_run']==0


def test_shared_thread_dedup_and_unchanged_snapshot_fast_path(tmp_path):
    work, source = setup_archive(tmp_path,[message('user','不要编号')])
    second, _ = setup_archive(tmp_path, [], work_id='2', source=source)
    output=tmp_path/'audit'
    report=audit_archived_feedback([work,second],output)
    assert report['coverage']['primary_threads']==1 and report['coverage']['user_events']==1
    event=next(iter(state(output)['events'].values()))
    assert event['work_ids']==['1','2']
    with patch('aippt_studio.feedback_audit._payload_text',side_effect=AssertionError('must not reread raw')):
        report=audit_archived_feedback([work,second],output)
    assert report['coverage']['raw_bytes_read_this_run']==0 and report['coverage']['snapshot_cache_hits']==1


def test_incremental_append_keeps_repeated_actual_requests_and_existing_review(tmp_path):
    work,source=setup_archive(tmp_path,[message('user','不要编号')])
    output=tmp_path/'audit';audit_work_feedback(work,output_dir=output)
    event_id=next(iter(state(output)['events']))
    record_feedback_review(output,event_id,classification='confirmed_feedback',reviewer='test-editor',reason='Decorative corner numbering, not process steps.',implementation_status='gap')
    with source.open('ab') as stream:
        stream.write(json.dumps(message('user','不要编号',timestamp='2026-09-15T01:00:00Z'),ensure_ascii=False).encode()+b'\n')
    refresh_work_conversations(work,codex_home=tmp_path/'codex')
    report=audit_work_feedback(work,output_dir=output)
    assert report['coverage']['user_events']==2 and report['coverage']['reviewed_events']==1
    assert state(output)['events'][event_id]['reviews'][0]['implementation_status']=='gap'
    assert state(output)['scans'][THREAD]['incremental_append']


def test_event_mirror_is_deduplicated_even_when_response_arrives_in_later_snapshot(tmp_path):
    mirror={'type':'event_msg','timestamp':'2026-09-15T00:00:00Z','payload':{'type':'user_message','message':'不要编号'}}
    work,source=setup_archive(tmp_path,[mirror]);output=tmp_path/'audit'
    audit_work_feedback(work,output_dir=output)
    event_id=next(iter(state(output)['events']))
    with source.open('ab') as stream:
        stream.write(json.dumps(message('user','不要编号'),ensure_ascii=False).encode()+b'\n')
    refresh_work_conversations(work,codex_home=tmp_path/'codex')
    report=audit_work_feedback(work,output_dir=output)
    assert report['coverage']['user_events']==1
    assert next(iter(state(output)['events']))==event_id


def test_review_coverage_includes_messages_without_keyword_hits_and_requires_evidence(tmp_path):
    work,_=setup_archive(tmp_path,[message('user','谢谢')]);output=tmp_path/'audit'
    report=audit_work_feedback(work,output_dir=output)
    event_id=next(iter(state(output)['events']))
    assert report['coverage']['candidate_events']==0 and report['coverage']['unreviewed_events']==1
    with pytest.raises(ValueError,match='evidence'):
        record_feedback_review(output,event_id,classification='confirmed_feedback',reviewer='editor',reason='test',implementation_status='implemented')
    report=record_feedback_review(output,event_id,classification='not_feedback',reviewer='editor',reason='Acknowledgment, no corrective direction.',implementation_status='not_applicable')
    assert report['coverage']['processed_event_ids']==[event_id]
    assert report['coverage']['unreviewed_events']==0
    record_feedback_review(output,event_id,classification='not_feedback',reviewer='editor',reason='Acknowledgment, no corrective direction.',implementation_status='not_applicable')
    assert len(state(output)['events'][event_id]['reviews']) == 1


def test_missing_index_and_tampered_line_index_are_visible_coverage_failures(tmp_path):
    work,_=setup_archive(tmp_path,[message('user','不要编号')]);store=work/'workspace/conversations'
    index=next(store.glob('derived/*/*/record-index.json'));data=json.loads(index.read_text())
    data['records'][1]['line']=99;index.write_text(json.dumps(data))
    report=audit_work_feedback(work)
    assert report['status']=='coverage_incomplete' and report['coverage']['user_events']==0
    assert 'line/byte' in report['warnings'][0]['detail']
    index.unlink()
    report=audit_work_feedback(work)
    assert report['status']=='coverage_incomplete'


def test_metadata_only_retention_excludes_purged_archive_without_reading_it(tmp_path, monkeypatch):
    work, _ = setup_archive(tmp_path, [message('user', '不要编号')])
    store = work / 'workspace/conversations'
    raw = next((store / 'snapshots').glob('*/*.jsonl'))
    derived = next((store / 'derived').glob('*/*'))
    raw.unlink()
    import shutil
    shutil.rmtree(derived)
    (store / 'retention-policy.json').write_text(json.dumps({'schema_version': 1, 'mode': 'metadata_only'}))

    def forbidden(*_args, **_kwargs):
        raise AssertionError('purged raw/index must not be opened or fingerprinted')

    monkeypatch.setattr('aippt_studio.feedback_audit._fingerprint', forbidden)
    first = audit_work_feedback(work)
    second = audit_work_feedback(work)
    assert first['status'] == second['status'] == 'retention_limited'
    assert first['retention_limited'] is True
    assert first['coverage']['metadata_only_excluded_threads'] == 1
    assert first['coverage']['user_events'] == 0
    assert first['warnings'] == second['warnings'] == []
    assert first['excluded_threads'] == second['excluded_threads']
    assert first['excluded_threads'][0]['reason'] == 'metadata_only_retention_policy'


def test_invalid_retention_policy_does_not_scan_raw_snapshot(tmp_path, monkeypatch):
    work, _ = setup_archive(tmp_path, [message('user', '不要编号')])
    store = work / 'workspace/conversations'
    (store / 'retention-policy.json').write_text(json.dumps({'mode': 'misspelled'}))
    monkeypatch.setattr('aippt_studio.feedback_audit._fingerprint', lambda *_a: (_ for _ in ()).throw(
        AssertionError('invalid policy must not scan conversation')))
    report = audit_work_feedback(work)
    assert report['status'] == 'coverage_incomplete'
    assert report['warnings'][0]['kind'] == 'retention_policy_invalid'
    assert report['coverage']['user_events'] == 0


def test_unbound_and_subagent_manifests_never_count_as_user_feedback(tmp_path):
    work,_=setup_archive(tmp_path,[message('user','不要编号')]);store=work/'workspace/conversations'
    manifest=json.loads((store/'manifest.json').read_text());manifest['threads'][THREAD]['conversation_role']='subagent_trace'
    (store/'manifest.json').write_text(json.dumps(manifest))
    report=audit_work_feedback(work)
    assert report['coverage']['user_events']==0 and report['coverage']['excluded_subagent_associations']==1
    manifest['threads'][THREAD]['conversation_role']='user_conversation';(store/'manifest.json').write_text(json.dumps(manifest))
    (store/'bindings.json').write_text('{"bindings":[]}')
    report=audit_work_feedback(work)
    assert report['coverage']['user_events']==0 and report['warnings'][0]['kind']=='not_explicitly_bound'


def test_business_number_sequences_are_candidates_not_an_automatic_prohibition():
    text='第01、02步骤必须按工艺顺序执行。'
    signals=candidate_signals(text)
    assert any(s['category']=='decorative_numbering' for s in signals)
    assert all(s['status']=='candidate_requires_semantic_review' for s in signals)
    assert text=='第01、02步骤必须按工艺顺序执行。'


def test_changed_snapshot_bytes_never_receive_the_old_source_hash(tmp_path):
    work,_=setup_archive(tmp_path,[message('user','不要编号')])
    snapshot=next((work/'workspace/conversations').glob('snapshots/*/*.jsonl'))
    with snapshot.open('ab') as stream:
        stream.write(b'{}\n')
    report=audit_work_feedback(work)
    assert report['status']=='coverage_incomplete' and report['coverage']['user_events']==0
    assert 'archival hash' in report['warnings'][0]['detail']


def test_one_broken_archive_does_not_hide_other_works(tmp_path):
    work,_=setup_archive(tmp_path,[message('user','不要编号')]);bad=tmp_path/'work/2'
    (bad/'workspace/conversations').mkdir(parents=True)
    (bad/'workspace/conversations/manifest.json').write_text('{broken')
    report=audit_archived_feedback([work,bad],tmp_path/'audit')
    assert report['status']=='coverage_incomplete' and report['coverage']['user_events']==1
    assert report['warnings'][0]['kind']=='unreadable_archive_metadata'
