"""Plan and apply traceable, content-led names for opaque library PPTX files.

Only files with numeric/opaque stems are candidates. A rename is reversible
through the saved manifest; file bytes stay unchanged and all active library
indexes are updated as one operation.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_BAD_TITLE = re.compile(
    r"(?:请输入|请插入|请添加|请填写|请替换|请在此处|在此处?输入|输入您的|复制.*(?:标题|文字)|"
    r"相关标题文字|标题文字|请删除|可删除|删除此页|您的内容打在这里|节标题[一二三四五六七八九十0-9]?|"
    r"文字内容|点击添加|单击|此处添加|添加.*(?:标题|文字|内容)|插入.*(?:标题|文字|内容)|"
    r"Add\s+(?:Your\s+)?(?:Title|Subtitle|Text)|Click\s+Here|LOGO|演讲人|汇报人|XX|xxx|20\dX|"
    r"PPT模(?:板|版)|POWERPOINT|(?:PPT)$|动态模板|模板展示|高端PPT|高阶PPT|"
    r"https?://|www\.|(?:[\w-]+\.)+(?:com|cn|net|org|edu|gov)\b|DESIGNED\s+BY|POWERED\s+BY|"
    r"豌豆素材|素材提供|素材来源|亮亮图文|图文旗舰店|壹德设计|文化传媒有限公司|延迟符|PART\s*\d+|PowerPo$|"
    r"^汇报[:：]|(?:汇报部门|负责人|指导老师|项目组别|项目类别|项目负责人|项目名称)[:：]|负责人展示设备)", re.I)
_GENERIC = {"姓名", "目录", "感谢", "谢谢", "谢谢观看", "欢迎指导", "封面", "正文", "标题", "项目名称",
            "政策背景", "市场分析", "项目概述", "解决方案", "CONTENTS"}
_GENERIC_ENGLISH = {"xiaohongshu", "sales", "fan juan", "business", "contents", "powerpoint",
                    "work report", "annual report", "summary", "sales report", "business report"}
_GENERIC_CATEGORIES = {"历史样稿", "待定主题", "模板", "分类", "其他", "未分类", "单人", "占位版式", "通用风格与版式", "作品集", "工作汇报"}
_INVALID_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_INDEXES = ("CATALOG.jsonl", "DESIGN-STRUCTURE.jsonl", "DEEP-INDEX.jsonl",
            "DESIGN-LANGUAGE.json", "REVIEWED-SAMPLES.json", "分类记录.csv",
            "CONTENT-CURATION.jsonl")
_ALIASES = "RENAME-ALIASES.jsonl"
_MIGRATION_SCHEMA = "aippt.library-migration-plan.v1"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _opaque(stem: str) -> bool:
    compact = re.sub(r"[\s_().-]+", "", stem)
    if not compact:
        return False
    if re.fullmatch(r"[0-9a-fA-F]{10,}", compact):
        return True
    if re.fullmatch(r"\d+[A-Za-z0-9]*", compact):
        return True
    return bool(re.match(r"^\d{3,}[-_ ]", stem) and len(re.findall(r"\d", stem)) >= 5)


def _clean(value: str, *, maximum: int = 28) -> str:
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", str(value or ""))
    lines = text.splitlines()
    if not lines:
        return ""
    text = lines[0]
    text = _INVALID_NAME.sub(" ", text)
    text = re.sub(r"[\s—–·]+", " ", text).strip(" _.-（）()，。:：;；")
    text = re.sub(r"\s+", " ", text)
    return text[:maximum].strip(" _.-")


def _usable_evidence(value: str, *, maximum: int) -> str:
    """Clean evidence while refusing truncation and known filler/credit text."""
    raw = str(value or "").strip().splitlines()[0].strip() if str(value or "").strip() else ""
    if not raw or len(raw) > maximum:
        return ""
    phrase = _clean(raw, maximum=maximum).lstrip("◎●○■□◆◇☆★▶▷•◦ ")
    folded = phrase.casefold().strip(" .,:;|—-_")
    if (not phrase or folded in {item.casefold() for item in _GENERIC}
            or folded in _GENERIC_ENGLISH or _BAD_TITLE.search(phrase)
            or re.search(r"(?:及|和|与|的|为|在|对|把|将)$", phrase)):
        return ""
    # A one- or two-word English banner is usually a section label, brand, or
    # watermark. Keep the cover naming path for Chinese titles and full phrases.
    if (re.search(r"[A-Za-z]", phrase) and not re.search(r"[\u3400-\u9fff]", phrase)
            and len(re.findall(r"[A-Za-z]+", phrase)) < 3):
        return ""
    return phrase


def _subject(slides: list[dict], category: str) -> tuple[str, str]:
    # Use the first visible slide only. A body page title can be a KPI or a
    # claim, and using it as the file identity would misname the whole deck.
    for page in slides[:1]:
        title = _usable_evidence(str(page.get("title", "")), maximum=28)
        source = str(page.get("title_source") or "")
        confidence = float(page.get("title_confidence") or 0)
        valid_text = (len(title) >= 5
                      and len(re.findall(r"[\u3400-\u9fffA-Za-z]", title)) >= 4
                      and len(re.findall(r"\d", title)) <= len(title) / 3)
        if (valid_text and source in {"title_placeholder", "title_shape", "placeholder_title"}
                and confidence >= 0.75):
            return title, "slide_title"
        # Secondary path for a meaningful large-font cover headline. First
        # visible slide only, with strict fragment/template-placeholder filters.
        if valid_text and source == "largest_text" and confidence >= 0.4:
            return title, "cover_headline"
    # Generic templates often have placeholder text; retain the category as
    # an honest subject instead of inventing a theme from filler copy.
    parts = [part for part in Path(category).parts if part not in {"模板库", "PPT模板", "PPT", "含内容"}]
    for part in reversed(parts):
        if part.startswith("打开文件夹查看"):
            continue
        # A category can name the actual theme even when its directory label
        # ends in a generic packaging phrase such as "PPT模板". Remove only
        # that packaging phrase so the precise theme survives.
        descriptive = re.sub(r"\d+套|\d+款|\d+例", "", part)
        descriptive = re.sub(r"(?i)PPT模板|PPT文件|PPTS?", "", descriptive)
        cleaned = _clean(descriptive)
        if (len(cleaned) >= 2 and re.search(r"[\u3400-\u9fff]", cleaned)
                and cleaned not in _GENERIC_CATEGORIES and not _BAD_TITLE.search(cleaned)):
            return cleaned, "category_fallback"
    return "", "unavailable"


def _content_qualifiers(slides: list[dict], subject: str, basis: str) -> list[str]:
    """Use verified early body-page titles only to distinguish named covers."""
    if basis not in {"slide_title", "cover_headline"}:
        return []
    candidates: list[str] = []
    for slide in slides[1:4]:
        if (slide.get("title_source") not in {"title_placeholder", "title_shape", "placeholder_title"}
                or float(slide.get("title_confidence") or 0) < 0.75):
            continue
        phrase = _usable_evidence(str(slide.get("title") or ""), maximum=20)
        if (len(phrase) >= 5 and phrase != subject
                and len(re.findall(r"[\u3400-\u9fffA-Za-z]", phrase)) >= 4 and phrase not in candidates):
            candidates.append(phrase)
    return candidates


def _role(slides: list[dict], asset_type: str) -> str:
    if asset_type == "diagram_asset":
        return "图表素材"
    first = slides[0] if slides else {}
    roles = first.get("role_candidates", [])
    names = {item if isinstance(item, str) else item.get("role") for item in roles if isinstance(item, (str, dict))}
    if "cover" in names:
        return "封面与正文" if len(slides) > 2 else "封面版式"
    counts = (first.get("composition") or {}).get("object_counts", {})
    if counts.get("picture") and counts.get("text_shape"):
        return "图文版式"
    if counts.get("graphic_frame") or counts.get("connector"):
        return "图表流程"
    return "正文版式"


def propose_library_renames(catalog: Path, depth_index: Path, *, limit: int | None = None) -> dict[str, Any]:
    """Return high-confidence file moves without changing any library bytes."""
    catalog = catalog.resolve()
    library = catalog.parent
    rows = [json.loads(line) for line in catalog.open(encoding="utf-8-sig") if line.strip()]
    by_path = {row.get("path"): row for row in rows if isinstance(row, dict)}
    depth = [json.loads(line) for line in depth_index.open(encoding="utf-8-sig") if line.strip()]
    reserved = {str(row.get("path")).casefold() for row in rows}
    proposals: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for record in depth:
        old = record.get("path")
        if not isinstance(old, str) or old not in by_path or record.get("parse_status") != "parsed":
            continue
        rel = Path(old)
        if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() != ".pptx" or not _opaque(rel.stem):
            continue
        row = by_path[old]
        source = (library / rel).resolve()
        if library not in source.parents or source.is_symlink() or not source.is_file():
            skipped["missing_or_unsafe"] = skipped.get("missing_or_unsafe", 0) + 1
            continue
        stat = source.stat()
        if stat.st_size != record.get("source_bytes") or stat.st_mtime_ns != record.get("mtime_ns"):
            skipped["stale_depth_record"] = skipped.get("stale_depth_record", 0) + 1
            continue
        expected = record.get("expected_sha256")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            skipped["missing_expected_hash"] = skipped.get("missing_expected_hash", 0) + 1
            continue
        slides = record.get("slides") if isinstance(record.get("slides"), list) else []
        subject, basis = _subject(slides, str(rel.parent))
        if not subject:
            skipped["no_meaningful_subject"] = skipped.get("no_meaningful_subject", 0) + 1
            continue
        descriptor = _role(slides, str(row.get("asset_type") or "template"))
        name = _clean(f"{subject}-{descriptor}", maximum=52)
        if not name:
            continue
        target = rel.with_name(name + rel.suffix).as_posix()
        if target.casefold() in reserved or (library / target).exists():
            for qualifier in _content_qualifiers(slides, subject, basis):
                qualified = _clean(f"{subject}-{qualifier}-{descriptor}", maximum=72)
                candidate = rel.with_name(qualified + rel.suffix).as_posix()
                if candidate.casefold() not in reserved and not (library / candidate).exists():
                    target = candidate
                    break
        if target.casefold() in reserved or (library / target).exists():
            target = rel.with_name(f"{name}-{expected[:7]}{rel.suffix}").as_posix()
        if target.casefold() in reserved or (library / target).exists():
            skipped["collision"] = skipped.get("collision", 0) + 1
            continue
        reserved.add(target.casefold())
        proposals.append({"old_path": old, "new_path": target, "expected_sha256": expected,
                          "source_bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns,
                          "naming_basis": basis, "subject": subject, "role": descriptor,
                          "source_title": slides[0].get("title", "") if slides else ""})
    proposals.sort(key=lambda item: (item["naming_basis"] not in {"slide_title", "cover_headline"}, item["old_path"]))
    if limit is not None:
        proposals = proposals[:max(0, limit)]
    return {"schema": "aippt.library-rename-plan.v1", "catalog": str(catalog), "depth_index": str(depth_index.resolve()),
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "planned",
            "proposals": proposals, "skipped": skipped,
            "rule": "Only opaque filenames with current source evidence; first-slide title or filtered largest-text cover headline first, category fallback identified separately."}


def _replace_paths(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, str):
        if value in mapping:
            return mapping[value]
        if value.startswith("模板库/") and value[4:] in mapping:
            return "模板库/" + mapping[value[4:]]
        return value
    if isinstance(value, list):
        return [_replace_paths(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: _replace_paths(item, mapping) for key, item in value.items()}
    return value


def _updated_index(path: Path, mapping: dict[str, str]) -> bytes:
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.open(encoding="utf-8-sig") if line.strip()]
        updated = [_replace_paths(row, mapping) for row in rows]
        if path.name == "CATALOG.jsonl":
            # Keep the displayed name aligned with the new descriptive stem.
            for row in updated:
                if row.get("path") in mapping.values():
                    display = Path(row["path"]).stem
                    if row.get("title") and _opaque(Path(str(row.get("title"))).stem):
                        row["title"] = display
                    if row.get("display_name") and _opaque(Path(str(row.get("display_name"))).stem):
                        row["display_name"] = display
                rel = Path(str(row.get("path", "")))
                if rel.parts:
                    row["category"] = rel.parts[0]
                    row["subcategory"] = "/".join(rel.parts[1:-1])
        elif path.name == "DESIGN-STRUCTURE.jsonl":
            # Structural measurements are unchanged by a rename, while their
            # directory-derived category labels must follow the new work path.
            for row in updated:
                if row.get("source_path") not in mapping.values():
                    continue
                rel = Path(str(row["source_path"]))
                row["category"] = "/".join(rel.parts[:-1])
                row["category_group"] = rel.parts[0]
        return ("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in updated)).encode()
    if path.suffix == ".json":
        return (json.dumps(_replace_paths(json.loads(path.read_text(encoding="utf-8-sig")), mapping),
                           ensure_ascii=False, indent=2) + "\n").encode()
    if path.name == "分类记录.csv":
        import io
        with path.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        if not rows:
            return path.read_bytes()
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            row["final_path"] = mapping.get(row.get("final_path", ""), row.get("final_path", ""))
            if row.get("final_path") in mapping.values():
                display = Path(row["final_path"]).stem
                if row.get("display_title") and _opaque(Path(str(row.get("display_title"))).stem):
                    row["display_title"] = display
            writer.writerow(row)
        return ("\ufeff" + out.getvalue()).encode("utf-8")
    raise ValueError(f"unsupported index: {path}")


def apply_library_renames(plan: dict[str, Any], *, backup_dir: Path) -> dict[str, Any]:
    """Apply verified moves and update active indexes, rolling back on failure."""
    if plan.get("schema") != "aippt.library-rename-plan.v1" or plan.get("status") != "planned":
        raise ValueError("invalid or already applied library rename plan")
    catalog = Path(str(plan["catalog"])).resolve()
    library = catalog.parent
    proposals = plan.get("proposals")
    if not isinstance(proposals, list):
        raise ValueError("rename proposals must be a list")
    mapping: dict[str, str] = {}
    new_paths_casefold: set[str] = set()
    for item in proposals:
        old, new = item["old_path"], item["new_path"]
        if old in mapping or new.casefold() in new_paths_casefold:
            raise ValueError("duplicate rename path")
        source, target = (library / old).resolve(), (library / new).resolve()
        if library not in source.parents or library not in target.parents or target.exists():
            raise ValueError(f"unsafe or occupied rename target: {new}")
        if (source.is_symlink() or not source.is_file() or source.stat().st_size != item["source_bytes"]
                or source.stat().st_mtime_ns != item["mtime_ns"]
                or _hash(source) != item["expected_sha256"]):
            raise ValueError(f"source changed after rename planning: {old}")
        mapping[old] = new
        new_paths_casefold.add(new.casefold())
    indexes = [library / name for name in _INDEXES if (library / name).is_file()]
    if catalog not in indexes:
        raise ValueError("catalog is outside the managed library")
    backup_dir = backup_dir.resolve()
    if backup_dir.exists():
        raise ValueError("backup directory already exists")
    updated = {path: _updated_index(path, mapping) for path in indexes}
    alias_path = library / _ALIASES
    old_aliases = alias_path.read_bytes() if alias_path.exists() else b""
    backup_dir.mkdir(parents=True)
    try:
        for path in indexes:
            shutil.copy2(path, backup_dir / path.name)
        if alias_path.exists():
            shutil.copy2(alias_path, backup_dir / alias_path.name)
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise


    moved: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    try:
        for old, new in mapping.items():
            source, target = library / old, library / new
            source.rename(target)
            moved.append((source, target))
        for path, content in updated.items():
            temporary = path.with_name(path.name + ".rename-tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
            replaced.append(path)
        prior_aliases = [json.loads(line) for line in old_aliases.decode("utf-8-sig").splitlines() if line.strip()]
        now = datetime.now(timezone.utc).isoformat()
        prior_aliases.extend({"schema": "aippt.library-path-alias.v1", "old_path": old, "current_path": new,
                              "source_sha256": next(x["expected_sha256"] for x in proposals if x["old_path"] == old),
                              "renamed_at": now} for old, new in mapping.items())
        alias_tmp = alias_path.with_name(alias_path.name + ".rename-tmp")
        alias_tmp.write_text("".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in prior_aliases), encoding="utf-8")
        alias_tmp.replace(alias_path)
        manifest = {"schema": "aippt.library-rename-manifest.v1", "applied_at": datetime.now(timezone.utc).isoformat(),
                    "catalog": str(catalog), "backup_dir": str(backup_dir), "moves": proposals,
                    "index_backups": [path.name for path in indexes] + [_ALIASES],
                    "alias_index": _ALIASES, "alias_existed_before": bool(old_aliases), "status": "applied"}
        (backup_dir / "rename-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
    except Exception:
        for path in replaced:
            backup = backup_dir / path.name
            if backup.exists():
                temp = path.with_name(path.name + ".rollback-tmp")
                shutil.copy2(backup, temp)
                temp.replace(path)
        if old_aliases:
            alias_tmp = alias_path.with_name(alias_path.name + ".rollback-tmp")
            alias_tmp.write_bytes(old_aliases)
            alias_tmp.replace(alias_path)
        elif alias_path.exists():
            alias_path.unlink()
        for source, target in reversed(moved):
            if target.exists():
                target.rename(source)
        raise


def _safe_library_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("migration paths must be non-empty relative paths")
    rel = Path(value)
    allowed = {".pptx", ".ppt", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp",
               ".doc", ".docx", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".mp4", ".mov"}
    if rel.is_absolute() or ".." in rel.parts or rel.suffix.lower() not in allowed:
        raise ValueError(f"unsafe or unsupported migration path: {value}")
    return rel


def _reject_symlink_components(root: Path, rel: Path) -> None:
    """Check lexical components before resolving so links cannot redirect a path."""
    cursor = root
    for part in rel.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f"symlink component is not allowed: {cursor}")


def _ensure_transaction_paths_available(paths: list[Path]) -> None:
    for path in paths:
        if path.exists() or path.is_symlink():
            raise ValueError(f"migration transaction path already exists: {path}")


def _write_new_bytes(path: Path, payload: bytes, created: set[Path] | None = None) -> None:
    with path.open("xb") as stream:
        if created is not None:
            created.add(path)
        stream.write(payload)


def apply_library_migration(plan: dict[str, Any], *, backup_dir: Path) -> dict[str, Any]:
    """Transactionally apply a complete, caller-reviewed file mapping.

    Unlike the heuristic rename helper, this accepts one explicit row for every
    in-scope file and supports category moves, legacy PPT, and PDF. Source
    bytes are only moved; every source SHA-256 is checked before and after.
    """
    if plan.get("schema") != _MIGRATION_SCHEMA or plan.get("status") != "planned":
        raise ValueError("invalid or already applied library migration plan")
    catalog = Path(str(plan["catalog"])).resolve()
    library = catalog.parent
    rows = plan.get("files")
    if not isinstance(rows, list) or not rows:
        raise ValueError("migration files must be a non-empty list")
    if plan.get("total_files") is not None and plan["total_files"] != len(rows):
        raise ValueError("migration total_files does not match plan rows")

    mapping: dict[str, str] = {}
    hashes: dict[str, str] = {}
    source_paths: dict[str, Path] = {}
    target_paths: dict[str, Path] = {}
    target_keys: set[str] = set()
    real_sources: set[Path] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("migration rows must be objects")
        old_rel = _safe_library_relative(row.get("old_path"))
        new_rel = _safe_library_relative(row.get("new_path"))
        old, new = old_rel.as_posix(), new_rel.as_posix()
        digest = row.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"invalid SHA-256 for {old}")
        if old in hashes:
            raise ValueError(f"duplicate source path: {old}")
        _reject_symlink_components(library, old_rel)
        _reject_symlink_components(library, new_rel)
        src, dst = (library / old_rel).resolve(), (library / new_rel).resolve()
        if library not in src.parents or library not in dst.parents or not src.is_file():
            raise ValueError(f"unsafe or missing migration source: {old}")
        if src in real_sources:
            raise ValueError(f"multiple source rows resolve to the same file: {old}")
        real_sources.add(src)
        if _hash(src) != digest:
            raise ValueError(f"source changed after migration planning: {old}")
        # APFS commonly treats canonically equivalent Unicode spellings as the
        # same name. Reject those collisions on every host, before any moves.
        key = unicodedata.normalize("NFKC", new).casefold()
        if key in target_keys:
            raise ValueError(f"duplicate migration target: {new}")
        target_keys.add(key)
        mapping[old], hashes[old] = new, digest
        source_paths[old], target_paths[old] = src, dst

    baseline = plan.get("baseline_sources")
    if baseline is not None:
        if not isinstance(baseline, list):
            raise ValueError("baseline_sources must be a list of path/SHA-256 rows")
        if any(not isinstance(item, dict) or not isinstance(item.get("path"), str)
               or not isinstance(item.get("sha256"), str) for item in baseline):
            raise ValueError("baseline_sources rows require string path and sha256 fields")
        expected = {(item.get("path"), item.get("sha256")) for item in baseline if isinstance(item, dict)}
        supplied = set(zip(hashes.keys(), hashes.values()))
        if len(expected) != len(baseline) or expected != supplied:
            raise ValueError("migration rows do not match the independent baseline source set")

    moving_sources = {unicodedata.normalize("NFKC", str(path)).casefold()
                      for old, path in source_paths.items() if mapping[old] != old}
    source_keys = {unicodedata.normalize("NFKC", str(path)).casefold(): old
                   for old, path in source_paths.items()}
    existing_keys = {unicodedata.normalize("NFKC", str(path.resolve())).casefold()
                     for path in library.rglob("*") if path.exists()}
    for old, dst in target_paths.items():
        target_key = unicodedata.normalize("NFKC", str(dst)).casefold()
        occupied_source = source_keys.get(target_key)
        if (mapping[old] != old and
                ((target_key in existing_keys and target_key not in moving_sources)
                 or (occupied_source is not None and mapping[occupied_source] == occupied_source))):
            raise ValueError(f"occupied migration target: {mapping[old]}")
    indexes = [library / name for name in _INDEXES if (library / name).is_file()]
    if catalog not in indexes:
        raise ValueError("catalog is outside the managed library")
    managed_indexes = [*indexes, library / _ALIASES]
    for path in managed_indexes:
        if path.is_symlink():
            raise ValueError(f"managed index cannot be a symlink: {path.name}")
    backup_dir = backup_dir.resolve()
    if backup_dir.exists():
        raise ValueError("backup directory already exists")
    if library == backup_dir or library in backup_dir.parents or backup_dir in library.parents:
        raise ValueError("backup directory must be outside the library tree")
    alias_path = library / _ALIASES
    transaction_temps = [path.with_name(path.name + suffix)
                         for path in [*indexes, alias_path]
                         for suffix in (".migration-tmp", ".migration-rollback-tmp")]
    transaction_temps.extend(library / f".library-migration-{index}-{source.name}"
                             for index, (old, source) in enumerate(source_paths.items())
                             if mapping[old] != old)
    _ensure_transaction_paths_available(transaction_temps)
    active_mapping = {old: new for old, new in mapping.items() if old != new}
    if not active_mapping:
        raise ValueError("migration plan contains no moves; refusing to rewrite indexes")
    updated = {path: _updated_index(path, active_mapping) for path in indexes}
    alias_existed = alias_path.exists()
    old_aliases = alias_path.read_bytes() if alias_path.exists() else b""
    try:
        aliases = [json.loads(line) for line in old_aliases.decode("utf-8-sig").splitlines() if line.strip()]
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError("existing rename aliases are invalid") from error

    # Apply this batch once to prior current paths so swaps and chained targets
    # do not cascade through multiple mappings in the same transaction.
    for alias in aliases:
        if not isinstance(alias, dict) or not isinstance(alias.get("old_path"), str):
            continue
        current = alias.get("current_path")
        if isinstance(current, str) and current in active_mapping:
            alias["current_path"] = active_mapping[current]
    now = datetime.now(timezone.utc).isoformat()
    for old, new in active_mapping.items():
        aliases = [a for a in aliases if not (isinstance(a, dict) and a.get("old_path") == old)]
        aliases.append({"schema": "aippt.library-path-alias.v1", "old_path": old,
                        "current_path": new, "source_sha256": hashes[old], "renamed_at": now})

    backup_dir.mkdir(parents=True)
    try:
        for path in indexes:
            shutil.copy2(path, backup_dir / path.name)
        if alias_path.exists():
            shutil.copy2(alias_path, backup_dir / alias_path.name)
    except Exception:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise

    staged: dict[str, Path] = {}
    completed: list[tuple[Path, Path]] = []
    replaced: list[Path] = []
    created_dirs: set[Path] = set()
    created_temps: set[Path] = set()
    try:
        # Stage all moving sources first so swaps and occupied moving targets work.
        for index, (old, src) in enumerate(source_paths.items()):
            if mapping[old] == old:
                continue
            temp = library / f".library-migration-{index}-{src.name}"
            if temp.exists():
                raise ValueError(f"migration staging path occupied: {temp.name}")
            src.rename(temp)
            staged[old] = temp
            created_temps.add(temp)
        for old, temp in staged.items():
            dst = target_paths[old]
            missing: list[Path] = []
            parent = dst.parent
            while parent != library and library in parent.parents and not parent.exists():
                missing.append(parent)
                parent = parent.parent
            created_dirs.update(missing)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                raise ValueError(f"occupied migration target after staging: {mapping[old]}")
            temp.rename(dst)
            completed.append((source_paths[old], dst))
            created_temps.discard(temp)
            if _hash(dst) != hashes[old]:
                raise OSError(f"source bytes changed during migration: {old}")
        for path, content in updated.items():
            temporary = path.with_name(path.name + ".migration-tmp")
            _write_new_bytes(temporary, content, created_temps)
            temporary.replace(path)
            created_temps.discard(temporary)
            replaced.append(path)
        alias_tmp = alias_path.with_name(alias_path.name + ".migration-tmp")
        _write_new_bytes(alias_tmp, "".join(json.dumps(a, ensure_ascii=False, separators=(",", ":")) + "\n"
                                             for a in aliases).encode("utf-8"), created_temps)
        alias_tmp.replace(alias_path)
        created_temps.discard(alias_tmp)
        manifest = {"schema": "aippt.library-migration-manifest.v1",
                    "applied_at": datetime.now(timezone.utc).isoformat(), "catalog": str(catalog),
                    "backup_dir": str(backup_dir), "files": rows,
                    "moved_count": len(active_mapping), "index_backups": [p.name for p in indexes] + [_ALIASES],
                    "status": "applied"}
        (backup_dir / "library-migration-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
    except Exception:
        for path in replaced:
            backup = backup_dir / path.name
            if backup.exists():
                temp = path.with_name(path.name + ".migration-rollback-tmp")
                _write_new_bytes(temp, backup.read_bytes(), created_temps)
                temp.replace(path)
                created_temps.discard(temp)
        if alias_existed:
            temp = alias_path.with_name(alias_path.name + ".migration-rollback-tmp")
            _write_new_bytes(temp, old_aliases, created_temps)
            temp.replace(alias_path)
            created_temps.discard(temp)
        elif alias_path.exists():
            alias_path.unlink()
        for source, destination in reversed(completed):
            if destination.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                destination.rename(source)
        for old, temp in reversed(list(staged.items())):
            if temp.exists():
                source = source_paths[old]
                source.parent.mkdir(parents=True, exist_ok=True)
                temp.rename(source)
        # Remove only now-empty directories created by this migration.
        for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass
        for temp in created_temps:
            temp.unlink(missing_ok=True)
        raise
