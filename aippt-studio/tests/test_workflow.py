from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from aippt_studio import workflow
from aippt_studio.config import jobs_root
from aippt_studio.ingest import ingest_sources
from aippt_studio.models import DeckPlan, JobState, PptxEditBatch, PptxEditOperation, ProductionRoute, ReviewComment
from aippt_studio.planner import create_brief
from aippt_studio.renderer import RenderError, sha256
from aippt_studio.storage import job_dir, load_job, read_json, read_model, write_json
from aippt_studio.workflow import (
    WorkflowError,
    add_comment,
    approve_gate,
    complete_revision,
    create_job,
    export_job,
    queue_revisions,
    resume_job,
    run_job,
)


def sample_source(tmp_path: Path) -> Path:
    path = tmp_path / "client-notes.md"
    path.write_text(
        """# 项目进展

2026 年上半年收入增长 28%，核心客户续约率达到 93%。
现有材料缺少清晰的决策主线，需要把事实、风险与下一步行动重新组织。
产品交付周期从 21 天缩短至 14 天，但两个关键模块仍存在资源风险。
下一阶段应优先完成标杆客户复盘、模板标准化和交付质量度量。
企业客户贡献了新增收入的 72%，业务结构正在进一步聚焦。
第三季度需要补充关键岗位并建立统一的指标看板。
""",
        encoding="utf-8",
    )
    return path


def test_brief_honors_explicit_total_page_count(tmp_path: Path) -> None:
    source = tmp_path / "explicit-pages.md"
    source.write_text(
        """# 用户要求

PPT 总页数必须为 14 页。每一页承担独立的信息任务，不能重复拆分内容凑页。
需要保留投票数据、角色设定、品牌故事、酒店体验和周边落地建议。
参考图片只定义角色方向，最终交付为原生可编辑 PPTX。
""",
        encoding="utf-8",
    )
    job = create_job("明确页数", job_id="explicit-page-count")
    ingest_sources(job.id, [str(source)])

    assert create_brief(job.id).target_pages == 14


def advance_to_final(tmp_path: Path) -> str:
    job = create_job("季度经营汇报", job_id="demo")
    ingest_sources(job.id, [str(sample_source(tmp_path))])
    assert run_job(job.id)["state"] == "brief_review"
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == "prototype_review"
    approve_gate(job.id, "prototype")
    assert run_job(job.id)["state"] == "final_review"
    return job.id


def test_three_gate_happy_path_and_delivery(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    approve_gate(job_id, "final")
    manifest = export_job(job_id, skip_powerpoint=True)
    assert load_job(job_id).state == JobState.FINAL_REVIEW
    assert manifest.status == "candidate"
    assert "editable_pptx" in manifest.outputs
    assert "native_adapter_result" in manifest.outputs
    assert manifest.qa["adapter"]["engine"] == "dom-to-pptx"
    assert (job_dir(job_id) / "deliverables" / "build-manifest.json").is_file()


def test_final_export_delivers_pptx_without_pdf(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    job_id = advance_to_final(tmp_path)
    approve_gate(job_id, "final")

    def native_roundtrip(pptx: Path, output_dir: Path, *args: object) -> dict[str, object]:
        assert pptx.name == "final_editable.pptx"
        slides = output_dir / "slides"
        slides.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (320, 180), "white").save(slides / "slide-1.png")
        return {"status": "passed", "backend": "mock-native", "slide_count": 1, "slides": str(slides)}

    monkeypatch.setattr(workflow, "powerpoint_roundtrip", native_roundtrip)
    manifest = export_job(job_id)
    delivery = job_dir(job_id) / "deliverables"

    assert manifest.status == "final"
    assert (delivery / "final_editable.pptx").is_file()
    assert not (delivery / "final.pdf").exists()
    assert "pdf" not in manifest.outputs


def test_native_build_runs_motion_postprocessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, str, str | None]] = []

    def capture_motion(job_id: str, _deck: Path, slides: list, *, batch_id: str, reported_path: str | None = None):
        calls.append((job_id, len(slides), batch_id, reported_path))
        return {"status": "applied", "operations": len(slides)}

    monkeypatch.setattr(workflow, "apply_motion_plan", capture_motion)
    job = create_job("motion workflow", job_id="motion-workflow")
    ingest_sources(job.id, [str(sample_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
    approve_gate(job.id, "prototype")
    assert run_job(job.id)["state"] == JobState.FINAL_REVIEW.value

    assert calls[0] == (job.id, 3, "motion-prototype", "build/prototype.pptx")
    assert calls[1][0] == job.id
    assert calls[1][2:] == ("motion-final", None)


def test_candidate_html_uses_portable_delivery_assets(tmp_path: Path) -> None:
    source = sample_source(tmp_path)
    image = tmp_path / "reference.svg"
    image.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="160" height="90"><rect width="160" height="90" fill="#14b8a6"/></svg>', encoding="utf-8")
    job = create_job("可迁移 HTML", job_id="portable-html")
    ingest_sources(job.id, [str(source), str(image)])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
    approve_gate(job.id, "prototype")
    assert run_job(job.id)["state"] == JobState.FINAL_REVIEW.value
    approve_gate(job.id, "final")

    export_job(job.id, skip_powerpoint=True)

    delivery = job_dir(job.id) / "deliverables"
    html = (delivery / "deck.html").read_text(encoding="utf-8")
    assert "file:///" not in html
    import base64
    encoded = base64.b64encode(image.read_bytes()).decode("ascii")
    assert f"data:image/svg+xml;base64,{encoded}" in html


def test_cannot_skip_a_gate(tmp_path: Path) -> None:
    job = create_job("不能跳过确认", job_id="gate-test")
    ingest_sources(job.id, [str(sample_source(tmp_path))])
    run_job(job.id)
    with pytest.raises(WorkflowError):
        approve_gate(job.id, "prototype")


def test_final_approval_requires_precise_evidence_for_factual_slides(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    root = job_dir(job_id)
    plan_path = root / "planning" / "deck-plan.json"
    plan = read_model(plan_path, DeckPlan)
    assert any("#line:" in reference for slide in plan.slides for reference in slide.evidence_refs)
    plan.slides[2].evidence_refs = [plan.slides[2].evidence_refs[0].split("#", 1)[0]]
    write_json(plan_path, plan)

    with pytest.raises(WorkflowError, match="requires a line locator"):
        approve_gate(job_id, "final")


def test_single_line_text_source_is_precise_without_line_locator(tmp_path: Path) -> None:
    source = tmp_path / "single-line-brief.md"
    source.write_text(
        "制作一份八页学生课堂演讲稿，包含封面、概念介绍、四项功能、两个现场演示、使用局限、未来影响、个人感受和结束提问；要求十六比九、明亮科技感、大字号短句、普通文字与图形可编辑，并为每一页准备面向学生的演讲备注。",
        encoding="utf-8",
    )
    job = create_job("单行来源校验", job_id="single-line-source")
    source_id = ingest_sources(job.id, [str(source)])[0].id
    root = job_dir(job.id)
    plan = DeckPlan.model_validate(
        {
            "title": "单行来源校验",
            "slides": [
                {
                    "id": f"slide-{index:02d}",
                    "title": f"第 {index} 页",
                    "purpose": "验证单行来源精确性",
                    "layout": "content" if index == 3 else "summary",
                    "points": ["来自唯一非空行的内容"],
                    "evidence_refs": [source_id],
                }
                for index in range(1, 9)
            ],
        }
    )

    result = workflow._validate_evidence_traceability(
        root,
        plan,
        read_model(root / "evidence-ledger.json", workflow.EvidenceLedger),
    )

    assert result["status"] == "passed"
    assert result["located_references"] >= 1


def test_review_comments_create_targeted_revision_batch(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    root = job_dir(job_id)
    candidate = root / "deliverables" / "candidate_editable.pptx"
    candidate.write_bytes(b"candidate before revision")
    add_comment(job_id, "slide-03", "把这一页标题改成强调增长结果的结论句")
    add_comment(job_id, "slide-05", "减少文字并突出资源风险")
    batch = queue_revisions(job_id)
    assert batch.slide_ids == ["slide-03", "slide-05"]
    assert load_job(job_id).state == JobState.BLOCKED
    assert not candidate.exists()

    plan_path = root / "planning" / "deck-plan.json"
    plan = read_model(plan_path, DeckPlan)
    plan.slides[2].title = "增长结果已经形成可验证的业务贡献"
    plan.slides[4].title = "资源风险已经收敛为两项明确行动"
    write_json(plan_path, plan)
    complete_revision(job_id, batch.id)
    assert load_job(job_id).state == JobState.FULL_BUILD
    assert set(load_job(job_id).dirty_slides) == {"slide-03", "slide-05"}
    assert not candidate.exists()
    archived = list((root / "history" / "superseded-deliverables").rglob("candidate_editable.pptx"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"candidate before revision"
    receipt = next((root / "reviews" / "delivery-invalidations").glob("*-deliverables.json"))
    assert read_json(receipt)["reason"] == "revision batch queued for deck plan changes"


def test_revision_requires_every_target_slide_to_change(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    first = add_comment(job_id, "slide-03", "修改第三页")
    second = add_comment(job_id, "slide-05", "修改第五页")
    batch = queue_revisions(job_id)
    plan_path = job_dir(job_id) / "planning" / "deck-plan.json"
    plan = read_model(plan_path, DeckPlan)
    plan.slides[2].title = "第三页已经修改"
    write_json(plan_path, plan)

    with pytest.raises(WorkflowError, match="slide-05"):
        complete_revision(job_id, batch.id)

    stored_batch = read_json(job_dir(job_id) / "revisions" / f"{batch.id}.json")
    comments = {item["id"]: item["status"] for item in read_json(job_dir(job_id) / "reviews" / "comments.json")}
    assert stored_batch["status"] == "pending_agent"
    assert comments == {first.id: "queued", second.id: "queued"}
    assert load_job(job_id).state == JobState.BLOCKED


def test_revision_comments_resolve_only_after_successful_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = advance_to_final(tmp_path)
    comment = add_comment(job_id, "slide-03", "重写第三页结论")
    batch = queue_revisions(job_id)
    plan_path = job_dir(job_id) / "planning" / "deck-plan.json"
    plan = read_model(plan_path, DeckPlan)
    plan.slides[2].title = "第三页结论已经完成重写"
    write_json(plan_path, plan)
    complete_revision(job_id, batch.id)

    comments_path = job_dir(job_id) / "reviews" / "comments.json"
    assert read_json(comments_path)[0]["status"] == "applied"
    assert read_json(job_dir(job_id) / "revisions" / f"{batch.id}.json")["status"] == "applied"

    original_render = workflow.render_html

    def fail_rebuild(html_path: Path, pptx_path: Path, preview_dir: Path) -> dict[str, object]:
        raise RenderError("rebuild renderer failed")

    monkeypatch.setattr(workflow, "render_html", fail_rebuild)
    failed = run_job(job_id)

    assert failed["state"] == JobState.BLOCKED.value
    assert read_json(comments_path)[0]["status"] == "applied"
    assert read_json(job_dir(job_id) / "revisions" / f"{batch.id}.json")["status"] == "applied"

    monkeypatch.setattr(workflow, "render_html", original_render)
    rebuilt = resume_job(job_id)

    assert rebuilt["state"] == JobState.FINAL_REVIEW.value
    assert read_json(comments_path) == [
        {
            **comment.model_dump(mode="json"),
            "status": "resolved",
        }
    ]
    stored_batch = read_json(job_dir(job_id) / "revisions" / f"{batch.id}.json")
    assert stored_batch["status"] == "rebuilt"
    assert stored_batch["rebuilt_at"] is not None


def test_comment_after_final_approval_revokes_approval(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    approve_gate(job_id, "final")
    assert load_job(job_id).approvals["final"] is True

    add_comment(job_id, "slide-03", "请突出这一页的核心结论")

    assert load_job(job_id).approvals["final"] is False


@pytest.mark.parametrize("artifact", ["deck", "plan"])
def test_export_revokes_final_approval_when_approved_artifacts_change(
    tmp_path: Path, artifact: str
) -> None:
    job_id = advance_to_final(tmp_path)
    approve_gate(job_id, "final")
    root = job_dir(job_id)
    if artifact == "deck":
        with (root / "build" / "deck.pptx").open("ab") as stream:
            stream.write(b"tampered")
    else:
        plan_path = root / "planning" / "deck-plan.json"
        plan = read_model(plan_path, DeckPlan)
        plan.title = "未经重新确认的标题"
        write_json(plan_path, plan)

    with pytest.raises(WorkflowError, match="approved final artifacts changed"):
        export_job(job_id, skip_powerpoint=True)

    job = load_job(job_id)
    assert job.approvals["final"] is False
    assert "final" not in job.approval_hashes
    assert not (root / "deliverables" / "candidate_editable.pptx").exists()


def test_final_approval_binds_motion_batch(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    root = job_dir(job_id)
    motion_path = root / "edits" / "motion-final.json"
    motion_path.write_text('{"status":"original"}', encoding="utf-8")
    approve_gate(job_id, "final")
    motion_path.write_text('{"status":"changed"}', encoding="utf-8")

    with pytest.raises(WorkflowError, match="approved final artifacts changed"):
        export_job(job_id, skip_powerpoint=True)

    assert load_job(job_id).approvals["final"] is False


def test_export_includes_motion_audit(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    root = job_dir(job_id)
    batch = PptxEditBatch(
        id="motion-final",
        job_id=job_id,
        input_path="build/deck.pptx",
        output_path="build/deck.pptx",
        input_sha256="before",
        output_sha256="after",
        operations=[
            PptxEditOperation(
                id="slide-01-transition",
                action="set",
                target="/slide[1]",
                properties={"transition": "fade-slow"},
            )
        ],
        validation={"status": "passed"},
    )
    write_json(root / "edits" / "motion-final.json", batch)
    approve_gate(job_id, "final")

    manifest = export_job(job_id, skip_powerpoint=True)

    assert manifest.qa["motion"] == {"status": "passed", "operations": 1, "warnings": []}
    assert manifest.engines["postprocessor"] == "OfficeCLI motion"
    assert (root / "deliverables" / "motion-batch.json").is_file()


def test_export_includes_native_powerpoint_audit_and_animated_previews(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    root = job_dir(job_id)
    preview = root / "build" / "animated-previews" / "slide-06.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"native animation preview")
    write_json(
        root / "edits" / "native-powerpoint.json",
        {
            "schema_version": 1,
            "job_id": job_id,
            "input_path": "build/deck.pptx",
            "input_sha256": "before",
            "output_sha256": sha256(root / "build" / "deck.pptx"),
            "plan": {
                "version": 1,
                "effects": [{"slide": 6, "shape": "LIGHT", "effect": "motion-right"}],
                "transitions": [{"slide": 8, "type": "morph"}],
                "previews": [{"id": "slide-06", "slides": [6]}],
            },
            "report": {
                "status": "passed",
                "previews": [{"id": "slide-06", "sha256": sha256(preview)}],
            },
        },
    )
    approve_gate(job_id, "final")

    manifest = export_job(job_id, skip_powerpoint=True)

    assert manifest.qa["native_powerpoint"] == {
        "status": "passed",
        "effects": 1,
        "carousels": 0,
        "morph_links": 0,
        "transitions": 1,
        "previews": 1,
    }
    assert manifest.engines["postprocessor"] == "PowerPoint native"
    assert (root / "deliverables" / "native-powerpoint.json").is_file()
    assert (root / "deliverables" / "animated-previews" / "slide-06.mp4").is_file()
    assert "animated-previews" in manifest.outputs


def test_export_rejects_animated_preview_changed_after_final_approval(tmp_path: Path) -> None:
    job_id = advance_to_final(tmp_path)
    root = job_dir(job_id)
    preview = root / "build" / "animated-previews" / "slide-06.mp4"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"approved preview")
    write_json(
        root / "edits" / "native-powerpoint.json",
        {
            "job_id": job_id,
            "output_sha256": sha256(root / "build" / "deck.pptx"),
            "plan": {"effects": [], "transitions": [], "previews": [{"id": "slide-06"}]},
            "report": {"status": "passed", "previews": [{"id": "slide-06", "sha256": sha256(preview)}]},
        },
    )
    approve_gate(job_id, "final")
    preview.write_bytes(b"changed preview")

    with pytest.raises(WorkflowError, match="approved final artifacts changed"):
        export_job(job_id, skip_powerpoint=True)


@pytest.mark.parametrize("comment_status", ["open", "queued"])
def test_export_rejects_active_review_comments(tmp_path: Path, comment_status: str) -> None:
    job_id = advance_to_final(tmp_path)
    approve_gate(job_id, "final")
    comments_path = job_dir(job_id) / "reviews" / "comments.json"
    comment = ReviewComment(
        id=f"comment-{comment_status}",
        slide_id="slide-03",
        body="仍需处理的修改意见",
        status=comment_status,
    )
    write_json(comments_path, [comment.model_dump(mode="json")])

    with pytest.raises(WorkflowError):
        export_job(job_id, skip_powerpoint=True)

    assert not (job_dir(job_id) / "deliverables" / "final_editable.pptx").exists()


def test_failed_powerpoint_roundtrip_preserves_existing_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id = advance_to_final(tmp_path)
    approve_gate(job_id, "final")
    root = job_dir(job_id)
    delivery = root / "deliverables"
    (delivery / "final_editable.pptx").write_bytes(b"previous editable deck")
    (delivery / "final.pdf").write_bytes(b"previous pdf")
    (delivery / "build-manifest.json").write_text('{"status":"previous"}', encoding="utf-8")
    (delivery / "slides" / "old-preview.png").write_bytes(b"previous preview")
    before = {
        path.relative_to(delivery).as_posix(): path.read_bytes()
        for path in delivery.rglob("*")
        if path.is_file()
    }
    calls: list[tuple[Path, Path]] = []

    def fail_roundtrip(pptx: Path, output_dir: Path) -> dict[str, object]:
        calls.append((pptx, output_dir))
        raise RenderError("PowerPoint COM failed: 0x80070570")

    monkeypatch.setattr("aippt_studio.workflow.powerpoint_roundtrip", fail_roundtrip)

    with pytest.raises(RenderError, match="0x80070570"):
        export_job(job_id)

    after = {
        path.relative_to(delivery).as_posix(): path.read_bytes()
        for path in delivery.rglob("*")
        if path.is_file()
    }
    assert calls and calls[0][0].name == "final_editable.pptx"
    assert before == after
    assert load_job(job_id).state == JobState.FINAL_REVIEW
    assert not list((root / "build").glob("export-*"))
    assert not list((root / "build").glob("deliverables-backup-*"))


def test_resume_continues_after_optional_route_capability_is_restored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    monkeypatch.setenv("AIPPT_UPSTREAM_DIR", str(upstream))
    job = create_job("客户模板复用", job_id="resume-route", route=ProductionRoute.TEMPLATE)
    ingest_sources(job.id, [str(sample_source(tmp_path))])

    blocked = run_job(job.id)
    assert blocked["state"] == JobState.BLOCKED.value
    assert blocked["resume_state"] == JobState.INTAKE.value

    (upstream / "pptx-automizer").mkdir()
    resumed = resume_job(job.id)

    assert resumed["state"] == JobState.BRIEF_REVIEW.value
    assert resumed["resume_state"] is None
    assert resumed["blocked_reason"] is None


def test_insufficient_material_can_be_supplemented_and_resumed(tmp_path: Path) -> None:
    short = tmp_path / "short.txt"
    short.write_text("信息不足", encoding="utf-8")
    enough = sample_source(tmp_path)
    job = create_job("补充材料", job_id="supplement-material")
    ingest_sources(job.id, [str(short)])

    blocked = run_job(job.id)
    assert blocked["state"] == JobState.BLOCKED.value
    assert blocked["resume_state"] == JobState.INTAKE.value

    ingest_sources(job.id, [str(enough)])
    resumed = resume_job(job.id)
    assert resumed["state"] == JobState.BRIEF_REVIEW.value
    assert resumed["blocked_reason"] is None


def test_resume_restores_intermediate_state_after_tool_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capability = {"ready": True}
    monkeypatch.setattr(
        "aippt_studio.workflow.route_ready",
        lambda route: (capability["ready"], "ready" if capability["ready"] else "missing: renderer"),
    )
    job = create_job("工具恢复", job_id="resume-tool")
    ingest_sources(job.id, [str(sample_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")

    capability["ready"] = False
    blocked = run_job(job.id)
    assert blocked["state"] == JobState.BLOCKED.value
    assert blocked["resume_state"] == JobState.PROTOTYPE_REVIEW.value

    capability["ready"] = True
    resumed = resume_job(job.id)

    assert resumed["state"] == JobState.PROTOTYPE_REVIEW.value
    assert resumed["resume_state"] is None
    assert resumed["artifacts"]["prototype"] is not None


def test_run_missing_job_does_not_create_an_empty_job_directory() -> None:
    missing = jobs_root() / "does-not-exist"
    assert not missing.exists()

    with pytest.raises(FileNotFoundError):
        run_job("does-not-exist")

    assert not missing.exists()


def test_run_blocks_when_job_state_and_delivery_artifacts_disagree() -> None:
    job = create_job("状态不一致", job_id="inconsistent-state")
    (job_dir(job.id) / "deliverables" / "final_editable.pptx").write_bytes(b"premature final")

    result = run_job(job.id)

    assert result["state"] == JobState.BLOCKED.value
    assert "final_artifacts_before_final_review" in str(result["blocked_reason"])


def test_resume_rebuilds_complete_prototype_after_partial_render_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = create_job("样张失败恢复", job_id="partial-prototype")
    ingest_sources(job.id, [str(sample_source(tmp_path))])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    root = job_dir(job.id)
    original_render = workflow.render_html

    def leave_partial_pptx(html_path: Path, pptx_path: Path, preview_dir: Path) -> dict[str, object]:
        pptx_path.parent.mkdir(parents=True, exist_ok=True)
        pptx_path.write_bytes(b"partial prototype")
        raise RenderError("prototype renderer stopped halfway")

    monkeypatch.setattr(workflow, "render_html", leave_partial_pptx)
    failed = run_job(job.id)

    assert failed["state"] == JobState.BLOCKED.value
    assert failed["resume_state"] == JobState.PROTOTYPE_REVIEW.value
    assert not (root / "build" / "prototype.pptx").exists()
    assert not [path for path in (root / "build").glob("prototype-*") if path.is_dir()]

    monkeypatch.setattr(workflow, "render_html", original_render)
    resumed = resume_job(job.id)
    plan = read_model(root / "planning" / "deck-plan.json", DeckPlan)
    expected_ids = {plan.slides[0].id, plan.slides[len(plan.slides) // 2].id, plan.slides[-1].id}

    assert resumed["state"] == JobState.PROTOTYPE_REVIEW.value
    assert (root / "build" / "prototype.pptx").read_bytes() != b"partial prototype"
    assert {path.stem for path in (root / "build" / "prototype-previews").glob("*.png")} == expected_ids
    assert (root / "build" / "prototype-montage.png").is_file()


def test_job_files_do_not_capture_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret-value")
    job = create_job("密钥脱敏", job_id="secrets")
    ingest_sources(job.id, [str(sample_source(tmp_path))])
    run_job(job.id)
    all_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in job_dir(job.id).rglob("*") if path.is_file())
    assert "super-secret-value" not in all_text
