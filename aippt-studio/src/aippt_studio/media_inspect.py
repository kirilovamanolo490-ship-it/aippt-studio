"""No-model, local-only media checks through the pinned Mediabunny runtime."""
import json
import math
import shutil
from pathlib import Path
from .config import PROJECT_ROOT
from .platform_runtime import probe_node_runtime
from .storage import write_json


def inspect_media(request: dict, output: Path) -> dict:
    from .upstream_tools import _run, _sha
    allowed = {'source', 'require_audio', 'require_video', 'expected_end_seconds', 'tolerance_seconds'}
    if set(request) - allowed:
        raise ValueError('unknown media inspection fields')
    for flag in ('require_audio', 'require_video'):
        if type(request.get(flag, False)) is not bool:
            raise ValueError(f'{flag} must be boolean')
    for key in ('expected_end_seconds', 'tolerance_seconds'):
        if key in request:
            value = request[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} must be a finite nonnegative number')
    source = Path(request['source']).expanduser().resolve()
    if not source.is_file() or source.stat().st_size > 500_000_000:
        raise ValueError('inspect requires an existing local media file up to 500 MB')
    if source.suffix.lower() not in {'.mp4', '.mov', '.m4a', '.mkv', '.webm', '.wav', '.mp3', '.ogg', '.flac', '.aac'}:
        raise ValueError('unsupported local media extension; manifests and URLs are not accepted')
    runtime = PROJECT_ROOT / '.tools/media-inspect-runtime'
    if not (runtime/'node_modules/mediabunny/package.json').is_file():
        raise ValueError('install the isolated runtime with aippt-studio/scripts/setup-media-inspect.sh from the workspace root')
    node_runtime = probe_node_runtime()
    if node_runtime.get('status') != 'passed':
        reason = str(node_runtime.get('reason') or node_runtime.get('probe') or 'no compatible executable was found')
        raise ValueError('A compatible host-native Node.js 22.12+ runtime is unavailable: ' + reason)
    node = str(node_runtime['path'])
    snapshot = output/('input'+source.suffix.lower())
    shutil.copy2(source, snapshot)
    script = PROJECT_ROOT/'node/inspect-media.cjs'
    _run([node, str(script), str(snapshot), str(output/'media.json')], output, 'mediabunny-inspect')
    report = json.loads((output/'media.json').read_text())
    tracks = report['tracks']
    if not tracks or any(t['packet_stats']['packetCount'] <= 0 for t in tracks):
        raise ValueError('media contains no packets or an empty track')
    for flag, kind in [('require_audio', 'audio'), ('require_video', 'video')]:
        if request.get(flag) and not any(t['type'] == kind for t in tracks):
            raise ValueError(f'required {kind} track is missing')
    if 'expected_end_seconds' in request:
        if abs(report['end_timestamp_seconds'] - request['expected_end_seconds']) > request.get('tolerance_seconds', 0.1):
            raise ValueError('media end timestamp differs from the expected value')
    report['constraints_passed'] = True
    write_json(output/'media.json', report)
    return {'status': 'inspected', 'artifact': 'media.json', 'input_sha256': _sha(snapshot),
            'runtime_version': report['runtime_version'], 'runtime_lock_sha256': _sha(runtime/'package-lock.json'),
            'worker_sha256': _sha(script), 'bridge_sha256': _sha(Path(__file__)),
            'tracks': tracks, 'end_timestamp_seconds': report['end_timestamp_seconds'],
            'decoded': False, 'audible_content_verified': False, 'semantic_sync_verified': False}
