"""Optional asset generators. Inputs and outputs stay in the active Work."""
from __future__ import annotations

import hashlib
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .config import PROJECT_ROOT, jobs_root
from .platform_runtime import probe_node_runtime
from .runtime import chromium_executable


def workspace_output(output: Path) -> Path:
    if not os.environ.get("AIPPT_JOBS_DIR"):
        raise ValueError("Invoke support through work/<N>/workspace/aippt.ps1 so outputs belong to a Work")
    workspace = jobs_root().parent.resolve()
    target = output.expanduser().resolve()
    if target == workspace or workspace not in target.parents:
        raise ValueError("Support output must be inside the active Work workspace")
    return target


def render_support(kind: str, source: Path, output: Path) -> dict[str, object]:
    if kind not in {"chart", "diagram"}:
        raise ValueError("support kind must be chart or diagram")
    source = source.expanduser().resolve()
    target = workspace_output(output)
    if not source.is_file():
        raise ValueError(f"input file does not exist: {source}")
    if target.suffix.lower() != ".svg" or target == source:
        raise ValueError("Choose a distinct .svg output file")
    runtime_root = PROJECT_ROOT / ".tools" / "extension-runtime"
    package = "echarts" if kind == "chart" else "@mermaid-js/mermaid-cli"
    package_root = runtime_root / "node_modules" / package
    metadata = package_root / "package.json"
    if not metadata.is_file():
        raise ValueError("Extension runtime is missing; see docs/UPSTREAM_INTEGRATION.md")
    version = json.loads(metadata.read_text(encoding="utf-8"))["version"]
    node_runtime = probe_node_runtime()
    if node_runtime.get("status") != "passed":
        reason = str(node_runtime.get("reason") or node_runtime.get("probe") or "no compatible executable was found")
        raise ValueError("A compatible host-native Node.js 22.12+ runtime is unavailable: " + reason)
    node = Path(str(node_runtime["path"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="support-", dir=target.parent) as temp:
        staging = Path(temp)
        candidate = staging / "asset.svg"
        if kind == "chart":
            command = [str(node), str(PROJECT_ROOT / "node" / "support.mjs"), str(runtime_root), str(source), str(candidate)]
        else:
            browser = chromium_executable()
            if browser is None:
                raise ValueError("Chrome/Chromium is required for Mermaid rendering")
            config = staging / "puppeteer.json"
            config.write_text(json.dumps({"executablePath": str(browser)}), encoding="utf-8")
            mermaid = staging / "mermaid.json"
            mermaid.write_text(json.dumps({"htmlLabels": False, "flowchart": {"htmlLabels": False}}), encoding="utf-8")
            command = [str(node), str(package_root / "src" / "cli.js"), "-i", str(source), "-o", str(candidate), "-p", str(config), "-c", str(mermaid), "-b", "transparent"]
        result = subprocess.run(command, cwd=staging, capture_output=True, text=True, timeout=180, check=False,
                                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        if result.returncode or not candidate.is_file() or candidate.stat().st_size == 0:
            raise ValueError(f"{kind} renderer failed: {(result.stderr or result.stdout)[-1500:]}")
        text = candidate.read_text(encoding="utf-8")
        if "<svg" not in text:
            raise ValueError("Renderer did not return an SVG")
        candidate.replace(target)
    # The HTML-to-PPTX renderer cannot reliably resolve relative SVG file URLs.
    # Provide a self-contained image fragment while retaining the vector source.
    embed_path = target.with_suffix(".embed.html")
    encoded = base64.b64encode(target.read_bytes()).decode("ascii")
    embed_path.write_text(f'<img alt="{kind}" src="data:image/svg+xml;base64,{encoded}">\n', encoding="utf-8")
    receipt = {"status": "succeeded", "kind": kind, "package": package, "version": version,
               "runtime_lock": str(runtime_root / "package-lock.json"),
               "runtime_lock_sha256": hashlib.sha256((runtime_root / "package-lock.json").read_bytes()).hexdigest(),
               "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
               "output": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
               "html_embedding": str(embed_path), "html_embedding_sha256": hashlib.sha256(embed_path.read_bytes()).hexdigest(),
               "pptx_editability": "graphic asset; not a native editable chart",
               "source_repository_modified": False}
    target.with_suffix(".provenance.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    return receipt
