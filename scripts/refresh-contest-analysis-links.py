#!/usr/bin/env python3
"""Update current analysis Markdown links after source-hash-bound collection moves."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "模板库"
COLLECTIONS = ("创赛", "BB0086 大学生职业生涯规划大赛 PPT")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.plan.open(encoding="utf-8") if line.strip()]
    mapping = {row["old_path"]: row["new_path"] for row in rows if row["source_kind"] == "presentation"}
    source_sha = {row["old_path"]: row["sha256"] for row in rows if row["source_kind"] == "presentation"}
    changed = []
    ambiguous = []
    for collection in COLLECTIONS:
        base = ROOT / collection / "分析索引"
        for page in sorted(base.rglob("*.md")):
            original = page.read_text(encoding="utf-8-sig")

            def replace(match):
                label = match.group(1)
                # Only code spans that name source presentations are candidates.
                if not label.lower().endswith((".ppt", ".pptx", ".ppsx", ".pptm")):
                    return match.group()
                old = label.removeprefix("模板库/")
                mode = "root"
                if old not in mapping:
                    old = collection + "/" + label
                    mode = "collection"
                if old not in mapping:
                    matches = [candidate for candidate in mapping
                               if candidate.startswith(collection + "/") and candidate.endswith("/" + label)]
                    if len(matches) > 1:
                        line_start = original.rfind("\n", 0, match.start()) + 1
                        line_end = original.find("\n", match.end())
                        context = original[line_start:line_end if line_end >= 0 else len(original)]
                        prefixes = re.findall(r"[0-9a-f]{12,64}", context)
                        matches = [candidate for candidate in matches
                                   if any(source_sha[candidate].startswith(prefix) for prefix in prefixes)]
                    if len(matches) == 1:
                        old, mode = matches[0], "collection"
                    elif len(matches) > 1:
                        ambiguous.append({"page": str(page), "label": label, "matches": len(matches)})
                        return match.group()
                    else:
                        return match.group()
                new = mapping[old]
                if label.startswith("模板库/"):
                    new = "模板库/" + new
                elif mode == "collection":
                    new = new[len(collection) + 1:]
                return "`" + new + "`"

            revised = re.sub(r"`([^`]+)`", replace, original)
            if revised != original:
                changed.append({"path": str(page.relative_to(ROOT)),
                                "old_bytes": len(original.encode()), "new_bytes": len(revised.encode())})
                if args.apply:
                    page.write_text(revised, encoding="utf-8")
    print(json.dumps({"mode": "applied" if args.apply else "preview",
                      "files_changed": len(changed), "changed": changed,
                      "ambiguous": ambiguous}, ensure_ascii=False))
    if ambiguous:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
