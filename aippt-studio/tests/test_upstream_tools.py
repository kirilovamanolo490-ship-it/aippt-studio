from __future__ import annotations

import json
from pathlib import Path

import pytest

from aippt_studio import upstream_tools as tools
from aippt_studio.assets.acquisition import AssetAcquisitionError, add_asset_need, import_upstream_asset
from aippt_studio.html_deck import _image_uri
from aippt_studio.ingest import ingest_sources
from aippt_studio.native_scene import set_native_scene
from aippt_studio.models import JobState
from aippt_studio.storage import job_dir, load_job, save_job
from aippt_studio.workflow import create_job, run_job, approve_gate


def test_reject_resource_traversal_and_symlink(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("private")
    (root / "link").symlink_to(outside)
    for path in ("../private.txt", "link", ".git/config", str(outside)):
        with pytest.raises(ValueError):
            tools._inside(root, path)


def test_dirty_source_stops_execution_before_output(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "upstream_report", lambda **kw: [{"source_present": True, "verification": {"matches_lock": False}}])
    with pytest.raises(ValueError, match="dirty"):
        tools.invoke("GSAP", "timeline", {}, tmp_path / "call")
    assert not (tmp_path / "call").exists()


def test_executable_upstream_operations_reject_an_unverified_node(monkeypatch):
    monkeypatch.setattr(tools, "probe_node_runtime", lambda: {"status": "failed", "reason": "foreign native executable"})
    with pytest.raises(ValueError, match="host-native Node"):
        tools._host_node()


def test_icon_respects_collection_license_and_dimensions(tmp_path):
    (tmp_path / "json").mkdir()
    (tmp_path / "json/demo.json").write_text(json.dumps({"width": 24, "height": 32,
        "info": {"license": {"spdx": "ISC"}, "author": {"name": "Artist"}},
        "icons": {"test": {"body": '<path d="M1 1h2v2z" fill="currentColor"/>'}}, "aliases": {"alias": {"parent": "test", "rotate": 1}}}))
    out = tmp_path / "out"
    out.mkdir()
    result = tools._icon(tmp_path, {"name": "demo:test"}, out)
    assert result["asset_license"]["spdx"] == "ISC"
    assert 'viewBox="0 0 24 32"' in (out / "asset.svg").read_text()
    assert _image_uri(out / "asset.svg").startswith("data:image/svg+xml;base64,")
    with pytest.raises(ValueError, match="concrete"):
        tools._icon(tmp_path, {"name": "demo:alias"}, out)


def _job(tmp_path):
    job = create_job("调用测试", job_id="tools-test")
    source = tmp_path / "source.md"
    source.write_text("\n".join(f"第 {i} 项业务结果是客户验证的数据与事实，保留事实支撑设计。" for i in range(9)), encoding="utf-8")
    ingest_sources(job.id, [str(source)])
    run_job(job.id)
    approve_gate(job.id, "brief")
    run_job(job.id)
    return job


def test_bind_native_scene_invalidates_gates_and_rejects_missing_slides(tmp_path):
    job = _job(tmp_path)
    approve_gate(job.id, "prototype")
    root = job_dir(job.id)
    candidate = root / "deliverables" / "candidate_editable.pptx"
    candidate.write_bytes(b"candidate before native scene")
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
             "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    with pytest.raises(ValueError, match="every"):
        set_native_scene(job.id, {"slides": scene["slides"][:-1]})
    result = set_native_scene(job.id, scene)
    assert result["engine"] == "PptxGenJS"
    assert not load_job(job.id).approvals["prototype"]
    assert json.loads((root / "planning/native-scene.json").read_text())["deck_plan_sha256"]
    assert not candidate.exists()
    archived = list((root / "history" / "superseded-deliverables").rglob("candidate_editable.pptx"))
    assert len(archived) == 1
    assert archived[0].read_bytes() == b"candidate before native scene"


@pytest.mark.parametrize(
    ("visible_copy", "code"),
    [
        ("我会为你调整这一页PPT的排版。", "ASSISTANT_REPLY"),
        ("制作说明：请把这个数据再放大。", "INTERNAL_LABEL"),
    ],
)
def test_native_scene_rejects_assistant_and_internal_production_copy(tmp_path, visible_copy, code):
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {
        "slides": [
            {
                "id": spec["id"],
                "elements": [
                    {"type": "text", "text": spec["title"], "options": {"x": 1, "y": 1, "w": 10, "h": 1}},
                    *(
                        [{"type": "text", "text": visible_copy, "options": {"x": 1, "y": 2, "w": 10, "h": 1}}]
                        if index == 0
                        else []
                    ),
                ],
            }
            for index, spec in enumerate(plan["slides"])
        ]
    }

    with pytest.raises(ValueError, match=code):
        set_native_scene(job.id, scene)
    assert not (root / "planning/native-scene.json").exists()


def test_bind_corrected_native_scene_recovers_native_layout_block(tmp_path):
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
             "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    blocked = load_job(job.id)
    blocked.state = JobState.BLOCKED
    blocked.resume_state = JobState.FULL_BUILD
    blocked.blocked_reason = "工具执行失败：native layout needs review/repair; see build/native-qa.json"
    save_job(blocked)

    result = set_native_scene(job.id, scene)
    recovered = load_job(job.id)

    assert result["state"] == JobState.FULL_BUILD.value
    assert recovered.state == JobState.FULL_BUILD
    assert recovered.resume_state is None
    assert recovered.blocked_reason is None


def test_bind_corrected_native_scene_recovers_schema_validation_block(tmp_path):
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
             "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    blocked = load_job(job.id)
    blocked.state = JobState.BLOCKED
    blocked.resume_state = JobState.FULL_BUILD
    blocked.blocked_reason = "工具执行失败：Found 2 validation error(s): chart schema mismatch"
    save_job(blocked)

    result = set_native_scene(job.id, scene)
    recovered = load_job(job.id)

    assert result["state"] == JobState.FULL_BUILD.value
    assert recovered.state == JobState.FULL_BUILD
    assert recovered.resume_state is None
    assert recovered.blocked_reason is None


def test_bind_corrected_native_scene_recovers_native_author_block(tmp_path):
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
             "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    blocked = load_job(job.id)
    blocked.state = JobState.BLOCKED
    blocked.resume_state = JobState.PROTOTYPE_REVIEW
    blocked.blocked_reason = "工具执行失败：native-author failed (exit 1); inspect build/native-author.log"
    save_job(blocked)

    result = set_native_scene(job.id, scene)
    recovered = load_job(job.id)

    assert result["state"] == JobState.PROTOTYPE_REVIEW.value
    assert recovered.state == JobState.PROTOTYPE_REVIEW
    assert recovered.resume_state is None
    assert recovered.blocked_reason is None


def test_bind_corrected_native_scene_recovers_evidence_validation_block(tmp_path):
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
             "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    blocked = load_job(job.id)
    blocked.state = JobState.BLOCKED
    blocked.resume_state = JobState.FULL_BUILD
    blocked.blocked_reason = "证据校验失败：factual slide requires a line locator or image source: slide-03"
    save_job(blocked)

    result = set_native_scene(job.id, scene)
    recovered = load_job(job.id)

    assert result["state"] == JobState.FULL_BUILD.value
    assert recovered.state == JobState.FULL_BUILD
    assert recovered.resume_state is None
    assert recovered.blocked_reason is None


def test_bind_native_scene_does_not_bypass_unrelated_block(tmp_path):
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
             "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    blocked = load_job(job.id)
    blocked.state = JobState.BLOCKED
    blocked.resume_state = JobState.FULL_BUILD
    blocked.blocked_reason = "required assets are not acquired"
    save_job(blocked)

    with pytest.raises(ValueError, match="native-scene requires"):
        set_native_scene(job.id, scene)


def test_local_asset_import_preserves_proof_and_detects_tampering(tmp_path):
    job = _job(tmp_path)
    root = job_dir(job.id)
    need = add_asset_need(job.id, slide_id="slide-03", kind="icon", query="growth", purpose="explain growth")
    call = tmp_path / "call"
    call.mkdir()
    svg = call / "asset.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24"><rect width="20" height="20"/></svg>')
    receipt = {"call_id": "test-call", "status": "succeeded", "operation": "icon", "source_url": "https://github.com/iconify/icon-sets",
        "license": {"spdx": "MIT"}, "result": {"artifact": "asset.svg", "asset_license": {"spdx": "ISC"}},
        "artifacts": [{"path": "asset.svg", "sha256": "0" * 64}]}
    receipt_path = call / "receipt.json"
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(AssetAcquisitionError, match="hash"):
        import_upstream_asset(job.id, need_id=need.id, receipt_path=receipt_path)
    receipt["artifacts"][0]["sha256"] = tools._sha(svg)
    receipt_path.write_text(json.dumps(receipt))
    acquired = import_upstream_asset(job.id, need_id=need.id, receipt_path=receipt_path)
    assert acquired.license == "ISC"
    assert (root / acquired.stored_path).with_suffix(".upstream-receipt.json").is_file()
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    assert f"acquired:{acquired.id}" in plan["slides"][2]["asset_refs"]


def test_all_checked_out_providers_have_resource_operations():
    catalog = tools.tool_catalog()
    expected = {row['id'] for row in tools.upstream_report() if row['acquisition_status'] != 'pending_snapshot' and row.get('lifecycle') != 'retired'}
    assert {row['provider'] for row in catalog} == expected
    assert 'inspect' in next(row for row in catalog if row['provider'] == 'mediabunny')['operations']
    assert all({"read", "search"} <= row["operations"].keys() for row in catalog)
    assert "icon" in next(row for row in catalog if row["provider"] == "iconify-icon-sets")["operations"]


def test_catalog_distinguishes_runtime_source_and_reference_only_paths():
    catalog = {row["provider"]: row for row in tools.tool_catalog()}
    assert catalog["dom-to-pptx"]["execution_profile"]["execution_kind"] == "managed_package_runtime"
    assert catalog["dom-to-pptx"]["execution_profile"]["executed"] is False
    assert catalog["codex-ppt-skill"]["execution_profile"]["execution_kind"] == "direct_checked_out_source_adapter"
    assert tools.provider_execution_profile("remotion")["execution_kind"] == "reference_only"
    assert "not proof" in catalog["pptx-automizer"]["note"]


def test_retired_provider_is_not_advertised_or_invoked(monkeypatch, tmp_path):
    retired = {"id": "old-tool", "lifecycle": "retired", "source_present": False,
        "retirement": {"reason": "removed as unused", "restore_guidance": "restore checkout then reactivate in lock"},
        "acquisition_status": "present_git", "path": None, "invocation": {}, "license": {}, "commit_sha": "old",
        "source_url": "https://github.com/example/old-tool", "role": "retired", "runtime": {}}
    active = {**retired, "id": "active-tool", "lifecycle": "active", "source_present": True,
        "path": str(tmp_path / "active"), "retirement": None}
    def report(**kwargs):
        if kwargs.get("identifier"):
            return [retired if kwargs["identifier"] == "old-tool" else active]
        return [active, retired]
    monkeypatch.setattr(tools, "upstream_report", report)

    assert [row["provider"] for row in tools.tool_catalog()] == ["active-tool"]
    with pytest.raises(ValueError, match="retired.*restore checkout then reactivate"):
        tools._row("old-tool", verify=True)

    monkeypatch.setattr(tools, "workspace_output", lambda path: path)
    with pytest.raises(ValueError, match="retired.*restore checkout then reactivate"):
        tools.invoke("old-tool", "read", {"path": "README.md"}, tmp_path / "call")
    assert not (tmp_path / "call").exists()


def test_iconify_search_returns_concrete_keys_with_per_collection_license(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "workspace_output", lambda output: output)
    root = tmp_path / "icon-sets"
    (root / "json").mkdir(parents=True)
    (root / "collections.json").write_text(json.dumps({
        "flower-pack": {
            "name": "Flower Pack", "author": {"name": "Studio Author"},
            "license": {"title": "CC BY 4.0", "spdx": "CC-BY-4.0", "url": "https://example.test/license"},
        },
        "other-pack": {"name": "Other Pack", "author": "Maker", "license": {"spdx": "MIT"}},
    }), encoding="utf-8")
    (root / "json/flower-pack.json").write_text(
        '{\n\t"icons": {\n\t\t"flower": {\n\t\t\t"body": "<path/>"\n\t\t},\n'
        '\t\t"flower-outline": {\n\t\t\t"body": "<path/>"\n\t\t},\n\t\t"leaf": {\n\t\t\t"body": "<path/>"\n\t\t}\n\t}\n}\n', encoding="utf-8")
    (root / "json/other-pack.json").write_text(
        '{\n\t"icons": {\n\t\t"tiny-flower": {\n\t\t\t"body": "<path/>"\n\t\t}\n\t}\n}\n', encoding="utf-8")
    monkeypatch.setattr(tools, "_row", lambda *args, **kwargs: {
        "path": str(root), "commit_sha": "locked-sha", "source_url": "https://example.test/icon-sets",
        "license": {"spdx": "MIT"}, "source_present": True,
    })

    receipt = tools.invoke("iconify-icon-sets", "search", {"query": "flower", "limit": 2}, tmp_path / "call")

    assert receipt["status"] == "succeeded"
    assert receipt["result"]["search_scope"] == "concrete_icon_keys"
    assert receipt["result"]["total"] == 3
    assert receipt["result"]["truncated"] is True
    assert [row["name"] for row in receipt["result"]["matches"]] == ["flower-pack:flower", "flower-pack:flower-outline"]
    assert receipt["result"]["matches"][0]["license"]["spdx"] == "CC-BY-4.0"
    assert receipt["result"]["matches"][0]["source_commit"] == "locked-sha"


def test_iconify_search_rejects_unknown_collection_and_punctuation_query(tmp_path):
    root = tmp_path / "icon-sets"
    (root / "json").mkdir(parents=True)
    with pytest.raises(ValueError, match="letters or numbers"):
        tools._search_iconify(root, {"query": "!!!"}, 5)
    with pytest.raises(ValueError, match="unknown Iconify collection"):
        tools._search_iconify(root, {"query": "flower", "collection": "missing"}, 5)


def test_successful_call_receipt_marks_its_actual_execution_class(monkeypatch, tmp_path):
    from test_upstream_embedding_provenance import _isolate

    _isolate(monkeypatch, tmp_path)

    def icon(root, request, output):
        (output / "asset.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
        return {"artifact": "asset.svg"}

    monkeypatch.setattr(tools, "_icon", icon)
    receipt = tools.invoke("iconify-icon-sets", "icon", {"name": "demo:test"}, tmp_path / "call")

    assert receipt["execution"]["execution_kind"] == "direct_checked_out_asset_materialization"
    assert receipt["execution"]["executed"] is True


def test_native_dispatch_reports_real_engine_and_scene_source(tmp_path, monkeypatch):
    from aippt_studio import native_scene
    from aippt_studio.renderer import render_html
    job = _job(tmp_path)
    root = job_dir(job.id)
    plan = json.loads((root / "planning/deck-plan.json").read_text())
    scene = {"slides": [{"id": s["id"], "elements": [{"type": "text", "text": s["title"],
        "options": {"x": 1, "y": 1, "w": 10, "h": 1}}]} for s in plan["slides"]]}
    set_native_scene(job.id, scene)
    calls = []
    def fake_scene(root, pptx, previews, *, prototype=False):
        calls.append(prototype)
        render_html(root / ("build/prototype.html" if prototype else "build/deck.html"), pptx, previews)
    monkeypatch.setattr(native_scene, "render_scene", fake_scene)
    run_job(job.id)
    approve_gate(job.id, "prototype")
    run_job(job.id)
    assert calls == [True, False]
    result = json.loads((root / "build/native-adapter-result.json").read_text())
    assert result["receipt"]["engine"] == "PptxGenJS"
    assert result["receipt"]["reviewed_commit"] is None  # must not inherit dom-to-pptx's commit
    assert any(a["path"] == "planning/native-scene.json" for a in result["artifacts"])


@pytest.mark.parametrize("declared,expected", [(False, 2), (True, 3)])
def test_native_compat_only_removes_undeclared_series_axis(tmp_path, declared, expected):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from aippt_studio.native_compat import normalize_native_candidate
    ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"
    xml = f'<c:chartSpace xmlns:c="{ns}"><c:chart><c:plotArea><c:barChart><c:axId val="1"/><c:axId val="2"/><c:axId val="2094734556"/></c:barChart><c:catAx><c:axId val="1"/></c:catAx><c:valAx><c:axId val="2"/></c:valAx>'
    if declared:
        xml += '<c:serAx><c:axId val="2094734556"/></c:serAx>'
    xml += '</c:plotArea></c:chart></c:chartSpace>'
    pptx = tmp_path / "candidate.pptx"
    with ZipFile(pptx, "w") as z:
        z.writestr("ppt/charts/chart1.xml", xml)
    normalize_native_candidate(pptx)
    with ZipFile(pptx) as z:
        chart = ET.fromstring(z.read("ppt/charts/chart1.xml"))
    assert len(chart.findall(".//c:barChart/c:axId", {"c": ns})) == expected


def test_native_compat_honors_explicitly_hidden_pie_point_labels_only(tmp_path):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from aippt_studio.native_compat import normalize_native_candidate

    ns = "http://schemas.openxmlformats.org/drawingml/2006/chart"

    def series(points, parent):
        return f"<c:ser><c:dLbls>{points}{parent}</c:dLbls></c:ser>"

    hidden_points = '<c:dLbl><c:showCatName val="0"/><c:showPercent val="0"/></c:dLbl>' * 2
    all_off = '<c:showCatName val="1"/><c:showPercent val="1"/>'
    mixed_points = ('<c:dLbl><c:showCatName val="0"/><c:showPercent val="0"/></c:dLbl>'
                    '<c:dLbl><c:showCatName val="1"/><c:showPercent val="0"/></c:dLbl>')
    missing_points = ('<c:dLbl><c:showCatName val="0"/><c:showPercent val="0"/></c:dLbl>'
                      '<c:dLbl><c:showCatName val="0"/></c:dLbl>')
    chart = f'''<c:chartSpace xmlns:c="{ns}"><c:chart><c:plotArea>
      <c:pieChart>{series(hidden_points, all_off)}</c:pieChart>
      <c:doughnutChart>{series(hidden_points, all_off)}</c:doughnutChart>
      <c:barChart>{series(hidden_points, all_off)}</c:barChart>
      <c:pieChart>{series(mixed_points, all_off)}</c:pieChart>
      <c:pieChart>{series(missing_points, all_off)}</c:pieChart>
    </c:plotArea></c:chart></c:chartSpace>'''
    pptx = tmp_path / "candidate.pptx"
    with ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/charts/chart1.xml", chart)

    normalize_native_candidate(pptx)

    with ZipFile(pptx) as archive:
        root = ET.fromstring(archive.read("ppt/charts/chart1.xml"))
    plots = root.find(".//c:plotArea", {"c": ns})
    pie, doughnut, bar, mixed, missing = list(plots)

    def parent_flags(chart):
        labels = chart.find("c:ser/c:dLbls", {"c": ns})
        return {flag.tag.rsplit("}", 1)[-1]: flag.get("val") for flag in labels.findall("c:showCatName", {"c": ns}) + labels.findall("c:showPercent", {"c": ns})}

    for chart in (pie, doughnut):
        flags = parent_flags(chart)
        assert flags == {"showCatName": "0", "showPercent": "0"}
    assert parent_flags(bar) == {"showCatName": "1", "showPercent": "1"}
    assert parent_flags(mixed) == {"showCatName": "1", "showPercent": "0"}
    assert parent_flags(missing) == {"showCatName": "0", "showPercent": "1"}


def test_native_compat_preserves_table_fonts_while_removing_charset_hints(tmp_path):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from aippt_studio.native_compat import normalize_native_candidate
    pptx = tmp_path / "table.pptx"
    ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    xml = f'<a:tbl xmlns:a="{ns}"><a:tr><a:tc><a:ea typeface="PingFang SC" charset="-122" pitchFamily="34"/></a:tc></a:tr></a:tbl>'
    with ZipFile(pptx, "w") as z:
        z.writestr("ppt/slides/slide1.xml", '<root>' + xml + '</root>')
    report = normalize_native_candidate(pptx)
    with ZipFile(pptx) as z:
        root = ET.fromstring(z.read("ppt/slides/slide1.xml"))
    assert root.find(f".//{{{ns}}}ea").attrib == {"typeface": "PingFang SC"}
    assert report["removed_table_font_hints"] == 2


def test_native_compat_renumbers_duplicate_shape_ids_in_unanimated_native_scenes(tmp_path):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from aippt_studio.native_compat import normalize_native_candidate

    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    xml = f'''<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>
      <p:sp><p:nvSpPr><p:cNvPr id="4" name="Text 2"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>
        <p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>保留标题</a:t></a:r></a:p></p:txBody></p:sp>
      <p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table 0"/><p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>
        <p:xfrm/><a:graphic><a:graphicData><a:tbl><a:tr><a:tc><a:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:t>保留表格</a:t></a:r></a:p></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic>
      </p:graphicFrame>
      <p:sp><p:nvSpPr><p:cNvPr id="6" name="Text 3"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr><p:spPr/></p:sp>
    </p:spTree></p:cSld></p:sld>'''
    pptx = tmp_path / "candidate.pptx"
    with ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/slides/slide3.xml", xml)

    report = normalize_native_candidate(pptx)

    with ZipFile(pptx) as archive:
        normalized_xml = archive.read("ppt/slides/slide3.xml")
        root = ET.fromstring(normalized_xml)
    ids = [node.get("id") for node in root.findall(".//p:spTree//p:cNvPr", {"p": p})]
    assert len(ids) == len(set(ids))
    assert ids == ["1", "4", "7", "6"]
    assert [node.text for node in root.findall(".//a:t", {"a": a})] == ["保留标题", "保留表格"]
    assert root.find(".//p:timing", {"p": p}) is None
    assert report["renumbered_duplicate_shape_ids"] == 1
    assert normalized_xml.decode("utf-8") == xml.replace(
        '<p:cNvPr id="4" name="Table 0"/>', '<p:cNvPr id="7" name="Table 0"/>')


def test_native_compat_leaves_duplicate_ids_on_timed_slides_untouched(tmp_path):
    from zipfile import ZipFile
    from xml.etree import ElementTree as ET
    from aippt_studio.native_compat import normalize_native_candidate

    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    xml = f'''<p:sld xmlns:p="{p}"><p:cSld><p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/></p:nvGrpSpPr>
      <p:sp><p:nvSpPr><p:cNvPr id="4" name="Text"/></p:nvSpPr></p:sp>
      <p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table"/></p:nvGraphicFramePr></p:graphicFrame>
    </p:spTree></p:cSld><p:timing/></p:sld>'''
    pptx = tmp_path / "timed-candidate.pptx"
    with ZipFile(pptx, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", xml)

    report = normalize_native_candidate(pptx)

    with ZipFile(pptx) as archive:
        root = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
    ids = [node.get("id") for node in root.findall(".//p:spTree//p:cNvPr", {"p": p})]
    assert ids == ["1", "4", "4"]
    assert root.find(".//p:timing", {"p": p}) is not None
    assert report["renumbered_duplicate_shape_ids"] == 0


def test_presenton_configuration_and_cloud_gate(tmp_path, monkeypatch):
    from aippt_studio.upstream_services import generate_presenton
    job = _job(tmp_path)
    monkeypatch.delenv("AIPPT_PRESENTON_URL", raising=False)
    with pytest.raises(ValueError, match="not configured"):
        generate_presenton({"job": job.id, "content": "test"}, tmp_path)
    path = job_dir(job.id) / "planning/brief.json"
    brief = json.loads(path.read_text())
    brief["cloud_allowed"] = False
    path.write_text(json.dumps(brief))
    with pytest.raises(ValueError, match="disabled"):
        generate_presenton({"job": job.id, "content": "test"}, tmp_path)


def test_presenton_contract_downloads_candidate_without_sending_token_in_body(tmp_path, monkeypatch):
    import io
    from zipfile import ZipFile
    from aippt_studio import upstream_services
    job = _job(tmp_path)
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as z:
        z.writestr("ppt/presentation.xml", "<presentation/>")
    requests = []
    class Response:
        status_code = 200
        def json(self):
            return {"path": "/app_data/exports/demo.pptx", "presentation_id": "demo", "edit_path": "/presentation?id=demo"}
        def iter_content(self, size):
            yield buffer.getvalue()
    class Session:
        def post(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()
        def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(upstream_services.requests, "Session", Session)
    monkeypatch.setenv("AIPPT_PRESENTON_URL", "http://127.0.0.1:5000")
    monkeypatch.setenv("AIPPT_PRESENTON_TOKEN", "test-secret")
    result = upstream_services.generate_presenton({"job": job.id, "content": "test content"}, tmp_path)
    assert result["status"] == "candidate_requires_review"
    assert requests[0][0].endswith("/api/v1/ppt/presentation/generate")
    assert "test-secret" not in json.dumps(requests[0][1]["json"])
    assert requests[0][1]["json"]["web_search"] is False
    assert requests[1][1]["allow_redirects"] is False


def test_pptagent_requires_configuration_and_keeps_runtime_copy_outside_source(tmp_path, monkeypatch):
    import sys
    from aippt_studio.upstream_services import prepare_pptagent
    job = _job(tmp_path)
    source = tmp_path / "upstream"
    source.mkdir()
    for name in ("pptagent", "deeppresenter"):
        (source / name).mkdir()
        (source / name / "__init__.py").write_text("# upstream")
    (source / "LICENSE").write_text("MIT")
    output = tmp_path / "call"
    output.mkdir()
    request = {"job": job.id, "steps": [{"name": "list_templates"}]}
    monkeypatch.delenv("AIPPT_PPTAGENT_PYTHON", raising=False)
    with pytest.raises(ValueError, match="requires"):
        prepare_pptagent(request, output, source)
    monkeypatch.setenv("AIPPT_PPTAGENT_PYTHON", sys.executable)
    monkeypatch.setenv("PPTAGENT_MODEL", "test-model")
    python, copy = prepare_pptagent(request, output, source)
    assert python.is_file()
    (copy / "pptagent/cache.txt").write_text("runtime cache")
    assert not (source / "pptagent/cache.txt").exists()
