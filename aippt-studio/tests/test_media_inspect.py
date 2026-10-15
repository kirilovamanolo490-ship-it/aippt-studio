import json
import shutil
import wave
from pathlib import Path
import pytest
from aippt_studio.config import PROJECT_ROOT
from aippt_studio.media_inspect import inspect_media


def audio(path):
    with wave.open(str(path),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*16000)
    return path


def require_runtime():
    if not shutil.which('node') or not (PROJECT_ROOT/'.tools/media-inspect-runtime/node_modules/mediabunny/package.json').exists():
        pytest.skip('requires the optional pinned media inspection runtime')


def test_real_wave_tracks_do_not_claim_silence_is_audible(tmp_path):
    require_runtime();src=audio(tmp_path/'silence.wav');before=src.read_bytes();out=tmp_path/'call';out.mkdir()
    result=inspect_media({'source':str(src),'require_audio':True,'expected_end_seconds':1,'tolerance_seconds':0.001},out)
    assert result['tracks'][0]['sample_rate']==16000 and result['tracks'][0]['channels']==1
    assert not result['decoded'] and not result['audible_content_verified'] and not result['semantic_sync_verified']
    assert src.read_bytes()==before


def test_real_audio_only_media_rejects_required_video(tmp_path):
    require_runtime();src=audio(tmp_path/'audio.wav');out=tmp_path/'call';out.mkdir()
    with pytest.raises(ValueError,match='video track is missing'):
        inspect_media({'source':str(src),'require_video':True},out)
    assert not json.loads((out/'media.json').read_text()).get('constraints_passed',False)


@pytest.mark.parametrize('changes',[{'require_audio':'true'},{'expected_end_seconds':float('nan')},{'tolerance_seconds':-1},{'unknown':1}])
def test_invalid_media_contract_rejected_before_execution(tmp_path,changes):
    src=audio(tmp_path/'audio.wav');out=tmp_path/'call';out.mkdir()
    with pytest.raises(ValueError):inspect_media({'source':str(src)}|changes,out)
    assert not list(out.iterdir())


def test_real_media_duration_expectation_failure(tmp_path):
    require_runtime();src=audio(tmp_path/'audio.wav');out=tmp_path/'call';out.mkdir()
    with pytest.raises(ValueError,match='end timestamp'):
        inspect_media({'source':str(src),'expected_end_seconds':2,'tolerance_seconds':0.01},out)
