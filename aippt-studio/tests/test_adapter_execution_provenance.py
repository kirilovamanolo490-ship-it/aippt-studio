from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from aippt_studio.adapter_contracts import AdapterBuildRequest, AdapterStatus, CapabilityMaturity
from aippt_studio.adapters.base import ContractProcessAdapter
from aippt_studio.adapters.template import TemplateAdapter
from aippt_studio.models import ProductionRoute


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_checkout(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "fixture@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Fixture"], check=True)
    (path / "README.md").write_text("fixture", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "fixture"], check=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", "https://example.invalid/fixture.git"], check=True)
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_template_maturity_requires_the_accepted_package_lock_and_node_bridge(tmp_path: Path) -> None:
    project, upstream = tmp_path / "studio", tmp_path / "github"
    (upstream / "pptx-automizer").mkdir(parents=True)
    command = project / "scripts/adapters/template_adapter.py"
    runner = project / "node/template_adapter.mjs"
    package = project / "node_modules/pptx-automizer/package.json"
    for path, content in (
        (command, "# bridge\n"),
        (runner, "// runner\n"),
        (package, json.dumps({"version": "0.8.2"})),
        (project / "package-lock.json", "lock-v1\n"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    acceptance = project / "adapter-acceptance/template.json"
    acceptance.parent.mkdir()
    acceptance.write_text(json.dumps({
        "status": "passed", "engine": "pptx-automizer", "reviewed_commit": "runtime-reviewed",
        "runtime": {"package": "pptx-automizer", "version": "0.8.2", "lock_file": "package-lock.json", "lock_sha256": _sha(project / "package-lock.json")},
        "bridge": {"command": "template_adapter.py", "command_sha256": _sha(command), "node_runner": "node/template_adapter.mjs", "node_runner_sha256": _sha(runner)},
    }), encoding="utf-8")

    adapter = TemplateAdapter(project_root=project, upstream_root=upstream, reviewed_commit="runtime-reviewed")
    assert adapter.maturity() == CapabilityMaturity.AUTOMATIC_READY

    (project / "package-lock.json").write_text("lock-v2\n", encoding="utf-8")
    assert adapter.maturity() == CapabilityMaturity.ACCEPTANCE_TESTED


def test_direct_source_adapter_detects_checkout_mutation_after_bridge_returns(tmp_path: Path) -> None:
    project = tmp_path / "studio"
    upstream = tmp_path / "github/image/codex-ppt-skill"
    (upstream / "skills/codex-ppt/scripts").mkdir(parents=True)
    (upstream / "skills/codex-ppt/scripts/assemble_ppt.py").write_text("# upstream entry\n", encoding="utf-8")
    revision = _git_checkout(upstream)
    command = project / "scripts/adapters/bridge.py"
    command.parent.mkdir(parents=True)
    command.write_text(
        "\n".join([
            "import json, pathlib, sys",
            f"pathlib.Path({str(upstream / 'README.md')!r}).write_text('mutated', encoding='utf-8')",
            "result = pathlib.Path(sys.argv[sys.argv.index('--result') + 1])",
            f"result.write_text(json.dumps({{'status':'succeeded','receipt':{{'engine':'codex-ppt-skill','version':'{revision[:12]}'}},'editability':'image_based','artifacts':[]}}), encoding='utf-8')",
        ]),
        encoding="utf-8",
    )
    acceptance = project / "adapter-acceptance/visual.json"
    acceptance.parent.mkdir()
    acceptance.write_text(json.dumps({
        "status": "passed", "engine": "codex-ppt-skill", "reviewed_commit": revision,
        "bridge": {"command": "bridge.py", "command_sha256": _sha(command)},
    }), encoding="utf-8")
    adapter = ContractProcessAdapter(
        route=ProductionRoute.VISUAL,
        name="codex-ppt-skill",
        version=revision[:12],
        command_environment="AIPPT_TEST_ADAPTER",
        acceptance_file=acceptance,
        upstream_path=upstream,
        reviewed_commit=revision,
        default_command=command,
    )
    assert adapter.maturity() == CapabilityMaturity.AUTOMATIC_READY
    job = tmp_path / "job"
    result = adapter.build(AdapterBuildRequest(job_id="fixture", route=ProductionRoute.VISUAL, brief="brief.json", deck_plan="deck-plan.json", evidence_ledger="evidence-ledger.json"), job)

    assert result.status == AdapterStatus.FAILED
    assert result.failure is not None and result.failure.category == "source_repository_modified"
    assert result.receipt is not None
    assert result.receipt.provenance["execution_kind"] == "direct_checked_out_source"
