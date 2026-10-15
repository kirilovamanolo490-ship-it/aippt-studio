from __future__ import annotations
import json
import os
import struct
import subprocess
import zlib
from pathlib import Path
import pytest
from aippt_studio import runtime, renderer, mac_powerpoint, native_powerpoint
from aippt_studio.adapters.visual import VisualAdapter
from aippt_studio.adapter_contracts import CapabilityMaturity


@pytest.fixture(autouse=True)
def isolate_office_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(mac_powerpoint, "_office_temporary_root", lambda: tmp_path / "office-cache")
    # These unit tests replace the AppleEvent subprocess; retain a stable
    # canonical bridge on non-macOS CI without allowing PATH resolution.
    monkeypatch.setattr(mac_powerpoint, "system_osascript", lambda: Path("/usr/bin/osascript"))
    def fake_officecli_preview(source, slide_number, output_png, *, receipt_path=None, **kwargs):
        _write_exported_png(output_png)
        count = getattr(mac_powerpoint, '_test_preview_count', 1)
        return {'slide_count': count, 'slide_number': slide_number, 'renderer': 'officecli-html',
                'approximate_renderer': True, 'output_sha256': f'fixture-{slide_number}',
                'width': 2, 'height': 2, 'output_bytes': output_png.stat().st_size}
    monkeypatch.setattr('aippt_studio.local_preview_officecli.render_officecli_slide_preview', fake_officecli_preview)


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    body = kind + data
    return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xffffffff)


def _write_exported_png(path: Path, *, width: int = 2, height: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = b''.join(b'\x00' + b'\x00\x00\x00\xff' * width for _ in range(height))
    path.write_bytes(
        b'\x89PNG\r\n\x1a\n'
        + _png_chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b'IDAT', zlib.compress(raw))
        + _png_chunk(b'IEND', b'')
    )


@pytest.mark.skipif(os.name == 'nt', reason='Unix executable semantics')
def test_copied_pe_and_non_executable_are_not_runtimes(tmp_path, monkeypatch):
    exe = tmp_path / 'officecli.exe'
    exe.write_bytes(b'MZ' + b'\0'*32); exe.chmod(0o755)
    monkeypatch.setenv('AIPPT_OFFICECLI', str(exe))
    assert runtime.officecli_executable() is None
    exe.write_text('#!/bin/sh\necho fixture'); exe.chmod(0o644)
    assert runtime.officecli_executable() is None
    exe.chmod(0o755)
    assert runtime.officecli_executable() == exe


def test_explicit_bad_browser_does_not_silently_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv('AIPPT_CHROMIUM', str(tmp_path/'missing'))
    assert runtime.chromium_executable() is None


def test_system_osascript_uses_only_the_canonical_path_not_a_path_shadow(tmp_path, monkeypatch):
    system_bridge = tmp_path / "verified-system-osascript"
    system_bridge.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    system_bridge.chmod(0o755)
    shadow = tmp_path / "shadow-bin" / "osascript"
    shadow.parent.mkdir()
    shadow.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    shadow.chmod(0o755)
    monkeypatch.setattr(runtime, "SYSTEM_OSASCRIPT", system_bridge)
    monkeypatch.setenv("PATH", str(shadow.parent))

    assert runtime.system_osascript() == system_bridge


def test_html_renderer_rejects_a_historical_node_before_attempting_render(tmp_path, monkeypatch):
    from aippt_studio import renderer

    old_node = tmp_path / 'work/8/workspace/node'
    old_node.parent.mkdir(parents=True)
    old_node.write_text('#!/bin/sh\necho should-not-run\n', encoding='utf-8')
    old_node.chmod(0o755)
    monkeypatch.delenv('AIPPT_FAKE_RENDER', raising=False)
    monkeypatch.setenv('AIPPT_NODE', str(old_node))
    monkeypatch.setattr(renderer, '_run_command', lambda *args, **kwargs: pytest.fail('historical Node started'))
    html = tmp_path / 'deck.html'
    html.write_text('<section class="slide" data-slide-id="one"></section>', encoding='utf-8')

    with pytest.raises(renderer.RenderError, match='host-native Node'):
        renderer.render_html(html, tmp_path / 'deck.pptx', tmp_path / 'previews')


def test_empty_upstream_is_not_automatic(tmp_path, monkeypatch):
    monkeypatch.delenv('AIPPT_VISUAL_ADAPTER', raising=False)
    project=tmp_path/'project'; upstream=tmp_path/'github'
    (upstream/'image'/'codex-ppt-skill').mkdir(parents=True)
    (project/'scripts/adapters').mkdir(parents=True)
    (project/'scripts/adapters/visual_adapter.py').write_text('# entry')
    (project/'adapter-acceptance').mkdir()
    (project/'adapter-acceptance/visual.json').write_text(json.dumps({'status':'passed','engine':'codex-ppt-skill','reviewed_commit':'a'}))
    adapter=VisualAdapter(project_root=project, upstream_root=upstream, reviewed_commit='a')
    assert adapter.maturity() == CapabilityMaturity.INSTALLED_ONLY


def test_mac_roundtrip_validates_officecli_page_count_before_replacing_input(tmp_path, monkeypatch):
    source=tmp_path/'source.pptx'; source.write_bytes(b'original')
    previous_slides = tmp_path / 'out' / 'slides'
    previous_slides.mkdir(parents=True)
    (previous_slides / 'prior.png').write_bytes(b'prior-review')
    def run(argv, **kwargs):
        Path(argv[2]).write_bytes(b'office-saved')
        monkeypatch.setattr(mac_powerpoint, '_test_preview_count', 1, raising=False)
        return subprocess.CompletedProcess(argv,0,json.dumps({'status':'passed','slide_count':2}), '')
    monkeypatch.setattr(mac_powerpoint.subprocess,'run',run)
    with pytest.raises(renderer.RenderError,match='page count/order mismatch on slide 1'):
        mac_powerpoint.mac_powerpoint_roundtrip(source,tmp_path/'out')
    assert source.read_bytes()==b'original'
    assert (previous_slides / 'prior.png').read_bytes() == b'prior-review'
    assert not (tmp_path / '.powerpoint-roundtrip-candidate.pptx').exists()


def test_mac_roundtrip_publishes_approximate_officecli_png_previews_without_pdf(tmp_path, monkeypatch):
    source=tmp_path/'中文 source.pptx';source.write_bytes(b'original')
    def run(argv, **kwargs):
        assert argv[2] != str(source)
        Path(argv[2]).write_bytes(b'office-saved')
        assert Path(argv[2]).name == '.powerpoint-roundtrip-candidate.pptx'
        monkeypatch.setattr(mac_powerpoint, '_test_preview_count', 2, raising=False)
        return subprocess.CompletedProcess(argv,0,json.dumps({'status':'passed','slide_count':2}), '')
    monkeypatch.setattr(mac_powerpoint.subprocess,'run',run)
    report=mac_powerpoint.mac_powerpoint_roundtrip(source,tmp_path/'out')
    assert source.read_bytes()==b'office-saved'
    assert (tmp_path/'out/slides/slide-1.png').is_file()
    assert (tmp_path/'out/slides/slide-2.png').is_file()
    assert report['backend']=='mac-applescript'
    assert report['preview_source'] == 'officecli-html-approximate'
    assert report['native_save_reopen'] is True
    assert report['native_visual_verified'] is False
    assert 'one-based' in report['preview_ordering']
    assert [item['slide_number'] for item in report['preview_slides']] == [1, 2]
    assert [item['output_sha256'] for item in report['preview_slides']] == ['fixture-1', 'fixture-2']
    assert 'pdf' not in report
    assert not list((tmp_path / 'out').glob('*.pdf'))


def test_mac_roundtrip_rejects_corrupt_officecli_png_before_replacing_source(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')

    def run(argv, **kwargs):
        Path(argv[2]).write_bytes(b'office-saved')
        return subprocess.CompletedProcess(argv, 0, json.dumps({'status': 'passed', 'slide_count': 1}), '')

    def corrupt_preview(source, slide_number, output_png, *, receipt_path=None, **kwargs):
        output_png.parent.mkdir(parents=True, exist_ok=True)
        output_png.write_bytes(b'not a png')
        from aippt_studio.local_preview_officecli import OfficeCliPreviewError
        raise OfficeCliPreviewError('fixture invalid PNG')
    monkeypatch.setattr('aippt_studio.local_preview_officecli.render_officecli_slide_preview', corrupt_preview)
    monkeypatch.setattr(mac_powerpoint.subprocess, 'run', run)
    with pytest.raises(renderer.RenderError, match='approximate preview failed'):
        mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')
    assert source.read_bytes() == b'original'
    assert not (tmp_path / '.powerpoint-roundtrip-candidate.pptx').exists()


def test_mac_roundtrip_uses_stable_sibling_candidate_and_exclusive_lock(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')
    lock = tmp_path / '.powerpoint-roundtrip.lock'
    lock.mkdir()
    with pytest.raises(renderer.RenderError, match='Another PowerPoint roundtrip'):
        mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')
    assert source.read_bytes() == b'original'
    assert lock.is_dir()


def test_mac_roundtrip_source_hash_change_does_not_get_overwritten(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')

    def run(argv, **kwargs):
        Path(argv[2]).write_bytes(b'office-saved-staging-copy')
        source.write_bytes(b'concurrent-user-update')
        return subprocess.CompletedProcess(argv, 0, json.dumps({'status': 'passed', 'slide_count': 1}), '')

    monkeypatch.setattr(mac_powerpoint.subprocess, 'run', run)
    with pytest.raises(renderer.RenderError, match='source changed during roundtrip'):
        mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')
    assert source.read_bytes() == b'concurrent-user-update'
    assert not (tmp_path / '.powerpoint-roundtrip-candidate.pptx').exists()


def test_mac_roundtrip_rolls_back_previous_slides_if_pptx_publish_fails(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')
    slides = tmp_path / 'out' / 'slides'
    slides.mkdir(parents=True)
    (slides / 'prior-review.txt').write_text('previous deck preview', encoding='utf-8')
    monkeypatch.setattr(mac_powerpoint, '_test_preview_count', 2, raising=False)

    def run(argv, **kwargs):
        Path(argv[2]).write_bytes(b'office-saved-candidate')
        return subprocess.CompletedProcess(argv, 0, json.dumps({'status': 'passed', 'slide_count': 2}), '')

    monkeypatch.setattr(mac_powerpoint.subprocess, 'run', run)
    original_replace = Path.replace

    def fail_candidate_publish(path, target):
        if path.name == '.powerpoint-roundtrip-candidate.pptx' and Path(target) == source:
            raise OSError('injected PPTX publish failure')
        return original_replace(path, target)

    monkeypatch.setattr(Path, 'replace', fail_candidate_publish)
    with pytest.raises(OSError, match='injected PPTX publish failure'):
        mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')

    assert source.read_bytes() == b'original'
    assert (slides / 'prior-review.txt').read_text(encoding='utf-8') == 'previous deck preview'
    assert not (slides / 'slide-1.png').exists()
    assert not list((tmp_path / 'out').glob('.roundtrip-*-slides-backup'))
    assert not (tmp_path / '.powerpoint-roundtrip-candidate.pptx').exists()


def test_mac_roundtrip_executes_the_verified_bridge_not_a_path_shadow(tmp_path, monkeypatch):
    source = tmp_path / "source.pptx"
    source.write_bytes(b"original")
    verified = tmp_path / "verified-system-osascript"
    verified.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    verified.chmod(0o755)
    shadow = tmp_path / "shadow-bin" / "osascript"
    shadow.parent.mkdir()
    shadow.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
    shadow.chmod(0o755)
    monkeypatch.setenv("PATH", str(shadow.parent))
    monkeypatch.setattr(mac_powerpoint, "system_osascript", lambda: verified)

    def run(argv, **kwargs):
        assert argv[0] == str(verified)
        assert argv[0] != str(shadow)
        Path(argv[2]).write_bytes(b"office-saved")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"status": "passed", "slide_count": 1}), "")

    monkeypatch.setattr(mac_powerpoint.subprocess, "run", run)
    mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / "out")


def test_mac_roundtrip_always_uses_work_owned_cache_for_officecli(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')
    protected = tmp_path / 'protected-container'
    protected.write_bytes(b'not a directory')
    monkeypatch.setattr(mac_powerpoint, '_office_temporary_root', lambda: protected)
    calls = []
    preview_dirs = []
    def capture_preview(source, slide_number, output_png, *, receipt_path=None, **kwargs):
        preview_dirs.append(output_png.parent)
        _write_exported_png(output_png)
        return {'slide_count': 1, 'slide_number': slide_number, 'renderer': 'officecli-html',
                'approximate_renderer': True, 'output_sha256': 'fixture', 'width': 2, 'height': 2,
                'output_bytes': output_png.stat().st_size}
    monkeypatch.setattr('aippt_studio.local_preview_officecli.render_officecli_slide_preview', capture_preview)

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({'status': 'passed', 'slide_count': 1}), '')

    monkeypatch.setattr(mac_powerpoint.subprocess, 'run', run)
    report = mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')

    assert preview_dirs and all(path.is_relative_to(tmp_path / 'out' / '.powerpoint-roundtrip-cache') for path in preview_dirs)
    assert report['temporary_cache_mode'] == 'work_output'


def test_mac_roundtrip_keeps_outer_timeout_longer_than_applescript_timeout(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')
    observed = {}

    def run(argv, **kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, json.dumps({'status': 'passed', 'slide_count': 1}), '')

    monkeypatch.setattr(mac_powerpoint.subprocess, 'run', run)
    mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')
    assert observed['timeout'] > 180


def test_mac_roundtrip_retries_only_transient_powerpoint_busy_state(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')
    calls = []
    pauses = []

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            return subprocess.CompletedProcess(argv, 1, '', 'AIPPT_STAGE=candidate_open: busy (-9074)')
        return subprocess.CompletedProcess(argv, 0, json.dumps({'status': 'passed', 'slide_count': 1}), '')

    monkeypatch.setattr(mac_powerpoint.subprocess, 'run', run)
    monkeypatch.setattr(mac_powerpoint.time, 'sleep', pauses.append)
    report = mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')
    assert len(calls) == 2
    assert pauses == [2]
    assert report['automation_attempts'] == 2


def test_mac_roundtrip_reports_persistent_powerpoint_busy_as_environment_not_deck_damage(tmp_path, monkeypatch):
    source = tmp_path / 'source.pptx'
    source.write_bytes(b'original')
    monkeypatch.setattr(
        mac_powerpoint.subprocess,
        'run',
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, '', 'AIPPT_STAGE=candidate_open: busy (-9074)'),
    )
    monkeypatch.setattr(mac_powerpoint.time, 'sleep', lambda _: None)
    with pytest.raises(renderer.PowerPointRoundtripError) as raised:
        mac_powerpoint.mac_powerpoint_roundtrip(source, tmp_path / 'out')
    assert raised.value.report['classification'] == 'environment'
    assert raised.value.report['retryable'] is True
    assert raised.value.report['automation_attempts'] == 3
    assert 'no conclusion was made about the PPTX' in str(raised.value)


def test_mac_permission_error_is_not_a_corrupt_deck(tmp_path, monkeypatch):
    source=tmp_path/'source.pptx';source.write_bytes(b'original')
    monkeypatch.setattr(mac_powerpoint.subprocess,'run',lambda *a,**k:subprocess.CompletedProcess(a[0],1,'','AIPPT_STAGE=candidate_open: Not authorized (-1743)'))
    with pytest.raises(renderer.PowerPointRoundtripError) as exc:
        mac_powerpoint.mac_powerpoint_roundtrip(source,tmp_path/'out')
    assert exc.value.report['classification']=='environment'
    assert not list((tmp_path / 'out').glob('*.pdf'))
    assert source.read_bytes()==b'original'


@pytest.mark.skipif(os.name=='nt', reason='Windows COM is supported')
def test_native_com_is_explicitly_unsupported_on_mac():
    with pytest.raises(native_powerpoint.NativePowerPointError,match='require Windows'):
        native_powerpoint._powershell_executable()
