from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aippt_studio import cli, native_powerpoint
from aippt_studio.models import JobState
from aippt_studio.storage import job_dir, load_job, save_job
from aippt_studio.workflow import create_job


def sample_plan() -> dict[str, object]:
    return {
        "version": 1,
        "replace_existing": True,
        "renames": [{"slide": 25, "from": "Picture 3", "to": "!!P3_IMAGE"}],
        "effects": [
            {
                "slide": 20,
                "shape": "TASK6_LIGHT",
                "effect": "fade",
                "trigger": "onClick",
                "duration": 0.4,
                "delay": 0,
                "repeat": 2,
                "autoreverse": False,
            },
            {
                "slide": 20,
                "shape": "TASK6_LIGHT",
                "effect": "motion-right",
                "trigger": "afterPrevious",
                "duration": 2.4,
                "motion_by_x": 0.55,
            },
        ],
        "transitions": [{"slide": 26, "type": "morph", "duration": 1.4}],
        "previews": [{"id": "task-06", "slides": [20], "seconds_per_slide": 5}],
    }


def test_validate_native_plan_normalizes_optional_fields() -> None:
    plan = native_powerpoint.validate_native_plan(sample_plan())

    assert plan["effects"][1]["delay"] == 0
    assert plan["effects"][1]["autoreverse"] is False
    assert plan["transitions"][0]["advance_on_click"] is True
    assert plan["previews"][0]["vertical_resolution"] == 1080
    assert plan["previews"][0]["frames_per_second"] == 30


@pytest.mark.parametrize("direction", ["left", "right", "up", "down"])
def test_validate_native_plan_accepts_wipe_directions(direction: str) -> None:
    plan = sample_plan()
    plan["effects"] = [
        {
            "slide": 1,
            "shape": "PHOTO",
            "effect": "wipe",
            "direction": direction,
        }
    ]

    normalized = native_powerpoint.validate_native_plan(plan)

    assert normalized["effects"][0]["direction"] == direction


@pytest.mark.parametrize(
    ("effect", "direction"),
    [("fly", "left"), ("zoom", "in_slightly"), ("swivel", "horizontal")],
)
def test_validate_native_plan_accepts_effect_specific_directions(effect: str, direction: str) -> None:
    plan = sample_plan()
    plan["effects"] = [
        {
            "slide": 1,
            "shape": "PHOTO",
            "effect": effect,
            "direction": direction,
        }
    ]

    normalized = native_powerpoint.validate_native_plan(plan)

    assert normalized["effects"][0]["direction"] == direction


@pytest.mark.parametrize("effect", ["fly", "zoom", "swivel", "float", "bounce"])
def test_validate_native_plan_accepts_distinct_entrance_families(effect: str) -> None:
    plan = sample_plan()
    plan["effects"] = [{"slide": 1, "shape": "PHOTO", "effect": effect}]

    normalized = native_powerpoint.validate_native_plan(plan)

    assert normalized["effects"][0]["effect"] == effect


def test_compile_carousel_creates_ordered_cross_fade_loop() -> None:
    plan = sample_plan()
    plan["effects"] = []
    plan["carousels"] = [
        {
            "slide": 23,
            "shapes": ["PHOTO_1", "PHOTO_2", "PHOTO_3"],
            "duration": 0.4,
            "hold_seconds": 1.2,
            "loops": 2,
            "return_to_first": True,
        }
    ]

    compiled = native_powerpoint.compile_native_plan(plan)

    assert compiled["carousels"] == []
    assert len(compiled["effects"]) == 12
    assert compiled["effects"][0] == {
        "slide": 23,
        "shape": "PHOTO_1",
        "effect": "fade",
        "trigger": "onClick",
        "duration": 0.4,
        "delay": 1.2,
        "autoreverse": False,
        "exit": True,
    }
    assert compiled["effects"][1]["shape"] == "PHOTO_2"
    assert compiled["effects"][1]["trigger"] == "withPrevious"
    assert compiled["effects"][-1]["shape"] == "PHOTO_1"
    assert compiled["effects"][-1]["exit"] is False


def test_compile_morph_link_assigns_shared_identity_and_transition() -> None:
    plan = sample_plan()
    plan["renames"] = []
    plan["transitions"] = []
    plan["morph_links"] = [
        {
            "from_slide": 25,
            "from_shape": "Picture 3",
            "to_slide": 26,
            "to_shape": "Picture 7",
            "identity": "TASK8_IMAGE",
            "duration": 1.4,
        }
    ]

    compiled = native_powerpoint.compile_native_plan(plan)

    assert compiled["morph_links"] == []
    assert compiled["renames"] == [
        {"slide": 25, "from": "Picture 3", "to": "!!TASK8_IMAGE"},
        {"slide": 26, "from": "Picture 7", "to": "!!TASK8_IMAGE"},
    ]
    assert compiled["transitions"] == [
        {
            "slide": 26,
            "type": "morph",
            "duration": 1.4,
            "advance_on_click": True,
        }
    ]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda plan: plan["effects"][0].update(effect="spin"), "effect must be one of"),
        (lambda plan: plan["effects"][0].update(direction="left"), "not supported for fade"),
        (
            lambda plan: plan["effects"][0].update(effect="wipe", direction="diagonal"),
            "direction must be one of",
        ),
        (lambda plan: plan["previews"][0].update(id="../escape"), "only ASCII"),
        (lambda plan: plan["previews"][0].update(slides=[20, 20]), "unique and ascending"),
        (
            lambda plan: plan.update(
                carousels=[{"slide": 23, "shapes": ["A", "B"], "loops": 2, "return_to_first": False}]
            ),
            "needs return_to_first",
        ),
        (
            lambda plan: plan.update(
                morph_links=[
                    {
                        "from_slide": 25,
                        "from_shape": "A",
                        "to_slide": 27,
                        "to_shape": "B",
                        "identity": "IMAGE",
                    }
                ]
            ),
            "adjacent slides",
        ),
        (lambda plan: plan.update(layout_operations=[]), "unsupported native plan fields"),
    ],
)
def test_validate_native_plan_rejects_unsafe_or_non_native_contract_fields(change, match: str) -> None:
    plan = sample_plan()
    change(plan)

    with pytest.raises(ValueError, match=match):
        native_powerpoint.validate_native_plan(plan)


def test_build_apply_command_passes_paths_as_distinct_arguments(tmp_path: Path) -> None:
    command = native_powerpoint.build_apply_command(
        "powershell.exe",
        tmp_path / "powerpoint native.ps1",
        tmp_path / "输入 文件.pptx",
        tmp_path / "输出 文件.pptx",
        tmp_path / "合同.json",
    )

    assert command[:6] == [
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
    ]
    assert command[-8:] == [
        "-Mode",
        "Apply",
        "-InputPptx",
        str(tmp_path / "输入 文件.pptx"),
        "-OutputPptx",
        str(tmp_path / "输出 文件.pptx"),
        "-ContractPath",
        str(tmp_path / "合同.json"),
    ]


def test_build_probe_command_exposes_all_diagnostic_inputs(tmp_path: Path) -> None:
    command = native_powerpoint.build_probe_command(
        "powershell.exe",
        tmp_path / "native.ps1",
        tmp_path / "baseline.pptx",
        tmp_path / "candidate.pptx",
        tmp_path / "probe",
    )

    assert command[-8:] == [
        "-Mode",
        "Probe",
        "-BaselinePptx",
        str(tmp_path / "baseline.pptx"),
        "-CandidatePptx",
        str(tmp_path / "candidate.pptx"),
        "-ProbeOutputDir",
        str(tmp_path / "probe"),
    ]


def test_windows_probe_roundtrip_exports_png_without_pdf():
    script = native_powerpoint.PROJECT_ROOT / "scripts" / "powerpoint_native.ps1"
    text = script.read_text(encoding="utf-8")
    probe = text.split("function Invoke-ProbeMode {", 1)[1].split("\ntry {", 1)[0]

    assert "probe.pdf" not in probe
    assert ".SaveAs(" not in probe
    assert "artifacts.pdf" not in probe
    assert "$script:report.artifacts.slides = $slidesPath" in probe
    assert '$script:presentation.Export($slidesPath, "PNG", 1920, 1080)' in probe
    assert "$script:presentation.SaveCopyAs($roundtripPath, $ppSaveAsOpenXMLPresentation)" in probe
    assert '$script:powerPoint.Presentations.Open($roundtripPath' in probe


def test_run_native_classifies_structured_com_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "status": "failed",
        "failed_stage": "candidate_open",
        "error": {"message": "文件损坏", "hresult": "0x80070570"},
        "stages": [
            {"name": "startup", "status": "passed"},
            {"name": "baseline_open", "status": "passed"},
            {"name": "candidate_open", "status": "failed"},
        ],
    }
    monkeypatch.setattr(
        native_powerpoint.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, json.dumps(payload, ensure_ascii=False), ""),
    )

    with pytest.raises(native_powerpoint.NativePowerPointError) as captured:
        native_powerpoint._run_native(["powershell"], 30)

    assert captured.value.stage == "candidate_open"
    assert captured.value.report["error"]["hresult"] == "0x80070570"
    assert "文件损坏" in str(captured.value)


def test_apply_native_plan_returns_preview_hashes_inside_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_pptx = tmp_path / "jobs" / "order-1" / "build" / "input.pptx"
    output_pptx = input_pptx.with_name("native.pptx")
    input_pptx.parent.mkdir(parents=True)
    input_pptx.write_bytes(b"source")
    script = tmp_path / "powerpoint_native.ps1"
    script.write_text("# test", encoding="utf-8")

    def fake_run(command: list[str], timeout: int) -> dict[str, object]:
        output_pptx.write_bytes(b"native")
        preview = input_pptx.parent / "animated-previews" / "task-06.mp4"
        preview.parent.mkdir()
        preview.write_bytes(b"video")
        return {
            "status": "passed",
            "output": str(output_pptx),
            "previews": [{"id": "task-06", "path": str(preview), "slides": [20]}],
        }

    monkeypatch.setattr(native_powerpoint, "_run_native", fake_run)

    report = native_powerpoint.apply_native_plan(
        input_pptx,
        output_pptx,
        sample_plan(),
        powershell="powershell.exe",
        script=script,
    )

    preview = report["previews"][0]
    assert Path(preview["path"]) == input_pptx.parent / "animated-previews" / "task-06.mp4"
    assert preview["sha256"] == native_powerpoint._sha256(Path(preview["path"]))
    assert preview["bytes"] == 5


def test_apply_native_plan_never_overwrites_source(tmp_path: Path) -> None:
    source = tmp_path / "source.pptx"
    source.write_bytes(b"source")

    with pytest.raises(ValueError, match="must not overwrite"):
        native_powerpoint.apply_native_plan(source, source, sample_plan())


def test_apply_native_plan_rejects_missing_requested_preview(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "build" / "source.pptx"
    output = source.with_name("native.pptx")
    source.parent.mkdir()
    source.write_bytes(b"source")
    script = tmp_path / "powerpoint_native.ps1"
    script.write_text("# test", encoding="utf-8")

    def fake_run(command: list[str], timeout: int) -> dict[str, object]:
        output.write_bytes(b"native")
        return {"status": "passed", "output": str(output), "previews": []}

    monkeypatch.setattr(native_powerpoint, "_run_native", fake_run)

    with pytest.raises(native_powerpoint.NativePowerPointError) as captured:
        native_powerpoint.apply_native_plan(
            source,
            output,
            sample_plan(),
            powershell="powershell.exe",
            script=script,
        )

    assert captured.value.stage == "create_video"


def test_apply_job_native_plan_replaces_deck_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = create_job("原生动画", job_id="native-job")
    root = job_dir(job.id)
    deck = root / "build" / "deck.pptx"
    deck.write_bytes(b"before")
    old_preview = root / "build" / "animated-previews" / "old.mp4"
    old_preview.parent.mkdir(parents=True)
    old_preview.write_bytes(b"old")
    candidate = root / "deliverables" / "candidate_editable.pptx"
    candidate.write_bytes(b"candidate before native edit")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(sample_plan(), ensure_ascii=False), encoding="utf-8")
    job.state = JobState.FINAL_REVIEW
    job.approvals["final"] = True
    job.approval_hashes["final"] = {"deck_pptx": "old"}
    save_job(job)

    def fake_apply(input_pptx: Path, output_pptx: Path, plan: dict[str, object]) -> dict[str, object]:
        assert input_pptx == deck
        output_pptx.write_bytes(b"after")
        preview = root / "build" / "animated-previews" / "task-06.mp4"
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b"video")
        return {"status": "passed", "previews": [{"id": "task-06", "path": str(preview)}]}

    monkeypatch.setattr(native_powerpoint, "apply_native_plan", fake_apply)

    result = native_powerpoint.apply_job_native_plan(job.id, plan_path)

    assert deck.read_bytes() == b"after"
    assert not old_preview.exists()
    assert (root / "build" / "animated-previews" / "task-06.mp4").is_file()
    assert result["output_sha256"] == native_powerpoint.sha256(deck)
    assert (root / "edits" / "native-powerpoint.json").is_file()
    assert load_job(job.id).approvals["final"] is False
    assert "final" not in load_job(job.id).approval_hashes
    assert not candidate.exists()
    archived = list((root / "history" / "superseded-deliverables").rglob("candidate_editable.pptx"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"candidate before native edit"


def test_apply_job_native_plan_restores_previous_previews_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job = create_job("原生动画失败", job_id="native-job-failure")
    root = job_dir(job.id)
    deck = root / "build" / "deck.pptx"
    deck.write_bytes(b"before")
    old_preview = root / "build" / "animated-previews" / "old.mp4"
    old_preview.parent.mkdir(parents=True)
    old_preview.write_bytes(b"old")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(sample_plan(), ensure_ascii=False), encoding="utf-8")

    def fail_apply(input_pptx: Path, output_pptx: Path, plan: dict[str, object]) -> dict[str, object]:
        preview = root / "build" / "animated-previews" / "partial.mp4"
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b"partial")
        raise native_powerpoint.NativePowerPointError("create_video", "preview failed")

    monkeypatch.setattr(native_powerpoint, "apply_native_plan", fail_apply)

    with pytest.raises(native_powerpoint.NativePowerPointError, match="preview failed"):
        native_powerpoint.apply_job_native_plan(job.id, plan_path)

    assert deck.read_bytes() == b"before"
    assert old_preview.read_bytes() == b"old"
    assert not (root / "build" / "animated-previews" / "partial.mp4").exists()


def test_probe_powerpoint_command_returns_structured_classification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "baseline.pptx"
    candidate = tmp_path / "candidate.pptx"
    output = tmp_path / "probe"
    baseline.write_bytes(b"baseline")
    candidate.write_bytes(b"candidate")

    def fake_probe(baseline_path: Path, candidate_path: Path, output_path: Path) -> dict[str, object]:
        assert baseline_path == baseline
        assert candidate_path == candidate
        assert output_path == output
        return {
            "status": "passed",
            "classification": "passed",
            "stages": [{"name": "candidate_reopen", "status": "passed"}],
        }

    monkeypatch.setattr(cli, "probe_powerpoint", fake_probe)

    result = CliRunner().invoke(
        cli.app,
        [
            "probe-powerpoint",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--output",
            str(output),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["classification"] == "passed"
    assert payload["artifacts_retained"] is True
    assert payload["report_path"] == str((output / "probe-report.json").resolve())
    assert json.loads((output / "probe-report.json").read_text(encoding="utf-8")) == payload


def test_probe_powerpoint_command_preserves_hresult_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = tmp_path / "baseline.pptx"
    candidate = tmp_path / "candidate.pptx"
    baseline.write_bytes(b"baseline")
    candidate.write_bytes(b"candidate")
    failure = {
        "status": "failed",
        "failed_stage": "candidate_open",
        "classification": "candidate",
        "error": {"message": "文件损坏", "hresult": "0x80070570"},
    }

    def fail_probe(*args, **kwargs):
        raise native_powerpoint.NativePowerPointError("candidate_open", "文件损坏", failure)

    monkeypatch.setattr(cli, "probe_powerpoint", fail_probe)

    result = CliRunner().invoke(
        cli.app,
        [
            "probe-powerpoint",
            "--baseline",
            str(baseline),
            "--candidate",
            str(candidate),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["classification"] == "candidate"
    assert payload["error"]["hresult"] == "0x80070570"
    assert payload["artifacts_retained"] is False
