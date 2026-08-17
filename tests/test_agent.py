#!/usr/bin/env python3
"""Tests for the research agent. No network, no LLM, no pytest required:

    python tests/test_agent.py

Every test here corresponds to a specific failure measured in pass 1, so the file
doubles as the changelog. The pass-1 numbers quoted in the comments come from
`outputs/dataset_v1.json` and are reproducible from it.
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent import config, derive, evidence, pipeline, probe, prompts, registry, schema
from agent import providers as providers_mod

FAILURES: list[str] = []


def check(label, got, want):
    ok = got == want
    if not ok:
        FAILURES.append(f"{label}: got {got!r}, want {want!r}")
    print(f"{'ok  ' if ok else 'FAIL'} {label:<52} {got!r}")


def section(title):
    print(f"\n=== {title} ===")


# --------------------------------------------------------------------------------
def test_source_tier():
    """PLAN.md promised the citation validator would require an official-domain page.
    That half never shipped, so 79 of 483 pass-1 citations pointed at third-party
    integration directories and every one was graded `valid`."""
    section("source tiers -- authority, not just fidelity")
    check("vendor's own domain", schema.source_tier("https://developer.salesforce.com/docs/x", "salesforce.com"), 1)
    check("vendor docs subdomain", schema.source_tier("https://docs.stripe.com/api", "stripe.com"), 1)
    check("vendor code host", schema.source_tier("https://github.com/acme/x", "acme.dev"), 2)
    check("n8n integration docs", schema.source_tier("https://docs.n8n.io/x", "highlevel.stoplight.io"), 3)
    check("apis.io directory", schema.source_tier("https://apis.io/x", "plain.com"), 3)
    check("marketing blog", schema.source_tier("https://topadvisor.com/x", "gladly.com"), 4)
    # binance.us is a different legal entity with different KYC rules from
    # binance.com. Pass 1 cited it for Binance's approval gate and graded it valid.
    check("binance.us is a separate entity", schema.source_tier("https://binance.us/faq", "binance.com"), 4)
    check("brand across TLD is the same vendor", schema.source_tier("https://developers.notion.com/x", "notion.so"), 1)
    # 8 of the brief's 100 hints point at a third-party docs host rather than the
    # vendor, so the app name is a second brand candidate.
    check("stoplight hint, vendor found by app name",
          schema.source_tier("https://marketplace.gohighlevel.com/x",
                             "highlevel.stoplight.io", "GoHighLevel"), 1)
    check("github.io hint, vendor found by app name",
          schema.source_tier("https://www.binance.com/en/support",
                             "binance-docs.github.io", "Binance"), 1)
    check("cake.com marketplace is a directory",
          schema.source_tier("https://dev-docs.marketplace.cake.com/x", "pumble.com", "Pumble"), 3)
    check("no url at all", schema.source_tier("", "x.com"), 5)


def test_auth_family():
    """BEARER and BASIC are transports; API_KEY is a credential kind. Treating them
    as coequal enum values made Attio come out as four auth methods, and accounted
    for 6 of the 10 disagreements against Composio's own registry."""
    section("auth families -- credential kind, not envelope")
    check("BASIC is a static secret", derive.auth_family(["BASIC"]), "static-secret")
    check("API_KEY, same family", derive.auth_family(["API_KEY"]), "static-secret")
    check("BEARER, same family", derive.auth_family(["BEARER"]), "static-secret")
    check("four values collapse to one fact", derive.auth_family(["API_KEY", "BASIC", "BEARER", "OAUTH2"]), "both")
    check("OAuth only", derive.auth_family(["OAUTH2"]), "oauth-dance")
    check("no auth at all", derive.auth_family(["NONE"]), "none")
    check("nothing found", derive.auth_family([]), "unknown")


def test_normalisation():
    """Pass 1 normalised auth_methods only, which is why `protocol` shipped both
    'REST' and 'rest' as distinct values across the 100 rows."""
    section("normalisation applies to every enum field")
    ex = schema.normalise({
        "protocol": {"value": ["rest", "GraphQL", "Rest API"], "quote": "q", "url": "u"},
        "auth_methods": {"value": ["OAuth 2.0", "bearer token", "personal access token", "hmac"],
                         "quote": "q", "url": "u"},
    })
    check("protocol deduped and cased", ex["protocol"]["value"], ["GraphQL", "REST"])
    check("auth mapped, unknowns to OTHER", ex["auth_methods"]["value"],
          ["API_KEY", "BEARER", "OAUTH2", "OTHER"])
    check("absent field gets a shape", ex["existing_mcp"]["value"], "unknown")
    check("absent field gets empty citation", ex["existing_mcp"]["url"], "")


PAGES = {
    "https://docs.acme.com/auth": "Acme uses OAuth 2.0 for all requests.",
    "https://docs.acme.com/pricing": "API access requires the Pro plan.",
}


def _graded():
    ex = schema.normalise({
        "auth_methods": {"value": ["OAUTH2"], "quote": "Acme uses OAuth 2.0 for all requests.",
                         "url": "https://docs.acme.com/auth"},
        # real sentence, wrong page attributed
        "api_access_tier": {"value": "paid", "quote": "API access requires the Pro plan.",
                            "url": "https://docs.acme.com/auth"},
        # invented outright
        "protocol": {"value": ["REST"], "quote": "Acme exposes a GraphQL endpoint.",
                     "url": "https://docs.acme.com/auth"},
        "approval_gate": {"value": "none", "quote": "", "url": ""},
        "existing_mcp": {"value": "none", "quote": "", "url": ""},
        "signup_self_serve": {"value": "yes", "quote": "", "url": ""},
        "primary_blocker": {"value": "paid-plan", "quote": "API access requires the Pro plan.",
                            "url": "https://docs.acme.com/pricing"},
    })
    return ex, evidence.grade_record(ex, PAGES, "acme.com")


def test_grading_is_url_strict():
    """Pass 1's grader fell back to matching against every page concatenated when the
    cited URL was not among the chosen sources, so a quote lifted from page A and
    attributed to page B graded `valid`."""
    section("grading is URL-strict")
    _, checks = _graded()
    check("exact quote on the cited page", checks["auth_methods"]["verdict"], "valid")
    check("real quote, wrong page cited", checks["api_access_tier"]["verdict"], "wrong-url")
    check("quote appears nowhere", checks["protocol"]["verdict"], "QUOTE_NOT_FOUND")
    check("blocker is gradable at all", checks["primary_blocker"]["verdict"], "valid")
    # In pass 1 the blocker field asked for a free-text `reason` instead of a quote,
    # so 88 of 100 records had no grade for the most consequential field.


def test_absence_claims():
    """You cannot quote a page for the absence of an app review -- no vendor writes
    'there is no approval process for this API'. Demanding a verbatim span for a
    negative would quarantine every honest 'none', so absences are graded as resting
    on absence of evidence and counted separately."""
    section("absence claims are not fabrications")
    _, checks = _graded()
    check("no approval gate", checks["approval_gate"]["verdict"], "absence-claim")
    check("no MCP server", checks["existing_mcp"]["verdict"], "absence-claim")
    check("absence does not count as evidenced", checks["approval_gate"]["evidenced"], False)
    check("a positive claim still needs a quote", checks["signup_self_serve"]["verdict"], "no-quote")


def test_quarantine_acts():
    """Pass 1 detected 34 fabricated quotes and left all 34 values in the dataset,
    visually identical to the 385 verified ones."""
    section("the grader's verdict is acted on")
    ex, checks = _graded()
    reasons: dict = {}
    ex, removed = evidence.quarantine(ex, checks, reasons)
    check("fabricated value removed", ex["protocol"]["value"], [])
    check("reason recorded", reasons.get("protocol"), "quote-failed-validation")
    check("unevidenced positive removed", ex["signup_self_serve"]["value"], "unknown")
    check("absence claim survives", ex["approval_gate"]["value"], "none")
    check("verified claim untouched", ex["auth_methods"]["value"], ["OAUTH2"])
    check("exhibits kept for the taxonomy", len(removed), 2)


def test_admin_consent_detected():
    """The brief names four gate kinds: paid plan, admin approval, partnership /
    contact-sales. Pass 1's enum had no admin-consent value."""
    section("admin consent -- the gate kind pass 1 could not express")
    hits = evidence.scan([{"url": "https://docs.acme.com/install",
                           "text": "To install this app a workspace administrator must approve "
                                   "the request. A free tier is available."}])
    tags = {h["tag"] for h in hits}
    check("admin-consent found", "admin-consent" in tags, True)
    check("free-tier also found", "free-tier" in tags, True)


def test_derivation_and_basis():
    section("derivations carry their basis, hardest gate wins")
    free_but_reviewed = schema.normalise({
        "api_access_tier": {"value": "free"}, "approval_gate": {"value": "app-review"},
        "credential_self_issue": {"value": "yes"}, "product_class": {"value": "api"}})
    got = derive.derive_access(free_but_reviewed)
    check("free tier + app review is not self-serve", got["value"], "app-review")

    cli = schema.normalise({"product_class": {"value": "cli-only"}})
    check("a local CLI has no API to gate", derive.derive_access(cli)["value"], "no-public-api")

    # The Pylon case: usepylon.com/pricing redirects to /schedule-demo. The docs-only
    # pass returned unknown; one redirect settles it.
    pylon = derive.derive_access(schema.normalise({}),
                                 detectors=[{"tag": "contact-sales", "kind": "gate"}],
                                 pricing={"sales_gate": True})
    check("pricing redirect resolves an unknown", pylon["value"], "partner-or-sales-gate")
    check("and says why", pylon["basis"], "pricing page redirects to a sales flow")


def test_buildability():
    """A required column the brief asks for by name, which pass 1 had no field for."""
    section("buildability verdict")
    mermaid = derive.derive_buildability(schema.normalise({"product_class": {"value": "cli-only"}}),
                                        "no-public-api")
    check("Mermaid-CLI shape: wrap the CLI", (mermaid["value"], mermaid["blocker"]),
          ("build-with-caveats", "no-public-api"))
    # Google Ads is in Composio's catalog AND its developer token needs Google's
    # approval. Catalog membership proves a toolkit is possible; it does not delete the
    # gate the next integrator hits. So it is its own verdict and the blocker survives.
    google_ads = derive.derive_buildability(
        schema.normalise({"protocol": {"value": ["REST"]}, "product_class": {"value": "api"}}),
        "app-review", {"in_catalog": True, "composio_slug": "google_ads"})
    check("in catalog is its own verdict", google_ads["value"], "already-built")
    check("and the real gate survives it", google_ads["blocker"], "app-review")
    free_and_new = derive.derive_buildability(
        schema.normalise({"protocol": {"value": ["REST"]}, "product_class": {"value": "api"}}),
        "free", {})
    check("not in catalog, self-serve", free_and_new["value"], "build-now")


def test_contradictions():
    """Pass 1 computed the blocker in the prompt and access in code, never compared
    them, and shipped 7 rows where they disagreed. Google Ads was one."""
    section("self-contradicting rows are surfaced")
    ex = schema.normalise({"primary_blocker": {"value": "none"},
                           "api_access_tier": {"value": "paid"},
                           "product_class": {"value": "api"},
                           "protocol": {"value": ["REST"]}})
    access = derive.derive_access(ex)
    problems = derive.reconcile(ex, access, derive.derive_buildability(ex, access["value"]))
    check("contradiction caught", len(problems) >= 1, True)
    check("and named", "primary_blocker=none" in problems[0], True)


def test_breadth_buckets():
    """'Roughly how broad' -- one vocabulary shared by Composio tool counts (56 apps)
    and OpenAPI operation counts (the other 44), so the column means one thing."""
    section("breadth buckets")
    for count, want in [(0, "unknown"), (12, "narrow"), (88, "medium"),
                        (184, "broad"), (871, "very-broad")]:
        check(f"breadth({count})", probe.breadth_bucket(count), want)


def test_registry():
    """Plaid matched a toolkit slugged `placid` at 0.909 similarity. Placid is an
    image-generation product. The rejection is in version control, not a silent edit."""
    section("registry -- ground truth, and the false positive it produced")
    matches = registry.load_matches()
    if not matches:
        print("skip  data/composio_match.csv not present")
        return
    check("in catalog after rejecting Placid", sum(1 for m in matches.values() if m["in_catalog"]), 56)
    check("Plaid stays out", matches[82]["in_catalog"], False)
    check("GitHub tool count present", matches[61]["composio_tools_count"] > 800, True)
    # Google Ads (31), Meta Ads (32) and LinkedIn Ads (33) are all in the catalog and
    # all three are approval- or partner-gated. So catalog membership proves the
    # credential is obtainable, NOT that it is self-serve.
    check("catalog implies obtainable, not self-serve", matches[31]["credential_obtainable"], True)


def test_cross_check_families_are_sets():
    """Family compatibility is a set intersection, not equality. Composio recording
    {API_KEY, OAUTH2} and us finding {API_KEY} is agreement -- checking equality called
    it a miss, which made family-level agreement score LOWER than token-level, i.e.
    backwards by construction."""
    section("registry cross-check: family compatibility")
    recs = [
        {"id": 1, "app": "Both vs one", "extracted": {"auth_methods": {"value": ["API_KEY"]}}},
        {"id": 2, "app": "Transport confusion", "extracted": {"auth_methods": {"value": ["BEARER"]}}},
        {"id": 3, "app": "Real recall miss", "extracted": {"auth_methods": {"value": ["API_KEY"]}}},
    ]
    matches = {
        1: {"in_catalog": True, "composio_auth_schemes": ["API_KEY", "OAUTH2"]},
        2: {"in_catalog": True, "composio_auth_schemes": ["API_KEY"]},
        3: {"in_catalog": True, "composio_auth_schemes": ["OAUTH2"]},
    }
    out = registry.cross_check_auth(recs, matches)
    check("sample size", out["sample"], 3)
    check("token-level agreement", out["token_level_agree"], 1)
    check("family-level is never worse", out["family_level_agree"] >= out["token_level_agree"], True)
    check("family-level agreement", out["family_level_agree"], 2)
    causes = {r["app"]: r["cause"] for r in out["disagreements"]}
    check("Bearer vs API_KEY is our taxonomy", causes.get("Transport confusion"),
          "transport-vs-credential")
    check("missing OAuth2 is a real miss", causes.get("Real recall miss"), "recall-miss")


def test_redaction():
    """Quoting Stripe's docs verbatim captured one of their example sk_test_ keys and
    GitHub's secret scanner blocked the push. Pass 1 defined redact() and never
    called it; it now runs on every record before it is written."""
    section("credential redaction is wired in")
    out = config.redact({"quote": "use sk_test_ABCDEF1234567890 as your key",
                         "nested": [{"t": "ghp_ABCDEFGHIJKLMNOPQRSTUV12345"}]})
    check("stripe-shaped key gone", "sk_test" not in out["quote"], True)
    check("nested token gone", "ghp_" not in out["nested"][0]["t"], True)


def test_prompt_renders():
    section("prompt template renders")
    text = prompts.extract_prompt({"app": "Notion", "category": "Productivity",
                                   "hint": "developers.notion.com"}, "  (none)", "--- SOURCE x\ntext")
    check("json shape survives templating", '"auth_methods": {"value": []' in text, True)
    check("no unsubstituted placeholders", "$" in text, False)
    check("blocker asks for a quote", '"primary_blocker": {"value": "", "quote": ""' in text, True)


def test_end_to_end_offline():
    """Whole pipeline against stub providers: no network, no model."""
    section("end to end, offline")
    pages = {
        "https://developers.acme.com/docs/auth":
            "Acme API. Authenticate with a Bearer token in the Authorization header. "
            "Generate an API key in your account settings. Rate limits: 100 requests per minute.",
        "https://acme.com/pricing":
            "Plans. Free: dashboard only. Pro: API access included. Contact sales for enterprise.",
        "https://github.com/acme/acme-mcp": "Official Acme MCP server. Maintained by Acme.",
    }
    answer = json.dumps({
        "one_liner": {"value": "Acme is a widget CRM.", "quote": "", "url": "https://acme.com/pricing"},
        "auth_methods": {"value": ["bearer", "api key"],
                         "quote": "Authenticate with a Bearer token in the Authorization header.",
                         "url": "https://developers.acme.com/docs/auth"},
        "signup_self_serve": {"value": "yes", "quote": "Free: dashboard only.",
                              "url": "https://acme.com/pricing"},
        "api_access_tier": {"value": "paid", "quote": "Pro: API access included.",
                            "url": "https://acme.com/pricing"},
        "credential_self_issue": {"value": "yes",
                                  "quote": "Generate an API key in your account settings.",
                                  "url": "https://developers.acme.com/docs/auth"},
        "approval_gate": {"value": "none", "quote": "", "url": ""},
        "protocol": {"value": ["rest"], "quote": "Acme API.",
                     "url": "https://developers.acme.com/docs/auth"},
        "rate_limits_documented": {"value": "yes", "quote": "Rate limits: 100 requests per minute.",
                                   "url": "https://developers.acme.com/docs/auth"},
        "existing_mcp": {"value": "official", "quote": "Official Acme MCP server.",
                         "url": "https://github.com/acme/acme-mcp"},
        "product_class": {"value": "api", "quote": "Acme API.",
                          "url": "https://developers.acme.com/docs/auth"},
        "primary_blocker": {"value": "none", "quote": "", "url": ""},
        "unknown_reason": {},
    })
    provs = providers_mod.Providers(
        lambda q: [(u, "") for u in pages],
        lambda urls, mc: [{"url": u, "text": pages[u]} for u in urls if u in pages],
        lambda p: (answer, ""), "stub")

    app = {"id": 4, "app": "Attio", "category": "CRM and Sales", "hint": "acme.com"}
    rec = pipeline.research_app(app, provs, matches={4: {
        "in_catalog": True, "composio_slug": "attio",
        "composio_auth_schemes": ["API_KEY", "OAUTH2"], "composio_tools_count": 184,
        "composio_triggers_count": 2, "composio_categories": ["crm"],
        "credential_obtainable": True}})

    check("auth normalised", rec["extracted"]["auth_methods"]["value"], ["API_KEY", "BEARER"])
    check("auth family", rec["auth_family"], "static-secret")
    check("protocol normalised", rec["extracted"]["protocol"]["value"], ["REST"])
    check("access derived", rec["self_serve"]["value"], "paid-tier-required")
    check("buildability derived", rec["buildability"]["value"], "already-built")
    check("breadth from the registry", rec["breadth"]["bucket"], "broad")
    check("contradiction caught in its own output", len(rec["contradictions"]), 1)
    # No network in CI: probes must degrade to a recorded error, not raise.
    check("probes degrade gracefully", isinstance(rec["probes"], list), True)
    check("page text retained for offline re-grade", bool(rec["_texts_kept"]), True)

    baseline = pipeline.baseline_app(app, provs)
    check("pass-0 path runs", baseline["mode"], "pass0-no-retrieval")


def main() -> int:
    for fn in [test_source_tier, test_auth_family, test_normalisation,
               test_grading_is_url_strict, test_absence_claims, test_quarantine_acts,
               test_admin_consent_detected, test_derivation_and_basis, test_buildability,
               test_contradictions, test_breadth_buckets, test_registry,
               test_cross_check_families_are_sets, test_redaction,
               test_prompt_renders, test_end_to_end_offline]:
        fn()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURES")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
