from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from ..adapter_contracts import CapabilityMaturity
from ..config import PROJECT_ROOT, tools_root, upstream_root
from ..models import ProductionRoute
from ..runtime import chromium_executable, officecli_executable, executable_version, resolve_executable, system_osascript
from .registry import AdapterRegistry, build_registry


@dataclass(frozen=True)
class Capability:
    name: str
    available: bool
    role: str
    path: str | None = None
    required_for: str | None = None
    maturity: str | None = None
    version: str | None = None
    automatic: bool = False


def _command(name: str) -> Path | None:
    return resolve_executable(name)


def _chromium() -> Path | None:
    return chromium_executable()


def _node_supported(node: Path | None) -> bool:
    if not node:
        return False
    from ..platform_runtime import probe_node_runtime
    return probe_node_runtime(node)["status"] == "passed"


def _powerpoint_endpoint() -> str | None:
    if sys.platform == "darwin":
        app = Path("/Applications/Microsoft PowerPoint.app")
        return str(app) if app.is_dir() and system_osascript() is not None else None
    configured = os.environ.get("AIPPT_POWERPOINT")
    candidates = [
        Path(configured) if configured else None,
        Path("C:/Program Files/Microsoft Office/root/Office16/POWERPNT.EXE"),
        Path("C:/Program Files (x86)/Microsoft Office/root/Office16/POWERPNT.EXE"),
    ]
    executable = next((path.resolve() for path in candidates if path and path.is_file()), None)
    if executable:
        return str(executable)
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application\CLSID"):
                return "COM:PowerPoint.Application"
        except OSError:
            pass
    return None


def registry() -> AdapterRegistry:
    return build_registry(PROJECT_ROOT, upstream_root())


def capabilities() -> list[Capability]:
    upstream = upstream_root()
    node = _command("node")
    chromium = _chromium()
    powerpoint = _powerpoint_endpoint()
    officecli = officecli_executable()
    officecli_ok = executable_version(officecli) is not None
    base = [
        Capability("python", True, "orchestrator", sys.executable, "all", CapabilityMaturity.AUTOMATIC_READY.value, automatic=True),
        Capability("node", node is not None, "HTML/PPTX renderer", str(node) if node else None, "native"),
        Capability("node-runtime", _node_supported(node), "Host-native Node.js 22.12+ runtime", str(node) if node else None, "native"),
        Capability("chromium", chromium is not None, "HTML layout and preview runtime", str(chromium) if chromium else None, "native"),
        Capability("dom-to-pptx", (PROJECT_ROOT / "node_modules" / "dom-to-pptx").is_dir(), "editable HTML-to-PPTX engine", str(PROJECT_ROOT / "node_modules" / "dom-to-pptx"), "native", CapabilityMaturity.AUTOMATIC_READY.value, "2.0.3", True),
        Capability("powerpoint", powerpoint is not None, "desktop roundtrip and slide PNG review", powerpoint, "export"),
        Capability("officecli", officecli_ok, "OOXML inspection and post-processing", str(officecli) if officecli else None, "all"),
        Capability("powerpoint-native-com", os.name == "nt" and powerpoint is not None, "Windows COM animation and video operators", powerpoint if os.name == "nt" else None, "native-animation"),
        Capability("ppt-master", (upstream / "ppt-master").is_dir(), "source parsing and planning evidence", str(upstream / "ppt-master")),
    ]
    # Operation-specific readiness is separate from a checkout being present.
    from ..mac_video import media_executable
    mac = sys.platform == "darwin"
    motion_worker = PROJECT_ROOT / "scripts/adapters/pptmaster_motion.py"
    motion_library = upstream / "ppt-master/skills/ppt-master/scripts/pptx_animations.py"
    base.extend([
        Capability("mac-native-effects", mac and officecli_ok and powerpoint is not None and motion_worker.is_file() and motion_library.is_file(),
                   "Mac animation/paths/repeat/carousel/Morph authoring with native Office save QA", str(motion_worker), "native-animation"),
        Capability("mac-native-video", mac and powerpoint is not None and media_executable() is not None,
                   "actual Mac PowerPoint movie export plus MP4 transcode; visual review required", str(media_executable()) if media_executable() else None, "native-video"),
        Capability("pptagent-model-runtime", bool(os.environ.get("AIPPT_PPTAGENT_PYTHON") and Path(os.environ["AIPPT_PPTAGENT_PYTHON"]).is_file() and os.environ.get("PPTAGENT_MODEL")),
                   "configured isolated model runtime (configuration detection only; not a live generation test)", None, "model-generation"),
        Capability("presenton-service-config", bool(os.environ.get("AIPPT_PRESENTON_URL")),
                   "configured service URL (configuration detection only; not a health or generation test)", None, "model-generation"),
    ])
    for adapter in registry().all():
        if adapter.route == ProductionRoute.NATIVE:
            continue
        maturity = adapter.maturity()
        base.append(
            Capability(
                adapter.name.replace("-skill", "") if adapter.route != ProductionRoute.REBUILD else "image-to-editable-ppt",
                maturity != CapabilityMaturity.UNAVAILABLE,
                {
                    ProductionRoute.TEMPLATE: "native template reuse",
                    ProductionRoute.REBUILD: "1:1 editable reconstruction",
                    ProductionRoute.VISUAL: "optional image-first visual version",
                }[adapter.route],
                str(getattr(adapter, "upstream_path", "")) or None,
                adapter.route.value,
                maturity.value,
                adapter.version,
                maturity.is_automatic,
            )
        )
    return base


def capability_report() -> list[dict[str, object]]:
    report: list[dict[str, object]] = []
    for item in capabilities():
        payload = asdict(item)
        if item.maturity is None:
            payload.pop("maturity")
            payload.pop("version")
            payload.pop("automatic")
        report.append(payload)
    return report


def route_ready(route: ProductionRoute) -> tuple[bool, str]:
    if os.environ.get("AIPPT_FAKE_RENDER") == "1" and route == ProductionRoute.NATIVE:
        return True, "test renderer"
    items = capabilities()
    common_names = {"python", "node", "node-runtime", "chromium", "dom-to-pptx", "officecli"}
    missing_common = [item.name for item in items if item.name in common_names and not item.available]
    if missing_common:
        return False, f"missing: {', '.join(missing_common)}"
    if route == ProductionRoute.NATIVE:
        return True, "ready"
    adapter = registry().get(route)
    maturity = adapter.maturity()
    if maturity == CapabilityMaturity.UNAVAILABLE:
        return False, f"missing: {adapter.name}"
    return True, f"{maturity.value}: {'automatic' if maturity.is_automatic else 'manual fallback'}"


__all__ = [
    "AdapterRegistry",
    "Capability",
    "capabilities",
    "capability_report",
    "registry",
    "route_ready",
]
