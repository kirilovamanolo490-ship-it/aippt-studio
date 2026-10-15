from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from aippt_studio.assets.commons import pick_commons_asset, search_commons_assets
from aippt_studio.assets.acquisition import AssetAcquisitionError, add_asset_need
from aippt_studio.models import DeckPlan, JobBrief, JobState
from aippt_studio.storage import job_dir, read_model, write_json
from aippt_studio.workflow import approve_gate, create_job, run_job
from aippt_studio.ingest import ingest_sources


def _commons_job(tmp_path: Path, job_id: str) -> tuple[str, str]:
    source = tmp_path / f"{job_id}.md"
    source.write_text("重点：区域农业品牌传播与乡村产业升级。\n" * 5, encoding="utf-8")
    job = create_job("Commons素材检索", job_id=job_id)
    ingest_sources(job.id, [str(source)])
    assert run_job(job.id)["state"] == JobState.BRIEF_REVIEW.value
    approve_gate(job.id, "brief")
    assert run_job(job.id)["state"] == JobState.PROTOTYPE_REVIEW.value
    plan = read_model(job_dir(job.id) / "planning" / "deck-plan.json", DeckPlan)
    slide_id = plan.slides[0].id
    need = add_asset_need(job.id, slide_id=slide_id, kind="image", query="乡村农业", purpose="呈现乡村农业场景")
    return job.id, need.id


class _Response:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return "https://commons.wikimedia.org/w/api.php"

    def read(self, amount: int = -1):
        return self.body[:amount]


class _Opener:
    def __init__(self, response: _Response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        assert timeout == 25
        return self.response


def test_commons_search_returns_per_file_license_and_attribution_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, need_id = _commons_job(tmp_path, "commons-search")
    body = {
        "query": {
            "pages": [
                {
                    "title": "File:Terraced rice fields.jpg",
                    "canonicalurl": "https://commons.wikimedia.org/wiki/File:Terraced_rice_fields.jpg",
                    "imageinfo": [
                        {
                            "url": "https://upload.wikimedia.org/example/rice.jpg",
                            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Terraced_rice_fields.jpg",
                            "width": 1920,
                            "height": 1080,
                            "mime": "image/jpeg",
                            "extmetadata": {
                                "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                "LicenseUrl": {"value": "http://creativecommons.org/licenses/by-sa/4.0/"},
                                "Artist": {"value": "<a href=\"https://example.test\">Li &amp; Co.</a>"},
                                "AttributionRequired": {"value": "true"},
                            },
                        }
                    ],
                }
            ]
        }
    }
    opener = _Opener(_Response(json.dumps(body).encode()))
    monkeypatch.setattr("aippt_studio.assets.commons.urllib.request.build_opener", lambda *_args: opener)
    result = search_commons_assets(job_id, need_id=need_id)

    assert result["candidate_count"] == 1
    candidate = result["candidates"][0]
    assert candidate["license_spdx"] == "CC-BY-SA-4.0"
    assert candidate["attribution"] == "Li & Co.; CC BY-SA 4.0"
    assert candidate["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
    assert candidate["download_url"].startswith("https://upload.wikimedia.org/")
    assert "asset-pick-commons" in result["next_step"]
    assert not list((job_dir(job_id) / "assets" / "acquired").glob("*"))
    query = parse_qs(urlparse(opener.request.full_url).query)
    assert query["gsrsearch"] == ["filetype:bitmap 乡村农业"]
    assert query["iiprop"] == ["url|mime|size|extmetadata"]


def test_unknown_commons_license_is_reported_as_noassertion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, need_id = _commons_job(tmp_path, "commons-unknown-license")
    body = {"query": {"pages": [{"title": "File:unclear.png", "imageinfo": [{
        "url": "https://upload.wikimedia.org/unclear.png",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:unclear.png",
        "mime": "image/png",
        "extmetadata": {"LicenseShortName": {"value": "Free use"}, "LicenseUrl": {"value": ""}},
    }]}]}}
    opener = _Opener(_Response(json.dumps(body).encode()))
    monkeypatch.setattr("aippt_studio.assets.commons.urllib.request.build_opener", lambda *_args: opener)
    candidate = search_commons_assets(job_id, need_id=need_id)["candidates"][0]
    assert candidate["license_spdx"] == "NOASSERTION"
    assert candidate["license_name"] == "Free use"


def test_commons_search_uses_vector_predicate_and_drops_non_image_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, need_id = _commons_job(tmp_path, "commons-icon")
    root = job_dir(job_id)
    plan = json.loads((root / "planning/asset-plan.json").read_text(encoding="utf-8"))
    plan["needs"][0]["kind"] = "icon"
    write_json(root / "planning/asset-plan.json", plan)
    body = {"query": {"pages": [
        {"title": "File:diagram.pdf", "imageinfo": [{"mime": "application/pdf",
            "url": "https://upload.wikimedia.org/diagram.pdf",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:diagram.pdf"}]},
        {"title": "File:flower.svg", "imageinfo": [{"mime": "image/svg+xml",
            "url": "https://upload.wikimedia.org/flower.svg",
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:flower.svg"}]},
    ]}}
    opener = _Opener(_Response(json.dumps(body).encode()))
    monkeypatch.setattr("aippt_studio.assets.commons.urllib.request.build_opener", lambda *_args: opener)

    result = search_commons_assets(job_id, need_id=need_id)

    assert [item["mime"] for item in result["candidates"]] == ["image/svg+xml"]
    assert parse_qs(urlparse(opener.request.full_url).query)["gsrsearch"] == ["filetype:drawing 乡村农业"]


def test_commons_search_obeys_cloud_permission_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, need_id = _commons_job(tmp_path, "commons-cloud-denied")
    root = job_dir(job_id)
    brief = read_model(root / "planning" / "brief.json", JobBrief)
    brief.cloud_allowed = False
    write_json(root / "planning" / "brief.json", brief)
    monkeypatch.setattr(
        "aippt_studio.assets.commons.urllib.request.build_opener",
        lambda *_args: (_ for _ in ()).throw(AssertionError("network must not start")),
    )
    with pytest.raises(AssetAcquisitionError, match="cloud"):
        search_commons_assets(job_id, need_id=need_id)


def test_commons_pick_uses_exact_saved_candidate_and_records_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, need_id = _commons_job(tmp_path, "commons-pick")
    body = {"query": {"pages": [{"title": "File:Field.jpg", "imageinfo": [{
        "url": "https://upload.wikimedia.org/example/field.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Field.jpg",
        "mime": "image/jpeg",
        "extmetadata": {
            "LicenseShortName": {"value": "CC BY 4.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
            "Artist": {"value": "Li"},
        },
    }]}]}}
    monkeypatch.setattr("aippt_studio.assets.commons.urllib.request.build_opener",
                        lambda *_args: _Opener(_Response(json.dumps(body).encode())))
    search = search_commons_assets(job_id, need_id=need_id)
    ticket = job_dir(job_id) / search["ticket_path"]
    assert ticket.is_file()
    assert len(search["search_id"]) == 64
    captured = {}

    def fake_acquire(_job_id, **kwargs):
        captured.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr("aippt_studio.assets.commons.acquire_asset", fake_acquire)
    result = pick_commons_asset(job_id, need_id=need_id, search_id=search["search_id"],
                                candidate_number=1, license_review_note="文件页注明 CC BY 4.0，作者 Li",
                                selection_note="与乡村农业主题相符")
    assert result == {"status": "captured"}
    assert captured["download_url"] == "https://upload.wikimedia.org/example/field.jpg"
    assert captured["license_id"] == "CC-BY-4.0"
    assert "文件页注明 CC BY 4.0" in captured["selection_note"]
    ticket_data = json.loads(ticket.read_text(encoding="utf-8"))
    ticket_data["candidates"][0]["download_url"] = "https://upload.wikimedia.org/changed.jpg"
    ticket.write_text(json.dumps(ticket_data), encoding="utf-8")
    with pytest.raises(AssetAcquisitionError, match="hash mismatch"):
        pick_commons_asset(job_id, need_id=need_id, search_id=search["search_id"],
                           candidate_number=1, license_review_note="已核对", selection_note="相关")


@pytest.mark.parametrize("changed_field,new_value", [
    ("slide_id", "different-slide"),
    ("kind", "icon"),
    ("purpose", "另一页的图标说明"),
])
def test_commons_pick_rejects_ticket_after_need_destination_or_role_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, changed_field: str, new_value: str
) -> None:
    job_id, need_id = _commons_job(tmp_path, f"commons-stale-{changed_field}")
    body = {"query": {"pages": [{"title": "File:Field.jpg", "imageinfo": [{
        "url": "https://upload.wikimedia.org/example/field.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Field.jpg",
        "mime": "image/jpeg",
        "extmetadata": {"LicenseShortName": {"value": "CC0 1.0"},
                        "LicenseUrl": {"value": "https://creativecommons.org/publicdomain/zero/1.0/"}},
    }]}]}}
    monkeypatch.setattr("aippt_studio.assets.commons.urllib.request.build_opener",
                        lambda *_args: _Opener(_Response(json.dumps(body).encode())))
    search = search_commons_assets(job_id, need_id=need_id)
    plan_path = job_dir(job_id) / "planning" / "asset-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["needs"][0][changed_field] = new_value
    write_json(plan_path, plan)
    monkeypatch.setattr("aippt_studio.assets.commons.acquire_asset",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not download")))
    with pytest.raises(AssetAcquisitionError, match="no longer matches"):
        pick_commons_asset(job_id, need_id=need_id, search_id=search["search_id"],
                           candidate_number=1, license_review_note="已核对", selection_note="相关")


def test_commons_pick_rejects_unlicensed_or_missing_creator_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    job_id, need_id = _commons_job(tmp_path, "commons-pick-blocked")
    body = {"query": {"pages": [{"title": "File:Field.jpg", "imageinfo": [{
        "url": "https://upload.wikimedia.org/example/field.jpg",
        "descriptionurl": "https://commons.wikimedia.org/wiki/File:Field.jpg",
        "mime": "image/jpeg",
        "extmetadata": {
            "LicenseShortName": {"value": "CC BY 4.0"},
            "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
        },
    }]}]}}
    monkeypatch.setattr("aippt_studio.assets.commons.urllib.request.build_opener",
                        lambda *_args: _Opener(_Response(json.dumps(body).encode())))
    search = search_commons_assets(job_id, need_id=need_id)
    monkeypatch.setattr("aippt_studio.assets.commons.acquire_asset",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not download")))
    with pytest.raises(AssetAcquisitionError, match="creator metadata"):
        pick_commons_asset(job_id, need_id=need_id, search_id=search["search_id"],
                           candidate_number=1, license_review_note="已核对", selection_note="相关")
