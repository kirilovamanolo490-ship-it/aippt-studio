#!/usr/bin/env python3
"""Build offline status reports for the Gaojie cultural library downloads.

Reads the captured inventory, append-only detail/download receipts, and the
archive receipt. It makes no network requests and embeds no remote previews.
Only existing archive files whose SHA-256 matches the archive receipt receive
a local open link.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import quote
from zoneinfo import ZoneInfo


PROJECT = Path(__file__).resolve().parents[1]
LIBRARY = PROJECT / "模板库"
COLLECTION_ROOT = LIBRARY / "高阶文化"
INVENTORY = LIBRARY / "图表与视觉素材" / "版式与排版参考" / "高阶文化-待下载清单" / "资源清单.jsonl"
STATE_ROOT = PROJECT / "aippt-studio" / ".maintenance" / "gaojie-downloads"
DETAILS = STATE_ROOT / "详情链接.jsonl"
DOWNLOADS = STATE_ROOT / "下载回执.jsonl"
ARCHIVE_RECEIPT = COLLECTION_ROOT / "归档回执.jsonl"
CORRUPT_EVIDENCE = STATE_ROOT / "源文件损坏证据.json"
EXPECTED_TOTAL = 6578
EXPECTED_EXCLUDED = 196
EXCLUDED_CATEGORY_ID = "57"
STATUSES = ("成功", "源文件损坏已保留", "待归档", "待下载", "链接待解析", "需核查", "网站无附件", "非PPTX跳过", "失败")
GROUP_ORDER = ("演示结构", "内容排版", "关系与图表", "素材与组件", "专题与案例")


def read_jsonl(path: Path, *, optional: bool = False) -> list[dict]:
    if not path.is_file():
        if optional:
            return []
        raise FileNotFoundError(path)
    rows: list[dict] = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: JSON 无效: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: 每行必须为对象")
            row["_line"] = line_number
            rows.append(row)
    return rows


def latest_by_id(rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in rows:
        item_id = str(row.get("id", "")).strip()
        if item_id:
            latest[item_id] = row
    return latest


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_archives(rows: list[dict], archive_root: Path) -> dict[str, dict]:
    """Index only current, in-root files with a matching receipt digest."""
    valid: dict[str, dict] = {}
    root = archive_root.resolve(strict=True)
    for row in rows:
        item_id = str(row.get("id", "")).strip()
        if not item_id or row.get("archive_status") not in {"archived", "deduplicated"}:
            continue
        archived_value = row.get("archived_path")
        digest = str(row.get("sha256", "")).lower()
        if not isinstance(archived_value, str) or not archived_value or len(digest) != 64:
            continue
        path = Path(archived_value)
        if not path.is_absolute():
            path = archive_root / path
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_file() or (resolved != root and root not in resolved.parents):
                continue
            actual = sha256(resolved)
            if actual != digest:
                continue
            # If duplicate valid archive receipts exist for an ID, keep the
            # last valid row, consistent with append-only receipt ordering.
            valid[item_id] = {**row, "_resolved_path": resolved, "_sha256_verified": actual}
        except OSError:
            continue
    return valid


def verified_corrupt_retention(path: Path, inventory: list[dict], archive_root: Path) -> dict[str, dict]:
    """Return only retained corrupt originals backed by the complete evidence record."""
    if not path.is_file():
        return {}
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    item = evidence.get("item") if isinstance(evidence, dict) else None
    files = evidence.get("files") if isinstance(evidence, dict) else None
    if (not isinstance(item, dict) or not isinstance(files, list) or len(files) != 2
            or evidence.get("source_download_reproduced") is not True
            or not evidence.get("comparison") or not all(isinstance(entry, dict) for entry in files)
            or str(files[0].get("path", "")) == str(files[1].get("path", ""))):
        return {}
    item_id = str(item.get("id", "")).strip()
    candidates = [row for row in inventory if str(row.get("id", "")).strip() == item_id]
    if len(candidates) != 1:
        return {}
    source = candidates[0]
    if (str(source.get("category_id", "")) != str(item.get("category_id", ""))
            or str(source.get("category", "")) != str(item.get("category", ""))
            or str(source.get("title", "")) != str(item.get("title", ""))
            or str(source.get("detail_url", "")) != str(item.get("detail_url", ""))
            or str(item.get("observed_download_url", "")) == ""):
        return {}
    try:
        measurements = []
        for entry in files:
            candidate = Path(str(entry.get("path", ""))).resolve(strict=True)
            digest = str(entry.get("sha256", "")).lower()
            byte_count = int(entry.get("bytes", -1))
            if not candidate.is_file() or len(digest) != 64 or candidate.stat().st_size != byte_count or sha256(candidate) != digest:
                return {}
            measurements.append((byte_count, digest))
        if measurements[0] != measurements[1]:
            return {}
        retained = Path(str(evidence.get("retained_path", ""))).resolve(strict=True)
        category_dir = (archive_root / str(source.get("category", ""))).resolve(strict=True)
        if (not retained.is_file() or retained.parent != category_dir
                or retained.suffix.lower() != ".pptx"
                or not retained.name.startswith(f"{item_id} -")
                or "网站原文件损坏" not in retained.name):
            return {}
        expected_bytes = int(evidence.get("retained_bytes", -1))
        expected_digest = str(evidence.get("retained_sha256", "")).lower()
        if (evidence.get("retention_status") != "corrupt_source_preserved_without_modification"
                or expected_bytes != measurements[0][0] or expected_digest != measurements[0][1]
                or retained.stat().st_size != expected_bytes or sha256(retained) != expected_digest):
            return {}
    except (OSError, ValueError, TypeError):
        return {}
    return {item_id: {"_resolved_path": retained, "_sha256_verified": expected_digest,
                      "_bytes_verified": expected_bytes, "evidence": evidence}}


def classify(item: dict, detail: dict | None, download: dict | None,
             archived: dict | None, corrupt_retained: dict | None,
             archive_receipt: dict | None, archive_root: Path) -> dict:
    row = dict(item)
    row["detail_status"] = str((detail or {}).get("status", ""))
    row["download_receipt_status"] = str((download or {}).get("status", ""))
    row["resolved_download_url"] = str((detail or {}).get("url", "")) if row["detail_status"] == "resolved" else ""
    row["local_open_path"] = ""
    row["archive_relative_path"] = ""
    row["retained_corrupt_path"] = ""
    row["retained_corrupt_relative_path"] = ""
    row["bytes"] = 0
    row["sha256"] = ""
    row["failure_detail"] = ""

    if archived:
        path: Path = archived["_resolved_path"]
        row["status"] = "成功"
        row["status_detail"] = "已归档，文件存在且 SHA-256 与归档回执一致"
        row["local_open_path"] = str(path)
        row["archive_relative_path"] = path.relative_to(archive_root.resolve()).as_posix()
        row["bytes"] = path.stat().st_size
        row["sha256"] = archived["_sha256_verified"]
        row["source_url"] = str(row.get("detail_url", ""))
        row["archive_download_url"] = str(archived.get("url", ""))
        return row

    if corrupt_retained:
        path: Path = corrupt_retained["_resolved_path"]
        row["status"] = "源文件损坏已保留"
        row["status_detail"] = "网站原始PPTX经两次同源下载确认内容损坏；原件按原字节保留，不作为完整模板"
        row["retained_corrupt_path"] = str(path)
        row["retained_corrupt_relative_path"] = path.relative_to(archive_root.resolve()).as_posix()
        row["bytes"] = corrupt_retained["_bytes_verified"]
        row["sha256"] = corrupt_retained["_sha256_verified"]
        row["source_url"] = str((download or {}).get("detail_url") or row.get("detail_url", ""))
        return row

    if archive_receipt and archive_receipt.get("archive_status") == "failed":
        row["status"] = "失败"
        row["status_detail"] = f"归档回执失败：{archive_receipt.get('reason') or archive_receipt.get('format_check') or '原因未记录'}"
        row["failure_detail"] = str(archive_receipt.get("reason") or archive_receipt.get("format_check") or "archive_failed")
        row["source_url"] = str((download or {}).get("detail_url") or row.get("detail_url", ""))
        return row

    if download is not None:
        dl_status = row["download_receipt_status"]
        if dl_status == "browser_security_review_required":
            row["status"] = "需核查"
            row["status_detail"] = "浏览器阻止不安全下载，等待用户在浏览器中自行判断"
            row["source_url"] = str(download.get("detail_url") or row.get("detail_url", ""))
            return row
        if dl_status == "excluded_non_pptx":
            row["status"] = "非PPTX跳过"
            row["status_detail"] = "网站附件不是PPTX，按用户要求跳过"
            row["source_url"] = str(download.get("detail_url") or row.get("detail_url", ""))
            return row
        if dl_status == "downloaded":
            local_value = download.get("local_path")
            local = Path(str(local_value)).expanduser() if local_value else None
            if local is not None and local.is_file():
                row["status"] = "待归档"
                row["status_detail"] = "下载文件存在，等待归档；暂不提供本地打开路径"
                row["bytes"] = local.stat().st_size
                row["source_url"] = str(download.get("detail_url") or row.get("detail_url", ""))
                row["archive_download_url"] = str(download.get("download_url", ""))
                return row
            row["status"] = "失败"
            row["status_detail"] = "下载回执标记成功，但本地下载文件不存在"
            row["failure_detail"] = "local_file_missing_after_downloaded_receipt"
            row["source_url"] = str(download.get("detail_url") or row.get("detail_url", ""))
            return row
        if dl_status not in {"", "stopped"}:
            row["status"] = "失败"
            row["status_detail"] = f"最新下载回执状态：{dl_status}"
            row["failure_detail"] = str(download.get("error_type") or download.get("status") or "")
            row["source_url"] = str(download.get("detail_url") or row.get("detail_url", ""))
            return row

    detail_status = row["detail_status"]
    if detail_status == "resolved":
        row["status"] = "待下载"
        row["status_detail"] = "详情页链接已解析，等待下载"
    elif detail_status == "no_attachment":
        row["status"] = "网站无附件"
        row["status_detail"] = "详情回执明确记录该页面没有附件"
    elif detail_status in {"missing_or_ambiguous_download", "needs_browser_verification"}:
        row["status"] = "需核查"
        row["status_detail"] = "详情页未能确认唯一附件；需人工核查页面状态"
    elif detail_status in {"", "stopped"}:
        row["status"] = "链接待解析"
        row["status_detail"] = "尚未取得唯一、可用的下载链接"
    else:
        row["status"] = "失败"
        row["status_detail"] = f"最新详情链接回执状态：{detail_status}"
        row["failure_detail"] = str(detail.get("error_type") or detail.get("status") or "") if detail else detail_status
    row["source_url"] = str((detail or {}).get("detail_url") or row.get("detail_url", ""))
    return row


def build(inventory_path: Path, details_path: Path, downloads_path: Path,
          archive_receipt_path: Path, archive_root: Path,
          corrupt_evidence_path: Path = CORRUPT_EVIDENCE) -> tuple[dict, list[dict], list[dict]]:
    inventory = read_jsonl(inventory_path)
    excluded = [row for row in inventory if str(row.get("category_id", "")) == EXCLUDED_CATEGORY_ID]
    included = [row for row in inventory if str(row.get("category_id", "")) != EXCLUDED_CATEGORY_ID]
    ids = [str(row.get("id", "")).strip() for row in included]
    if len(included) != EXPECTED_TOTAL or len(excluded) != EXPECTED_EXCLUDED:
        raise ValueError(f"清单数量不符：可纳入 {len(included)}（预期 {EXPECTED_TOTAL}），排除 {len(excluded)}（预期 {EXPECTED_EXCLUDED}）")
    if len(set(ids)) != len(ids) or any(not item_id for item_id in ids):
        raise ValueError("清单存在空 ID 或重复 ID")

    groups = {str(row.get("group", "")) for row in included}
    categories = {(str(row.get("group", "")), str(row.get("category_id", "")), str(row.get("category", ""))) for row in included}
    if len(groups) != 5 or len({category for _, _, category in categories}) != 31:
        raise ValueError(f"分类拓扑不符：用途标签 {len(groups)}（预期 5），网站分类 {len({category for _, _, category in categories})}（预期 31）")

    details = latest_by_id(read_jsonl(details_path, optional=True))
    downloads = latest_by_id(read_jsonl(downloads_path, optional=True))
    archive_rows = read_jsonl(archive_receipt_path, optional=True)
    archives = verified_archives(archive_rows, archive_root)
    corrupt_retained = verified_corrupt_retention(corrupt_evidence_path, included, archive_root)
    archive_latest = latest_by_id(archive_rows)

    records = [classify(row, details.get(str(row["id"])), downloads.get(str(row["id"])),
                        archives.get(str(row["id"])), corrupt_retained.get(str(row["id"])),
                        archive_latest.get(str(row["id"])), archive_root) for row in included]
    counts = Counter(row["status"] for row in records)
    group_stats: dict[str, Counter] = defaultdict(Counter)
    category_stats: dict[tuple[str, str, str], Counter] = defaultdict(Counter)
    for row in records:
        group = str(row.get("group", "未分组"))
        category_key = (group, str(row.get("category_id", "")), str(row.get("category", "未分类")))
        for counter in (group_stats[group], category_stats[category_key]):
            counter[row["status"]] += 1
            counter["items"] += 1
            counter["bytes"] += int(row.get("bytes", 0))

    groups_out = []
    def stats_dict(counter: Counter) -> dict:
        return {"items": counter["items"], "bytes": counter["bytes"],
                "retained_corrupt": counter["源文件损坏已保留"],
                **{status: counter[status] for status in STATUSES}}

    for group in (*GROUP_ORDER, *sorted(groups - set(GROUP_ORDER))):
        if group not in groups:
            continue
        stats = group_stats[group]
        groups_out.append({"group": group, **stats_dict(stats)})
    categories_out = [
        {"group": group, "category_id": category_id, "category": category,
         **stats_dict(category_stats[(group, category_id, category)])}
        for group, category_id, category in sorted(categories, key=lambda x: (GROUP_ORDER.index(x[0]) if x[0] in GROUP_ORDER else 999, x[2], x[1]))
    ]
    report = {
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "source": {
            "inventory": str(inventory_path), "details_receipts": str(details_path),
            "download_receipts": str(downloads_path), "archive_receipts": str(archive_receipt_path),
            "offline_only": True,
        },
        "counts": {"total": len(records), "excluded_category_57": len(excluded),
                   "groups": len(groups), "categories": len(categories),
                   "by_status": {key: counts.get(key, 0) for key in STATUSES},
                   "archived": sum(bool(row["local_open_path"]) for row in records),
                   "archived_bytes": sum(int(row["bytes"]) for row in records if row["local_open_path"]),
                   "retained_corrupt": counts.get("源文件损坏已保留", 0),
                   "actually_saved": sum(bool(row["local_open_path"]) or row["status"] == "源文件损坏已保留" for row in records)},
        "groups": groups_out,
        "categories": categories_out,
        "excluded": {"category_id": EXCLUDED_CATEGORY_ID, "category": "优秀作品", "count": len(excluded)},
    }
    return report, records, excluded


def render_html(report: dict, records: list[dict]) -> str:
    rows = []
    status_classes = {"成功": "s-成功", "待下载": "s-待下载", "链接待解析": "s-链接待解析", "失败": "s-失败"}
    for row in records:
        source = html.escape(str(row.get("source_url", "")), quote=True)
        source_link = f'<a href="{source}" target="_blank" rel="noopener noreferrer">来源页</a>' if source else ""
        local = ""
        if row.get("local_open_path"):
            relative = quote(str(row["archive_relative_path"]), safe="/.-_~")
            local = f'<a href="{html.escape(relative, quote=True)}">打开归档文件</a>'
        elif row.get("retained_corrupt_path"):
            relative = quote(str(row["retained_corrupt_relative_path"]), safe="/.-_~（）")
            local = f'<a href="{html.escape(relative, quote=True)}">查看保留原件</a><br><small>网站源文件损坏，不能作为完整模板</small>'
        rows.append(
            '<tr data-group="{group}" data-category="{category}" data-status="{status}" data-search="{search}">'
            '<td>{id}</td><td>{title}</td><td>{group}</td><td>{category}</td><td><span class="badge {status_class}">{status}</span><br><small>{detail}</small></td>'
            '<td>{source}</td><td>{local}</td><td>{bytes}</td></tr>'.format(
                group=html.escape(str(row.get("group", "")), quote=True),
                category=html.escape(str(row.get("category", "")), quote=True),
                status=html.escape(str(row.get("status", "")), quote=True),
                status_class=status_classes.get(str(row.get("status", "")), ""),
                search=html.escape(" ".join(str(row.get(k, "")) for k in ("id", "title", "group", "category", "category_id", "status", "status_detail", "failure_detail")), quote=True),
                id=html.escape(str(row.get("id", ""))), title=html.escape(str(row.get("title", ""))),
                detail=html.escape(str(row.get("status_detail", ""))), source=source_link,
                local=local, bytes=f'{int(row.get("bytes", 0)):,}',
            )
        )
    counts = report["counts"]
    categories = sorted({str(row.get("category", "")) for row in records})
    groups = [g["group"] for g in report["groups"]]
    options = lambda values: "".join(f'<option value="{html.escape(v, quote=True)}">{html.escape(v)}</option>' for v in values)
    template = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>高阶文化下载与归档索引</title>
<style>
:root{color-scheme:light;--ink:#17202b;--muted:#667085;--line:#d9e0e8;--blue:#1769aa;--bg:#f4f7fb}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}header{padding:28px max(20px,calc((100% - 1400px)/2));background:#123b5d;color:white}h1{margin:0 0 5px;font-size:24px}header p{margin:0;color:#d6e5f2}.wrap{max-width:1400px;margin:auto;padding:20px}.stats{display:flex;gap:12px;flex-wrap:wrap;margin:0 0 18px}.stat{background:white;border:1px solid var(--line);border-radius:9px;padding:12px 16px;min-width:130px}.stat b{display:block;font-size:21px}.filters{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}.filters input,.filters select{font:inherit;border:1px solid #b9c4d0;border-radius:7px;padding:9px 11px;background:white}.filters input{flex:1;min-width:240px}table{width:100%;border-collapse:collapse;background:white;border:1px solid var(--line);font-size:13px}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid #e7ebf0;vertical-align:top}th{position:sticky;top:0;background:#edf3f8;z-index:1}tbody tr:hover{background:#f7fbff}small{color:var(--muted)}a{color:var(--blue)}.badge{display:inline-block;border-radius:99px;padding:1px 8px;background:#e8edf3;font-weight:600}.s-成功{background:#e4f5eb;color:#126339}.s-待下载{background:#fff2d4;color:#805400}.s-链接待解析{background:#e8efff;color:#304f97}.s-失败{background:#fde9e7;color:#9b271d}.s-源文件损坏已保留{background:#fff0e5;color:#9a4310}.foot{color:var(--muted);font-size:12px;margin-top:12px}.hidden{display:none}
</style></head><body><header><h1>高阶文化下载与归档索引</h1><p>按网站31个分类整理PPTX，排除优秀作品及图片附件；此页显示每项资源的实际下载与归档状态。</p></header><main class="wrap">
<section class="stats"><div class="stat">纳入总数<b>@@TOTAL@@</b></div><div class="stat">完整归档<b>@@ARCHIVED@@</b></div><div class="stat">实际保存<b>@@SAVED@@</b></div><div class="stat">损坏原件已保留<b>@@RETAINED@@</b></div><div class="stat">待下载<b>@@PENDING@@</b></div><div class="stat">链接待解析<b>@@LINK_PENDING@@</b></div><div class="stat">待归档<b>@@ARCHIVE_PENDING@@</b></div><div class="stat">网站无附件<b>@@NO_ATTACHMENT@@</b></div><div class="stat">失败<b>@@FAILED@@</b></div><div class="stat">排除优秀作品<b>@@EXCLUDED@@</b></div></section>
<div class="filters"><input id="q" type="search" placeholder="搜索 ID、标题、分组、分类或状态"><select id="group"><option value="">全部分组</option>@@GROUP_OPTIONS@@</select><select id="category"><option value="">全部分类</option>@@CATEGORY_OPTIONS@@</select><select id="status"><option value="">全部状态</option><option>成功</option><option>源文件损坏已保留</option><option>待归档</option><option>网站无附件</option><option>非PPTX跳过</option><option>待下载</option><option>链接待解析</option><option>需核查</option><option>失败</option></select><span id="shown"></span></div>
<table><thead><tr><th>ID</th><th>标题</th><th>分组</th><th>网站分类</th><th>状态</th><th>来源</th><th>本地归档</th><th>字节</th></tr></thead><tbody id="rows">@@ROWS@@</tbody></table><p class="foot">来源网页链接仅在点击时打开。只有归档回执记录的文件存在且 SHA-256 匹配时，才显示本地归档链接。排除 category 57（优秀作品）@@EXCLUDED@@ 项。</p>
</main><script>
const q=document.querySelector('#q'),g=document.querySelector('#group'),c=document.querySelector('#category'),s=document.querySelector('#status'),trs=[...document.querySelectorAll('#rows tr')],shown=document.querySelector('#shown');function filter(){let n=0;for(const tr of trs){const ok=(!q.value||tr.dataset.search.toLocaleLowerCase().includes(q.value.toLocaleLowerCase()))&&(!g.value||tr.dataset.group===g.value)&&(!c.value||tr.dataset.category===c.value)&&(!s.value||tr.dataset.status===s.value);tr.classList.toggle('hidden',!ok);if(ok)n++}shown.textContent=`显示 ${n.toLocaleString()} / ${trs.length.toLocaleString()} 项`}for(const el of [q,g,c,s])el.addEventListener('input',filter);filter();
</script></body></html>"""
    replacements = {
        "@@TOTAL@@": f'{counts["total"]:,}', "@@ARCHIVED@@": f'{counts["archived"]:,}',
        "@@SAVED@@": f'{counts["actually_saved"]:,}', "@@RETAINED@@": f'{counts["retained_corrupt"]:,}',
        "@@PENDING@@": f'{counts["by_status"]["待下载"]:,}',
        "@@LINK_PENDING@@": f'{counts["by_status"]["链接待解析"]:,}',
        "@@ARCHIVE_PENDING@@": f'{counts["by_status"]["待归档"]:,}',
        "@@NO_ATTACHMENT@@": f'{counts["by_status"]["网站无附件"]:,}',
        "@@FAILED@@": f'{counts["by_status"]["失败"]:,}',
        "@@EXCLUDED@@": f'{report["excluded"]["count"]:,}',
        "@@GROUP_OPTIONS@@": options(groups), "@@CATEGORY_OPTIONS@@": options(categories),
        "@@ROWS@@": "\n".join(rows),
    }
    for marker, value in replacements.items():
        template = template.replace(marker, value)
    return template


def csv_bytes(rows: list[dict], fields: list[str]) -> bytes:
    from io import StringIO
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="离线生成高阶文化下载/归档进度 JSON、CSV 与可搜索 HTML。")
    parser.add_argument("--inventory", type=Path, default=INVENTORY)
    parser.add_argument("--details", type=Path, default=DETAILS)
    parser.add_argument("--downloads", type=Path, default=DOWNLOADS)
    parser.add_argument("--archive-receipt", type=Path, default=ARCHIVE_RECEIPT)
    parser.add_argument("--archive-root", type=Path, default=COLLECTION_ROOT)
    args = parser.parse_args()
    try:
        report, records, _excluded = build(args.inventory, args.details, args.downloads,
                                           args.archive_receipt, args.archive_root)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    group_rows = report["groups"]
    category_rows = report["categories"]
    detail_fields = ["id", "title", "group", "category_id", "category", "status", "status_detail",
                     "failure_detail", "detail_url", "resolved_download_url", "source_url",
                     "archive_download_url", "archive_relative_path", "local_open_path", "retained_corrupt_path", "retained_corrupt_relative_path", "bytes", "sha256"]
    category_fields = ["group", "category_id", "category", "items", "retained_corrupt", *STATUSES, "bytes"]
    targets = {
        args.archive_root / "下载进度.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
        args.archive_root / "下载清单.csv": csv_bytes(records, detail_fields),
        args.archive_root / "分类汇总.csv": csv_bytes(category_rows, category_fields),
        args.archive_root / "可搜索归档索引.html": render_html(report, records).encode("utf-8"),
    }
    for path, data in targets.items():
        write_atomic(path, data)
    print(json.dumps({"output_root": str(args.archive_root), **report["counts"],
                      "states": report["counts"]["by_status"], "files": [str(path) for path in targets]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
