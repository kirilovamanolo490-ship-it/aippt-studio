from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from adapter_common import artifact, blocked, load_request, parse_contract_args, upstream_checkout, write_result, upstream_version


ENGINE = "codex-ppt-skill"
VERSION = upstream_version("codex-ppt-skill")


def main() -> int:
    args = parse_contract_args()
    request, job_root = load_request(args.request)
    plan = json.loads((job_root / request["deck_plan"]).read_text(encoding="utf-8"))
    slide_ids = [slide["id"] for slide in plan["slides"]]
    image_root = job_root / "workspace" / "visual-images"
    images = [next((image_root / f"{slide_id}{suffix}" for suffix in (".png", ".jpg", ".jpeg") if (image_root / f"{slide_id}{suffix}").is_file()), None) for slide_id in slide_ids]
    if any(image is None for image in images):
        category = "visual_assets_pending" if request.get("cloud_allowed", True) else "cloud_disabled"
        write_result(
            args.result,
            blocked(
                ENGINE,
                VERSION,
                category,
                "generate one approved full-slide image per slide in workspace/visual-images; remote generation is forbidden when cloud_allowed is false",
                "image_generation_workflow",
                expected_names=[f"{slide_id}.png" for slide_id in slide_ids],
            ),
        )
        return 0
    output_root = job_root / request["output_root"]
    if output_root.is_dir():
        shutil.rmtree(output_root)
    project = output_root / "visual-source"
    origin = project / "origin_image"
    origin.mkdir(parents=True)
    for index, image in enumerate(images, start=1):
        assert image is not None
        shutil.copy2(image, origin / f"slide_{index:02d}{image.suffix.lower()}")
    speech = "\n\n".join(f"## Slide {index}\n{slide.get('speaker_notes', '')}" for index, slide in enumerate(plan["slides"], start=1))
    (project / "speech.md").write_text(speech, encoding="utf-8")
    source_manifest = project / "visual-source.json"
    source_manifest.write_text(json.dumps({"engine": ENGINE, "slides": slide_ids, "image_based": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    assembler = upstream_checkout("codex-ppt-skill") / "skills" / "codex-ppt" / "scripts" / "assemble_ppt.py"
    output = output_root / "deck.pptx"
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run([sys.executable, str(assembler), str(output_root), "visual-source.pptx"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False, env=environment)
    generated = project / "visual-source.pptx"
    if completed.returncode != 0 or not generated.is_file():
        write_result(args.result, blocked(ENGINE, VERSION, "engine", (completed.stderr or completed.stdout or "codex-ppt assembly failed")[:1000], "manual_upstream_adapter"))
        return 0
    shutil.move(generated, output)
    write_result(
        args.result,
        {
            "schema_version": 1,
            "status": "succeeded",
            "receipt": {"engine": ENGINE, "version": VERSION, "command": ["python", "assemble_ppt.py"]},
            "editability": "image_based",
            "artifacts": [artifact(job_root, "pptx", output), artifact(job_root, "build_source", source_manifest)],
            "providers": [],
            "warnings": ["This alternate is image-based and is not the editable master."],
            "metadata": {"slides": len(slide_ids)},
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
