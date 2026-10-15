"""Mac PowerPoint save/reopen roundtrip with approximate OfficeCLI PNG previews."""
from __future__ import annotations
import json
import hashlib
import re
import shutil
import subprocess
import time
import uuid
import tempfile
from pathlib import Path
from .config import PROJECT_ROOT
from .runtime import system_osascript


def _office_temporary_root() -> Path:
    return Path.home() / "Library/Containers/com.microsoft.Powerpoint/Data/tmp"


def _powerpoint_temporary_root(output_dir: Path, *, fallback_name: str) -> tuple[Path, str]:
    """Choose a location both this process and PowerPoint can access.

    Modern macOS can protect another app's container from the Studio process
    even when PowerPoint itself owns that container.  The previous assumption
    that the host could always create ``Library/Containers/.../tmp`` made a
    healthy native Mac chain fail before AppleScript even started.  A staging
    directory beside the caller-owned Work output is the safe fallback: the
    candidate file is already opened from that same scope by PowerPoint.
    ``fallback_name`` keeps separate engine caches distinct inside a Work.
    """

    preferred = _office_temporary_root()
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        # ``mkdir(..., exist_ok=True)`` can succeed on an existing protected
        # directory although creating children is denied by TCC.  Probe the
        # exact operation TemporaryDirectory will need before selecting it.
        probe = preferred / f".aippt-write-probe-{uuid.uuid4().hex}"
        probe.mkdir()
        probe.rmdir()
        return preferred, "powerpoint_container"
    except OSError:
        fallback = output_dir / fallback_name
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback, "work_output_fallback"


def _roundtrip_temporary_root(output_dir: Path) -> tuple[Path, str]:
    """Return Work-local scratch space for OfficeCLI preview generation."""

    root = output_dir / '.powerpoint-roundtrip-cache'
    root.mkdir(parents=True, exist_ok=True)
    return root, 'work_output'


def _run_roundtrip_applescript(command: list[str]) -> tuple[subprocess.CompletedProcess[str], int]:
    """Run the native probe, retrying only PowerPoint's documented busy state.

    ``-9074`` is macOS ``kBusyErr``: PowerPoint has received the AppleEvent but
    is temporarily unable to open the staging deck.  It happens on cold launch
    during native open/save/reopen. Treating it as a damaged PPTX misdiagnoses a
    healthy deck; silently retrying arbitrary Office errors would be worse.
    """

    completed: subprocess.CompletedProcess[str] | None = None
    for attempt in range(1, 4):
        try:
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=210, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise RuntimeError(str(error)) from error
        detail = (completed.stderr or completed.stdout or "").strip()
        if completed.returncode == 0 or "-9074" not in detail or attempt == 3:
            return completed, attempt
        # Give only the reported busy application a bounded chance to finish.
        # 2, 4 seconds covers a cold launch without hiding a persistent defect.
        time.sleep(2 * attempt)
    assert completed is not None
    return completed, 3


def mac_powerpoint_roundtrip(pptx: Path, output_dir: Path, reference_pptx: Path | None = None) -> dict[str, object]:
    from .renderer import RenderError

    pptx = pptx.resolve()
    if not pptx.is_file():
        raise RenderError(f"PowerPoint input is missing: {pptx}")
    if reference_pptx is not None and not reference_pptx.is_file():
        raise RenderError(f"PowerPoint reference is missing: {reference_pptx}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    lock = pptx.parent / '.powerpoint-roundtrip.lock'
    try:
        lock.mkdir()
    except FileExistsError as error:
        raise RenderError(f'Another PowerPoint roundtrip is active or left a recovery lock at {lock}') from error
    try:
        return _mac_powerpoint_roundtrip_locked(pptx, output_dir, reference_pptx)
    finally:
        try:
            lock.rmdir()
        except OSError:
            pass


def _mac_powerpoint_roundtrip_locked(pptx: Path, output_dir: Path, reference_pptx: Path | None = None) -> dict[str, object]:
    from .renderer import RenderError, PowerPointRoundtripError
    bridge = system_osascript()
    if bridge is None:
        raise RenderError("Mac PowerPoint automation requires the verified system /usr/bin/osascript bridge")
    pptx = pptx.resolve()
    if not pptx.is_file():
        raise RenderError(f"PowerPoint input is missing: {pptx}")
    if reference_pptx is not None and not reference_pptx.is_file():
        raise RenderError(f"PowerPoint reference is missing: {reference_pptx}")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    def file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()

    source_hash = file_sha256(pptx)
    token = '.roundtrip-' + uuid.uuid4().hex
    # Stable sibling names let macOS retain file-specific access across runs.
    # The exclusive output lock above prevents two roundtrips from sharing them.
    candidate = pptx.parent / '.powerpoint-roundtrip-candidate.pptx'
    reference = pptx.parent / '.powerpoint-roundtrip-reference.pptx' if reference_pptx else None
    if candidate.exists() or (reference is not None and reference.exists()):
        raise RenderError('A stable PowerPoint staging file already exists; inspect it before retrying')
    previews = output_dir / (token + '-slides')
    try:
        shutil.copy2(pptx, candidate)
        if reference is not None:
            shutil.copy2(reference_pptx, reference)
        # OfficeCLI renders these pages, so its temporary files stay in the
        # caller-owned Work output and never require access to PowerPoint's container.
        cache_root, cache_mode = _roundtrip_temporary_root(output_dir)
        with tempfile.TemporaryDirectory(prefix="aippt-roundtrip-", dir=cache_root) as temporary:
            command = [str(bridge), str(PROJECT_ROOT / 'scripts/powerpoint_roundtrip.applescript'), str(candidate), str(reference) if reference else '', candidate.stem, reference.stem if reference else '']
            try:
                completed, attempts = _run_roundtrip_applescript(command)
            except RuntimeError as error:
                raise RenderError('Mac PowerPoint automation did not complete. Check PowerPoint and macOS Automation permission dialogs. ' + str(error)) from error
            if completed.returncode:
                detail = completed.stderr.strip() or completed.stdout.strip()
                match = re.search(r'AIPPT_STAGE=(\w+):', detail)
                phase = match.group(1) if match else 'initialize'
                busy = '-9074' in detail
                classification = 'environment' if busy or phase in {'initialize', 'reference_open'} or '-1743' in detail or '-1712' in detail else 'candidate'
                message = detail
                if busy:
                    message = ('PowerPoint remained busy after bounded native retries; no conclusion was made about the PPTX. '
                               'Close any PowerPoint dialogs or restart PowerPoint, then rerun the native doctor. ' + detail)
                raise PowerPointRoundtripError({'status':'failed', 'phase':phase, 'classification':classification, 'backend':'mac-applescript', 'message':message, 'automation_attempts':attempts, 'retryable':busy})
            try:
                report = json.loads(completed.stdout.strip())
            except (ValueError, TypeError) as error:
                raise RenderError(f'Mac PowerPoint returned invalid JSON: {completed.stdout}') from error
            count = report.get('slide_count')
            if report.get('status') != 'passed' or not isinstance(count, int) or count < 1:
                raise RenderError('Mac PowerPoint did not complete the save/reopen roundtrip')
            from .local_preview_officecli import OfficeCliPreviewError, render_officecli_slide_preview
            previews.mkdir()
            preview_receipts = []
            for index in range(1, count + 1):
                image_path = Path(temporary) / f'slide-{index}.png'
                receipt_path = Path(temporary) / f'slide-{index}.preview.json'
                try:
                    preview_receipt = render_officecli_slide_preview(candidate, index, image_path, receipt_path=receipt_path)
                except (OfficeCliPreviewError, OSError, ValueError) as error:
                    raise RenderError(f'OfficeCLI approximate preview failed for slide {index}: {error}') from error
                if preview_receipt.get('slide_count') != count or preview_receipt.get('slide_number') != index:
                    raise RenderError(f'OfficeCLI preview page count/order mismatch on slide {index}; original PPTX was not replaced')
                preview_receipts.append(preview_receipt)
                shutil.copy2(image_path, previews / f'slide-{index}.png')
        if file_sha256(pptx) != source_hash:
            raise RenderError('PowerPoint source changed during roundtrip; the original PPTX was not replaced')
        # Make both published artifacts a small rollback-capable transaction.
        # Keep the old preview directory until the candidate replaces the PPTX.
        slides = output_dir / 'slides'
        preview_evidence = [
            {'slide_number': receipt['slide_number'], 'output_sha256': receipt['output_sha256'],
             'width': receipt['width'], 'height': receipt['height'], 'output_bytes': receipt['output_bytes']}
            for receipt in preview_receipts
        ]
        result = {**report, 'backend':'mac-applescript', 'classification':'none', 'automation_attempts':attempts, 'temporary_cache_mode': cache_mode, 'slides':str(slides), 'preview_source':'officecli-html-approximate', 'native_save_reopen':True, 'native_visual_verified':False, 'preview_slide_count':len(preview_receipts), 'preview_slides':preview_evidence, 'preview_ordering':'OfficeCLI screenshot requested one-based in slide order'}
        slides_backup = output_dir / (token + '-slides-backup')
        had_previous_slides = slides.exists() or slides.is_symlink()
        new_slides_published = False
        try:
            if file_sha256(pptx) != source_hash:
                raise RenderError('PowerPoint source changed before publication; the original PPTX was not replaced')
            if had_previous_slides:
                slides.replace(slides_backup)
            previews.replace(slides)
            new_slides_published = True
            if reference is not None:
                reference.unlink(missing_ok=True)
            if file_sha256(pptx) != source_hash:
                raise RenderError('PowerPoint source changed during publication; the original PPTX was not replaced')
            candidate.replace(pptx)
        except BaseException as publish_error:
            if new_slides_published and (slides.exists() or slides.is_symlink()):
                if slides.is_dir() and not slides.is_symlink():
                    shutil.rmtree(slides)
                else:
                    slides.unlink(missing_ok=True)
            if had_previous_slides and slides_backup.exists():
                try:
                    slides_backup.replace(slides)
                except OSError as restore_error:
                    raise RenderError(
                        f'PPTX publication failed and previous slides could not be restored; '
                        f'the backup remains at {slides_backup}: {restore_error}'
                    ) from publish_error
            raise
        if had_previous_slides and slides_backup.exists():
            if slides_backup.is_dir() and not slides_backup.is_symlink():
                shutil.rmtree(slides_backup, ignore_errors=True)
            else:
                try:
                    slides_backup.unlink(missing_ok=True)
                except OSError:
                    pass
        return result
    except BaseException:
        candidate.unlink(missing_ok=True)
        if reference is not None:
            reference.unlink(missing_ok=True)
        if previews.is_dir():
            shutil.rmtree(previews, ignore_errors=True)
        raise
