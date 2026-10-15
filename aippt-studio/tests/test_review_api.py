from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from aippt_studio.ingest import ingest_sources
from aippt_studio.models import DeckPlan, JobState, SlideSpec
from aippt_studio.review_server import create_app
from aippt_studio.storage import job_dir, write_json
from aippt_studio.workflow import approve_gate, create_job, run_job


def test_review_server_is_job_scoped() -> None:
    job = create_job("审稿接口", job_id="review-api")
    client = TestClient(create_app())
    response = client.get(f"/api/jobs/{job.id}")
    assert response.status_code == 200
    assert response.json()["id"] == job.id
    # The local review surface tells a reviewer what the Studio understood;
    # it does not expose an implementation directory as UI content.
    assert response.json()["editorial_decision"] is None
    assert "root" not in response.json()
    assert "work_request" not in response.json()
    missing = client.get("/api/jobs/does-not-exist")
    assert missing.status_code == 404


def test_review_server_explains_a_classified_editorial_decision(tmp_path: Path) -> None:
    job = create_job("正式汇报审稿判断", job_id="review-editorial-decision")
    source = tmp_path / "brief-source.txt"
    source.write_text(
        "\n".join(f"第 {index} 条正式汇报事实材料，包含结论、证据与后续行动。" for index in range(1, 9)),
        encoding="utf-8",
    )
    ingest_sources(job.id, [str(source)])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value

    payload = TestClient(create_app()).get(f"/api/jobs/{job.id}").json()

    decision = payload["editorial_decision"]
    assert decision["status"] == "ready"
    assert decision["change_mode"]["label"] == "新建表达方案"
    assert decision["visual_system"]["label"] == "按本轮内容建立新视觉系统"
    assert "通用颜色" in decision["rule"]


def test_prototype_review_maps_previews_to_actual_sample_slide_ids(tmp_path: Path) -> None:
    job = create_job("样张预览映射", job_id="prototype-preview-map")
    plan = DeckPlan(
        title=job.name,
        slides=[
            SlideSpec(
                id=f"slide-{index:02d}",
                title=f"第 {index} 页",
                purpose="验证样张预览映射",
                layout="content",
                points=[f"第 {index} 页内容"],
            )
            for index in range(1, 10)
        ],
    )
    write_json(job_dir(job.id) / "planning" / "deck-plan.json", plan)
    source = tmp_path / "prototype-source.txt"
    source.write_text("\n".join(f"Evidence line {index} contains enough material for planning and review." for index in range(1, 8)), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value

    client = TestClient(create_app())
    payload = client.get(f"/api/jobs/{job.id}").json()
    expected_ids = ["slide-01", "slide-05", "slide-09"]

    assert payload["prototype_slide_ids"] == expected_ids
    preview_dir = job_dir(job.id) / "build" / "prototype-previews"
    assert {path.stem for path in preview_dir.glob("*.png")} == set(expected_ids)
    for slide_id in expected_ids:
        preview = client.get(f"/files/{job.id}/build/prototype-previews/{slide_id}.png")
        assert preview.status_code == 200


def test_prototype_review_honors_explicit_five_slide_sample(tmp_path: Path) -> None:
    job = create_job("五页样张预览映射", job_id="prototype-preview-five")
    requested_ids = ["slide-01", "slide-03", "slide-08", "slide-10", "slide-12"]
    plan = DeckPlan(
        title=job.name,
        prototype_slide_ids=requested_ids,
        slides=[
            SlideSpec(
                id=f"slide-{index:02d}",
                title=f"第 {index} 页",
                purpose="验证五页样张预览映射",
                layout="content",
                points=[f"第 {index} 页内容"],
            )
            for index in range(1, 15)
        ],
    )
    write_json(job_dir(job.id) / "planning" / "deck-plan.json", plan)
    source = tmp_path / "prototype-five-source.txt"
    source.write_text("\n".join(f"Evidence line {index} contains enough material for planning and review." for index in range(1, 8)), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value

    client = TestClient(create_app())
    payload = client.get(f"/api/jobs/{job.id}").json()
    assert payload["prototype_slide_ids"] == requested_ids
    preview_dir = job_dir(job.id) / "build" / "prototype-previews"
    assert {path.stem for path in preview_dir.glob("*.png")} == set(requested_ids)


def test_review_api_exposes_native_animated_previews() -> None:
    job = create_job("动画审稿映射", job_id="animated-preview-map")
    preview = job_dir(job.id) / "build" / "animated-previews" / "slide-20.mp4"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"native-powerpoint-preview")

    client = TestClient(create_app())
    payload = client.get(f"/api/jobs/{job.id}").json()

    assert payload["animated_previews"] == {
        "slide-20": "build/animated-previews/slide-20.mp4"
    }
    response = client.get(f"/files/{job.id}/build/animated-previews/slide-20.mp4")
    assert response.status_code == 200


def test_review_api_maps_preview_contract_slide_numbers_to_deck_ids() -> None:
    job = create_job("动画合同页码映射", job_id="animated-contract-map")
    plan = DeckPlan(
        title=job.name,
        slides=[
            SlideSpec(
                id=f"slide-{index:02d}",
                title=f"第 {index} 页",
                purpose="验证动态预览页码映射",
                layout="content",
                points=["测试内容"],
            )
            for index in range(1, 21)
        ],
    )
    root = job_dir(job.id)
    write_json(root / "planning" / "deck-plan.json", plan)
    preview = root / "build" / "animated-previews" / "task-08.mp4"
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_bytes(b"morph-preview")
    write_json(
        root / "edits" / "native-powerpoint.json",
        {
            "plan": {"previews": [{"id": "task-08", "slides": [19, 20]}]},
            "report": {"status": "passed"},
        },
    )

    payload = TestClient(create_app()).get(f"/api/jobs/{job.id}").json()

    assert payload["animated_previews"] == {
        "slide-19": "build/animated-previews/task-08.mp4",
        "slide-20": "build/animated-previews/task-08.mp4",
    }
