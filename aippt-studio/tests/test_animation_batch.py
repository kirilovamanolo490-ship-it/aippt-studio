from pathlib import Path
import pytest
from aippt_studio import animation_edit as ae
from test_animation_edit import deck


def batch(path):
    return {'source':str(path),'source_sha256':ae._sha(path),'edits':[
        {'slide':1,'shape_id':'2','animation_id':'10','delta_seconds':2},
        {'slide':1,'shape_id':'3','animation_id':'20','duration_seconds':1.2}]}


def test_dry_run_resolves_changes_without_patching_or_office(tmp_path,monkeypatch):
    source=deck(tmp_path); args=batch(source); out=tmp_path/'plan';out.mkdir()
    def forbidden(*a):raise AssertionError('dry run must not patch or validate')
    monkeypatch.setattr(ae,'_patch_duration',forbidden)
    monkeypatch.setattr('aippt_studio.renderer.officecli_validate',forbidden)
    result=ae.run_animation_operation('animation-batch',args|{'dry_run':True},out)
    assert result['status']=='planned' and not result['candidate_generated']
    assert [c['after_ms'] for c in result['changes']]==[2800,1200]
    assert not (out/'candidate.pptx').exists()
    assert ae._sha(source)==args['source_sha256']


def test_batch_is_one_validated_candidate_with_exact_byte_preservation(tmp_path,monkeypatch):
    source=deck(tmp_path);args=batch(source);before=ae._package(source)
    preview=tmp_path/'plan';preview.mkdir()
    plan=ae.run_animation_operation('animation-batch',args|{'dry_run':True},preview)
    out=tmp_path/'apply';out.mkdir();calls=[]
    monkeypatch.setattr('aippt_studio.renderer.officecli_validate',lambda p:calls.append(p) or {'status':'passed'})
    result=ae.run_animation_operation('animation-batch',args|{'expected_plan_sha256':plan['plan_sha256']},out)
    expected=dict(before);expected['ppt/slides/slide1.xml']=expected['ppt/slides/slide1.xml'].replace(b'dur="800"',b'dur="2800"').replace(b'dur="500"',b'dur="1200"')
    assert ae._package(out/'candidate.pptx')==expected
    assert ae._package(source)==before and len(calls)==1
    assert result['all_other_xml_bytes_preserved'] and result['plan_sha256']==plan['plan_sha256']
    assert not list((out/'edit-staging').iterdir())


@pytest.mark.parametrize('case',['duplicate','late_bad_target','old_source','old_plan','nonboolean_dry_run','unknown_field'])
def test_entire_batch_rejected_before_any_edit(tmp_path,monkeypatch,case):
    source=deck(tmp_path);args=batch(source);out=tmp_path/'call';out.mkdir()
    if case=='duplicate':args['edits'].append(args['edits'][0])
    elif case=='late_bad_target':args['edits'][1]['animation_id']='999'
    elif case=='old_source':args['source_sha256']='stale'
    elif case=='old_plan':args['expected_plan_sha256']='stale'
    elif case=='nonboolean_dry_run':args['dry_run']='false'
    else:args['edits'][1]['trigger']='onClick'
    def forbidden(*a):raise AssertionError('invalid batch must not begin patching')
    monkeypatch.setattr(ae,'_patch_duration',forbidden)
    with pytest.raises(ValueError):ae.run_animation_operation('animation-batch',args,out)
    assert not (out/'candidate.pptx').exists()


def test_failed_final_validation_never_publishes_partial_batch(tmp_path,monkeypatch):
    source=deck(tmp_path);before=source.read_bytes();out=tmp_path/'call';out.mkdir()
    def failed(*a):raise ValueError('invalid final candidate')
    monkeypatch.setattr('aippt_studio.renderer.officecli_validate',failed)
    with pytest.raises(ValueError,match='final candidate'):
        ae.run_animation_operation('animation-batch',batch(source),out)
    assert not (out/'candidate.pptx').exists() and source.read_bytes()==before


def test_changed_request_rejected_against_reviewed_plan(tmp_path):
    source=deck(tmp_path);args=batch(source);out=tmp_path/'plan';out.mkdir()
    plan=ae.run_animation_operation('animation-batch',args|{'dry_run':True},out)
    args['edits'][0]['delta_seconds']=3
    apply=tmp_path/'apply';apply.mkdir()
    with pytest.raises(ValueError,match='plan changed'):
        ae.run_animation_operation('animation-batch',args|{'expected_plan_sha256':plan['plan_sha256']},apply)
    assert not (apply/'candidate.pptx').exists()
