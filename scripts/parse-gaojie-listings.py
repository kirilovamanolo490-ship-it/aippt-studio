#!/usr/bin/env python3
"""Parse explicitly supplied saved Gaojie list-page HTML into an inventory JSON.

Reads only explicitly named direct-child files matching
gaojie-list-<category-id>[-<page>].html from --source-dir (~/Downloads by
default). It does not fetch links or download images or templates.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urljoin, urlparse
from zoneinfo import ZoneInfo


BASE_URL = "http://www.gaojiewenhua.cn/"
DEFAULT_SOURCE_DIR = Path.home() / "Downloads"
OUTPUT = (Path(__file__).resolve().parents[1] / "模板库" / "图表与视觉素材" / "版式与排版参考"
          / "高阶文化-待下载清单" / "站点盘点.json")
FILENAME_RE = re.compile(r"^gaojie-list-(\d+)(?:-(\d+))?\.html$", re.IGNORECASE)
EXPECTED_CATEGORY_COUNT = 32


class ListingParser(HTMLParser):
    """Extract navigation, product cards and pagination with the stdlib."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_text: list[str] = []
        self.h2_text: list[str] = []
        self._in_title = False
        self._h2_depth = 0
        self._h2_buffer: list[str] = []
        self.categories: list[dict] = []
        self.active_category_ids: set[str] = set()
        self.digg_text: list[str] = []
        self.pagination_links: list[dict] = []
        self.pagination_category_ids: set[str] = set()
        self._in_digg = 0
        self._product: dict | None = None
        self._in_h5 = 0
        self._product_title: list[str] = []
        self.products: list[dict] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {key.casefold(): value or "" for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        a = self._attrs(attrs)
        if tag == "title":
            self._in_title = True
        if tag == "h2":
            self._h2_depth += 1
            self._h2_buffer = []
        if tag == "div" and "digg" in a.get("class", "").split():
            self._in_digg += 1
        if tag == "a":
            href = a.get("href", "")
            parsed = urlparse(href)
            if parsed.path.casefold().endswith("/products.aspx") or parsed.path.casefold() == "/products.aspx":
                category_id = parse_qs(parsed.query).get("category_id", [""])[0]
                if category_id and category_id != "0" and a.get("title"):
                    entry = {"id": category_id, "name": a["title"].strip(), "url": urljoin(BASE_URL, href)}
                    self.categories.append(entry)
                    if "active" in a.get("class", "").split():
                        self.active_category_ids.add(category_id)
            elif "products_show.aspx" in parsed.path.casefold():
                item_id = parse_qs(parsed.query).get("id", [""])[0]
                if item_id:
                    self._product = {"id": item_id, "url": urljoin(BASE_URL, href), "preview_url": ""}
                    self._product_title = []
            if self._in_digg:
                pagination_category_id = parse_qs(parsed.query).get("category_id", [""])[0]
                if pagination_category_id:
                    self.pagination_category_ids.add(pagination_category_id)
                page = parse_qs(parsed.query).get("page", [""])[0]
                if page.isdigit():
                    self.pagination_links.append({"page": int(page), "url": urljoin(BASE_URL, href)})
        if tag == "h5" and self._product is not None:
            self._in_h5 += 1
        if tag == "img" and self._product is not None and not self._product["preview_url"]:
            self._product["preview_url"] = urljoin(BASE_URL, a.get("src", "")) if a.get("src") else ""

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "title":
            self._in_title = False
        if tag == "h5" and self._in_h5:
            self._in_h5 -= 1
        if tag == "a" and self._product is not None:
            self._product["title"] = "".join(self._product_title).strip()
            self.products.append(self._product)
            self._product = None
            self._product_title = []
        if tag == "div" and self._in_digg:
            self._in_digg -= 1
        if tag == "h2" and self._h2_depth:
            self._h2_depth -= 1
            if self._h2_depth == 0:
                self.h2_text.append("".join(self._h2_buffer).strip())

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_text.append(data)
        if self._h2_depth:
            self._h2_buffer.append(data)
        if self._in_digg:
            self.digg_text.append(data)
        if self._in_h5 and self._product is not None:
            self._product_title.append(data)


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def read_one(name: str, source_dir: Path) -> tuple[dict | None, dict | None, list[dict], str | None]:
    candidate_name = Path(name).name
    if candidate_name != name:
        return None, None, [], "只接受 Downloads 根目录下的文件名，不接受路径"
    match = FILENAME_RE.fullmatch(candidate_name)
    if not match:
        return None, None, [], "文件名不符合 gaojie-list-<category-id>[-<page>].html"
    category_id = match.group(1)
    filename_page = int(match.group(2) or 1)
    source_root = source_dir.expanduser().resolve()
    path = source_root / candidate_name
    try:
        if path.resolve().parent != source_root:
            return None, None, [], "输入文件必须是 source-dir 的直接子文件"
    except OSError:
        return None, None, [], "无法解析输入文件路径"
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, None, [], f"无法读取文件: {type(exc).__name__}"
    try:
        source = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        source = raw.decode("gb18030", errors="replace")
    parser = ListingParser()
    parser.feed(source)
    parser.close()

    active_id = category_id if category_id in parser.active_category_ids else None
    category = next((row for row in parser.categories if row["id"] == category_id), None)
    if not category or not active_id:
        return None, None, [], "页面缺少与文件名一致的活动分类导航，可能未保存完整"
    if parser.pagination_category_ids and parser.pagination_category_ids != {category_id}:
        return None, None, [], "分页链接分类 ID 与文件名类别不一致"
    current_matches = re.findall(r"共\s*(\d+)\s*记录", "".join(parser.digg_text))
    # The current-page marker comes from the exact pagination span, not from
    # arbitrary page numbers in its link labels.
    current_match = re.search(r'<span\b[^>]*class=["\'][^"\']*\bcurrent\b[^"\']*["\'][^>]*>\s*(\d+)\s*</span>', source, re.I)
    heading_ok = any(text.endswith(" - " + category["name"]) for text in parser.h2_text)
    if not heading_ok:
        return None, None, [], "主列表标题与当前分类导航名称不符"

    if not parser.products:
        return None, None, [], "目标主列表没有产品卡片，视为页面不完整"
    if current_match and current_matches:
        current_page = int(current_match.group(1))
        declared_total = int(current_matches[-1])
        if current_page != filename_page:
            return None, None, [], f"文件名页码 {filename_page} 与页面当前页 {current_page} 不符"
        count_basis = "website_pagination_total"
        links = {row["page"]: row["url"] for row in parser.pagination_links}
        links.setdefault(current_page, urljoin(BASE_URL, f"products.aspx?category_id={category_id}" +
                                               (f"&page={current_page}" if current_page > 1 else "")))
        declared_pages = max(links) if links else None
    else:
        # Some categories contain a single listing page and omit the site's
        # pagination widget entirely. Count visible cards only when the saved
        # document is structurally complete and is explicitly page 1.
        if filename_page != 1:
            return None, None, [], "无分页器却请求非第一页，缺页风险，拒绝计数"
        if parser.pagination_links or parser.pagination_category_ids:
            return None, None, [], "存在不完整分页线索但缺少当前页/总数，拒绝推断单页目录"
        if not re.search(r"<html\b", source, re.I) or not re.search(r"</html\s*>\s*$", source, re.I):
            return None, None, [], "无分页器且 HTML 缺少完整文档首尾标记，拒绝推断单页目录"
        if len(parser.products) > 40:
            return None, None, [], f"无分页器但主列表有 {len(parser.products)} 条，超过单页可见数量上限40，拒绝计数"
        current_page = 1
        declared_total = len(parser.products)
        declared_pages = 1
        count_basis = "single_page_visible_item_count"
        links = {1: urljoin(BASE_URL, f"products.aspx?category_id={category_id}")}
    all_pages = []
    for page_number, link in sorted(links.items()):
        all_pages.append({"page": page_number, "url": link})
    page_entry = {
        "key": f"{category_id}-{current_page}", "category_id": category_id,
        "page": current_page, "items": len(parser.products), "total": declared_total,
        "count_basis": count_basis,
        "url": links[current_page], "pagination_links": all_pages,
        "source_file": candidate_name, "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw), "html_title": "".join(parser.title_text).strip(),
        "page_heading": next((text for text in parser.h2_text if text.endswith(" - " + category["name"])), ""),
    }
    items = []
    for item in parser.products:
        if not item.get("title"):
            return None, None, [], "有产品卡片缺少标题，视为页面不完整"
        items.append({**item, "category_id": category_id, "category": category["name"],
                      "page": current_page, "download_status": "not_downloaded"})
    category.update({"total": declared_total, "pages": declared_pages,
                     "pagination_links": all_pages, "count_basis": count_basis})
    metadata = {"file": candidate_name, "sha256": page_entry["source_sha256"],
                "bytes": len(raw), "category_id": category_id, "page": current_page,
                "parsed_products": len(items)}
    return category, page_entry, items, None


def build(file_names: list[str], output: Path = OUTPUT,
          source_dir: Path = DEFAULT_SOURCE_DIR) -> dict:
    categories_by_id: dict[str, dict] = {}
    pages: list[dict] = []
    items: list[dict] = []
    files_read: list[dict] = []
    skipped_files: list[dict] = []
    issues: list[str] = []
    for name in file_names:
        category, page, page_items, error = read_one(name, source_dir)
        if error:
            skipped_files.append({"file": Path(name).name, "reason": error})
            continue
        assert category is not None and page is not None
        files_read.append({"file": page["source_file"], "sha256": page["source_sha256"],
                           "bytes": page["source_bytes"],
                           "category_id": page["category_id"], "page": page["page"],
                           "parsed_products": len(page_items)})
        previous = categories_by_id.get(category["id"])
        if previous:
            for field in ("name", "url", "total", "pages"):
                if previous.get(field) != category.get(field):
                    issues.append(f"分类 {category['id']} 跨页面 {field} 不一致")
            previous["pagination_links"] = merge_links(previous.get("pagination_links", []), category.get("pagination_links", []))
        else:
            categories_by_id[category["id"]] = dict(category)
        pages.append(page)
        items.extend(page_items)

    # The navigation list is present on each captured page. Keep unique IDs and
    # stable ordering; it yields names/URLs even for categories not yet opened.
    navigation: dict[str, dict] = {}
    for name in file_names:
        category, _, _, _ = read_one(name, source_dir)
        if category:
            # A captured category page only exposes the full nav through parser;
            # read_one returns its active category. Other navigation entries are
            # read separately from that same approved source file below.
            path = source_dir.expanduser().resolve() / Path(name).name
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig", errors="replace")
            parser = ListingParser()
            parser.feed(text)
            for entry in parser.categories:
                prior = navigation.get(entry["id"])
                if prior and prior["name"] != entry["name"]:
                    issues.append(f"导航分类 {entry['id']} 名称不一致")
                navigation.setdefault(entry["id"], entry)
            break
    for category_id, row in navigation.items():
        enriched = dict(row)
        enriched.update(categories_by_id.get(category_id, {}))
        enriched.setdefault("total", None)
        enriched.setdefault("pages", None)
        enriched.setdefault("pagination_links", [])
        categories_by_id[category_id] = enriched
    categories = sorted(categories_by_id.values(), key=lambda row: int(row["id"]) if row["id"].isdigit() else row["id"])

    seen_page_keys: set[str] = set()
    unique_pages = []
    for row in pages:
        if row["key"] in seen_page_keys:
            issues.append(f"页面重复: {row['key']}")
            continue
        seen_page_keys.add(row["key"])
        unique_pages.append(row)
    pages = sorted(unique_pages, key=lambda row: (int(row["category_id"]), row["page"]))
    id_counts: dict[str, int] = {}
    for item in items:
        id_counts[item["id"]] = id_counts.get(item["id"], 0) + 1
    duplicate_ids = sorted(key for key, count in id_counts.items() if count > 1)
    if duplicate_ids:
        issues.append(f"产品 ID 重复 {len(duplicate_ids)} 个: {', '.join(duplicate_ids[:20])}")

    coverage_gaps = []
    pages_by_category: dict[str, set[int]] = {}
    counts_by_category: dict[str, int] = {}
    for page in pages:
        pages_by_category.setdefault(page["category_id"], set()).add(page["page"])
        counts_by_category[page["category_id"]] = counts_by_category.get(page["category_id"], 0) + page["items"]
    for category in categories:
        cid = category["id"]
        declared_pages = category.get("pages")
        captured = pages_by_category.get(cid, set())
        captured_count = counts_by_category.get(cid, 0)
        category["captured_pages"] = sorted(captured)
        category["captured_page_count"] = len(captured)
        category["captured_item_count"] = captured_count
        if declared_pages is None:
            coverage_gaps.append({"category_id": cid, "reason": "未保存该分类页，网站总数/分页数未知"})
        else:
            missing = sorted(set(range(1, declared_pages + 1)) - captured)
            if missing:
                coverage_gaps.append({"category_id": cid, "declared_pages": declared_pages,
                                      "captured_pages": sorted(captured), "missing_pages": missing})
            if category.get("total") is not None and captured_count < category["total"]:
                coverage_gaps.append({"category_id": cid, "declared_total": category["total"],
                                      "captured_listing_count": captured_count,
                                      "missing_items_by_count": category["total"] - captured_count})
    if len(categories) != EXPECTED_CATEGORY_COUNT:
        issues.append(f"已读取来源导航发现 {len(categories)} 类，预期 {EXPECTED_CATEGORY_COUNT} 类")

    result = {
        "source": BASE_URL + "products_show.aspx?id=7729",
        "checked_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "categories": categories, "pages": pages, "items": items,
        "inventory_scope": "complete_catalog" if not coverage_gaps and not skipped_files and not issues else "captured_pages_only",
        "declared_total_sum": sum(row["total"] for row in categories if isinstance(row.get("total"), int)),
        "captured_listing_item_count": len(items),
        "files_read": files_read, "skipped_files": skipped_files, "issues": issues,
        "coverage_gaps": coverage_gaps,
        "status": "error" if issues else ("partial" if coverage_gaps else "complete"),
        "download_note": "本脚本只解析已保存的分类列表 HTML，不下载详情页、PPTX或预览图片；资源视觉与内容尚未核验。",
        "prior_download_note": "此前测试阶段有 1 个 PPTX 下载到 Downloads，未收入模板库。",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def merge_links(left: list[dict], right: list[dict]) -> list[dict]:
    merged = {row["page"]: row for row in [*left, *right]}
    return [merged[number] for number in sorted(merged)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", help="明确列出的 HTML 文件名；脚本不扫描目录")
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR,
                        help=f"HTML来源目录；只读取明确列出的直接子文件（默认: {DEFAULT_SOURCE_DIR}）")
    parser.add_argument("--output", type=Path, default=OUTPUT, help=f"输出 JSON（默认: {OUTPUT}）")
    args = parser.parse_args()
    result = build(args.files, args.output, args.source_dir)
    print(json.dumps({"status": result["status"], "categories": len(result["categories"]),
                      "pages": len(result["pages"]), "items": len(result["items"]),
                      "skipped_files": result["skipped_files"], "issues": result["issues"],
                      "coverage_gaps": len(result["coverage_gaps"])}, ensure_ascii=False))
    return 2 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
