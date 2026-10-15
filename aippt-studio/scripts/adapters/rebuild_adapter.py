from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from adapter_common import artifact, blocked, load_request, parse_contract_args, source_records, upstream_checkout, write_result, upstream_version


ENGINE = "image-to-editable-ppt-skill"
VERSION = upstream_version("image-to-editable-ppt-skill")


def finalized_output(workspace: Path) -> tuple[Path, Path] | None:
    deck_manifest = workspace / "deck_manifest.json"
    if deck_manifest.is_file():
        deck = json.loads(deck_manifest.read_text(encoding="utf-8"))
        output = (workspace / deck.get("output", "final/deck_edited.pptx")).resolve()
        if workspace.resolve() not in output.parents:
            raise ValueError("editppt final output escapes its Work run directory")
        summary_path = output.parent / "run_summary.json"
        validation_path = output.parent / "validation.json"
        state_path = workspace / "run_state.json"
        if not all(p.is_file() for p in (output, summary_path, validation_path, state_path)):
            return None
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if summary.get("status") != "complete" or state.get("status") != "complete" or validation.get("passed") is not True:
            return None
        if summary.get("page_count") != deck.get("page_count") or validation.get("slides") != deck.get("page_count"):
            raise ValueError("editppt final page counts disagree")
        return output, deck_manifest
    # Existing externally finalized jobs keep their original handoff contract.
    output, manifest = workspace / "final.pptx", workspace / "final-manifest.json"
    return (output, manifest) if output.is_file() and manifest.is_file() else None


def main() -> int:
    args = parse_contract_args()
    request, job_root = load_request(args.request)
    candidates = [item for item in source_records(job_root, request) if item[2] in {"flattened_input", "content"}]
    candidates = [item for item in candidates if item[0].get("kind") in {"image", "pdf", "powerpoint"}]
    if not candidates:
        write_result(args.result, blocked(ENGINE, VERSION, "input_role", "rebuild route requires an image, PDF, or PowerPoint source", "set_input_role"))
        return 0
    workspace = job_root / "workspace" / "editppt"
    try:
        finalized = finalized_output(workspace)
    except (ValueError, OSError) as error:
        write_result(args.result, blocked(ENGINE, VERSION, "finalization_contract", str(error), "editppt_page_workflow"))
        return 0
    if finalized:
        final_pptx, final_manifest = finalized
        output_root = job_root / request["output_root"]
        if output_root.is_dir():
            shutil.rmtree(output_root)
        output_root.mkdir(parents=True)
        output = output_root / "deck.pptx"
        source = output_root / "rebuild-manifest.json"
        shutil.copy2(final_pptx, output)
        shutil.copy2(final_manifest, source)
        write_result(
            args.result,
            {
                "schema_version": 1,
                "status": "succeeded",
                "receipt": {"engine": ENGINE, "version": VERSION, "command": ["editppt", "run", "finalize"]},
                "editability": "reconstructed_editable",
                "artifacts": [artifact(job_root, "pptx", output), artifact(job_root, "build_source", source)],
                "providers": [],
                "warnings": [],
                "metadata": {"run_dir": "workspace/editppt"},
            },
        )
        return 0
    cli = upstream_checkout("image-to-editable-ppt-skill") / "skills" / "image-to-editable-ppt" / "cli" / "editppt" / "cli.py"
    if not cli.is_file():
        write_result(args.result, blocked(ENGINE, VERSION, "runtime", "the reviewed editppt CLI is unavailable", "manual_upstream_adapter"))
        return 0
    workspace.mkdir(parents=True, exist_ok=True)
    state = workspace / "deck_manifest.json"
    if not state.is_file() and not (workspace / "deck.json").is_file():
        completed = subprocess.run(
            [sys.executable, str(cli), "prepare", *[str(item[1]) for item in candidates], "--job-dir", str(workspace), "--image-backend", "builtin-imagegen", "--no-text-hints"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            check=False,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        if completed.returncode != 0:
            write_result(args.result, blocked(ENGINE, VERSION, "prepare", (completed.stderr or completed.stdout or "editppt prepare failed")[:1000], "manual_upstream_adapter"))
            return 0
    write_result(
        args.result,
        blocked(
            ENGINE,
            VERSION,
            "reconstruction_pending",
            "editppt run is prepared; complete page reconstruction and use editppt run finalize on workspace/editppt",
            "editppt_page_workflow",
            run_dir="workspace/editppt",
            expected_pptx="path specified by workspace/editppt/deck_manifest.json output",
            expected_manifest="workspace/editppt/deck_manifest.json",
            cloud_allowed=bool(request.get("cloud_allowed", True)),
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
