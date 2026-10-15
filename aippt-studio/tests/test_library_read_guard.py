from pathlib import Path

import pytest

from aippt_studio.library_read_guard import LibraryMaintenanceError, require_library_available
from aippt_studio import library_assets, style_hybrid_workflow, style_page_match, style_search, template_library


def test_guard_allows_unlocked_library_and_blocks_broken_lock(tmp_path):
    require_library_available(tmp_path)
    (tmp_path / '.library-reorganization.lock').symlink_to(tmp_path / 'missing-owner')
    with pytest.raises(LibraryMaintenanceError, match='maintenance'):
        require_library_available(tmp_path)


@pytest.mark.parametrize('reader', [
    lambda root: style_search.search_style_library(root, '护理'),
    lambda root: style_search.search_layout_examples(root, '护理'),
    lambda root: style_page_match.retrieve_page_layout_candidates(root, {
        'current_template_source_sha256': '0' * 64,
        'current_project': {}, 'target_page': {'message': '护理实践', 'role': 'evidence'}}),
    lambda root: style_hybrid_workflow.plan_hybrid_adaptation(root, {'project': {}}),
    lambda root: style_hybrid_workflow.build_hybrid_candidate(root, {}, root.parent / 'output.pptx'),
    lambda root: template_library.search_catalog(root / 'CATALOG.jsonl', '护理'),
    lambda root: template_library.search_catalog(root / 'CATALOG.jsonl', '护理', _loaded=([], [], {})),
    lambda root: template_library.search_catalog_many(root / 'CATALOG.jsonl', ['护理']),
    lambda root: template_library.cataloged_template_by_path(root / 'CATALOG.jsonl', 'a.pptx'),
    lambda root: template_library.cataloged_template_by_sha256(root / 'CATALOG.jsonl', '0' * 64),
])
def test_reader_refuses_locked_library_before_index_reads(tmp_path, reader):
    root = tmp_path / '模板库'
    root.mkdir()
    (root / '.library-reorganization.lock').write_text('/transaction')
    # No catalogs or source files exist: maintenance must fail before reading them.
    with pytest.raises(ValueError, match='maintenance'):
        reader(root)


def test_asset_binding_preserves_asset_error_type(tmp_path, monkeypatch):
    root = tmp_path / '模板库'
    root.mkdir()
    (root / '.library-reorganization.lock').write_text('/transaction')
    monkeypatch.setattr(library_assets, 'WORKSPACE_ROOT', tmp_path)
    with pytest.raises(library_assets.LibraryAssetError, match='maintenance'):
        library_assets._library_source('a.pptx')
