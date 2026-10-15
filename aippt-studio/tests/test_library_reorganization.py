import json
from pathlib import Path
import pytest
from aippt_studio import library_reorganization as reorganization
from aippt_studio.library_reorganization import apply, dry_run, recover, sha, validate, index_updates


INDEXES = ['CATALOG.jsonl', '职业/STYLE-CATALOG.jsonl', '职业/SOURCE-TAXONOMY.json']

def fixture(tmp_path):
    root = tmp_path / 'library'
    root.mkdir()
    rows = []
    for name in ['slide.pptx', 'old.docx', 'font.ttf', 'video.mp4', 'audio.mp3']:
        p = root / '职业' / '散落' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(name.encode())
        rows.append(dict(old_path=p.relative_to(root).as_posix(), new_path='职业/分类/' + name, sha256=sha(p)))
    (root / 'CATALOG.jsonl').write_text(json.dumps(dict(path=rows[0]['old_path'], category='护理', asset_type='template', confidence=.9)))
    p = root / '职业' / 'STYLE-CATALOG.jsonl'
    p.write_text(json.dumps(dict(canonical_path=rows[0]['old_path'])))
    (root / '职业' / 'SOURCE-TAXONOMY.json').write_text(json.dumps(dict(source_path='散落/slide.pptx')))
    return root, rows


def test_all_formats_and_nested_indexes(tmp_path):
    root, rows = fixture(tmp_path)
    assert dry_run(root, rows, ['职业'], index_paths=INDEXES)['moves'] == 5
    apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, baseline_sources=[dict(path=r['old_path'], sha256=r['sha256']) for r in rows])
    for r in rows:
        assert sha(root / r['new_path']) == r['sha256']
        assert not (root / r['old_path']).exists()
    cat = json.loads((root / 'CATALOG.jsonl').read_text())
    assert cat['category'] == '护理' and cat['confidence'] == .9
    assert cat['path'] == rows[0]['new_path']
    assert json.loads((root / '职业/SOURCE-TAXONOMY.json').read_text())['source_path'] == '分类/slide.pptx'


@pytest.mark.parametrize('failure', [0, 3, 'index'])
def test_failure_restores_bytes_and_paths(tmp_path, failure):
    root, rows = fixture(tmp_path)
    original = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(RuntimeError):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, fail_after=failure, baseline_sources=[dict(path=r['old_path'], sha256=r['sha256']) for r in rows])
    after = {p.relative_to(root):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    assert original == after
    assert not (root / '职业/分类').exists()
    assert json.loads((tmp_path / 'txn/manifest.json').read_text())['status'] == 'rolled-back'


def test_directory_case_collision(tmp_path):
    root, rows = fixture(tmp_path)
    rows[0]['new_path'] = '职业/Case/a.pptx'
    rows[1]['new_path'] = '职业/case/b.docx'
    with pytest.raises(ValueError, match='directory'): validate(root, rows, ['职业'])


def test_unicode_collision(tmp_path):
    root, rows = fixture(tmp_path)
    rows[0]['new_path'] = '职业/分类/Ａ.pptx'
    rows[1]['new_path'] = '职业/分类/A.pptx'
    with pytest.raises(ValueError, match='duplicate'): validate(root, rows, ['职业'])


def test_symlink_and_sha(tmp_path):
    root, rows = fixture(tmp_path)
    (root / '职业/linked').symlink_to(root / '职业/散落', target_is_directory=True)
    rows[0]['new_path'] = '职业/linked/a.pptx'
    with pytest.raises(ValueError, match='symlink'): validate(root, rows, ['职业'])
    rows[0]['new_path'] = '职业/分类/a.pptx'
    rows[0]['sha256'] = '0'*64
    with pytest.raises(ValueError, match='SHA'): validate(root, rows, ['职业'])


def test_companion_group(tmp_path):
    root, rows = fixture(tmp_path)
    rows[0]['group_id'] = 'g'
    with pytest.raises(ValueError, match='incomplete'): validate(root, rows, ['职业'], groups={'g':[rows[0]['old_path'], rows[1]['old_path']]})


def test_swap_rollback(tmp_path):
    root, rows = fixture(tmp_path)
    rows = rows[:2]
    rows[0]['new_path'], rows[1]['new_path'] = rows[1]['old_path'], rows[0]['old_path']
    with pytest.raises(RuntimeError): apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, fail_after='index', baseline_sources=[dict(path=r['old_path'], sha256=r['sha256']) for r in rows])
    assert all(sha(root / r['old_path']) == r['sha256'] for r in rows)


def test_zip_traversal(tmp_path):
    import zipfile
    root, rows = fixture(tmp_path)
    p = root / '职业/散落/unsafe.zip'
    with zipfile.ZipFile(p, 'w') as z: z.writestr('../escape.txt', 'bad')
    rows.append(dict(old_path='职业/散落/unsafe.zip', new_path='职业/分类/unsafe.zip', sha256=sha(p)))
    with pytest.raises(ValueError, match='ZIP'): validate(root, rows, ['职业'])


def test_apply_requires_complete_baseline(tmp_path):
    root, rows = fixture(tmp_path)
    with pytest.raises(ValueError, match='baseline'):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, baseline_sources=[])
    assert all((root / r['old_path']).exists() for r in rows)


def test_exact_strings_and_allowlist(tmp_path):
    root, rows = fixture(tmp_path)
    (root / '职业/SOURCE-TAXONOMY.json').write_text(json.dumps({'source_path':'散落/slide.pptx', 'text':'文字 散落/slide.pptx'}))
    user_doc = root / '职业/source.json'
    user_doc.write_text('not JSON user bytes')
    updates = index_updates(root, {r['old_path']:r['new_path'] for r in rows}, index_paths=INDEXES)
    value = json.loads(updates['职业/SOURCE-TAXONOMY.json'])
    assert value['source_path'] == '分类/slide.pptx'
    assert value['text'] == '文字 散落/slide.pptx'
    assert user_doc.read_text() == 'not JSON user bytes'


def test_explicit_asset_target(tmp_path):
    root, rows = fixture(tmp_path)
    rows[2]['new_path'] = '字体包/职业/font.ttf'
    rows[3]['new_path'] = '视频素材/职业/video.mp4'
    validate(root, rows, ['职业'])


def test_rewritten_detail_updates_catalog_sha_link(tmp_path):
    root, rows = fixture(tmp_path)
    detail_rel = '职业/分析索引/逐份结构/detail.json'
    detail = root / detail_rel
    detail.parent.mkdir(parents=True)
    detail.write_text(json.dumps({'source_path': rows[0]['old_path']}))
    original_detail_digest = sha(detail)
    catalog = root / '职业/STYLE-CATALOG.jsonl'
    catalog.write_text(json.dumps({'path': rows[0]['old_path'], 'detail_ref': detail_rel,
                                   'detail_sha256': original_detail_digest}) + '\n')
    indexes = [*INDEXES, detail_rel]
    result = dry_run(root, rows, ['职业'], index_paths=indexes)
    assert detail_rel in result['indexes']
    assert '职业/STYLE-CATALOG.jsonl' in result['indexes']
    apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=indexes,
          baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    assert json.loads(detail.read_text())['source_path'] == rows[0]['new_path']
    catalog_row = json.loads(catalog.read_text())
    assert catalog_row['detail_sha256'] == sha(detail)
    assert catalog_row['detail_sha256'] != original_detail_digest


def test_changed_detail_requires_catalog_in_allowlist(tmp_path):
    root, rows = fixture(tmp_path)
    detail_rel = '职业/分析索引/逐份结构/detail.json'
    detail = root / detail_rel
    detail.parent.mkdir(parents=True)
    detail.write_text(json.dumps({'source_path': rows[0]['old_path']}))
    (detail.parent.parent / 'STYLE-CATALOG.jsonl').write_text(
        json.dumps({'detail_ref': detail_rel, 'detail_sha256': sha(detail)}) + '\n')
    with pytest.raises(ValueError, match='requires catalog allowlist'):
        dry_run(root, rows, ['职业'], index_paths=['CATALOG.jsonl', detail_rel])
    assert json.loads(detail.read_text())['source_path'] == rows[0]['old_path']


def test_stale_detail_sha_blocks_rewrite(tmp_path):
    root, rows = fixture(tmp_path)
    detail_rel = '职业/分析索引/逐份结构/detail.json'
    detail = root / detail_rel
    detail.parent.mkdir(parents=True)
    detail.write_text(json.dumps({'source_path': rows[0]['old_path']}))
    (root / '职业/STYLE-CATALOG.jsonl').write_text(
        json.dumps({'detail_ref': detail_rel, 'detail_sha256': '0' * 64}) + '\n')
    with pytest.raises(ValueError, match='stale detail_sha256'):
        dry_run(root, rows, ['职业'], index_paths=[*INDEXES, detail_rel])


def test_jsonl_is_read_one_record_at_a_time(tmp_path, monkeypatch):
    root, rows = fixture(tmp_path)
    original = Path.read_text

    def no_jsonl_read_text(path, *args, **kwargs):
        if path.suffix == '.jsonl':
            raise AssertionError('JSONL materialized into memory')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', no_jsonl_read_text)
    assert 'CATALOG.jsonl' in dry_run(root, rows, ['职业'], index_paths=INDEXES)['indexes']
    spool = tmp_path / 'spool'
    spool.mkdir()
    updates = index_updates(root, {r['old_path']: r['new_path'] for r in rows},
                            index_paths=INDEXES, output_dir=spool)
    assert json.loads((spool / updates['CATALOG.jsonl']['file']).read_text())['path'] == rows[0]['new_path']


def test_protected_directory_noop_is_allowed_but_move_is_not(tmp_path):
    root, _ = fixture(tmp_path)
    protected = root / '职业/分析索引/keep.json'
    protected.parent.mkdir(parents=True)
    protected.write_text('{}')
    row = {'old_path': '职业/分析索引/keep.json', 'new_path': '职业/分析索引/keep.json',
           'sha256': sha(protected)}
    assert validate(root, [row], ['职业']) == {row['old_path']: row['new_path']}
    row['new_path'] = '职业/其他/keep.json'
    with pytest.raises(ValueError, match='analysis directories'):
        validate(root, [row], ['职业'])


def test_existing_lock_is_not_disturbed_by_new_transaction(tmp_path):
    root, rows = fixture(tmp_path)
    lock = root / '.library-reorganization.lock'
    lock.write_text('/some/other/transaction')
    with pytest.raises(FileExistsError):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES,
              baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    assert lock.read_text() == '/some/other/transaction'
    assert json.loads((tmp_path / 'txn/manifest.json').read_text())['status'] == 'rolled-back'
    assert all((root / row['old_path']).is_file() for row in rows)


def test_post_commit_lock_directory_fsync_failure_keeps_applied_receipt(tmp_path, monkeypatch):
    root, rows = fixture(tmp_path)
    lock = root / '.library-reorganization.lock'
    original_fsync = reorganization.fsync_dir

    def fail_after_lock_unlink(path):
        if Path(path) == root and not lock.exists():
            raise OSError('injected root fsync failure after lock unlink')
        original_fsync(path)

    monkeypatch.setattr(reorganization, 'fsync_dir', fail_after_lock_unlink)
    with pytest.raises(OSError, match='after lock unlink'):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES,
              baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    monkeypatch.setattr(reorganization, 'fsync_dir', original_fsync)
    manifest = json.loads((tmp_path / 'txn/manifest.json').read_text())
    assert manifest['status'] == 'applied'
    assert not lock.exists()
    assert all(sha(root / row['new_path']) == row['sha256'] for row in rows)
    assert all(not (root / row['old_path']).exists() for row in rows)
    assert json.loads((root / 'CATALOG.jsonl').read_text())['path'] == rows[0]['new_path']
    assert recover(tmp_path / 'txn')['status'] == 'applied'


def test_post_commit_lock_unlink_failure_can_finish_on_recovery(tmp_path, monkeypatch):
    root, rows = fixture(tmp_path)
    lock = root / '.library-reorganization.lock'
    original_unlink = reorganization.durable_unlink

    def fail_before_lock_unlink(path):
        if Path(path) == lock:
            raise OSError('injected lock unlink failure')
        original_unlink(path)

    monkeypatch.setattr(reorganization, 'durable_unlink', fail_before_lock_unlink)
    with pytest.raises(OSError, match='lock unlink failure'):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES,
              baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    monkeypatch.setattr(reorganization, 'durable_unlink', original_unlink)
    assert json.loads((tmp_path / 'txn/manifest.json').read_text())['status'] == 'applied'
    assert lock.exists()
    assert recover(tmp_path / 'txn')['status'] == 'applied'
    assert not lock.exists()
    assert all(sha(root / row['new_path']) == row['sha256'] for row in rows)


def test_interrupted_recovery_of_identical_byte_swap_is_idempotent(tmp_path, monkeypatch):
    root, rows = fixture(tmp_path)
    rows = rows[:2]
    for row in rows:
        (root / row['old_path']).write_bytes(b'same bytes')
        row['sha256'] = sha(root / row['old_path'])
    original_inodes = {row['old_path']: (root / row['old_path']).stat().st_ino for row in rows}
    rows[0]['new_path'], rows[1]['new_path'] = rows[1]['old_path'], rows[0]['old_path']
    real_recover = reorganization.recover

    def simulated_process_exit(_):
        raise RuntimeError('simulated process exit before automatic rollback')

    monkeypatch.setattr(reorganization, 'recover', simulated_process_exit)
    with pytest.raises(RuntimeError, match='simulated process exit'):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, fail_after='index',
              baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    monkeypatch.setattr(reorganization, 'recover', real_recover)
    real_rename = reorganization.durable_rename
    restored = 0

    def interrupted_restore(src, dst):
        nonlocal restored
        real_rename(src, dst)
        if Path(src).parent == tmp_path / 'txn/staged':
            restored += 1
            if restored == 2:
                raise RuntimeError('simulated exit during original restoration')

    monkeypatch.setattr(reorganization, 'durable_rename', interrupted_restore)
    with pytest.raises(RuntimeError, match='during original restoration'):
        recover(tmp_path / 'txn')
    assert json.loads((tmp_path / 'txn/manifest.json').read_text())['status'] == 'restoring'
    monkeypatch.setattr(reorganization, 'durable_rename', real_rename)
    assert recover(tmp_path / 'txn')['status'] == 'rolled-back'
    assert recover(tmp_path / 'txn')['status'] == 'rolled-back'
    assert {row['old_path']: (root / row['old_path']).stat().st_ino for row in rows} == original_inodes
    assert not (root / '.library-reorganization.lock').exists()


def test_interrupted_collection_of_chain_resumes_before_restoration(tmp_path, monkeypatch):
    root, rows = fixture(tmp_path)
    rows = rows[:3]
    originals = {row['old_path']: sha(root / row['old_path']) for row in rows}
    rows[0]['new_path'] = rows[1]['old_path']
    rows[1]['new_path'] = rows[2]['old_path']
    rows[2]['new_path'] = '职业/分类/end.ttf'
    real_recover = reorganization.recover
    monkeypatch.setattr(reorganization, 'recover', lambda _: (_ for _ in ()).throw(RuntimeError('exit')))
    with pytest.raises(RuntimeError, match='exit'):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, fail_after='index',
              baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    monkeypatch.setattr(reorganization, 'recover', real_recover)
    real_rename = reorganization.durable_rename
    collected = 0

    def interrupted_collection(src, dst):
        nonlocal collected
        real_rename(src, dst)
        if Path(dst).parent == tmp_path / 'txn/staged':
            collected += 1
            if collected == 1:
                raise RuntimeError('exit during destination collection')

    monkeypatch.setattr(reorganization, 'durable_rename', interrupted_collection)
    with pytest.raises(RuntimeError, match='destination collection'):
        recover(tmp_path / 'txn')
    assert json.loads((tmp_path / 'txn/manifest.json').read_text())['status'] == 'collecting'
    monkeypatch.setattr(reorganization, 'durable_rename', real_rename)
    assert recover(tmp_path / 'txn')['status'] == 'rolled-back'
    assert {path: sha(root / path) for path in originals} == originals


def test_interrupted_index_restore_can_replay_from_backups(tmp_path, monkeypatch):
    root, rows = fixture(tmp_path)
    original = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    real_recover = reorganization.recover
    monkeypatch.setattr(reorganization, 'recover', lambda _: (_ for _ in ()).throw(RuntimeError('exit')))
    with pytest.raises(RuntimeError, match='exit'):
        apply(root, rows, ['职业'], tmp_path / 'txn', index_paths=INDEXES, fail_after='index',
              baseline_sources=[{'path': r['old_path'], 'sha256': r['sha256']} for r in rows])
    monkeypatch.setattr(reorganization, 'recover', real_recover)
    real_copy = reorganization.atomic_from_file
    restored = 0

    def interrupted_copy(path, source):
        nonlocal restored
        real_copy(path, source)
        if Path(source).parent == tmp_path / 'txn/backups':
            restored += 1
            if restored == 1:
                raise RuntimeError('exit during index restoration')

    monkeypatch.setattr(reorganization, 'atomic_from_file', interrupted_copy)
    with pytest.raises(RuntimeError, match='index restoration'):
        recover(tmp_path / 'txn')
    assert json.loads((tmp_path / 'txn/manifest.json').read_text())['status'] == 'restoring-indexes'
    monkeypatch.setattr(reorganization, 'atomic_from_file', real_copy)
    assert recover(tmp_path / 'txn')['status'] == 'rolled-back'
    assert original == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
