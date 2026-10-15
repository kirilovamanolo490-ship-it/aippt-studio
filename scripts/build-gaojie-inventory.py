#!/usr/bin/env python3
"""Build a traceable, offline inventory from a captured Gaojie resource listing.

This script organizes metadata only. It never downloads or opens the linked
PowerPoint files and does not update the root template catalog.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
from collections import Counter, defaultdict
from pathlib import Path
import re
from urllib.parse import urlparse


PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (PROJECT / "模板库" / "图表与视觉素材" / "版式与排版参考"
                  / "高阶文化-待下载清单")
EXPECTED_CATEGORY_COUNT = 32
EXPECTED_ITEM_COUNT = 6774
ARCHIVE_ROOT = "高阶文化"
CATEGORY_GROUPS = {
    "演示结构": {"3": "封面模板", "36": "目录模板", "37": "章节模板", "38": "标题模板", "39": "结尾模板"},
    "内容排版": {"59": "一段内容", "41": "二段内容", "42": "三段内容", "43": "四段内容",
                "44": "五段内容", "45": "六段内容", "46": "多段内容", "47": "人物介绍",
                "48": "荣誉奖项", "53": "金句模板", "54": "合作伙伴", "55": "图文排版"},
    "关系与图表": {"49": "时间轴图", "50": "架构流程", "51": "商业模型", "56": "表格图表",
                  "82": "地图排版", "104": "数据基座"},
    "素材与组件": {"40": "素材图片", "52": "样机展示", "58": "实用素材", "62": "风格配色",
                  "105": "文本组件", "106": "装饰形状"},
    "专题与案例": {"57": "优秀作品", "83": "发布会", "84": "专题模板"},
}
EXPECTED_CATEGORY_NAMES = {key: value for group in CATEGORY_GROUPS.values() for key, value in group.items()}


def load_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("输入 JSON 顶层必须是对象")
    for key in ("source", "checked_at", "categories", "pages", "items"):
        if key not in data:
            raise ValueError(f"输入缺少必需字段: {key}")
    for key in ("categories", "pages", "items"):
        if not isinstance(data[key], list):
            raise ValueError(f"{key} 必须是数组")
    return data


def safe_url(value: object) -> str:
    if not isinstance(value, str):
        return ""
    value = value.strip()
    return value if urlparse(value).scheme in {"http", "https"} else ""


def safe_component(value: object) -> str:
    text = str(value or "未分类").strip()
    return text.replace("/", "／").replace("\\", "＼").replace("..", "．") or "未分类"


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def sha_is_valid(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def build(data: dict, output: Path) -> dict:
    categories = data["categories"]
    pages = data["pages"]
    source_items = data["items"]
    issues: list[str] = []
    coverage_gaps: list[str] = []
    for message in data.get("issues", []):
        issues.append(f"上游页面解析错误：{message}")
    for skipped in data.get("skipped_files", []):
        filename = skipped.get("file", "未知文件") if isinstance(skipped, dict) else str(skipped)
        coverage_gaps.append(f"上游页面未纳入：{filename}")

    cat_by_id: dict[str, dict] = {}
    for index, row in enumerate(categories):
        if not isinstance(row, dict) or row.get("id") is None or not row.get("name"):
            issues.append(f"categories[{index}] 缺少 id 或 name")
            continue
        key = str(row["id"])
        if key in cat_by_id:
            issues.append(f"分类 ID 重复: {key}")
        cat_by_id[key] = row
    if len(categories) != EXPECTED_CATEGORY_COUNT:
        issues.append(f"分类数为 {len(categories)}，预期 {EXPECTED_CATEGORY_COUNT}")
    for category_id, expected_name in EXPECTED_CATEGORY_NAMES.items():
        actual = cat_by_id.get(category_id)
        if actual is None:
            issues.append(f"缺少预期分类 ID {category_id}（{expected_name}）")
        elif actual.get("name") != expected_name:
            issues.append(f"分类 ID {category_id} 名称为 {actual.get('name')}，预期 {expected_name}")

    page_keys: set[str] = set()
    page_map: dict[tuple[str, int], dict] = {}
    page_counts: Counter[str] = Counter()
    page_item_counts: Counter[tuple[str, int]] = Counter()
    seen_page_numbers: dict[str, set[int]] = defaultdict(set)
    for index, page_row in enumerate(pages):
        if not isinstance(page_row, dict):
            issues.append(f"pages[{index}] 不是对象")
            continue
        category_id = str(page_row.get("category_id", ""))
        key = str(page_row.get("key", ""))
        page_number = page_row.get("page")
        if category_id not in cat_by_id:
            issues.append(f"页记录引用未知分类 ID: {category_id}")
        if not key:
            issues.append(f"pages[{index}] 缺少 key")
        elif key in page_keys:
            issues.append(f"页 key 重复: {key}")
        page_keys.add(key)
        if not is_count(page_number) or page_number < 1:
            issues.append(f"页 {key or index} 的 page 必须是正整数")
            continue
        if key != f"{category_id}-{page_number}":
            issues.append(f"页 key {key!r} 与分类/页码 {category_id}-{page_number} 不一致")
        page_identity = (category_id, page_number)
        if page_identity in page_map:
            issues.append(f"分类 {category_id} 页码 {page_number} 有重复页面记录")
        else:
            page_map[page_identity] = page_row
        seen_page_numbers[category_id].add(page_number)
        items_count = page_row.get("items")
        if not is_count(items_count):
            issues.append(f"页 {key or index} 的 items 必须是非负整数")
            continue
        expected_page_total = page_row.get("total")
        if expected_page_total is not None and not is_count(expected_page_total):
            issues.append(f"页 {key} 的 total 必须是非负整数（收到 {expected_page_total!r}）")
        category_total = cat_by_id.get(category_id, {}).get("total")
        if is_count(category_total) and is_count(expected_page_total) and expected_page_total != category_total:
            issues.append(f"页 {key} 的 total={expected_page_total} 与分类 total={category_total} 不一致")
        if not page_row.get("source_file") or not sha_is_valid(page_row.get("source_sha256")):
            issues.append(f"页 {key} 缺少来源文件名或有效 source_sha256")
        page_counts[category_id] += items_count

    # Use the explicit top-level item list as the resource inventory; compare it
    # to page records by ID so page traversal errors remain visible.
    id_counts = Counter(str(row.get("id", "")) for row in source_items if isinstance(row, dict)
                        and row.get("id") is not None and str(row.get("id", "")))
    missing_id_rows = sum(1 for row in source_items if not isinstance(row, dict)
                          or row.get("id") is None or not str(row.get("id", "")).strip())
    if missing_id_rows:
        issues.append(f"有 {missing_id_rows} 条顶层资源缺少有效 ID")
    duplicate_ids = sorted(key for key, count in id_counts.items() if count > 1)
    if duplicate_ids:
        issues.append(f"资源 ID 重复 {len(duplicate_ids)} 个；示例: {', '.join(duplicate_ids[:10])}")
    inventory: list[dict] = []
    raw_item_counts: Counter[str] = Counter()
    for row in source_items:
        if not isinstance(row, dict):
            continue
        category_id = str(row.get("category_id", ""))
        category_record = cat_by_id.get(category_id, {})
        category_name = str(row.get("category") or category_record.get("name") or "未分类")
        title = str(row.get("title") or "（无标题）").strip()
        item_id = str(row.get("id", ""))
        page_number = row.get("page")
        url = safe_url(row.get("url"))
        if not url:
            issues.append(f"资源 {item_id or '(无ID)'} 缺少有效详情链接")
        status = str(row.get("download_status") or "not_downloaded")
        if status != "not_downloaded":
            issues.append(f"资源 {item_id or '(无ID)'} 的 download_status={status}；本脚本只生成待下载清单")
        if category_id not in cat_by_id:
            issues.append(f"资源 {item_id or '(无ID)'} 引用未知分类 ID {category_id}")
        elif row.get("category") and row.get("category") != category_record.get("name"):
            issues.append(f"资源 {item_id or '(无ID)'} 分类名与分类 ID {category_id} 不匹配")
        source_page = page_map.get((category_id, page_number)) if is_count(page_number) else None
        if source_page is None:
            issues.append(f"资源 {item_id or '(无ID)'} 找不到对应的已采集分类页")
            source_file = ""
            source_sha256 = ""
            source_page_key = ""
        else:
            source_file = str(source_page.get("source_file") or "")
            source_sha256 = str(source_page.get("source_sha256") or "")
            source_page_key = str(source_page.get("key") or "")
            page_item_counts[(category_id, page_number)] += 1
            if not source_file or not sha_is_valid(source_sha256):
                issues.append(f"资源 {item_id or '(无ID)'} 缺少对应来源页的有效 SHA-256")
        raw_item_counts[category_id] += 1
        group = next((name for name, mapping in CATEGORY_GROUPS.items() if category_id in mapping), "未分组")
        category_path = safe_component(category_name)
        planned_archive_path = f"{ARCHIVE_ROOT}/{category_path}/{item_id or '来源ID待补'}"
        reclassify_after_download = category_id in {"57", "83"}
        inventory.append({
            "id": item_id,
            "title": title,
            "category_id": category_id,
            "category": category_name,
            "page": page_number if is_count(page_number) else "",
            "detail_url": url,
            "download_status": "not_downloaded",
            "group": group,
            "planned_archive_path": planned_archive_path,
            "reclassify_after_download": reclassify_after_download,
            "source_page_key": source_page_key,
            "source_file": source_file,
            "source_sha256": source_sha256,
            "source": data["source"],
            "checked_at": data["checked_at"],
        })

    valid_totals = {cid: row["total"] for cid, row in cat_by_id.items() if is_count(row.get("total"))}
    missing_totals = sorted(cid for cid, row in cat_by_id.items() if row.get("total") is None)
    for cid, row in cat_by_id.items():
        if row.get("total") is not None and not is_count(row.get("total")):
            issues.append(f"分类 {cid} 网站 total 必须是非负整数")
    expected_sum = sum(valid_totals.values())
    actual_total = len(inventory)
    page_count_sum = sum(page_counts.values())
    if missing_totals:
        coverage_gaps.append("以下分类网站 total 未知：" + ", ".join(missing_totals))

    page_coverage_complete = True
    for category_id, category in cat_by_id.items():
        if not safe_url(category.get("url")):
            issues.append(f"分类 {category_id} 缺少有效分类导航 URL")
        declared = category.get("total")
        declared_pages = category.get("pages")
        actual_pages = seen_page_numbers[category_id]
        count_from_pages = page_counts[category_id]
        if not is_count(declared):
            page_coverage_complete = False
        if not is_count(declared_pages) or declared_pages < 1:
            if declared_pages is None:
                coverage_gaps.append(f"分类 {category_id} {category.get('name')} 网站分页数未知")
            else:
                issues.append(f"分类 {category_id} 网站 pages 必须是正整数")
            page_coverage_complete = False
            expected_pages = set()
        else:
            expected_pages = set(range(1, declared_pages + 1))
            missing_pages = sorted(expected_pages - actual_pages)
            extra_pages = sorted(actual_pages - expected_pages)
            if missing_pages:
                coverage_gaps.append(f"分类 {category_id} {category.get('name')} 缺少分页 {missing_pages}")
                page_coverage_complete = False
            if extra_pages:
                issues.append(f"分类 {category_id} {category.get('name')} 存在声明范围外分页 {extra_pages}")
                page_coverage_complete = False
        if is_count(declared):
            if count_from_pages > declared:
                issues.append(f"分类 {category_id} 分页计数合计 {count_from_pages} 超过网站 total={declared}")
                page_coverage_complete = False
            elif count_from_pages < declared:
                if is_count(declared_pages) and actual_pages == expected_pages:
                    issues.append(f"分类 {category_id} 所有分页已采集，但页面计数合计 {count_from_pages} 与 total={declared} 不一致")
                    page_coverage_complete = False
                else:
                    coverage_gaps.append(f"分类 {category_id} {category.get('name')} 已登记分页计数 {count_from_pages}/{declared}")
                    page_coverage_complete = False
            if raw_item_counts[category_id] != count_from_pages:
                # Incomplete pages mean the listing remains incomplete; counts
                # on pages that were actually captured must still reconcile.
                captured_page_items = sum(page_item_counts[(category_id, number)] for number in actual_pages)
                if captured_page_items != count_from_pages:
                    issues.append(f"分类 {category_id} 已采集页内资源数 {captured_page_items} 与分页计数 {count_from_pages} 不一致")
                elif count_from_pages == declared:
                    issues.append(f"分类 {category_id} 完整分页的资源明细数 {raw_item_counts[category_id]} 与网站 total={declared} 不一致")
                else:
                    coverage_gaps.append(f"分类 {category_id} 资源明细覆盖 {raw_item_counts[category_id]}/{count_from_pages} 条已登记分页计数")
                page_coverage_complete = False
        for (page_category, page_number), page_row in page_map.items():
            if page_category == category_id and page_item_counts[(category_id, page_number)] != page_row.get("items"):
                issues.append(f"分类 {category_id} 第 {page_number} 页声明 {page_row.get('items')} 条，资源明细实际 {page_item_counts[(category_id, page_number)]} 条")
                page_coverage_complete = False

    total_sum_matches = False
    if not missing_totals:
        total_sum_matches = expected_sum == EXPECTED_ITEM_COUNT
        if not total_sum_matches:
            issues.append(f"32个分类网站 total 合计 {expected_sum}，预期 {EXPECTED_ITEM_COUNT}")
    else:
        page_coverage_complete = False
    if page_count_sum < expected_sum and not missing_totals:
        coverage_gaps.append(f"已登记分页计数 {page_count_sum}/{expected_sum} 条")
    elif page_count_sum > expected_sum and not missing_totals:
        issues.append(f"已登记分页计数 {page_count_sum} 超过分类声明合计 {expected_sum}")
    if actual_total < expected_sum and not missing_totals:
        coverage_gaps.append(f"已采集资源记录 {actual_total}/{expected_sum} 条")
    elif actual_total > expected_sum and not missing_totals:
        issues.append(f"资源明细数 {actual_total} 超过网站声明总数 {expected_sum}")

    complete = (len(cat_by_id) == EXPECTED_CATEGORY_COUNT
                and not missing_totals and total_sum_matches and expected_sum == EXPECTED_ITEM_COUNT
                and page_coverage_complete and actual_total == EXPECTED_ITEM_COUNT
                and len(id_counts) == EXPECTED_ITEM_COUNT and not duplicate_ids
                and not issues and not coverage_gaps)
    scope_label = "完整资源清单" if complete else "已采集资源记录"
    total_label = "已知分类声明合计" if missing_totals else "网站声明总数"

    output.mkdir(parents=True, exist_ok=True)
    readme = [
        "# 高阶文化站点分类目录与待下载清单", "",
        f"- 来源：{data['source']}", f"- 盘点时间：{data['checked_at']}",
        f"- 分类数：{len(categories)}；{scope_label}：{actual_total:,} 条；{total_label}：{expected_sum:,} 条。",
        ("- 32个分类、总数、逐分类分页覆盖及页面资源明细均核对一致。" if complete else
         "- 这是当前实际采集到的资源记录；缺失总数或分页覆盖缺口见核对结果。"),
        "- 这是网站完整目录的原始盘点。用户后续要求下载除优秀作品外的 31 类、6,578 项；实际下载与归档状态见相邻高阶文化目录的可搜索归档索引.html。",
        "- 未检查 PPTX 内容、视觉效果、可编辑性或授权；标题与来源 ID 按网站记录保留。",
        "- 每条资源记录绑定其来源列表页文件名和 SHA-256；HTML 是清单证据，不含模板二进制，计划路径不代表模板已下载。",
        "- `离线清单.html` 可按分类、标题和资源 ID 搜索，资源表每页最多显示100条。",
        "", "## 再生成", "",
        "将已保存的 `gaojie-list-分类ID[-页码].html` 文件放入 `<资料目录>/网页清单证据`。以下在项目根目录的 Bash/Zsh 中运行；shell 会先把通配符展开成明确文件名，解析脚本不会扫描目录。",
        "",
        "```bash",
        "source_dir='<资料目录>/网页清单证据'",
        "files=(\"$source_dir\"/gaojie-list-*.html)",
        "names=(\"${files[@]##*/}\")",
        "python3 scripts/with-studio-runtime.py --maintenance -- python scripts/parse-gaojie-listings.py --source-dir \"$source_dir\" \"${names[@]}\"",
        "python3 scripts/with-studio-runtime.py --maintenance -- python scripts/build-gaojie-inventory.py '模板库/图表与视觉素材/版式与排版参考/高阶文化-待下载清单/站点盘点.json'",
        "```",
        "", "## 分类目录规划", "",
    ]
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_id = str(category.get("id", ""))
        group = next((name for name, mapping in CATEGORY_GROUPS.items() if category_id in mapping), "未分组")
        planned = f"{ARCHIVE_ROOT}/{safe_component(category.get('name'))}/<资源ID>"
        extra = "；下载后按实际主题二次归类" if category_id in {"57", "83"} else ""
        declared_value = category.get("total")
        declared_total = f"{declared_value:,}" if is_count(declared_value) else "unknown"
        declared_pages = category.get("pages") if is_count(category.get("pages")) else "unknown"
        readme.append(f"- {category.get('name', '未命名')}（ID {category_id}）：网站声明 {declared_total} 条、"
                      f"{declared_pages} 页；已采集分页计数 {page_counts[category_id]:,}；归属 `{group}`，"
                      f"计划路径 `{planned}`{extra}。")
    (output / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")

    fields = ["id", "title", "category_id", "category", "group", "page", "detail_url",
              "download_status", "planned_archive_path", "reclassify_after_download",
              "source_page_key", "source_file", "source_sha256"]
    with (output / "资源清单.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(inventory)
    with (output / "资源清单.jsonl").open("w", encoding="utf-8") as stream:
        for row in inventory:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")

    category_counts = Counter(row["category_id"] for row in inventory)
    with (output / "分类汇总.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["category_id", "category", "group", "website_total", "website_pages",
                         "captured_page_rows", "page_item_count_sum", "listed_resource_count", "directory_count_matches"])
        for category_id, category in cat_by_id.items():
            website_total = category.get("total", "")
            current = category_counts[category_id]
            group = next((name for name, mapping in CATEGORY_GROUPS.items() if category_id in mapping), "未分组")
            writer.writerow([category_id, category["name"], group, website_total, category.get("pages", ""),
                             len(seen_page_numbers[category_id]), page_counts[category_id], current,
                             "是" if is_count(website_total) and website_total == page_counts[category_id]
                             and category_counts[category_id] == page_counts[category_id] else "否"])

    group_nav = []
    for group, group_categories in CATEGORY_GROUPS.items():
        links = []
        for category_id, category_name in group_categories.items():
            category = cat_by_id.get(category_id)
            if category is None:
                continue
            category_url = html.escape(safe_url(category.get("url")), quote=True)
            links.append(
                f'<li><button type="button" data-category-filter="{html.escape(category_id, quote=True)}">'
                f'<span>{html.escape(category_name)}</span><small>{category_counts[category_id]:,}</small></button> '
                f'<a class="official" href="{category_url}" target="_blank" rel="noopener noreferrer">官网</a></li>'
            )
        group_nav.append(f'<section class="group"><h3>{html.escape(group)}</h3><ul class="category-grid">{"".join(links)}</ul></section>')

    listing_urls = {
        (str(row.get("category_id", "")), row.get("page")): safe_url(row.get("url"))
        for row in pages if isinstance(row, dict)
    }
    html_items = [{key: row[key] for key in (
        "id", "title", "category_id", "category", "group", "page", "detail_url",
        "download_status", "source_file", "source_sha256")}
        for row in inventory]
    for row in html_items:
        row["source_page_url"] = listing_urls.get((row["category_id"], row["page"]), "")
    embedded_json = json.dumps(html_items, ensure_ascii=False, separators=(",", ":"))
    embedded_json = embedded_json.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    scope_label = "完整资源清单" if complete else "已采集资源记录"
    count_label = "完整条目数" if complete else "已采集条目数"
    page = f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>高阶文化 · 版式资料索引</title><style>
body{{font-family:system-ui,-apple-system,"PingFang SC",sans-serif;max-width:1180px;margin:22px auto;padding:0 16px;color:#263244;background:#f5f7fa}}h1{{font-size:1.55rem;margin:0 0 10px}}h2{{font-size:1.15rem;margin:24px 0 10px}}h3{{font-size:1rem;margin:0 0 9px}}p{{color:#536174}}.meta{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 18px}}.meta span{{padding:5px 10px;border-radius:999px;background:#e9eef4;color:#435166;font-size:.9rem}}.paused{{background:#fff0d4!important;color:#78551e!important}}.searchbar{{display:flex;gap:8px;align-items:center;margin:14px 0}}input{{flex:1;max-width:640px;padding:11px 12px;border:1px solid #bbc5d2;border-radius:7px;font-size:1rem}}.groups{{display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));gap:10px}}.group{{background:white;border:1px solid #e1e6ec;border-radius:9px;padding:12px}}.category-grid{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:5px 8px}}.category-grid li{{display:flex;align-items:center;gap:4px;min-width:0}}.category-grid button{{display:flex;align-items:center;justify-content:space-between;gap:6px;flex:1;min-width:0;padding:6px 8px;margin:0;border:1px solid transparent;border-radius:6px;background:#f4f6f9;text-align:left;cursor:pointer}}.category-grid button:hover,.category-grid button.active{{background:#e6eef8;border-color:#b7c9df}}.category-grid button span{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.category-grid button small,.official{{font-size:.78rem;color:#718096;white-space:nowrap}}a{{color:#1769aa;text-decoration:none}}a:hover{{text-decoration:underline}}.official{{padding:3px 4px}}.table-wrap{{overflow:auto;background:white;border:1px solid #e1e6ec;border-radius:8px}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid #e8edf2;padding:9px 10px;text-align:left;vertical-align:top}}th{{background:#edf2f8;color:#425168;font-weight:600}}tbody tr:last-child td{{border-bottom:0}}td .source{{margin-top:4px;color:#718096;font-size:.78rem}}td .source summary{{cursor:pointer}}td .source code{{font-size:.72rem;overflow-wrap:anywhere}}.table-controls{{display:flex;align-items:center;gap:8px;margin:10px 0}}.table-controls button,.searchbar button{{padding:8px 12px;border:1px solid #c6d0dc;border-radius:6px;background:white;cursor:pointer}}button:disabled{{opacity:.5;cursor:default}}#active-filter{{color:#536174;font-size:.9rem}}@media(max-width:700px){{.groups{{grid-template-columns:1fr}}.category-grid{{grid-template-columns:1fr 1fr}}table{{min-width:760px}}}}
</style><body><h1>高阶文化 · 版式资料索引</h1>
<div class="meta"><span>32 类</span><span>{count_label}：{actual_total:,}</span><span class="paused">网站资源目录 · 下载状态见归档索引</span></div>
<p>来源：{html.escape(str(data['source']))}　盘点：{html.escape(str(data['checked_at']))}　{total_label}：{expected_sum:,} 条</p>
<div class="searchbar"><label for="q">搜索</label><input id="q" type="search" placeholder="输入名称、分类或来源编号" autocomplete="off"><button id="show-all" type="button">显示全部</button></div>
<h2>分类</h2><div class="groups">{''.join(group_nav)}</div>
<h2 id="resource-table">{scope_label}</h2><p id="active-filter">全部分类</p>
<div class="table-wrap"><table><thead><tr><th>名称（链接）</th><th>分类</th><th>来源编号</th><th>网站列表页</th></tr></thead><tbody id="rows"></tbody></table></div>
<div class="table-controls"><button id="prev">上一页</button><span id="page-info"></span><button id="next">下一页</button><span id="count"></span></div>
<script>const allItems={embedded_json};let term='',cat='',pageIndex=0;const q=document.querySelector('#q');function esc(v){{return String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}function filtered(){{return allItems.filter(x=>(!cat||x.category_id===cat)&&(!term||[x.title,x.id,x.category,x.category_id].join(' ').toLocaleLowerCase().includes(term)))}}function render(){{const rows=filtered(),size=100,pages=Math.max(1,Math.ceil(rows.length/size));pageIndex=Math.min(pageIndex,pages-1);document.querySelector('#rows').innerHTML=rows.slice(pageIndex*size,(pageIndex+1)*size).map(x=>`<tr><td><a target="_blank" rel="noopener noreferrer" href="${{esc(x.detail_url)}}">${{esc(x.title)}}</a><details class="source"><summary>来源信息</summary><div>${{esc(x.source_file)}}</div><code>${{esc(x.source_sha256)}}</code></details></td><td>${{esc(x.category)}}</td><td>${{esc(x.id)}}</td><td>${{x.source_page_url?`<a target="_blank" rel="noopener noreferrer" href="${{esc(x.source_page_url)}}">第 ${{esc(x.page)}} 页</a>`:'—'}}</td></tr>`).join('');document.querySelector('#count').textContent=`匹配 ${{rows.length.toLocaleString()}} 条`;document.querySelector('#page-info').textContent=`第 ${{pageIndex+1}} / ${{pages}} 页`;document.querySelector('#prev').disabled=pageIndex===0;document.querySelector('#next').disabled=pageIndex>=pages-1;document.querySelector('#active-filter').textContent=cat?`当前分类：${{esc(allItems.find(x=>x.category_id===cat)?.category||cat)}}${{term?`；搜索：${{esc(term)}}`:''}}`:(term?`搜索：${{esc(term)}}`:'全部分类');document.querySelectorAll('[data-category-filter]').forEach(b=>b.classList.toggle('active',b.dataset.categoryFilter===cat))}}q.addEventListener('input',()=>{{term=q.value.trim().toLocaleLowerCase();pageIndex=0;cat='';render()}});document.querySelector('#prev').onclick=()=>{{pageIndex--;render()}};document.querySelector('#next').onclick=()=>{{pageIndex++;render()}};document.querySelector('#show-all').onclick=()=>{{term='';cat='';q.value='';pageIndex=0;render()}};document.querySelectorAll('[data-category-filter]').forEach(b=>b.addEventListener('click',()=>{{cat=b.dataset.categoryFilter;term='';q.value='';pageIndex=0;render();document.querySelector('#resource-table').scrollIntoView({{behavior:'smooth',block:'start'}})}}));render();</script></body></html>"""
    (output / "离线清单.html").write_text(page, encoding="utf-8")

    result = {
        "status": "error" if issues else ("complete" if complete else "partial"),
        "source": data["source"], "checked_at": data["checked_at"],
        "expected_category_count": EXPECTED_CATEGORY_COUNT, "actual_category_count": len(categories),
        "expected_website_item_count": EXPECTED_ITEM_COUNT,
        "category_declared_total_sum": expected_sum, "missing_category_total_ids": missing_totals,
        "page_item_count_sum": page_count_sum,
        "top_level_item_count": actual_total, "unique_id_count": len(id_counts),
        "duplicate_id_count": len(duplicate_ids), "duplicate_ids_sample": duplicate_ids[:100],
        "category_count_checks": [{"id": key, "name": row.get("name"), "website_total": row.get("total"),
                                   "page_item_count": page_counts[key], "top_level_item_count": category_counts[key],
                                   "page_count": len(seen_page_numbers[key]), "website_pages": row.get("pages"),
                                   "matches": (is_count(row.get("total")) and is_count(row.get("pages"))
                                               and seen_page_numbers[key] == set(range(1, row["pages"] + 1))
                                               and row.get("total") == page_counts[key] == category_counts[key])}
                                  for key, row in cat_by_id.items()],
        "inventory_scope": "complete_catalog" if complete else "captured_pages_only",
        "resource_item_count": actual_total,
        "sample_item_count": 0 if complete else actual_total,
        "issues": issues,
        "coverage_gaps": coverage_gaps,
        "downloads_performed_by_this_script": 0,
        "download_progress": "此处仅核对网站目录；实际文件状态见相邻高阶文化目录/下载进度.json。",
        "catalog_updated": False,
        "visual_reviewed": False,
        "message": ("本站点完整资源清单；本文件核对目录元数据，实际文件下载及归档状态另行记录。" if complete else
                    "本站点当前已采集资源记录；未核验PPTX内容与视觉，分页覆盖不足单独记录。"),
    }
    dump_json(output / "核对结果.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="已读取的站点清单 JSON 文件")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT,
                        help=f"输出目录（默认: {DEFAULT_OUTPUT}）")
    args = parser.parse_args()
    result = build(load_json(args.input), args.output_dir)
    print(json.dumps({key: result[key] for key in (
        "status", "top_level_item_count", "unique_id_count", "duplicate_id_count", "issues", "coverage_gaps")},
        ensure_ascii=False))
    return 2 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
