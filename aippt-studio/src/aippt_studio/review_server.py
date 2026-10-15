from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from .models import DeckPlan
from .storage import job_dir, load_job, read_json, read_model
from .workflow import add_comment, approve_gate, status_payload


class CommentRequest(BaseModel):
    slide_id: str
    body: str


class ApprovalRequest(BaseModel):
    note: str = ""


def create_app() -> FastAPI:
    app = FastAPI(title="AI PPT Studio Review", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    ui_path = Path(__file__).resolve().parent / "web" / "index.html"

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        return HTMLResponse(ui_path.read_text(encoding="utf-8"))

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        try:
            job = load_job(job_id)
            root = job_dir(job_id)
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        payload = status_payload(job)
        # This endpoint feeds a non-technical local review surface.  The CLI
        # retains full diagnostic paths, while the browser receives the task
        # decision and previews it needs rather than a local workspace map.
        payload.pop("root", None)
        payload.pop("work_request", None)
        payload["brief"] = read_json(root / "planning" / "brief.json") if (root / "planning" / "brief.json").is_file() else None
        plan_path = root / "planning" / "deck-plan.json"
        plan = read_model(plan_path, DeckPlan) if plan_path.is_file() else None
        payload["deck_plan"] = plan.model_dump(mode="json") if plan else None
        if plan:
            from .models import select_prototype_slides
            prototype_slides = select_prototype_slides(plan)
        else:
            prototype_slides = []
        payload["prototype_slide_ids"] = [slide.id for slide in prototype_slides]
        payload["comments"] = read_json(root / "reviews" / "comments.json")
        payload["preview_base"] = f"/files/{job.id}"
        animated_preview_dir = root / "build" / "animated-previews"
        animated_previews: dict[str, str] = {}
        mapped_files: set[str] = set()
        native_audit_path = root / "edits" / "native-powerpoint.json"
        try:
            native_audit = read_json(native_audit_path) if native_audit_path.is_file() else None
        except (OSError, ValueError):
            native_audit = None
        native_plan = native_audit.get("plan") if isinstance(native_audit, dict) else None
        preview_specs = native_plan.get("previews") if isinstance(native_plan, dict) else None
        if plan and isinstance(preview_specs, list):
            for spec in preview_specs:
                if not isinstance(spec, dict):
                    continue
                preview_id = spec.get("id")
                slide_numbers = spec.get("slides")
                if (
                    not isinstance(preview_id, str)
                    or Path(preview_id).name != preview_id
                    or not isinstance(slide_numbers, list)
                ):
                    continue
                preview_file = animated_preview_dir / f"{preview_id}.mp4"
                if not preview_file.is_file():
                    continue
                relative_path = f"build/animated-previews/{preview_file.name}"
                mapped_files.add(preview_file.name)
                for slide_number in slide_numbers:
                    if isinstance(slide_number, int) and not isinstance(slide_number, bool) and 1 <= slide_number <= len(plan.slides):
                        animated_previews.setdefault(plan.slides[slide_number - 1].id, relative_path)
        for path in sorted(animated_preview_dir.glob("*.mp4")):
            if path.is_file() and path.name not in mapped_files:
                animated_previews.setdefault(path.stem, f"build/animated-previews/{path.name}")
        payload["animated_previews"] = animated_previews
        return payload

    @app.post("/api/jobs/{job_id}/comments")
    def post_comment(job_id: str, request: CommentRequest) -> dict[str, object]:
        try:
            return add_comment(job_id, request.slide_id, request.body).model_dump(mode="json")
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.post("/api/jobs/{job_id}/approve/{gate}")
    def post_approval(job_id: str, gate: str, request: ApprovalRequest) -> dict[str, object]:
        try:
            return approve_gate(job_id, gate, request.note).model_dump(mode="json")
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @app.get("/files/{job_id}/{relative_path:path}")
    def files(job_id: str, relative_path: str) -> FileResponse:
        root = job_dir(job_id).resolve()
        target = (root / relative_path).resolve()
        if root not in target.parents or not target.is_file():
            raise HTTPException(status_code=404, detail="file not found")
        return FileResponse(target)

    return app
