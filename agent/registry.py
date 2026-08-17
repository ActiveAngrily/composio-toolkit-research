"""Lane A -- Composio's own toolkit registry, the one source in this project that
cannot hallucinate.

It answers three things no amount of documentation reading can:
  * which of the 100 apps Composio already covers (56 of them),
  * what auth scheme Composio actually implemented for each,
  * how many tools each toolkit exposes -- a non-LLM answer to the brief's
    "roughly how broad".

And it gives us an accuracy check that needs no human at all: for those 56 apps we
know the auth scheme, so we can score the research agent against a non-LLM
authority on 56 rows instead of the 20 a person can hand-check in an evening.

The matcher deliberately reports fuzzy matches for review instead of trusting them.
That is not decoration -- it caught Plaid being matched to a Composio toolkit
slugged `placid` at 0.909 similarity. Placid is an image-generation product with no
relationship to Plaid. One false positive in 57, found because the deterministic
lane was made to show its uncertainty.
"""
from __future__ import annotations

import csv
import difflib
import json
import re
import urllib.request

from . import config

TOOLKITS_JSON = config.DATA / "composio_toolkits.json"
MATCH_CSV = config.DATA / "composio_match.csv"

# Verified against the raw registry: there is no Plaid toolkit. Kept explicit and
# in version control so the correction is auditable rather than a silent edit.
KNOWN_FALSE_POSITIVES = {("Plaid", "placid")}

FUZZY_THRESHOLD = 0.88


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def fetch_registry(api_key: str, page_limit: int = 1000) -> list[dict]:
    """Paginate Composio's public toolkit list. Standard library only, so this runs
    anywhere with a key and no install step."""
    out, cursor = [], None
    while True:
        url = f"{config.COMPOSIO_BASE}/toolkits?limit={page_limit}"
        if cursor:
            url += f"&cursor={urllib.parse.quote(str(cursor))}"
        req = urllib.request.Request(url, headers={"x-api-key": api_key,
                                                  "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        items = body.get("items") or body.get("data") or []
        out.extend(items)
        cursor = (body.get("next_cursor") or (body.get("meta") or {}).get("next_cursor"))
        if not items or not cursor:
            break
    return out


def match(apps: list[dict], toolkits: list[dict]) -> list[dict]:
    """Exact slug match first, then fuzzy with the score recorded so a human can
    check it. Nothing is accepted on fuzzy grounds without the score being visible."""
    by_slug = {(t.get("slug") or "").lower(): t for t in toolkits}
    by_norm = {_slugify(t.get("name") or ""): t for t in toolkits}

    rows = []
    for app in apps:
        name = app["app"]
        guess = _slugify(name)
        hit, method, score = None, "", 0.0

        if guess in by_slug:
            hit, method, score = by_slug[guess], "exact:slug", 1.0
        elif guess in by_norm:
            hit, method, score = by_norm[guess], "exact:name", 1.0
        else:
            best = difflib.get_close_matches(guess, list(by_slug), n=1, cutoff=FUZZY_THRESHOLD)
            if best:
                score = difflib.SequenceMatcher(None, guess, best[0]).ratio()
                hit, method = by_slug[best[0]], "fuzzy:slug"

        if hit and (name, (hit.get("slug") or "").lower()) in KNOWN_FALSE_POSITIVES:
            rows.append({**app, "matched": False, "match_method": "rejected:false-positive",
                         "match_score": round(score, 3), "composio_slug": "",
                         "note": f"fuzzy match to '{hit.get('slug')}' rejected by human review"})
            continue

        if not hit:
            rows.append({**app, "matched": False, "match_method": "", "match_score": 0.0,
                         "composio_slug": ""})
            continue

        auth = hit.get("auth_schemes") or hit.get("authSchemes") or []
        meta = hit.get("meta") or {}
        rows.append({
            **app,
            "matched": True,
            "match_method": method,
            "match_score": round(score, 3),
            "composio_slug": hit.get("slug", ""),
            "composio_name": hit.get("name", ""),
            "auth_schemes": "|".join(auth if isinstance(auth, list) else [str(auth)]),
            "no_auth": bool(hit.get("no_auth") or hit.get("noAuth")),
            "tools_count": meta.get("tools_count") or hit.get("tools_count") or 0,
            "triggers_count": meta.get("triggers_count") or hit.get("triggers_count") or 0,
            "composio_categories": "|".join(
                c.get("name", c) if isinstance(c, dict) else str(c)
                for c in (hit.get("categories") or [])),
            "needs_review": method.startswith("fuzzy"),
        })
    return rows


def load_matches(path=MATCH_CSV) -> dict[int, dict]:
    """Read the generated match table keyed by app id. Joining on id, not name --
    apps.csv says 'Lark (Larksuite)' where the dataset says 'Lark', and a name join
    silently drops those rows."""
    if not path.exists():
        return {}
    out = {}
    for row in csv.DictReader(open(path, encoding="utf-8")):
        matched = str(row.get("matched", "")).strip().lower() == "true"
        slug = (row.get("composio_slug") or "").strip()
        if matched and (row.get("app"), slug) in KNOWN_FALSE_POSITIVES:
            matched = False
        out[int(row["id"])] = {
            "in_catalog": matched,
            "composio_slug": slug if matched else "",
            "composio_auth_schemes": [s for s in (row.get("auth_schemes") or "").split("|") if s]
                                     if matched else [],
            "composio_tools_count": int(row.get("tools_count") or 0) if matched else 0,
            "composio_triggers_count": int(row.get("triggers_count") or 0) if matched else 0,
            "composio_categories": [c for c in (row.get("composio_categories") or "").split("|") if c],
            # What catalog membership does and does not prove. See derive.py.
            "credential_obtainable": matched,
        }
    return out


# Composio's vocabulary -> ours, so the cross-check compares like with like.
REGISTRY_AUTH_MAP = {
    "OAUTH2": "OAUTH2", "OAUTH1": "OAUTH2", "S2S_OAUTH2": "OAUTH2",
    "API_KEY": "API_KEY", "BEARER_TOKEN": "BEARER", "BASIC": "BASIC",
    "BASIC_WITH_JWT": "BASIC", "GOOGLE_SERVICE_ACCOUNT": "JWT", "NO_AUTH": "NONE",
}


def cross_check_auth(records: list[dict], matches: dict[int, dict]) -> dict:
    """Score the research agent's auth answers against Composio's implementation.

    Reported two ways on purpose. Token-level agreement is the strict read.
    Family-level agreement collapses the credential/transport confusion, and the
    gap between the two numbers is the measurement of how much of the apparent
    error was our own schema rather than the model's retrieval.
    """
    from .derive import auth_family

    strict_hit, family_hit, rows = 0, 0, []
    for rec in records:
        reg = matches.get(rec["id"])
        if not reg or not reg["in_catalog"]:
            continue
        registry_auth = {REGISTRY_AUTH_MAP.get(s, s) for s in reg["composio_auth_schemes"]}
        ours = set((rec.get("extracted", {}).get("auth_methods") or {}).get("value") or [])

        strict = bool(registry_auth & ours) or (registry_auth == {"NONE"} and not ours)
        family = auth_family(sorted(registry_auth)) == auth_family(sorted(ours)) or \
                 auth_family(sorted(ours)) == "both" and auth_family(sorted(registry_auth)) in \
                 ("oauth-dance", "static-secret")
        strict_hit += strict
        family_hit += family
        if not strict:
            rows.append({"app": rec["app"], "registry": sorted(registry_auth),
                         "agent": sorted(ours),
                         "family_agrees": family,
                         "cause": "transport-vs-credential" if family else "recall-miss"})

    total = sum(1 for r in records if matches.get(r["id"], {}).get("in_catalog"))
    return {
        "sample": total,
        "token_level_agree": strict_hit,
        "family_level_agree": family_hit,
        "token_pct": round(100 * strict_hit / total, 1) if total else 0,
        "family_pct": round(100 * family_hit / total, 1) if total else 0,
        "disagreements": rows,
    }
