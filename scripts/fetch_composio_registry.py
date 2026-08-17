#!/usr/bin/env python3
"""
Lane A, step 1: pull Composio's own toolkit registry.

WHY THIS RUNS ON YOUR MACHINE AND NOT IN THE AGENT SANDBOX
----------------------------------------------------------
The research sandbox has an outbound network allowlist that does not include
composio.dev. Your laptop has normal internet. So this one step runs here.
Your API key never leaves your machine.

WHAT IT DOES
------------
1. Pages through Composio's toolkit registry (~1100+ toolkits).
2. Fuzzy-matches the 100 assignment apps against it.
3. Pulls per-toolkit auth detail for every app that matched.
4. Writes three files into ./data and prints a summary.

The registry is the deterministic ground truth for Lane A: for every app
Composio already supports, the auth scheme is *known*, not inferred by a model.

USAGE
-----
    export COMPOSIO_API_KEY=ak_xxx          # or paste when prompted
    python3 scripts/fetch_composio_registry.py

Standard library only. No pip install required.
"""

from __future__ import annotations

import csv
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
APPS_CSV = DATA / "apps.csv"

RAW_OUT = DATA / "composio_toolkits.json"
DETAIL_OUT = DATA / "composio_toolkit_details.json"
MATCH_OUT = DATA / "composio_match.csv"
DIAG_OUT = DATA / "composio_fetch_diagnostics.txt"

# The docs reference /api/v3.1/toolkits; older material references /api/v3.
# We probe in order and use the first that answers, then record which one worked.
BASE_CANDIDATES = [
    "https://backend.composio.dev/api/v3.1",
    "https://backend.composio.dev/api/v3",
    "https://api.composio.dev/api/v3.1",
    "https://api.composio.dev/api/v3",
]

TIMEOUT = 30
DIAG: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    DIAG.append(msg)


def request_json(url: str, api_key: str) -> tuple[int, object | None, str]:
    """GET a URL. Returns (status, parsed_json_or_None, error_text)."""
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "composio-take-home-research/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body), ""
            except json.JSONDecodeError:
                return resp.status, None, f"non-JSON body: {body[:300]}"
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        return e.code, None, body
    except Exception as e:  # noqa: BLE001 - diagnostics matter more than typing here
        return 0, None, f"{type(e).__name__}: {e}"


def pick_base(api_key: str) -> str | None:
    log("Probing API bases...")
    for base in BASE_CANDIDATES:
        url = f"{base}/toolkits?limit=1"
        status, payload, err = request_json(url, api_key)
        log(f"  {status:>3}  {url}   {('ok' if status == 200 else err[:120])}")
        if status == 200 and payload is not None:
            return base
        if status in (401, 403):
            log("")
            log("  -> Reached Composio but the key was rejected. Check COMPOSIO_API_KEY.")
            return None
    return None


def extract_items(payload: object) -> list[dict]:
    """Response shape has moved around between versions; handle the variants."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("items", "data", "toolkits", "results"):
            val = payload.get(key)
            if isinstance(val, list):
                return [x for x in val if isinstance(x, dict)]
            if isinstance(val, dict):
                for key2 in ("items", "toolkits"):
                    inner = val.get(key2)
                    if isinstance(inner, list):
                        return [x for x in inner if isinstance(x, dict)]
    return []


def extract_cursor(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("next_cursor", "nextCursor", "cursor"):
        val = payload.get(key)
        if isinstance(val, str) and val:
            return val
    for key in ("meta", "pagination", "page_info"):
        sub = payload.get(key)
        if isinstance(sub, dict):
            for key2 in ("next_cursor", "nextCursor", "cursor"):
                val = sub.get(key2)
                if isinstance(val, str) and val:
                    return val
    return None


def fetch_all_toolkits(base: str, api_key: str) -> list[dict]:
    toolkits: list[dict] = []
    cursor: str | None = None
    page = 0
    while True:
        page += 1
        params = {"limit": "1000"}
        if cursor:
            params["cursor"] = cursor
        url = f"{base}/toolkits?{urllib.parse.urlencode(params)}"
        status, payload, err = request_json(url, api_key)
        if status != 200 or payload is None:
            log(f"  page {page}: HTTP {status} {err[:200]}")
            break
        items = extract_items(payload)
        toolkits.extend(items)
        log(f"  page {page}: +{len(items)} toolkits (total {len(toolkits)})")
        cursor = extract_cursor(payload)
        if not cursor or not items:
            break
        if page > 30:  # backstop
            log("  stopping: pagination backstop hit")
            break
        time.sleep(0.2)
    return toolkits


def norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\(.*?\)", " ", s)                    # drop parentheticals
    s = re.sub(r"\b(inc|ltd|api|app|cli|the)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", "", s)
    return s


def build_index(toolkits: list[dict]) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for tk in toolkits:
        for field in ("slug", "name"):
            val = tk.get(field)
            if isinstance(val, str) and val:
                idx.setdefault(norm(val), tk)
    return idx


def match_app(app: str, slug_guess: str, idx: dict[str, dict]) -> tuple[dict | None, float, str]:
    for candidate, method in ((slug_guess, "slug_guess"), (app, "name")):
        key = norm(candidate or "")
        if key and key in idx:
            return idx[key], 1.0, f"exact:{method}"
    key = norm(app)
    close = difflib.get_close_matches(key, list(idx.keys()), n=1, cutoff=0.86)
    if close:
        return idx[close[0]], difflib.SequenceMatcher(None, key, close[0]).ratio(), "fuzzy"
    return None, 0.0, "none"


def main() -> int:
    api_key = os.environ.get("COMPOSIO_API_KEY", "").strip()
    if not api_key:
        try:
            api_key = input("COMPOSIO_API_KEY: ").strip()
        except EOFError:
            api_key = ""
    if not api_key:
        print("No API key supplied. Get one at https://platform.composio.dev -> Settings -> API Keys")
        return 2

    if not APPS_CSV.exists():
        print(f"Missing {APPS_CSV}. Run from the repo root.")
        return 2

    with APPS_CSV.open(newline="", encoding="utf-8") as f:
        apps = list(csv.DictReader(f))
    log(f"Loaded {len(apps)} apps from {APPS_CSV.relative_to(ROOT)}")
    log("")

    base = pick_base(api_key)
    if not base:
        log("")
        log("Could not reach any Composio API base. Saving diagnostics and exiting.")
        DIAG_OUT.write_text("\n".join(DIAG), encoding="utf-8")
        return 1
    log(f"Using base: {base}")
    log("")

    log("Fetching toolkit registry...")
    toolkits = fetch_all_toolkits(base, api_key)
    if not toolkits:
        log("No toolkits returned. Saving diagnostics and exiting.")
        DIAG_OUT.write_text("\n".join(DIAG), encoding="utf-8")
        return 1
    RAW_OUT.write_text(json.dumps(toolkits, indent=2), encoding="utf-8")
    log(f"Wrote {RAW_OUT.relative_to(ROOT)} ({len(toolkits)} toolkits)")
    log("")

    idx = build_index(toolkits)
    rows, matched_slugs = [], []
    for app in apps:
        tk, score, method = match_app(app["app"], app.get("slug_guess", ""), idx)
        meta = (tk or {}).get("meta") or {}
        slug = (tk or {}).get("slug", "")
        if slug:
            matched_slugs.append(slug)
        rows.append(
            {
                "id": app["id"],
                "app": app["app"],
                "category": app["category"],
                "matched": bool(tk),
                "match_method": method,
                "match_score": round(score, 3),
                "composio_slug": slug,
                "composio_name": (tk or {}).get("name", ""),
                "auth_schemes": "|".join((tk or {}).get("auth_schemes") or []),
                "managed_auth_schemes": "|".join((tk or {}).get("composio_managed_auth_schemes") or []),
                "no_auth": (tk or {}).get("no_auth", ""),
                "auth_guide_url": (tk or {}).get("auth_guide_url", "") or "",
                "tools_count": meta.get("tools_count", ""),
                "triggers_count": meta.get("triggers_count", ""),
                "composio_categories": "|".join(
                    c.get("name", "") for c in (meta.get("categories") or []) if isinstance(c, dict)
                ),
            }
        )

    with MATCH_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"Wrote {MATCH_OUT.relative_to(ROOT)}")

    log("")
    log(f"Fetching per-toolkit detail for {len(matched_slugs)} matched slugs...")
    details: dict[str, object] = {}
    for i, slug in enumerate(matched_slugs, 1):
        status, payload, err = request_json(f"{base}/toolkits/{slug}", api_key)
        if status == 200 and payload is not None:
            details[slug] = payload
        else:
            details[slug] = {"_error": f"HTTP {status} {err[:200]}"}
        if i % 10 == 0:
            log(f"  {i}/{len(matched_slugs)}")
        time.sleep(0.15)
    DETAIL_OUT.write_text(json.dumps(details, indent=2), encoding="utf-8")
    log(f"Wrote {DETAIL_OUT.relative_to(ROOT)}")

    # ---- Gate A summary -------------------------------------------------
    n_matched = sum(1 for r in rows if r["matched"])
    exact = sum(1 for r in rows if r["match_method"].startswith("exact"))
    fuzzy = sum(1 for r in rows if r["match_method"] == "fuzzy")
    log("")
    log("=" * 62)
    log(f"  GATE A:  {n_matched}/100 apps found in Composio's catalog")
    log(f"           {exact} exact, {fuzzy} fuzzy (fuzzy ones need eyeballing)")
    log("=" * 62)
    if n_matched < 30:
        log("  < 30 matched -> Lane A degrades to spec-discovery only.")
    else:
        log("  Lane A is viable as the ground-truth oracle.")
    log("")
    log("Per-category matches:")
    by_cat: dict[str, list[int]] = {}
    for r in rows:
        b = by_cat.setdefault(r["category"], [0, 0])
        b[1] += 1
        if r["matched"]:
            b[0] += 1
    for cat, (hit, tot) in by_cat.items():
        log(f"  {hit:>2}/{tot:<3} {cat}")

    DIAG_OUT.write_text("\n".join(DIAG), encoding="utf-8")
    log("")
    log(f"Diagnostics: {DIAG_OUT.relative_to(ROOT)}")
    log("Done. Paste the GATE A block back into the Cowork session.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
