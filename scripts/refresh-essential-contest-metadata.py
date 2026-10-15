#!/usr/bin/env python3
"""Refresh SHA-bound discovery labels after the two contest collections move.

The source PPTX/PPT files and their analysis sidecars are not modified here.
This operates on two root-level discovery indexes and summary metadata only.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
ROOT = PROJECT / "模板库"


def rows(path):
    with Path(path).open(encoding="utf-8-sig") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def atomic(path, content: bytes):
    path = Path(path)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.refresh-", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def replace_paths(value, mapping):
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, list):
        return [replace_paths(item, mapping) for item in value]
    if isinstance(value, dict):
        return {key: replace_paths(item, mapping) for key, item in value.items()}
    return value


def build(plan_path, proposal_path):
    plan = {item["new_path"]: item for item in rows(plan_path) if item["source_kind"] == "presentation"}
    old_to_new = {item["old_path"]: item["new_path"] for item in plan.values()}
    variants = dict(old_to_new)
    variants.update({"模板库/" + old: "模板库/" + new for old, new in old_to_new.items()})
    variants.update({str(ROOT / old): str(ROOT / new) for old, new in old_to_new.items()})
    proposed = {item["path"]: item for item in rows(proposal_path)}
    if set(proposed) != set(old_to_new):
        raise ValueError("proposal does not cover exactly the planned presentations")

    curation_path = ROOT / "CONTENT-CURATION.jsonl"
    catalog_path = ROOT / "CATALOG.jsonl"
    seen_curation = set()
    curation_out = []
    for existing in rows(curation_path):
        path = existing["path"]
        if path in plan:
            item = plan[path]
            if existing.get("sha256") != item["sha256"]:
                raise ValueError("current curation SHA mismatch: " + path)
            proposal = proposed[item["old_path"]]
            if proposal["sha256"] != item["sha256"]:
                raise ValueError("proposed curation SHA mismatch: " + path)
            proposal = replace_paths(proposal, variants)
            proposal["path"] = path
            parts = Path(path).parts
            proposal["physical_category"] = "/".join(parts[1:-1])
            proposal["source_collection"] = parts[0]
            existing.update(proposal)
            seen_curation.add(path)
        curation_out.append(existing)
    if seen_curation != set(plan):
        raise ValueError("curation index missing moved presentation rows")
    curated = {item["path"]: item for item in curation_out if item["path"] in plan}

    seen_catalog = set()
    catalog_out = []
    for row in rows(catalog_path):
        path = row["path"]
        if path in plan:
            item = plan[path]
            if row.get("source_sha256") != item["sha256"]:
                raise ValueError("current catalog SHA mismatch: " + path)
            curated_row = curated[path]
            for key in ("category", "subcategory", "asset_type", "confidence"):
                row[key] = curated_row[key]
            row["title"] = curated_row["display_name"]
            row["content_subcategory"] = curated_row["subcategory"]
            row["curation_ref"] = "CONTENT-CURATION.jsonl"
            row["curation_review_status"] = curated_row["review_status"]
            row["curation_evidence_basis"] = curated_row["evidence_basis"]
            row["review_status"] = curated_row["review_status"]
            row["tag_basis"] = curated_row["evidence_basis"]
            row["physical_category"] = curated_row["physical_category"]
            row["source_collection"] = curated_row["source_collection"]
            occasion = [curated_row["category"], curated_row["subcategory"],
                        curated_row["physical_category"].split("/")[0]]
            if curated_row["source_collection"].startswith("BB0086") and curated_row["asset_type"] == "template":
                occasion.append("职业规划竞赛")
            if curated_row["source_collection"] == "创赛":
                occasion.append("创新创业竞赛")
            row["occasion_tags"] = list(dict.fromkeys(occasion))
            seen_catalog.add(path)
        catalog_out.append(row)
    if seen_catalog != set(plan):
        raise ValueError("catalog index missing moved presentation rows")

    by_category = defaultdict(Counter)
    basis = defaultdict(set)
    for row in catalog_out:
        kind = row.get("file_format") or Path(row["path"]).suffix.lstrip(".")
        by_category[row["category"]][kind] += 1
        by_category[row["category"]]["bytes"] += row.get("bytes", 0)
        basis[row["category"]].add(row.get("tag_basis", ""))
    summary = io.StringIO()
    writer = csv.writer(summary)
    writer.writerow(["category", "pptx", "ppt_legacy", "pdf", "deck_bytes", "label_basis", "ppsx", "pptm"])
    for category, counts in sorted(by_category.items()):
        writer.writerow([category, counts["pptx"], counts["ppt"], counts["pdf"],
                         counts["bytes"], ";".join(sorted(basis[category])), counts["ppsx"], counts["pptm"]])

    report_path = ROOT / "CATALOG-BUILD.json"
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    report["category_count"] = len(by_category)
    report["content_curated_count"] = sum(bool(row.get("curation_ref")) for row in catalog_out)
    report["deck_count"] = len(catalog_out)
    report["reorganization_note"] = "Contest collection labels refreshed after source-hash-bound physical reorganization."
    return {
        "CONTENT-CURATION.jsonl": "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in curation_out).encode(),
        "CATALOG.jsonl": "".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n" for item in catalog_out).encode(),
        "CATEGORY-SUMMARY.csv": summary.getvalue().encode("utf-8"),
        "CATALOG-BUILD.json": (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode(),
    }, len(plan)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--proposal", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply and args.backup_dir.exists():
        raise ValueError("backup directory already exists")
    if (ROOT / ".library-reorganization.lock").exists():
        raise ValueError("library maintenance lock already exists")
    output, count = build(args.plan, args.proposal)
    preview = {name: {"before_sha256": digest((ROOT / name).read_bytes()),
                      "after_sha256": digest(data), "after_bytes": len(data)}
               for name, data in output.items()}
    if args.apply:
        args.backup_dir.mkdir(parents=True)
        lock = ROOT / ".library-reorganization.lock"
        with lock.open("x", encoding="utf-8") as stream:
            stream.write("metadata-refresh:" + str(args.backup_dir))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            for name in output:
                (args.backup_dir / name).write_bytes((ROOT / name).read_bytes())
            for name, data in output.items():
                atomic(ROOT / name, data)
            receipt = {"schema": "aippt.essential-contest-metadata-refresh.v1",
                       "status": "applied", "presentations": count, "indexes": preview}
            atomic(args.backup_dir / "receipt.json", (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode())
        except BaseException:
            for name in output:
                backup = args.backup_dir / name
                if backup.exists():
                    atomic(ROOT / name, backup.read_bytes())
            raise
        finally:
            lock.unlink(missing_ok=True)
    print(json.dumps({"mode": "applied" if args.apply else "preview", "presentations": count,
                      "indexes": preview, "backup_dir": str(args.backup_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
