from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
from xml.etree import ElementTree as ET

import pytest

from aippt_studio import source_semantics as ss

REPO = Path(__file__).resolve().parents[2]
FIXTURE = REPO / "github/markitdown/packages/markitdown/tests/test_files/test.pptx"
NS = {"c": "http://schemas.openxmlformats.org/drawingml/2006/chart"}


@pytest.fixture(autouse=True)
def isolated_reader_cache(monkeypatch):
    monkeypatch.setattr(ss, "_CACHE", {})


def test_real_locked_reader_extracts_existing_fixture_without_mutation():
    before = FIXTURE.read_bytes()
    path_before = list(sys.path)
    result = ss.analyze_source_semantics(FIXTURE)
    assert result["status"] == "complete", result
    assert len(result["slides"]) == 6
    chart = result["slides"][3]["charts"][0]
    assert chart["categories"] == ["2000", "2001", "2002", "2003"]
    assert chart["series"][0]["values"] == ["2000", "2001", "2002", "2003"]
    assert chart["display_cache"]["series"][0]["fields"]["val"]["formulas"] == ["Sheet1!$B$2:$B$5"]
    assert chart["display_cache"]["cache_freshness"] == "not_verified"
    assert chart["edit_capability"]["supported"] is True
    table = result["slides"][2]["tables"][0]
    assert table["row_count"] == 4 and table["column_count"] == 6
    assert "merge_topology" in table
    assert set(result["provider"]["module_sha256"]) == set(ss.READER_FILES)
    assert FIXTURE.read_bytes() == before and sys.path == path_before
    assert "page_type" not in json.dumps(result) and "font_size" not in json.dumps(result)
    assert result["coverage"]["visual_reviewed"] is False
    assert result["coverage"]["edit_authorized"] is False


def test_missing_checkout_is_visible_degradation(monkeypatch):
    monkeypatch.setattr(ss, "_upstream_row", lambda **kwargs: {"source_present": False})
    result = ss.analyze_source_semantics(FIXTURE)
    assert result["status"] == "unavailable" and result["slides"] == []
    assert result["source_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert "unavailable" in ss.semantic_text(result)


def test_changed_source_identity_discards_all_previously_read_facts(monkeypatch):
    original = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    hashes = iter([original, "f" * 64])
    monkeypatch.setattr(ss, "_sha", lambda path: next(hashes))
    result = ss.analyze_source_semantics(FIXTURE)
    assert result["status"] == "unavailable"
    assert result["slides"] == []
    assert result["issues"][0]["code"] == "source_identity_changed"
    assert "Series 1" not in ss.semantic_text(result)


def test_reader_verification_cache_invalidates_on_source_change(tmp_path, monkeypatch):
    source = REPO / "github/ppt-master/skills/ppt-master/scripts/pptx_ooxml"
    target = tmp_path / "skills/ppt-master/scripts/pptx_ooxml"
    target.mkdir(parents=True)
    for name in ss.READER_FILES:
        shutil.copy2(source / name, target / name)
    calls = []
    def row(*, verify):
        calls.append(verify)
        return {"source_present": True, "path": str(tmp_path), "commit_sha": "test-commit",
                "source_url": "https://example.invalid/fixture", "license": {"spdx": "MIT"},
                "verification": {"matches_lock": True}}
    monkeypatch.setattr(ss, "_upstream_row", row)
    first = ss._backend()[1]
    ss._backend()
    assert calls.count(True) == 1
    with (target / "chart_read.py").open("a") as stream:
        stream.write("\n# fixture source identity changed\n")
    second = ss._backend()[1]
    assert calls.count(True) == 2
    assert first["module_sha256"]["chart_read.py"] != second["module_sha256"]["chart_read.py"]


def test_dirty_reader_is_not_executed(monkeypatch):
    real = ss._upstream_row
    def dirty(*, verify):
        value = real(verify=False)
        if verify:
            value["verification"] = {"matches_lock": False}
        return value
    monkeypatch.setattr(ss, "_upstream_row", dirty)
    result = ss.analyze_source_semantics(FIXTURE)
    assert result["status"] == "unavailable"
    assert "differs" in result["issues"][0]["details"]


def test_missing_chart_cache_keeps_formula_but_never_guesses_values():
    xml = ET.fromstring(f'<c:chartSpace xmlns:c="{NS["c"]}"><c:chart><c:plotArea><c:barChart><c:ser><c:val><c:numRef><c:f>Sheet1!B2:B4</c:f></c:numRef></c:val></c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>')
    cache = ss._cache_details(xml, NS)
    assert cache["status"] == "missing_cache"
    assert cache["series"][0]["fields"]["val"]["formulas"] == ["Sheet1!B2:B4"]
    chart = ss._factual_chart({"chart_id": "c1", "series": [{"name": "系列1", "values": [1, 2, 3]}]}, {"display_cache": cache})
    assert chart["series"][0]["name"] is None and chart["series"][0]["values"] == []
    assert cache["formulas_evaluated"] is False and cache["workbook_checked"] is False


def test_incomplete_cache_exposes_indices_and_exact_decimal_values():
    xml = ET.fromstring(f'<c:chartSpace xmlns:c="{NS["c"]}"><c:ser><c:val><c:numRef><c:numCache><c:ptCount val="3"/><c:pt idx="2"><c:v>0.100000000000000001</c:v></c:pt></c:numCache></c:numRef></c:val></c:ser></c:chartSpace>')
    cache = ss._cache_details(xml, NS)
    assert cache["status"] == "partial_cache"
    point = cache["series"][0]["fields"]["val"]["caches"][0]["points"][0]
    assert point == {"index": "2", "value": "0.100000000000000001"}


def test_upstream_table_merge_and_smartart_readers_keep_source_topology():
    modules, _ = ss._backend()
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    p = "http://schemas.openxmlformats.org/presentationml/2006/main"
    xml = ET.fromstring(f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Table"/></p:nvGraphicFramePr><a:graphic><a:graphicData><a:tbl><a:tr><a:tc gridSpan="2"><a:txBody><a:p><a:r><a:t>合并指标</a:t></a:r></a:p></a:txBody></a:tc><a:tc hMerge="1"><a:txBody><a:p/></a:txBody></a:tc></a:tr></a:tbl></a:graphicData></a:graphic></p:graphicFrame></p:spTree></p:cSld></p:sld>')
    tables = modules["analyzer"]._analyze_tables(xml, 1)
    assert tables[0]["rows"][0]["cells"][1]["is_merge_slave"] is True
    assert tables[0]["rows"][0]["cells"][1]["anchor_col"] == 0
    dgm = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
    xml = ET.fromstring(f'<dgm:dataModel xmlns:dgm="{dgm}" xmlns:a="{a}"><dgm:ptLst><dgm:pt modelId="root"><dgm:t><a:p><a:r><a:t>技术方案</a:t></a:r></a:p></dgm:t></dgm:pt><dgm:pt modelId="child"><dgm:t><a:p><a:r><a:t>验证步骤</a:t></a:r></a:p></dgm:t></dgm:pt></dgm:ptLst><dgm:cxnLst><dgm:cxn modelId="edge" type="parOf" srcId="root" destId="child" srcOrd="0" destOrd="0"/></dgm:cxnLst></dgm:dataModel>')
    nodes, connections, warnings = modules["diagram_read"]._ordered_nodes(xml)
    assert [item["text"] for item in nodes] == ["技术方案", "验证步骤"]
    assert nodes[1]["parent_id"] == "root"
    assert connections[0]["source_id"] == "root" and connections[0]["destination_id"] == "child"
    assert not warnings


def test_normal_ingestion_records_sidecar_and_searchable_cache_text(monkeypatch):
    from aippt_studio import ingest
    from aippt_studio.storage import job_dir
    from aippt_studio.workflow import create_job
    monkeypatch.setattr(ingest, "_markitdown", lambda path: "Existing source text")
    job = create_job("source semantics fixture", job_id="source-semantics-fixture")
    record = ingest.ingest_sources(job.id, [str(FIXTURE)])[0]
    root = job_dir(job.id)
    sidecar = root / record.metadata["source_semantics_path"]
    report = json.loads(sidecar.read_text())
    assert report["status"] == "complete"
    assert report["source_sha256"] == record.sha256
    assert record.metadata["source_semantics_sha256"] == hashlib.sha256(sidecar.read_bytes()).hexdigest()
    assert "barChart" in (root / record.normalized_path).read_text()
    assert "2003" in (root / record.normalized_path).read_text()
    assert json.loads((root / "evidence-ledger.json").read_text())["locked_facts"] == []


def test_portfolio_index_and_normal_recall_use_new_chart_content(tmp_path):
    from aippt_studio import context_intelligence as ci
    path = tmp_path / "作品集/reference.pptx"
    path.parent.mkdir()
    shutil.copy2(FIXTURE, path)
    catalog = ci.build_case_index(tmp_path)
    assert catalog["schema_version"] == ci.SCHEMA_VERSION
    deck = catalog["documents"][0]
    assert deck["source_semantics"]["status"] == "complete"
    assert "barChart" in deck["slides"][3]["text"]
    found = ci.recall_context("barChart", tmp_path)
    assert found["portfolio_references"]
    assert found["portfolio_references"][0]["matching_slides"][0]["slide"] == 4
    # This string lives in the chart series cache, not in the slide title.
    series_match = ci.recall_context("Series 1", tmp_path)
    assert series_match["portfolio_references"][0]["matching_slides"][0]["slide"] == 4


def test_portfolio_cache_recovers_when_upstream_returns(tmp_path, monkeypatch):
    from aippt_studio import context_intelligence as ci
    path = tmp_path / "作品集/reference.pptx"
    path.parent.mkdir()
    shutil.copy2(FIXTURE, path)
    original = ss._upstream_row
    monkeypatch.setattr(ss, "_upstream_row", lambda **kwargs: {"source_present": False})
    first = ci.build_case_index(tmp_path)
    assert first["documents"][0]["source_semantics"]["status"] == "unavailable"
    monkeypatch.setattr(ss, "_upstream_row", original)
    second = ci.build_case_index(tmp_path)
    assert second["statistics"]["reparsed"] == 1
    assert second["documents"][0]["source_semantics"]["status"] == "complete"
    third = ci.build_case_index(tmp_path)
    assert third["statistics"]["reused"] == 1


def test_ingestion_degradation_is_persisted_without_rejecting_ordinary_text(monkeypatch):
    from aippt_studio import ingest
    from aippt_studio.storage import job_dir
    from aippt_studio.workflow import create_job
    monkeypatch.setattr(ingest, "_markitdown", lambda path: "User supplied source text")
    monkeypatch.setattr(ss, "_upstream_row", lambda **kwargs: {"source_present": False})
    job = create_job("missing reader fixture", job_id="missing-source-reader")
    record = ingest.ingest_sources(job.id, [str(FIXTURE)])[0]
    assert record.metadata["source_semantics_status"] == "unavailable"
    assert "User supplied source text" in (job_dir(job.id) / record.normalized_path).read_text()
    assert "unavailable" in (job_dir(job.id) / record.normalized_path).read_text()
