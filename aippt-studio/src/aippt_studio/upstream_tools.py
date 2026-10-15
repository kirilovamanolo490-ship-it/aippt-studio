"""Explicit, read-only upstream calls with Work-local execution receipts.

Resources are data, never commands. A source checkout is not a runtime. Each
executable operation below has its own input contract; there is no shell escape.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from .config import PROJECT_ROOT
from .platform_runtime import probe_node_runtime
from .storage import write_json
from .support import render_support, workspace_output
from .upstreams import upstream_report


OPERATIONS = {
    "mediabunny": {"inspect": "inspect local video/audio tracks, timestamps and packets without any model"},
    "iconify-icon-sets": {"icon": "materialize an exact Iconify icon, retaining collection license"},
    "lxgw-wenkai-gb": {"font": "copy a font binary and its license into the Work"},
    "LxgwWenkaiGB": {"font": "copy a font binary and its license into the Work"},
    "smiley-sans": {"font": "copy a font binary if present; source glyphs are not installed fonts"},
    "echarts": {"chart": "render ECharts JSON to SVG using the pinned isolated runtime"},
    "mermaid-cli": {"diagram": "render Mermaid to SVG using the pinned isolated runtime"},
    "pptxgenjs": {"author": "create editable text/shapes/charts/tables from a JSON scene"},
    "PptxGenJS": {"author": "create editable text/shapes/charts/tables from a JSON scene"},
    "ppt-master": {"svg-deck": "run canonical SVG quality gate and native SVG-to-PPTX export",
                   "native-motion": "author native animation sequences and Morph via upstream OOXML library"},
    "markitdown": {"convert": "convert a local document to Markdown using upstream code and Studio dependencies"},
    "presenton": {"generate": "call a configured Presenton service; requires a job with cloud allowed"},
    "pptagent": {"mcp-session": "run a stateful template/slide tool sequence in a configured isolated model runtime"},
    "gsap": {"timeline": "evaluate easing through the upstream GSAP runtime"},
    "GSAP": {"timeline": "evaluate easing through the upstream GSAP runtime"},
    "officecli": {"validate": "validate a PPTX with the host OfficeCLI binary",
                  "animations": "inspect objects and native animation IDs/durations",
                  "animation-add": "add a native fade/wipe entrance using OfficeCLI",
                  "animation-batch": "preview or atomically apply 1-100 duration edits with source/plan hashes",
                  "animation-duration": "change a selected duration with Studio byte-preserving adapter and OfficeCLI validation"},
    "OfficeCLI": {"validate": "validate a PPTX with the host OfficeCLI binary"},
}

HANDOFFS = {
    "PPTAgent": "Requires an isolated PPTAgent environment and configured model. Upstream exposes a stateful stdio MCP server (list_templates, set_template, create_slide, write_slide, generate_slide, save_generated_slides).",
    "pptagent": "Requires an isolated PPTAgent environment and configured model; use its stateful stdio MCP server.",
    "Presenton": "Requires a running Presenton service and model. API: POST /api/v1/ppt/presentation/generate. Service output must be imported and reviewed as a candidate.",
    "presenton": "Requires a running Presenton service and model. API: POST /api/v1/ppt/presentation/generate.",
    "remotion": "Video renderer needs a separate composition/runtime and applicable license; not a PPTX renderer.",
    "remotion-bits": "Components need a Remotion composition and runtime; resource retrieval is available.",
    "motion-canvas": "Video renderer needs a separate Motion Canvas project/runtime; resource retrieval is available.",
    "markitdown": "Conversion uses Studio's Python dependencies; unsupported formats report missing extras explicitly.",
    "academic-pptx-skill": "Automatic reuse blocked: source license declarations conflict; inspect license evidence first.",
}


def _host_node() -> str:
    """Return the verified Node runtime used by executable upstream operations.

    A checkout or a PATH entry is not enough: this closes the last direct
    Node lookups that could run a copied Work cache or a Windows executable
    after Work initialization had already rejected it.
    """

    report = probe_node_runtime()
    if report.get("status") != "passed":
        reason = str(report.get("reason") or report.get("probe") or "no compatible executable was found")
        raise ValueError("A compatible host-native Node.js 22.12+ runtime is unavailable: " + reason)
    return str(report["path"])


# A local clone, a selected design note, and a process that actually ran are
# different facts.  Keep that distinction machine-readable so planning and
# archive tooling cannot promote a directory listing into a usage claim.  A
# resource read is handled separately below because it never proves that the
# provider's renderer/editor ran.
_EXECUTION_PROFILES: dict[str, dict[str, str]] = {
    "GSAP": {
        "execution_kind": "direct_checked_out_runtime",
        "checkout_role": "the checked-out dist runtime is loaded directly",
        "proof": "a successful Work-local receipt.json with immutable request and artifact hashes",
    },
    "lxgw-wenkai-gb": {
        "execution_kind": "direct_checked_out_asset_copy",
        "checkout_role": "the selected font binary and OFL are copied from the checkout",
        "proof": "a successful Work-local receipt.json and the bound asset receipt",
    },
    "officecli": {
        "execution_kind": "managed_host_binary",
        "checkout_role": "source checkout is provenance only; the host OfficeCLI binary is executed",
        "proof": "a successful Work-local validation/edit receipt bound to exact input bytes",
    },
    "pptagent": {
        "execution_kind": "configured_work_copy_runtime",
        "checkout_role": "required packages are copied into the Work before the configured isolated runtime starts",
        "proof": "a successful MCP-session receipt; configuration alone is not execution",
    },
    "PptxGenJS": {
        "execution_kind": "managed_package_runtime",
        "checkout_role": "checkout is reviewed-source provenance; the locked Studio npm package is executed",
        "proof": "a successful native-scene or author receipt with package-lock identity",
    },
    "presenton": {
        "execution_kind": "configured_external_service",
        "checkout_role": "checkout is API/reference provenance; a separately configured service is called",
        "proof": "a successful Work-local service receipt and imported candidate hash",
    },
    "academic-pptx-skill": {
        "execution_kind": "reference_only",
        "checkout_role": "license-conflicted evaluation source; automatic execution is disabled",
        "proof": "none; a source read is not an engine execution",
    },
    "awesome-ai-ppt": {
        "execution_kind": "reference_only",
        "checkout_role": "discovery/reference catalog only",
        "proof": "none; a source read is not an engine execution",
    },
    "codex-ppt-skill": {
        "execution_kind": "direct_checked_out_source_adapter",
        "checkout_role": "the reviewed checkout script is invoked for the image-based visual route",
        "proof": "adapter receipt plus clean checked-out source before and after execution",
    },
    "dom-to-pptx": {
        "execution_kind": "managed_package_runtime",
        "checkout_role": "checkout is reviewed-source provenance; the locked Studio npm package is executed",
        "proof": "native-adapter result with engine, reviewed runtime commit and artifact hashes",
    },
    "echarts": {
        "execution_kind": "managed_isolated_runtime",
        "checkout_role": "checkout is provenance; the isolated extension-runtime package renders SVG",
        "proof": "a successful Work-local receipt.json with runtime lock and SVG hashes",
    },
    "editable-leadership-pptx": {
        "execution_kind": "source_validation_helper",
        "checkout_role": "design notes are reference-only; the bounded PptxGenJS helper is invoked only by native-scene/author",
        "proof": "the related PptxGenJS receipt naming this validation provider",
    },
    "iconify-icon-sets": {
        "execution_kind": "direct_checked_out_asset_materialization",
        "checkout_role": "the selected collection JSON is read directly from the checkout",
        "proof": "a successful Work-local icon receipt with collection license and asset hash",
    },
    "image-to-editable-ppt-skill": {
        "execution_kind": "direct_checked_out_source_adapter",
        "checkout_role": "the reviewed checkout CLI is invoked for reconstruction preparation/final import",
        "proof": "adapter receipt plus clean checked-out source before and after execution",
    },
    "markitdown": {
        "execution_kind": "direct_checked_out_source_with_studio_dependencies",
        "checkout_role": "the checked-out converter source is imported with Studio Python dependencies",
        "proof": "a successful Work-local conversion receipt with input/output hashes",
    },
    "mermaid-cli": {
        "execution_kind": "managed_isolated_runtime",
        "checkout_role": "checkout is provenance; the isolated extension-runtime CLI renders SVG",
        "proof": "a successful Work-local receipt.json with runtime lock and SVG hashes",
    },
    "motion-canvas": {
        "execution_kind": "reference_only",
        "checkout_role": "video/motion reference only; no Studio production route is enabled",
        "proof": "none; a source read is not an engine execution",
    },
    "ppt-master": {
        "execution_kind": "direct_checked_out_source_tool",
        "checkout_role": "bounded readers/SVG/motion modules are executed from the locked checkout",
        "proof": "source-semantics provenance or a successful Work-local upstream-call receipt",
    },
    "pptx-automizer": {
        "execution_kind": "managed_package_runtime",
        "checkout_role": "checkout is reviewed-source provenance; the locked Studio npm package is executed",
        "proof": "template adapter receipt with package-lock, bridge and source-bound map identities",
    },
    "remotion": {
        "execution_kind": "reference_only",
        "checkout_role": "video engine reference only; no PPTX route is enabled",
        "proof": "none; a source read is not an engine execution",
    },
    "remotion-bits": {
        "execution_kind": "reference_only",
        "checkout_role": "component reference only; an isolated Remotion project is still required",
        "proof": "none; a source read is not an engine execution",
    },
    "smiley-sans": {
        "execution_kind": "direct_checked_out_asset_copy",
        "checkout_role": "source assets may be copied only when a real font binary is present",
        "proof": "a successful Work-local font receipt and later PowerPoint font QA",
    },
    "mediabunny": {
        "execution_kind": "managed_isolated_runtime",
        "checkout_role": "checkout is provenance; the isolated media-inspection runtime is executed",
        "proof": "a successful Work-local inspection receipt bound to copied media bytes",
    },
}


def provider_execution_profile(identifier: str) -> dict[str, object]:
    """Describe a provider's enabled production path, never a usage claim."""

    profile = _EXECUTION_PROFILES.get(identifier)
    if profile is None:
        return {
            "catalog_state": "no_registered_execution_path",
            "execution_kind": "reference_only",
            "checkout_role": "no executable Studio integration is registered",
            "proof": "none; directory presence and planning selection are not execution",
            "executed": False,
        }
    return {"catalog_state": "operation_registered_not_executed", "executed": False, **profile}


def execution_profile(identifier: str, operation: str) -> dict[str, object]:
    """Describe one operation. Resource access never inherits engine status."""

    if operation in {"read", "search"}:
        return {
            "catalog_state": "resource_access_only",
            "execution_kind": "source_resource_read",
            "checkout_role": "a tracked source/resource is read; no provider engine is executed",
            "proof": "a successful receipt proves only this bounded read/search operation",
            "executed": False,
        }
    return provider_execution_profile(identifier)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("resource path must be a nonempty repository-relative path")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents or ".git" in Path(relative).parts:
        raise ValueError("resource path escapes repository or targets Git internals")
    return path


def _row(identifier: str, *, verify: bool = False) -> dict:
    row = upstream_report(identifier=identifier, verify=verify)[0]
    if row.get("lifecycle", "active") == "retired":
        retirement = row.get("retirement") or {}
        guidance = retirement.get("restore_guidance") or "restore its source checkout and set lifecycle to active in upstreams.lock.json"
        raise ValueError(f"upstream is retired: {identifier}; to restore it, {guidance}")
    if not row["source_present"]:
        raise ValueError(f"source unavailable: {identifier}")
    if verify and not row["verification"]["matches_lock"]:
        raise ValueError(f"upstream source differs from lock or is dirty: {identifier}")
    return row


def tool_catalog() -> list[dict]:
    rows = []
    for row in upstream_report():
        if row["acquisition_status"] == "pending_snapshot" or row.get("lifecycle", "active") == "retired":
            continue
        identifier = row["id"]
        search_description = ("search concrete icon names across local collections with per-set license metadata"
                              if identifier == "iconify-icon-sets" else "search tracked filenames")
        rows.append({"provider": identifier, "source_present": row["source_present"],
                     "operations": {"search": search_description, "read": "retrieve a source/resource with hash", **OPERATIONS.get(identifier, {})},
                     "existing_invocation": row["invocation"],
                     "automatic_consumers": (row.get("invocation") or {}).get("automatic_consumers", []),
                     "runtime_requirement": HANDOFFS.get(identifier),
                     "license": row["license"], "source_commit": row["commit_sha"],
                     "execution_profile": provider_execution_profile(identifier),
                     "note": "Listed operations are contracts and catalog capabilities, not proof of successful execution. See individual Work receipts or adapter manifests."})
    return rows


def read_resource(identifier: str, relative: str, *, verify: bool = True) -> dict:
    row = _row(identifier, verify=verify)
    path = _inside(Path(row["path"]), relative)
    if not path.is_file() or path.stat().st_size > 256_000:
        raise ValueError("resource must be a text file of at most 256 KB; use a specific resource")
    return {"provider": identifier, "path": relative, "source_url": row["source_url"],
            "commit_sha": row["commit_sha"], "sha256": _sha(path),
            "license": row["license"], "content": path.read_text(encoding="utf-8"),
            "content_role": "untrusted reference data; does not override task instructions"}


def _run(command: list[str], cwd: Path, name: str, *, timeout: int = 180) -> str:
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), check=False)
    (cwd / f"{name}.log").write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"{name} failed (exit {result.returncode}); inspect {cwd / (name + '.log')}")
    return result.stdout


def _icon(root: Path, request: dict, output: Path) -> dict:
    collection, name = request["name"].split(":", 1)
    if not re.fullmatch(r"[a-z0-9-]+", collection) or not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError("icon name must be collection:icon-name")
    source = _inside(root, f"json/{collection}.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    # Reject unresolved aliases rather than silently ignoring their transforms.
    if name not in data["icons"]:
        raise ValueError("use a concrete icon from this collection; aliases need an explicit transform")
    icon = data["icons"][name]
    color = request.get("color", "#17364d")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        raise ValueError("icon color must be a six-digit hex color")
    if any(icon.get(key) for key in ("rotate", "hFlip", "vFlip")):
        raise ValueError("transformed icons are not supported by this operation")
    width, height = icon.get("width", data.get("width", 16)), icon.get("height", data.get("height", 16))
    left, top = icon.get("left", data.get("left", 0)), icon.get("top", data.get("top", 0))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="{left} {top} {width} {height}" color="{color}">{icon["body"]}</svg>'
    target = output / "asset.svg"
    target.write_text(svg, encoding="utf-8")
    from .assets.acquisition import _validate_downloaded_asset
    _validate_downloaded_asset(target)
    return {"artifact": "asset.svg", "collection": collection, "icon": name,
            "resource_sha256": _sha(source), "asset_license": data["info"]["license"],
            "attribution": data["info"]["author"], "pptx_editability": "graphic asset"}


def _search_iconify(root: Path, request: dict, limit: int) -> dict:
    """Search concrete icon keys in the locked Iconify JSON without loading it all.

    The vendored icon sets are hundreds of megabytes. Scan only key lines in
    each pretty-printed collection, and attach that collection's own license
    metadata so a search result can be reviewed before materializing it.
    """
    query = str(request.get("query", "")).strip().casefold()
    if not query:
        raise ValueError("search requires a nonempty query")
    collection_filter = request.get("collection")
    if collection_filter is not None and (
        not isinstance(collection_filter, str)
        or not re.fullmatch(r"[a-z0-9-]+", collection_filter)
    ):
        raise ValueError("collection must be a lowercase Iconify collection slug")

    json_root = root / "json"
    if not json_root.is_dir():
        raise ValueError("Iconify collection JSON directory is unavailable")
    metadata_path = root / "collections.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    files = sorted(json_root.glob("*.json"))
    if collection_filter:
        files = [path for path in files if path.stem == collection_filter]
        if not files:
            raise ValueError(f"unknown Iconify collection: {collection_filter}")

    matches: list[tuple[int, dict[str, object]]] = []
    query_terms = [term for term in re.split(r"[^a-z0-9]+", query) if term]
    if not query_terms:
        raise ValueError("search query must contain letters or numbers")
    total = 0
    for collection_path in files:
        collection = collection_path.stem
        collection_meta = metadata.get(collection, {})
        # Iconify icon keys are two-tab-indented object members directly under
        # the top-level "icons" object. Streaming avoids parsing 449 MB of JSON.
        in_icons = False
        for line in collection_path.open(encoding="utf-8"):
            stripped = line.rstrip("\r\n")
            if stripped == '\t"icons": {':
                in_icons = True
                continue
            if in_icons and stripped.startswith('\t"') and stripped != '\t"icons": {':
                in_icons = False
            if not in_icons or not stripped.startswith('\t\t"'):
                continue
            end = stripped.find('": {', 3)
            if end < 0:
                continue
            icon_name = stripped[3:end]
            normalized = icon_name.casefold()
            if all(term in normalized for term in query_terms):
                score = 0 if normalized == query else (1 if normalized.startswith(query) else 2)
                info = collection_meta
                item = {
                    "name": f"{collection}:{icon_name}",
                    "collection": collection,
                    "collection_name": info.get("name", collection),
                    "author": info.get("author"),
                    "license": info.get("license"),
                    "resource_path": f"json/{collection}.json",
                }
                total += 1
                matches.append((score, item))
                matches.sort(key=lambda pair: (pair[0], str(pair[1]["collection"]), str(pair[1]["name"])))
                del matches[limit:]

    chosen = [item for _, item in matches]
    return {"matches": chosen, "total": total, "truncated": total > limit,
            "query": query, "search_scope": "concrete_icon_keys",
            "note": "Each collection has its own license; verify the selected icon with the icon operation before use."}


def invoke(identifier: str, operation: str, request: dict, output: Path) -> dict:
    output = workspace_output(output)
    if output.exists():
        raise ValueError("use a new call output directory; previous receipts are immutable")
    row = _row(identifier, verify=True)
    if operation not in {"read", "search", *OPERATIONS.get(identifier, {})}:
        raise ValueError(f"unsupported operation {identifier}:{operation}; see upstream-tools")
    output.mkdir(parents=True)
    receipt = {"schema_version": 1, "call_id": uuid.uuid4().hex, "provider": identifier,
               "operation": operation, "source_commit": row["commit_sha"],
               "source_url": row["source_url"], "license": row["license"],
               "request_sha256": hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest(),
               "status": "running", "execution": execution_profile(identifier, operation)}
    receipt["studio_bridge_sha256"] = _sha(Path(__file__))
    write_json(output / "request.json", request)
    root = Path(row["path"])
    result: dict = {}
    try:
        if operation == "read":
            result = read_resource(identifier, request["path"], verify=False)
            (output / "resource.txt").write_text(result.pop("content"), encoding="utf-8")
            result["artifact"] = "resource.txt"
        elif operation == "search":
            query = str(request.get("query", "")).casefold()
            limit = int(request.get("limit", 30))
            if not query or not 1 <= limit <= 100:
                raise ValueError("search requires query and a limit between 1 and 100")
            if identifier == "iconify-icon-sets":
                result = _search_iconify(root, request, limit)
                for item in result["matches"]:
                    item["source_commit"] = row["commit_sha"]
            else:
                listing = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout.decode()
                matches = [name for name in listing.split("\0") if query in name.casefold()]
                result = {"matches": matches[:limit], "total": len(matches), "truncated": len(matches) > limit}
        elif operation == "inspect" and identifier == "mediabunny":
            from .media_inspect import inspect_media
            result = inspect_media(request, output)
        elif operation == "icon":
            result = _icon(root, request, output)
        elif operation == "font":
            source = _inside(root, request["path"])
            if not source.is_file() or source.suffix.lower() not in {".ttf", ".otf", ".woff", ".woff2"}:
                raise ValueError("a real font binary is required; use search to locate one")
            shutil.copy2(source, output / source.name)
            license_path = _inside(root, row["license"]["evidence"])
            shutil.copy2(license_path, output / "FONT-LICENSE.txt")
            result = {"artifact": source.name, "resource_sha256": _sha(source), "installed": False,
                      "note": "Font copied for task use; PowerPoint target font availability still needs QA."}
        elif operation == "convert":
            source = Path(request["source"]).expanduser().resolve()
            if not source.is_file():
                raise ValueError("convert requires an existing local file")
            shutil.copy2(source, output / ("input" + source.suffix))
            script = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from markitdown import MarkItDown; r=MarkItDown(enable_plugins=False).convert_local(sys.argv[2]); Path(sys.argv[3]).write_text(r.text_content, encoding='utf-8')"
            _run([sys.executable, "-c", script, str(root / "packages/markitdown/src"),
                  str(output / ("input" + source.suffix)), str(output / "document.md")], output, "markitdown")
            result = {"artifact": "document.md", "input_sha256": _sha(source),
                      "note": "Local conversion; no LLM or external plugins configured."}
        elif operation == "generate" and identifier == "presenton":
            from .upstream_services import generate_presenton
            result = generate_presenton(request, output)
        elif operation == "mcp-session":
            from .upstream_services import prepare_pptagent
            python, runtime_source = prepare_pptagent(request, output, root)
            _run([str(python), "-B", str(PROJECT_ROOT / "scripts/adapters/pptagent_session.py"),
                  str(runtime_source), str(output / "request.json"), str(output)], output, "pptagent-session", timeout=1800)
            result = {"artifact": "mcp-results.json", "status": "session_completed", "runtime_source": "runtime-source",
                      "note": "MCP responses retained; inspect available layouts/schemas before authoring. Sessions do not share in-memory state."}
            if any(step["name"] == "save_generated_slides" for step in request["steps"]):
                from zipfile import ZipFile, BadZipFile
                try:
                    with ZipFile(output / "candidate.pptx") as archive:
                        if "ppt/presentation.xml" not in archive.namelist():
                            raise ValueError("PPTAgent did not return a PPTX")
                except BadZipFile as error:
                    raise ValueError("PPTAgent returned an invalid PPTX") from error
                result.update(artifact="candidate.pptx", status="candidate_requires_review")
        elif operation in {"chart", "diagram"}:
            result = render_support(operation, Path(request["source"]), output / "asset.svg")
            result["artifact"] = "asset.svg"
        elif operation == "author":
            runtime = PROJECT_ROOT / "node_modules/pptxgenjs/package.json"
            version = json.loads(runtime.read_text())["version"]
            if version != "4.0.1":
                raise ValueError("PptxGenJS runtime differs from the reviewed 4.0.1 version")
            helper_row = _row("editable-leadership-pptx", verify=True)
            helper = Path(helper_row["path"]) / "assets/pptxgenjs_helpers/layout.js"
            node = _host_node()
            _run([node, str(PROJECT_ROOT / "node" / "author-native.cjs"), str(output / "request.json"),
                  str(output / "candidate.pptx"), str(helper)], output, "native-author")
            result = json.loads((output / "native-qa.json").read_text())
            from .native_compat import normalize_native_candidate
            result["compatibility"] = normalize_native_candidate(output / "candidate.pptx")
            from .presentation_quality import audit_presentation
            quality = audit_presentation(output / "candidate.pptx", policy=request.get("quality_policy"))
            (output / "presentation-quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2), encoding="utf-8")
            from .authoring_policy import record_quality_findings
            record_quality_findings(output, quality)
            result["presentation_quality"] = {"status": quality["status"], "report": "presentation-quality.json", "sha256": _sha(output / "presentation-quality.json")}
            if quality["status"] != "passed":
                result["status"] = "review_required"
            result.update(artifact="candidate.pptx", validation_provider="editable-leadership-pptx",
                          validation_commit=helper_row["commit_sha"], runtime_version=version,
                          runtime_lock_sha256=_sha(PROJECT_ROOT / "package-lock.json"))
            _row("editable-leadership-pptx", verify=True)
        elif operation == "native-motion":
            from .native_powerpoint import compile_native_plan
            from .renderer import officecli_validate
            if set(request) != {"source", "source_sha256", "plan"}:
                raise ValueError("native-motion requires source, source_sha256 and plan")
            source = Path(request["source"]).resolve()
            shutil.copy2(source, output / "input.pptx")
            if _sha(output / "input.pptx") != request["source_sha256"]:
                raise ValueError("source changed; inspect the current PPTX hash")
            compiled = compile_native_plan(request["plan"])
            if compiled["previews"]:
                raise ValueError("use native-effects for integrated video previews; native-motion writes PPTX only")
            scripts = root / "skills/ppt-master/scripts"
            _run([sys.executable, str(scripts / "attribution_guard.py")], output, "attribution")
            worker = PROJECT_ROOT / "scripts/adapters/pptmaster_motion.py"
            payload = {"source": str(output / "input.pptx"), "compiled_plan": compiled}
            write_json(output / "worker-request.json", payload)
            stdout = _run([sys.executable, "-B", str(worker), str(scripts), str(output / "worker-request.json"), str(output / "staged.pptx")], output, "native-motion")
            result = json.loads(stdout)
            result["validation"] = officecli_validate(output / "staged.pptx")
            (output / "staged.pptx").replace(output / "candidate.pptx")
            result.update(artifact="candidate.pptx", worker_sha256=_sha(worker))
        elif operation == "svg-deck":
            source = Path(request["project"]).expanduser().resolve()
            # A project can contain input-relative resources: require Work ownership,
            # copy it, reject symlinks, then let upstream own its copied state.
            workspace_output(source)
            if not (source / "svg_output").is_dir():
                raise ValueError("project must contain svg_output")
            if any(p.is_symlink() for p in source.rglob("*")) or source == output or source in output.parents:
                raise ValueError("project must not contain symlinks or contain the output directory")
            project = output / "project"
            shutil.copytree(source, project)
            scripts = root / "skills/ppt-master/scripts"
            _run([sys.executable, str(scripts / "attribution_guard.py")], output, "attribution")
            _run([sys.executable, str(scripts / "svg_quality_checker.py"), str(project),
                  "--quick-generate", "--canonical-authoring", "--stage", "final", "--json"], output, "svg-quality")
            _run([sys.executable, str(scripts / "svg_to_pptx.py"), str(project),
                  "--quick-generate", "--no-notes", "--no-animations", "-o", str(output / "candidate.pptx")],
                 output, "svg-export", timeout=300)
            result = {"artifact": "candidate.pptx", "pptx_editability": "native SVG conversion; verify shapes/fonts in PowerPoint",
                      "quality_report": "project/validation/svg_quality_report.json"}
        elif operation == "timeline":
            node = _host_node()
            stdout = _run([node, str(PROJECT_ROOT / "node" / "motion-sample.cjs"),
                           str(root / "dist/gsap.js"), str(output / "request.json")], output, "gsap")
            result = json.loads(stdout)
            write_json(output / "timeline.json", result)
            result["artifact"] = "timeline.json"
            result["note"] = "Evaluated timing data; this does not embed browser animation into PPTX."
        elif operation in {"animations", "animation-add", "animation-duration", "animation-batch"}:
            from .animation_edit import run_animation_operation
            result = run_animation_operation(operation, request, output)
        elif operation == "validate":
            from .renderer import officecli_validate
            source = Path(request["source"]).resolve()
            # A path-only request cannot identify the bytes validated when the
            # user later revises that file. Bind this execution to its input;
            # never retrofit identities into historical receipts.
            before = {"path": str(source), "bytes": source.stat().st_size, "sha256": _sha(source)}
            binding = receipt["input_source"] = {"before": before, "status": "checking"}
            try:
                result = officecli_validate(source)
            finally:
                try:
                    after = {"path": str(source), "bytes": source.stat().st_size, "sha256": _sha(source)}
                except OSError:
                    after = {"path": str(source), "unavailable": True}
                binding["after"] = after
                binding["status"] = "verified" if before == after else "changed"
                if before != after:
                    raise ValueError("validation source changed or disappeared during execution")
        # Source execution must leave the checkout unchanged, including untracked files.
        _row(identifier, verify=True)
        receipt["execution"]["executed"] = True
        receipt.update(status="succeeded", result=result, source_repository_modified=False)
        if result.get("artifact", "").endswith(".svg"):
            asset = output / result["artifact"]
            embed_path = output / "asset.embed.html"
            # support already records this file's hash in its result/provenance.
            # Preserve those exact bytes; only resource-only providers need the
            # generic fragment. Rewriting it invalidated the nested evidence.
            if not embed_path.is_file():
                encoded = base64.b64encode(asset.read_bytes()).decode("ascii")
                embed_path.write_text(f'<img src="data:image/svg+xml;base64,{encoded}" alt="graphic">', encoding="utf-8")
        receipt["artifacts"] = [{"path": p.relative_to(output).as_posix(), "sha256": _sha(p), "bytes": p.stat().st_size}
                                for p in sorted(output.rglob("*")) if p.is_file() and p.name != "receipt.json"]
    except Exception as error:
        receipt.update(status="failed", error=str(error))
        write_json(output / "receipt.json", receipt)
        raise
    write_json(output / "receipt.json", receipt)
    return receipt
