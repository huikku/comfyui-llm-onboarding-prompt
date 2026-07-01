#!/usr/bin/env python3
"""Build the compressed template-catalog index bundled with comfy-mcp.

Fetches Comfy-Org/workflow_templates' templates/index.json, flattens it to a
compact {name, title, description, category} list, and writes it gzipped to
comfy_mcp/data/templates_index.json.gz.

Only the *index* is bundled (~60 KB gz) — the 567 workflow JSONs (~28 MB) stay
in the repo and are fetched on demand by get_template. Stdlib only, so the
GitHub Action needs no pip install.

Usage:  python scripts/build_template_index.py [--ref main]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import urllib.request
from pathlib import Path

REPO_RAW = "https://raw.githubusercontent.com/Comfy-Org/workflow_templates"
OUT = Path(__file__).resolve().parents[1] / "comfy_mcp" / "data" / "templates_index.json.gz"


def fetch(url: str) -> object:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310
        return json.load(r)


def flatten(categories: list[dict]) -> list[dict]:
    out: list[dict] = []
    for cat in categories:
        cat_title = cat.get("title", cat.get("moduleName", ""))
        for t in cat.get("templates", []):
            out.append(
                {
                    "name": t.get("name", ""),
                    "title": t.get("title", ""),
                    "description": t.get("description", ""),
                    "category": cat_title,
                }
            )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="main", help="git ref of workflow_templates")
    args = ap.parse_args()

    cats = fetch(f"{REPO_RAW}/{args.ref}/templates/index.json")
    entries = flatten(cats)
    payload = {"generated_from_ref": args.ref, "count": len(entries), "templates": entries}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {OUT} — {len(entries)} templates, {OUT.stat().st_size // 1024} KB gz")
    return 0


if __name__ == "__main__":
    sys.exit(main())
