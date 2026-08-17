"""Research one app, end to end.

    plan queries -> search -> rank -> fetch -> scan for signals -> extract
                 -> grade every quote -> quarantine what failed -> probe the server
                 -> derive the verdicts -> assemble one auditable record

Two things about the shape. The queries are planned per question rather than per
app, because "what auth does X use" and "which tier includes API access" live on
completely different pages and a single query returns only the first kind. And
pricing pages are guaranteed a slot in the source set: in the first run they were
ranked below API reference pages and cut before the model saw them, which produced
6 fabrications in the first batch of 10. Forcing them in dropped that to 1.
"""
from __future__ import annotations

import json
import re
import time

from . import derive, evidence, probe, prompts, schema


def plan_queries(app: dict) -> list[tuple[str, str]]:
    """(intent, query). Intent is kept so source ranking can guarantee coverage of
    each question rather than letting one topic win all the slots."""
    name, hint = app["app"], app.get("hint", "")
    return [
        ("auth",    f"{name} {hint} API authentication documentation"),
        ("pricing", f"{name} pricing plans which tier includes API access"),
        ("signup",  f"{name} how to get an API key developer access signup"),
        # Nobody wrote this query in pass 1, which is the entire reason 75 of 100
        # `existing_mcp` values came back unknown. The brief asks for it explicitly.
        ("mcp",     f"{name} MCP server model context protocol integration"),
    ]


PRICING_RE = re.compile(r"(pricing|plans|billing|/subscribe)", re.I)
MCP_RE = re.compile(r"(mcp|model-context-protocol|modelcontextprotocol)", re.I)
DOCS_RE = re.compile(r"(docs?|developers?|api|reference|auth)", re.I)
JUNK_RE = re.compile(r"(reddit|youtube|linkedin|quora|facebook|twitter|x)\.com", re.I)


def rank_urls(app: dict, citations: list[tuple[str, str]], k: int = 9) -> list[str]:
    """Prefer the vendor's own domain, then documentation-shaped paths. Third-party
    integration directories sort last but are not banned -- sometimes they are the
    only thing that exists, and source_tier records that fact rather than hiding it."""
    hint, name = app.get("hint", ""), app["app"]

    def score(url: str) -> tuple:
        tier = schema.source_tier(url, hint, name)
        return (
            tier,                                   # vendor's own domain first
            0 if DOCS_RE.search(url) else 1,
            0 if PRICING_RE.search(url) else 1,
            5 if JUNK_RE.search(url) else 0,
            len(url),
        )

    seen, out = set(), []
    for url, _title in sorted({u: t for u, t in citations if u}.items(), key=lambda c: score(c[0])):
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out[:k]


def pick_sources(pages: list[dict], n_docs: int = 3, n_pricing: int = 2, n_mcp: int = 1) -> list[dict]:
    """Reserve a slot for each question. Without this, API reference pages -- which
    never discuss pricing or MCP -- crowd out the pages that answer the access and
    agent-readiness fields."""
    pricing = [p for p in pages if PRICING_RE.search(p["url"])]
    mcp = [p for p in pages if MCP_RE.search(p["url"]) and p not in pricing]
    docs = [p for p in pages if p not in pricing and p not in mcp]
    chosen = docs[:n_docs] + pricing[:n_pricing] + mcp[:n_mcp]
    # Backfill if a category was empty so we do not waste the budget.
    for page in pages:
        if len(chosen) >= n_docs + n_pricing + n_mcp:
            break
        if page not in chosen:
            chosen.append(page)
    return chosen


def _parse_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {"_raw": (text or "")[:400]}
    try:
        return json.loads(match.group(0))
    except Exception as exc:
        return {"_parse_error": str(exc), "_raw": (text or "")[:400]}


def research_app(app: dict, providers, matches: dict | None = None,
                 sources_only: bool = False, max_chars: int = 9000) -> dict:
    """One record. Never raises: a failure becomes a record that says how it failed,
    because a run of 100 must not die on app 43."""
    started = time.time()
    matches = matches or {}
    registry = matches.get(app["id"], {})

    citations: list[tuple[str, str]] = []
    for _intent, query in plan_queries(app):
        citations += providers.search(query)

    urls = rank_urls(app, citations)
    pages = providers.fetch(urls, max_chars)
    chosen = pick_sources(pages)
    page_texts = {p["url"]: p["text"] for p in chosen}

    detectors = evidence.scan(pages)
    api_probes = probe.api_probe(app.get("hint", ""), pages)
    pricing = probe.pricing_probe(app.get("hint", ""))
    spec = probe.spec_probe(app.get("hint", ""), pages)

    record = {
        "id": app["id"], "app": app["app"], "category": app["category"],
        "hint": app.get("hint", ""),
        "urls_ranked": urls,
        "pages_fetched": [p["url"] for p in pages],
        "sources_used": list(page_texts),
        "source_tiers": {u: schema.source_tier(u, app.get("hint", ""), app["app"])
                         for u in page_texts},
        "detectors": detectors,
        "probes": api_probes,
        "pricing_probe": pricing,
        "spec_probe": spec,
        "registry": registry,
    }

    if sources_only:
        record.update({"extracted": {}, "quote_checks": {}, "secs": round(time.time() - started, 1),
                       "mode": "sources-only"})
        return record

    sources_block = "\n\n".join(
        f"--- SOURCE {p['url']}  (tier {schema.source_tier(p['url'], app.get('hint',''), app['app'])})\n"
        f"{p['text'][:5000]}" for p in chosen)

    prompt = prompts.extract_prompt(app, evidence.signals_for_prompt(detectors), sources_block)
    raw, llm_err = providers.llm(prompt)
    parsed = _parse_json(raw)
    unknown_reason = parsed.pop("unknown_reason", {}) or {}
    extracted = schema.normalise(parsed)

    # Deterministic override: if the page text plainly documents rate limits, we do
    # not need the model's opinion about whether it does.
    if schema.is_blank("rate_limits_documented", extracted["rate_limits_documented"]["value"]):
        if evidence.rate_limits_seen(chosen):
            extracted["rate_limits_documented"]["value"] = "yes"
            extracted["rate_limits_documented"]["quote"] = ""
            extracted["rate_limits_documented"]["url"] = next(iter(page_texts), "")
            extracted["rate_limits_documented"]["derived"] = "phrase-scanner"

    checks = evidence.grade_record(extracted, page_texts, app.get("hint", ""), app["app"])
    extracted, quarantined = evidence.quarantine(extracted, checks, unknown_reason)

    access = derive.derive_access(extracted, detectors, pricing)
    buildability = derive.derive_buildability(extracted, access["value"], registry)
    breadth_count = registry.get("composio_tools_count") or spec.get("operation_count")

    record.update({
        "extracted": extracted,
        "quote_checks": checks,
        "quote_summary": evidence.summarise(checks),
        "quarantined": quarantined,
        "unknown_reason": derive.fill_unknown_reasons(extracted, unknown_reason, len(pages)),
        "auth_family": derive.auth_family(extracted["auth_methods"]["value"]),
        "self_serve": access,
        "buildability": buildability,
        "breadth": {"count": breadth_count,
                    "bucket": probe.breadth_bucket(breadth_count),
                    "source": "composio-registry" if registry.get("composio_tools_count")
                              else ("openapi-spec" if spec.get("operation_count") else "none")},
        "contradictions": derive.reconcile(extracted, access, buildability),
        "llm_err": llm_err,
        "secs": round(time.time() - started, 1),
        # Kept so the validator can be re-run offline with stricter rules without
        # re-fetching a single page. This is what made the pass-2 re-grade free.
        "_texts_kept": {u: t[:2500] for u, t in page_texts.items()},
    })
    return record


def baseline_app(app: dict, providers) -> dict:
    """Pass 0 -- the deliberately naive version. One LLM call, no retrieval, answers
    from memory. This is the low point the improvement is measured from, and it has
    to be a real attempt rather than a strawman, so it gets the same schema."""
    started = time.time()
    raw, err = providers.llm(prompts.baseline_prompt(app))
    extracted = schema.normalise(_parse_json(raw))
    return {
        "id": app["id"], "app": app["app"], "category": app["category"],
        "hint": app.get("hint", ""), "extracted": extracted,
        "auth_family": derive.auth_family(extracted["auth_methods"]["value"]),
        "self_serve": derive.derive_access(extracted),
        "mode": "pass0-no-retrieval", "llm_err": err,
        "secs": round(time.time() - started, 1),
    }
