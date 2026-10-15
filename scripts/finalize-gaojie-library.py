#!/usr/bin/env python3
"""Publish the completed Gaojie download accounting from verified local evidence."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "模板库"
ARCHIVE = LIBRARY / "高阶文化"
STATE = ROOT / "aippt-studio/.maintenance/gaojie-downloads"
INVENTORY = LIBRARY / "图表与视觉素材/版式与排版参考/高阶文化-待下载清单/资源清单.jsonl"


def rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    audit_path = STATE / "最终验收.json"
    audit = json.loads(audit_path.read_text())
    assert audit["mode"] == "full" and audit["all_requested_resources_accounted"] is True
    assert audit["available_pptx_complete"] is False
    counts = audit["counts"]
    assert counts["actual_pptx_paths"] == 5694
    assert counts["pptx_archives_integrity_verified"] == 5693
    assert audit["physically_retained_corrupt_ids"] == ["3890"]
    progress = json.loads((ARCHIVE / "下载进度.json").read_text())
    statuses = progress["counts"]["by_status"]
    assert statuses["成功"] == 5693 and statuses["源文件损坏已保留"] == 1
    assert all(statuses.get(key, 0) == 0 for key in ("待归档", "待下载", "链接待解析", "需核查", "失败"))

    inventory = [row for row in rows(INVENTORY) if str(row["category_id"]) != "57"]
    categories = list(dict.fromkeys(row["category"] for row in inventory))
    directories = {path.name for path in ARCHIVE.iterdir() if path.is_dir()}
    assert len(categories) == 31 and directories == set(categories)
    files = list(ARCHIVE.rglob("*.pptx"))
    assert len(files) == 5694 and all(path.parent.name in categories and path.parent.parent == ARCHIVE for path in files)
    relative_files = {path.relative_to(LIBRARY).as_posix() for path in files}
    catalog = [row for row in rows(LIBRARY / "CATALOG.jsonl") if str(row.get("path", "")).startswith("高阶文化/")]
    curation = [row for row in rows(LIBRARY / "CONTENT-CURATION.jsonl") if str(row.get("path", "")).startswith("高阶文化/")]
    for records in (catalog, curation):
        assert len(records) == 5694 and {row["path"] for row in records} == relative_files
        assert Counter(row["asset_type"] for row in records) == {"template": 5693, "reference": 1}
        damaged = [row for row in records if row["asset_type"] == "reference"]
        assert len(damaged) == 1 and "3890 - " in damaged[0]["path"]
        assert damaged[0].get("evidence", {}).get("recommendation_status") == "do_not_recommend"

    damage = json.loads((STATE / "源文件损坏证据.json").read_text())
    retained = Path(damage["retained_path"])
    assert retained.is_file() and sha256(retained) == damage["retained_sha256"]
    item_totals = Counter(row["category"] for row in inventory)
    file_totals = Counter(path.parent.name for path in files)
    no_attachment_ids = {str(row["id"]) for row in audit["normal_page_no_attachment"]}
    no_attachment_totals = Counter(row["category"] for row in inventory if str(row["id"]) in no_attachment_ids)
    category_rows = []
    for category in categories:
        note = ""
        if category == "实用素材":
            note = "含1份网站原文件损坏，已标注保留"
        elif category == "素材图片":
            note = "549张图片跳过，1条无附件"
        elif category == "发布会":
            note = "325份PDF跳过"
        elif no_attachment_totals[category]:
            note = f"{no_attachment_totals[category]}条无附件"
        category_rows.append({"category": category, "website_resources": item_totals[category],
                              "saved_pptx": file_totals[category], "note": note})

    now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
    result = {
        "completed_at": now,
        "status": "download_and_classification_complete_with_source_defect",
        "all_requested_resources_accounted": True,
        "all_source_pptx_downloaded_and_classified": True,
        "all_saved_pptx_integrity_passed": False,
        "archive_root": str(ARCHIVE),
        "resource_scope": 6578, "category_folders": 31,
        "saved_pptx": 5694, "integrity_verified_pptx": 5693,
        "preserved_corrupt_source_pptx": 1,
        "logical_file_bytes": sum(path.stat().st_size for path in files),
        "skipped_image_attachments": counts["verified_image_attachments"],
        "skipped_pdf_attachments": counts["verified_pdf_attachments"],
        "normal_pages_without_attachment": counts["verified_normal_page_no_attachment"],
        "excluded_excellent_works": 196,
        "pending_download": 0, "pending_archive": 0, "unaccounted_resources": 0,
        "corrupt_source": {"id": "3890", "path": str(retained), "sha256": damage["retained_sha256"],
                           "reason": "Two independent source downloads reproduce corrupt ppt/slides/slide15.xml", "original_bytes_preserved": True},
        "categories": category_rows,
        "catalog": {"registered_pptx": len(catalog), "templates_not_visually_reviewed": 5693, "unavailable_reference": 1},
        "validation_scope": "Paths, IDs, category placement, byte sizes, SHA-256, ZIP CRC and required OOXML parts; no native PowerPoint or visual review",
        "evidence": [{"path": str(path), "sha256": sha256(path)} for path in
                     (audit_path, STATE / "源文件损坏证据.json", ARCHIVE / "归档回执.jsonl",
                      STATE / "详情链接.jsonl", STATE / "下载回执.jsonl", LIBRARY / "CONTENT-CURATION.jsonl", LIBRARY / "CATALOG.jsonl")],
    }
    write(ARCHIVE / "本次处理结果.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    task_path = STATE / "持续任务状态.json"
    previous = json.loads(task_path.read_text()) if task_path.exists() else {}
    previous.update({"status": result["status"], "updated_at": now, "pid": None,
                     "reason": "全部PPTX源文件已下载并分类；1份网站源文件损坏原样保留，图片/PDF与缺附件按要求单列。",
                     "saved_pptx": 5694, "archived": 5693, "retained_corrupt": 1,
                     "all_requested_resources_accounted": True, "result": str(ARCHIVE / "本次处理结果.json")})
    write(task_path, json.dumps(previous, ensure_ascii=False, indent=2) + "\n")

    table = "\n".join(f"| {row['category']} | {row['website_resources']} | {row['saved_pptx']} | {row['note']} |" for row in category_rows)
    readme = f"""# 高阶文化模板库

已完成本次范围内所有 PPTX 源文件的下载与分类。完成日期：2026-10-09。

本目录直接包含网站原有的 **31 个分类文件夹**，没有额外分组层或分类内子目录。“优秀作品”196条按要求排除，未重复下载。

- 本次核对网站资源：6,578条。
- 已保存PPTX：**5,694份**；文件逻辑大小合计约 **{result['logical_file_bytes'] / 1_000_000_000:.2f} GB**，相同内容的独立分类入口可能使用硬链接。
- 其中 **5,693份**通过路径、大小、SHA-256、ZIP成员CRC和必要OOXML部件检查。
- **1份网站原文件损坏**，原样保留并在文件名中明确标注，没有改造成完整性通过。
- 按“只要PPTX”的要求，跳过549张图片和325份PDF；10个已核实的正常详情页没有附件。
- 待下载、待归档及未核对资源均为0。

## 查找文件

打开 [可搜索归档索引](可搜索归档索引.html) 可按分类、标题和来源编号查找。文件命名为 `来源编号 - 网站标题.pptx`；同名条目保留独立编号。网站同一标题下的文件不会相互覆盖。

[分类汇总.csv](分类汇总.csv) 提供分类与处理状态统计；[下载清单.csv](下载清单.csv) 记录每条资源；[本次处理结果.json](本次处理结果.json) 记录最终计数和证据哈希。模板库总索引也已登记这些文件，其中损坏原件单列为不可用参考。

## 已确认的原站文件问题

[3890 - 实用素材（网站原文件损坏）.pptx](实用素材/3890%20-%20实用素材（网站原文件损坏）.pptx) 已保存原始字节。两次独立同源下载大小与SHA-256完全相同，均在 `ppt/slides/slide15.xml` 发现CRC错误和XML截断。该条不计入5,693份完整文件；索引标注“源文件损坏已保留”。源文件证据和独立重下文件保存在项目维护目录中。

“素材图片”和“发布会”文件夹按31类要求保留。前者提供图片、后者提供PDF，本次仅收录PPTX，因此两个文件夹为空。无附件条目编号为：2992、611、612、613、614、5065、8145、8146、8147、8151。

## 31个分类

| 文件夹 | 网站资源数 | 已保存PPTX | 说明 |
| --- | ---: | ---: | --- |
{table}
| 合计 | 6578 | 5694 | 5693份完整，1份原站损坏已保留 |

## 校验范围

本次执行了全量文件完整性和资源范围对账，未进行逐页视觉审阅或原生PowerPoint播放检查。模板库登记用于后续检索，不表示已确认每份素材的适配效果。

`归档回执.jsonl` 保留来源、实际路径、大小、哈希和格式结果；网站目录原始证据保存在 `../图表与视觉素材/版式与排版参考/高阶文化-待下载清单/`。该清单的6,774条是包含“优秀作品”的全站盘点量，不是本次PPTX文件数。下载、归档、状态报告和模板库登记各有独立证据，最终全量验收记录位于项目 `aippt-studio/.maintenance/gaojie-downloads/最终验收.json`。
"""
    write(ARCHIVE / "README.md", readme)
    # These three exact files were created only for this completed acquisition.
    # Check ownership and type before unlinking; never read or log credentials.
    removed = []
    for temporary in (Path("/tmp/gaojie-authorized-session/request.txt"),
                      Path("/tmp/gaojie-range-probe.headers"), Path("/tmp/gaojie-range-probe.bin")):
        try:
            metadata = temporary.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise RuntimeError(f"Refusing to remove unexpected temporary file type or owner: {temporary}")
        temporary.unlink()
        removed.append(str(temporary))
    assert not Path("/tmp/gaojie-authorized-session/request.txt").exists()
    result["private_temporary_session_removed"] = True
    result["removed_task_temporary_files"] = removed
    write(ARCHIVE / "本次处理结果.json", json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("status", "saved_pptx", "integrity_verified_pptx", "preserved_corrupt_source_pptx", "category_folders", "logical_file_bytes", "pending_download", "pending_archive")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
