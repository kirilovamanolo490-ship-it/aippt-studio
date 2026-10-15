"""Explicit, hash-bound internal library moves. Requires exclusive maintenance access.

Rows: old_path, new_path, sha256; optional group_id. Paths are relative to the
shared library root and must stay in the same supplied collection. Every batch
is staged before publishing files or indexes. recover() rolls back an unfinished
transaction, or finalizes lock release after the applied commit marker.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import unicodedata
import zipfile

TEXT = {'.json', '.jsonl', '.md', '.csv', '.tsv', '.txt', '.html', '.yaml', '.yml', '.xml'}
PROTECTED = {'分析索引', '索引', 'analysis', 'indexes', '_analysis'}


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_rename(src, dst):
    src, dst = Path(src), Path(dst)
    src.rename(dst)
    # A later manifest phase must never be durable before both rename entries.
    fsync_dir(src.parent)
    if src.parent != dst.parent:
        fsync_dir(dst.parent)


def durable_unlink(path):
    path = Path(path)
    path.unlink(missing_ok=True)
    fsync_dir(path.parent)


def durable_mkdir(path, *, stop):
    path, stop = Path(path), Path(stop)
    missing = []
    cursor = path
    while not cursor.exists():
        if cursor == stop or stop not in cursor.parents:
            raise ValueError('directory outside transaction root')
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir()
        fsync_dir(directory.parent)


def atomic_from_file(path, source):
    path, source = Path(path), Path(source)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.txn-', suffix='.tmp', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'wb') as out, source.open('rb') as inp:
            if path.exists():
                os.fchmod(out.fileno(), stat.S_IMODE(path.stat().st_mode))
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def key(value):
    return unicodedata.normalize('NFKC', str(value)).casefold()


def atomic(path, data):
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.txn-', suffix='.tmp', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'wb') as f:
            if path.exists():
                os.fchmod(f.fileno(), stat.S_IMODE(path.stat().st_mode))
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def safe(root, value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        raise ValueError('invalid relative path')
    p = Path(value)
    if p.is_absolute() or any(x in {'.', '..'} for x in value.split('/')):
        raise ValueError('unsafe relative path: ' + value)
    if len(os.fsencode(root / p)) > 1000 or any(len(os.fsencode(x)) > 255 for x in p.parts):
        raise ValueError('path too long: ' + value)
    cursor = root
    if cursor.is_symlink():
        raise ValueError('symlink root')
    for x in p.parts:
        cursor /= x
        if cursor.is_symlink():
            raise ValueError('symlink path: ' + value)
        if cursor.exists() and cursor.stat().st_dev != root.stat().st_dev:
            raise ValueError('cross-volume path: ' + value)
    return root / p


def zip_check(path):
    if path.suffix.lower() != '.zip':
        return
    with zipfile.ZipFile(path) as z:
        seen = set()
        for info in z.infolist():
            name = info.filename.rstrip('/')
            if not name:
                continue
            if name.startswith('/') or '\\' in name or '..' in name.split('/') or re.match(r'^[A-Za-z]:', name):
                raise ValueError('unsafe ZIP member: ' + info.filename)
            if stat.S_ISLNK(info.external_attr >> 16) or key(name) in seen:
                raise ValueError('ZIP symlink or collision: ' + info.filename)
            seen.add(key(name))
            if info.file_size > 4 * 1024**3 or (info.file_size > 100 * 1024**2 and info.file_size > max(1, info.compress_size) * 1000):
                raise ValueError('ZIP expansion limit: ' + info.filename)


def read_mapping(path):
    return [json.loads(s) for s in Path(path).read_text(encoding='utf-8-sig').splitlines() if s.strip()]


def validate(root, rows, collections, *, groups=None):
    root = Path(root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise ValueError('invalid library root')
    collections = list(collections)
    if not collections or any('/' in x or x in {'.', '..', ''} for x in collections):
        raise ValueError('invalid collection roots')
    mapping, targets = {}, set()
    if len(collections) != len(set(collections)):
        raise ValueError('duplicate collection roots')
    collections = set(collections)
    for row in rows:
        old, new, digest = row['old_path'], row['new_path'], row['sha256']
        src, dst = safe(root, old), safe(root, new)
        a, b = Path(old).parts, Path(new).parts
        asset_roots = {'.ttf': '字体包', '.otf': '字体包', '.ttc': '字体包', '.woff': '字体包', '.woff2': '字体包', '.mp4': '视频素材', '.mov': '视频素材', '.webm': '视频素材', '.mp3': '音频素材', '.wav': '音频素材', '.m4a': '音频素材'}
        asset_root = asset_roots.get(src.suffix.lower())
        if len(a) < 2 or len(b) < 2 or a[0] not in collections or (a[0] != b[0] and b[0] != asset_root):
            raise ValueError('move must remain inside collection or explicit asset root')
        if old != new and (any(x in PROTECTED for x in a[1:-1] + b[1:-1]) or any('INDEX' in x.upper() or 'CATALOG' in x.upper() or 'STRUCTURE' in x.upper() for x in a[1:-1] + b[1:-1])):
            raise ValueError('analysis directories cannot move')
        if old in mapping or key(new) in targets:
            raise ValueError('duplicate mapping or target')
        if not re.fullmatch('[0-9a-f]{64}', digest) or not src.is_file() or sha(src) != digest:
            raise ValueError('source missing or SHA changed: ' + old)
        zip_check(src)
        mapping[old] = new
        targets.add(key(new))
    if not mapping:
        raise ValueError('empty mapping')
    moving = {key(x) for x, y in mapping.items() if x != y}
    existing = {}
    dirs = {}
    for p in root.rglob('*'):
        rel = p.relative_to(root).as_posix()
        existing.setdefault(key(rel), []).append(rel)
        if p.is_dir():
            dirs[key(rel)] = rel
    intended_dirs = {}
    for old, new in mapping.items():
        if old != new and key(new) in existing and key(new) not in moving:
            raise ValueError('occupied target: ' + new)
        for parent in Path(new).parents:
            if str(parent) == '.':
                continue
            s = parent.as_posix()
            k = key(s)
            if k in existing and k not in dirs:
                raise ValueError('file occupies target directory: ' + s)
            if k in dirs and dirs[k] != s or k in intended_dirs and intended_dirs[k] != s:
                raise ValueError('directory normalization/case collision: ' + s)
            intended_dirs[k] = s
    for gid, members in (groups or {}).items():
        supplied = [r for r in rows if r.get('group_id') == gid]
        if {r['old_path'] for r in supplied} != set(members):
            raise ValueError('incomplete companion group: ' + gid)
        if len({str(Path(r['new_path']).parent) for r in supplied}) != 1:
            raise ValueError('companion group split: ' + gid)
    if any(r.get('group_id') and r['group_id'] not in (groups or {}) for r in rows):
        raise ValueError('group members require independent group manifest')
    return mapping


def _rewrite_walk(root, rel, mapping, detail_hashes):
    preserve_old_path = 'ALIASES' in Path(rel).name.upper()
    variants = dict(mapping)
    variants.update({str(root / old): str(root / new) for old, new in mapping.items()})
    variants.update({'模板库/' + old: '模板库/' + new for old, new in mapping.items()})
    for old, new in mapping.items():
        collection, local_old = old.split('/', 1)
        if Path(rel).parts[0] == collection:
            variants[local_old] = new.split('/', 1)[1] if new.startswith(collection + '/') else '../' + new

    def walk(value):
        if isinstance(value, str):
            updated = variants.get(value, value)
            return updated, updated != value
        if isinstance(value, list):
            changed = False
            for index, item in enumerate(value):
                value[index], item_changed = walk(item)
                changed |= item_changed
            return value, changed
        if not isinstance(value, dict):
            return value, False
        # JSON keys and historical original alias names are immutable.
        changed = False
        for name, item in value.items():
            if name == 'old_path' and preserve_old_path:
                continue
            value[name], item_changed = walk(item)
            changed |= item_changed
        detail_ref = value.get('detail_ref')
        if isinstance(detail_ref, str):
            detail_ref = detail_ref.removeprefix('模板库/')
            if detail_ref in detail_hashes:
                old_digest, new_digest = detail_hashes[detail_ref]
                if value.get('detail_sha256') != old_digest:
                    raise ValueError('stale detail_sha256 linkage: ' + rel + ' -> ' + detail_ref)
                value['detail_sha256'] = new_digest
                changed |= new_digest != old_digest
        return value, changed

    return walk


def _rewrite_index(root, rel, mapping, detail_hashes, output):
    path = safe(root, rel)
    walk = _rewrite_walk(root, rel, mapping, detail_hashes)
    changed = False
    with output.open('wb') as stream:
        if path.suffix.lower() == '.jsonl':
            # JSONL is bounded by one record even when the index has millions.
            with path.open('r', encoding='utf-8-sig') as source:
                for line in source:
                    if not line.strip():
                        continue
                    after, item_changed = walk(json.loads(line))
                    changed |= item_changed
                    stream.write((json.dumps(after, ensure_ascii=False) + '\n').encode('utf-8'))
        else:
            # The largest current detail is one JSON document; stdlib json
            # requires that individual document in memory, not the full set.
            after, changed = walk(json.loads(path.read_text(encoding='utf-8-sig')))
            stream.write((json.dumps(after, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    if not changed:
        output.unlink()
    return changed


def _prepare_index_updates(root, mapping, index_paths, output_dir):
    root, output_dir = Path(root), Path(output_dir)
    paths = list(index_paths)
    if len(paths) != len(set(paths)):
        raise ValueError('duplicate index allowlist path')
    for rel in paths:
        path = safe(root, rel)
        if path.suffix.lower() not in {'.json', '.jsonl'}:
            raise ValueError('index allowlist requires JSON/JSONL: ' + rel)
        if rel in mapping:
            raise ValueError('source cannot also be index: ' + rel)
        if not path.is_file():
            raise ValueError('allowlisted index missing: ' + rel)
    # Details first, catalogs last: the latter contain SHA links to the
    # rewritten bytes of the former, not merely their original source hashes.
    catalogs = {'STYLE-CATALOG.jsonl', 'MOTION-CATALOG.jsonl'}
    paths.sort(key=lambda rel: (Path(rel).name in catalogs, rel))
    updates = {}
    detail_hashes = {}
    for rel in paths:
        output = output_dir / str(len(updates))
        if not _rewrite_index(root, rel, mapping, detail_hashes, output):
            continue
        old_digest, new_digest = sha(root / rel), sha(output)
        updates[rel] = {'file': output.name, 'old_sha256': old_digest, 'sha256': new_digest}
        if (root / rel).suffix.lower() == '.json':
            detail_hashes[rel] = (old_digest, new_digest)
    if detail_hashes:
        # A detail rewrite must not leave an unlisted catalog pointing at the
        # old SHA. Scan catalog records once instead of guessing from names.
        for catalog_name in ('STYLE-CATALOG.jsonl', 'MOTION-CATALOG.jsonl'):
            for catalog_path in root.rglob(catalog_name):
                if not catalog_path.is_file():
                    continue
                catalog_rel = catalog_path.relative_to(root).as_posix()
                if catalog_rel in paths:
                    continue
                with catalog_path.open('r', encoding='utf-8-sig') as source:
                    for line in source:
                        if not line.strip():
                            continue
                        ref = json.loads(line).get('detail_ref')
                        if isinstance(ref, str) and ref.removeprefix('模板库/') in detail_hashes:
                            raise ValueError('rewritten detail requires catalog allowlist: ' + catalog_rel)
    fsync_dir(output_dir)
    return updates


def index_updates(root, mapping, *, index_paths, output_dir=None):
    """Rewrite allowlisted metadata; pass output_dir to keep payloads on disk.

    The default byte result is retained for small callers. apply() and
    dry_run() always use the spooled form to bound aggregate memory.
    """
    if output_dir is not None:
        return _prepare_index_updates(root, mapping, index_paths, output_dir)
    with tempfile.TemporaryDirectory(prefix='aippt-index-preview-') as directory:
        updates = _prepare_index_updates(root, mapping, index_paths, Path(directory))
        return {rel: (Path(directory) / info['file']).read_bytes() for rel, info in updates.items()}


def dry_run(root, rows, collections, *, index_paths, groups=None):
    root = Path(root).absolute()
    mapping = validate(root, rows, collections, groups=groups)
    with tempfile.TemporaryDirectory(prefix='aippt-index-preview-') as directory:
        updates = (index_updates(root, {x:y for x,y in mapping.items() if x != y},
                                 index_paths=index_paths, output_dir=Path(directory))
                   if any(x != y for x,y in mapping.items()) else {})
    return {'files': len(rows), 'moves': sum(x != y for x,y in mapping.items()), 'indexes': sorted(updates), 'hash_bytes': sum((root / x).stat().st_size for x in mapping), 'mode': 'validated-dry-run'}


def recover(transaction):
    transaction = Path(transaction).absolute()
    m = json.loads((transaction / 'manifest.json').read_text())
    if m.get('status') not in {'preparing', 'staging', 'publishing', 'applied',
                               'collecting', 'restoring', 'restoring-indexes', 'rolled-back'}:
        raise ValueError('unknown transaction phase')
    root = Path(m['root'])
    lock = root / '.library-reorganization.lock'
    if lock.exists() and lock.read_text() != str(transaction):
        raise ValueError('recovery lock belongs to another transaction')

    def phase(name):
        m['status'] = name
        atomic(transaction / 'manifest.json', json.dumps(m, ensure_ascii=False).encode())

    if m['status'] == 'rolled-back':
        if lock.exists():
            durable_unlink(lock)
        return m
    if m['status'] == 'applied':
        # The applied manifest is the commit marker. A crash or fsync failure
        # while releasing the lock cannot turn committed files into a rollback.
        if lock.exists():
            durable_unlink(lock)
        return m
    if m['status'] == 'publishing':
        phase('collecting')
    if m['status'] == 'collecting':
        # Collect *every* published destination before restoring *any*
        # original. Otherwise a resumed rollback can misidentify restored
        # originals as destinations in swaps and A -> B -> C chains.
        for i, row in enumerate(m['rows']):
            if row['old_path'] == row['new_path']:
                continue
            stage = transaction / 'staged' / str(i)
            dst = safe(root, row['new_path'])
            if stage.exists():
                if dst.exists():
                    raise ValueError('recovery stage and destination both occupied')
                if sha(stage) != row['sha256']:
                    raise ValueError('recovery stage SHA mismatch')
                continue
            if not dst.is_file() or sha(dst) != row['sha256']:
                raise ValueError('recovery destination missing or SHA mismatch')
            durable_rename(dst, stage)
        phase('restoring')
    elif m['status'] == 'staging':
        phase('restoring')
    elif m['status'] == 'preparing':
        phase('restoring-indexes')

    if m['status'] == 'restoring':
        for i, row in enumerate(m['rows']):
            if row['old_path'] == row['new_path']:
                continue
            stage = transaction / 'staged' / str(i)
            src = safe(root, row['old_path'])
            if not stage.exists():
                if not src.is_file() or sha(src) != row['sha256']:
                    raise ValueError('recovery original missing or SHA mismatch')
                continue
            if src.exists():
                raise ValueError('recovery source occupied')
            durable_mkdir(src.parent, stop=root)
            durable_rename(stage, src)
        phase('restoring-indexes')
    if m['status'] == 'restoring-indexes':
        for rel, info in m['indexes'].items():
            path = safe(root, rel)
            if info['existed']:
                backup = transaction / 'backups' / info['backup']
                if sha(backup) != info['old_sha256']:
                    raise ValueError('recovery index backup SHA mismatch: ' + rel)
                atomic_from_file(path, backup)
            elif path.exists():
                durable_unlink(path)
        for name in sorted(m.get('created_dirs', []), key=lambda x: len(Path(x).parts), reverse=True):
            directory = Path(name)
            try:
                directory.rmdir()
                fsync_dir(directory.parent)
            except OSError:
                pass
        phase('rolled-back')
    if lock.exists():
        durable_unlink(lock)
    return m


def _prepare_alias_update(root, rows, mapping, updates, output_dir):
    if not any(old != new for old, new in mapping.items()):
        return
    aliases = 'RENAME-ALIASES.jsonl'
    path = safe(root, aliases)
    output = output_dir / str(len(updates))
    with output.open('wb') as stream:
        if path.exists():
            with path.open('r', encoding='utf-8-sig') as source:
                for line in source:
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    row['current_path'] = mapping.get(row['current_path'], row['current_path'])
                    stream.write((json.dumps(row, ensure_ascii=False) + '\n').encode('utf-8'))
        for row in rows:
            old, new = row['old_path'], row['new_path']
            if old != new:
                alias = {'schema': 'aippt.library-path-alias.v1', 'old_path': old,
                         'current_path': new, 'source_sha256': row['sha256']}
                stream.write((json.dumps(alias, ensure_ascii=False) + '\n').encode('utf-8'))
        stream.flush()
        os.fsync(stream.fileno())
    fsync_dir(output_dir)
    updates[aliases] = {'file': output.name, 'old_sha256': sha(path) if path.exists() else None,
                        'sha256': sha(output)}


def apply(root, rows, collections, transaction, *, index_paths, groups=None, baseline_sources=None, fail_after=None):
    root, transaction = Path(root).absolute(), Path(transaction).absolute()
    if root == transaction or root in transaction.parents or transaction in root.parents or transaction.exists():
        raise ValueError('transaction must be a new directory outside library')
    transaction.parent.mkdir(parents=True, exist_ok=True)
    if transaction.parent.stat().st_dev != root.stat().st_dev or transaction.parent.is_symlink():
        raise ValueError('transaction must be on same volume without symlinks')
    for parent in [transaction.parent, *transaction.parent.parents]:
        if parent.is_symlink(): raise ValueError('symlink transaction parent')
    if baseline_sources is None:
        raise ValueError('apply requires independent complete baseline_sources')
    baseline = {(r['path'], r['sha256']) for r in baseline_sources}
    supplied = {(r['old_path'], r['sha256']) for r in rows}
    if len(baseline) != len(baseline_sources) or baseline != supplied or len(supplied) != len(rows):
        raise ValueError('mapping does not cover independent baseline')
    # The manifest exists before the library lock is created, so even a crash
    # immediately after lock creation leaves a recoverable transaction path.
    transaction.mkdir()
    fsync_dir(transaction.parent)
    for name in ('staged', 'backups', 'updates'):
        (transaction / name).mkdir()
    fsync_dir(transaction)
    m = {'created_dirs': [], 'schema': 'aippt.internal-reorganization.v1',
         'root': str(root), 'rows': rows, 'indexes': {}, 'status': 'preparing'}
    atomic(transaction / 'manifest.json', json.dumps(m, ensure_ascii=False).encode())
    lock = root / '.library-reorganization.lock'
    # A hard link installs the complete, already-fsynced owner record with
    # O_EXCL semantics; a crash cannot leave an empty or partial lock file.
    owner = transaction / 'lock-owner'
    atomic(owner, str(transaction).encode())
    try:
        os.link(owner, lock)
        fsync_dir(root)
        mapping = validate(root, rows, collections, groups=groups)
        if 'RENAME-ALIASES.jsonl' in index_paths:
            raise ValueError('alias ledger is managed separately from index allowlist')
        moved = {old: new for old, new in mapping.items() if old != new}
        updates = (index_updates(root, moved, index_paths=index_paths, output_dir=transaction / 'updates')
                   if moved else {})
        _prepare_alias_update(root, rows, mapping, updates, transaction / 'updates')
        indexes = {}
        for i, (rel, info) in enumerate(updates.items()):
            path = safe(root, rel)
            existed = path.exists()
            if existed:
                if sha(path) != info['old_sha256']:
                    raise ValueError('index changed after preparation: ' + rel)
                atomic_from_file(transaction / 'backups' / str(i), path)
                if sha(transaction / 'backups' / str(i)) != info['old_sha256']:
                    raise ValueError('index changed during backup: ' + rel)
            indexes[rel] = {'existed': existed, 'backup': str(i), **info}
        created_dirs = {str(parent) for r in rows for parent in (root / r['new_path']).parents if root in parent.parents and not parent.exists()}
        m.update({'created_dirs': sorted(created_dirs), 'indexes': indexes, 'status': 'staging'})
        atomic(transaction / 'manifest.json', json.dumps(m, ensure_ascii=False).encode())
        # Durable staging intent and numbered stage files identify every move;
        # avoid rewriting the full 22k-row manifest after each rename.
        for i, row in enumerate(rows):
            if row['old_path'] != row['new_path']:
                if sha(root / row['old_path']) != row['sha256']:
                    raise ValueError('source changed after validation')
                durable_rename(root / row['old_path'], transaction / 'staged' / str(i))
        m['status'] = 'publishing'
        atomic(transaction / 'manifest.json', json.dumps(m, ensure_ascii=False).encode())
        for i, row in enumerate(rows):
            if row['old_path'] == row['new_path']: continue
            dst = root / row['new_path']
            durable_mkdir(dst.parent, stop=root)
            durable_rename(transaction / 'staged' / str(i), dst)
            if sha(dst) != row['sha256']: raise ValueError('post-move SHA mismatch')
            if fail_after == i: raise RuntimeError('injected move failure')
        for rel, info in updates.items():
            path = safe(root, rel)
            if info['old_sha256'] is not None and sha(path) != info['old_sha256']:
                raise ValueError('index changed before publication: ' + rel)
            atomic_from_file(path, transaction / 'updates' / info['file'])
            if sha(path) != info['sha256']:
                raise ValueError('published index SHA mismatch: ' + rel)
            if fail_after == 'index': raise RuntimeError('injected index failure')
        m['status'] = 'applied'
        atomic(transaction / 'manifest.json', json.dumps(m, ensure_ascii=False).encode())
        durable_unlink(lock)
        return m
    except BaseException:
        # Read the on-disk marker: atomic() can replace the manifest and then
        # fail while syncing its directory, and lock release can fail after
        # unlinking the lock. In either case, applied files must stay applied.
        on_disk = json.loads((transaction / 'manifest.json').read_text())
        if on_disk.get('status') == 'applied':
            raise
        if lock.exists() and lock.read_text() == str(transaction):
            recover(transaction)
        else:
            # Another transaction held the lock; this candidate never touched
            # library files and must not interfere with that owner.
            m['status'] = 'rolled-back'
            atomic(transaction / 'manifest.json', json.dumps(m, ensure_ascii=False).encode())
        raise
