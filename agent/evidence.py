"""Evidence handling: the phrase scanner, quote grading, source tiering, and the
quarantine step that acts on what the grader finds.

Three corrections to pass 1 live here.

1.  Grading is URL-strict. Pass 1's grader fell back to matching the quote against
    every fetched page concatenated whenever the cited URL was not among the chosen
    sources -- so a quote lifted from page A and attributed to page B graded
    `valid`. It is now graded against the cited page only, and quote-somewhere-else
    is reported as `wrong-url`, which is a different failure and worth counting
    separately.

2.  Authority is checked, not just fidelity. A word-perfect quote from a third
    party's integration directory is not evidence about the vendor. Every citation
    carries a tier; tier > 2 means the value stays but stops counting as evidenced.

3.  The grader's verdict is acted on. Pass 1 detected 34 fabricated quotes and left
    all 34 values in the dataset at full confidence, indistinguishable from the 385
    good ones. Fabrications are now quarantined to `unknown` with a reason, and the
    originals are kept in `quarantined` so the error taxonomy has its exhibits.

Paraphrase (`near-miss`) is graded separately from fabrication on purpose. They are
different model failures and merging them hides which one is actually happening.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from . import schema
from .schema import norm_ws

# ----------------------------------------------------------------- phrase scanner

# Deterministic, no model involved. These already fired on 69 of 100 apps in pass 1
# and were then never shown to the extractor -- the single largest unused signal in
# the project. Now they are passed into the prompt as candidate evidence and used as
# a tiebreak in derive.py.
GATE_PATTERNS = [
    (r"contact (?:our )?sales", "contact-sales"),
    (r"talk to sales", "contact-sales"),
    (r"book a demo", "demo-gate"),
    (r"request (?:a )?demo", "demo-gate"),
    (r"schedule (?:a )?(?:personalized )?demo", "demo-gate"),
    (r"enterprise (?:plan|tier|customers) only", "enterprise-only"),
    (r"available (?:only )?(?:on|for) (?:the )?enterprise", "enterprise-only"),
    (r"request access", "request-access"),
    (r"apply for access", "request-access"),
    (r"approval (?:is )?required", "approval"),
    (r"subject to (?:review|approval)", "approval"),
    (r"app review", "app-review"),
    (r"developer token", "developer-token"),
    (r"business verification", "business-verification"),
    (r"partner program", "partner"),
    (r"become a partner", "partner"),
    (r"invite[- ]only", "invite-only"),
    (r"paid plan", "paid-plan"),
    (r"upgrade (?:your plan )?to access", "paid-plan"),
    # admin consent -- the gate kind the brief names and pass 1's schema omitted
    (r"(?:workspace|org(?:anization)?|domain|account) admin(?:istrator)? must", "admin-consent"),
    (r"requires? (?:an? )?admin(?:istrator)? (?:consent|approval|to install)", "admin-consent"),
    (r"only (?:workspace |org(?:anization)? )?admins?", "admin-consent"),
]

SELF_PATTERNS = [
    (r"create a free account", "free-account"),
    (r"sign up for free", "free-account"),
    (r"free (?:tier|plan)", "free-tier"),
    (r"no credit card required", "no-cc"),
    (r"start (?:your )?free trial", "free-trial"),
    (r"generate (?:an? )?api key", "self-issue"),
    (r"create (?:an? )?api (?:key|token)", "self-issue"),
    (r"in (?:your )?(?:account )?settings", "self-issue"),
]

# Defined and never used in pass 1; `rate_limits_documented` came from the model
# alone even though this is trivially checkable in the page text.
RATE_LIMIT_PATTERNS = [
    r"rate limit", r"ratelimit", r"too many requests", r"throttl",
    r"quota exceeded", r"requests per (?:second|minute|hour|day)",
]

_GATE = [(re.compile(p), t) for p, t in GATE_PATTERNS]
_SELF = [(re.compile(p), t) for p, t in SELF_PATTERNS]
_RATE = [re.compile(p) for p in RATE_LIMIT_PATTERNS]


def scan(pages: list[dict]) -> list[dict]:
    """Every match, with a surrounding span so a human can judge it, and the URL so
    the model can cite it. All matches, not just the first per pattern."""
    hits = []
    for page in pages:
        text = page.get("text") or ""
        low = text.lower()
        for rx, tag in _GATE + _SELF:
            kind = "gate" if (rx, tag) in _GATE else "self"
            for m in rx.finditer(low):
                s, e = max(0, m.start() - 90), min(len(text), m.end() + 90)
                hits.append({"tag": tag, "kind": kind, "url": page.get("url", ""),
                             "span": text[s:e]})
                break                                  # one span per pattern per page
    return hits


def rate_limits_seen(pages: list[dict]) -> bool:
    return any(rx.search((p.get("text") or "").lower()) for p in pages for rx in _RATE)


def signals_for_prompt(hits: list[dict], limit: int = 10) -> str:
    if not hits:
        return "  (none found)"
    seen, lines = set(), []
    for h in hits:
        if h["tag"] in seen:
            continue
        seen.add(h["tag"])
        lines.append(f'  [{h["kind"]}:{h["tag"]}] "...{h["span"].strip()[:170]}..."  {h["url"]}')
        if len(lines) >= limit:
            break
    return "\n".join(lines)


# ------------------------------------------------------------------ quote grading

def grade_quote(quote: str, page_text: str) -> str:
    """valid = the quote is literally there. near-miss = a real sentence, reformatted.
    QUOTE_NOT_FOUND = nothing close enough to be a paraphrase."""
    q, t = norm_ws(quote), norm_ws(page_text)
    if not q:
        return "abstained"
    if q in t:
        return "valid"
    win = len(q)
    best = 0.0
    for i in range(0, max(1, len(t) - win), max(1, win // 3)):
        best = max(best, SequenceMatcher(None, q, t[i:i + win]).ratio())
        if best > 0.9:
            break
    return "near-miss" if best >= 0.82 else "QUOTE_NOT_FOUND"


def grade_record(extracted: dict, page_texts: dict[str, str], app_hint: str,
                 app_name: str = "") -> dict:
    """One verdict per field, graded against the CITED page only."""
    checks: dict[str, dict] = {}
    for field in schema.FIELDS:
        cell = extracted.get(field.name) or {}
        quote, url, value = cell.get("quote", ""), cell.get("url", ""), cell.get("value")
        tier = schema.source_tier(url, app_hint, app_name)

        if not norm_ws(quote):
            if schema.is_blank(field.name, value):
                verdict = "abstained"
            elif schema.is_absence_claim(field.name, value):
                verdict = "absence-claim"
            else:
                verdict = "no-quote"
        elif url in page_texts:
            verdict = grade_quote(quote, page_texts[url])
            if verdict == "QUOTE_NOT_FOUND":
                elsewhere = any(grade_quote(quote, t) == "valid"
                                for u, t in page_texts.items() if u != url)
                if elsewhere:
                    verdict = "wrong-url"
        else:
            # The model cited a page we never fetched. It may be real, but we
            # cannot verify it here, so it does not get to be "valid".
            elsewhere = any(grade_quote(quote, t) == "valid" for t in page_texts.values())
            verdict = "wrong-url" if elsewhere else "unverifiable-url"

        # one_liner is exempt from the verbatim requirement by design.
        if not field.quoted and verdict in ("near-miss", "QUOTE_NOT_FOUND", "no-quote"):
            verdict = "unquoted-ok" if url else "no-source"

        # Fidelity and authority both pass on a real quote about the wrong product, so
        # the subject check overrides them when it fires.
        problem = subject_check(field.name, app_name, value, quote, tier,
                                cell.get("source", ""))
        if problem:
            verdict = problem

        checks[field.name] = {
            "verdict": verdict,
            "tier": tier,
            "evidenced": verdict in ("valid", "near-miss", "unquoted-ok")
                         and tier <= schema.EVIDENCED_MAX_TIER,
        }
    return checks


# --------------------------------------------------- is the claim even about this app?

# The failure that keeps recurring on this project is not a fabricated sentence -- it is
# a real sentence about the wrong product. Plaid matched a toolkit slugged `placid`;
# Sherlock's "official MCP server" describes the Covertlabs infostealer platform; iPayX's
# description welcomes you to iPaymu. Quote validation cannot catch any of these, because
# the quote is genuinely present on the page it cites. The page is just about something
# else.
#
# So: a cheap semantic check to sit beside the fidelity and authority ones.

_AUTH_INSTRUCTION = re.compile(
    r"^(authenticat|the api|to authenticat|use an? |include|pass |set the|add the|"
    r"send the|you can authenticat)|"
    r"(api key|access token|bearer token|oauth2?) (is|are|in the|to authenticate)", re.I)
_MCP_MENTION = re.compile(r"(mcp|model[- ]context[- ]protocol)", re.I)


def name_tokens(app: str) -> list[str]:
    parts = [w for w in re.split(r"[^a-z0-9]+", app.lower()) if len(w) > 2]
    return parts or [app.lower()]


def subject_check(field: str, app: str, value, quote: str, tier: int,
                  source: str = "") -> str | None:
    """Is this evidence even about this app? Returns a problem tag, or None.

    Three iterations to get the severity right, and the middle one is the interesting
    part. A first version flagged 79 claims and most were fine, because it fired on:

      * blank values -- "unknown" contains no app name, so every abstention looked like
        a wrong-product error;
      * Composio's own registry descriptions -- keyed by toolkit slug, so the subject is
        guaranteed by the lookup and the prose has no need to repeat the brand
        ("Collaborative workspace platform that transforms documents..." is Coda);
      * a vendor's own documentation, which routinely describes a product without naming
        it. That is normal writing, not evidence of contamination.

    So severity now depends on where the claim came from:

      QUARANTINE when the source is NOT the vendor's own domain (tier >= 3) and the text
      never names the app -- iPayX's description of iPaymu, Sherlock's MCP server for the
      Covertlabs platform, GoHighLevel's entire access story lifted from n8n's docs.

      QUARANTINE regardless of tier for two unambiguous shapes: a one-liner that is an
      authentication instruction, and MCP evidence that never mentions MCP.

    And one rule deliberately NOT here. A first attempt also flagged tier 1-2 claims whose
    text omits the app name, and that fired on 211 of them -- roughly two in five evidenced
    claims -- because a vendor's own documentation describes its own product without
    repeating the brand in every sentence. That is normal writing. More to the point the
    rule is redundant: at tier 1-2 the DOMAIN establishes the subject, so a sentence on
    docs.stripe.com is about Stripe whether or not it says so.

    The cost of dropping it is real and worth naming. Paygent Connect's description --
    "real-time cost visibility for your AI product" -- is not about a Japanese payment
    gateway, and it sits on a lookalike domain that passes every authority test in this
    project. No validator here catches it. It was caught by looking at the rendered table,
    which is the argument for presenting data where a person can see it rather than only
    scoring it.
    """
    if schema.is_blank(field, value):
        return None
    if source == "composio-registry":
        return None                        # subject guaranteed by the slug lookup

    text = f"{value if field == 'one_liner' else ''} {quote or ''}".strip()
    if not text:
        return None
    named = any(tok in text.lower() for tok in name_tokens(app))

    if field == "one_liner" and _AUTH_INSTRUCTION.search(str(value or "")):
        return "not-a-description"
    if field == "existing_mcp" and value not in ("none", "unknown", "") \
            and not _MCP_MENTION.search(text):
        return "off-topic-evidence"
    if not named and tier >= 3:
        return "unnamed-subject"
    return None


QUARANTINE_VERDICTS = {"QUOTE_NOT_FOUND", "no-quote", "unverifiable-url", "no-source",
                       "unnamed-subject", "not-a-description", "off-topic-evidence"}


def quarantine(extracted: dict, checks: dict, unknown_reason: dict) -> tuple[dict, list[dict]]:
    """Act on the grader. A claim whose quote could not be verified does not get to
    ship looking like one that could."""
    removed = []
    for name, check in checks.items():
        if check["verdict"] not in QUARANTINE_VERDICTS:
            continue
        cell = extracted.get(name) or {}
        if schema.is_blank(name, cell.get("value")):
            continue
        removed.append({"field": name, "verdict": check["verdict"],
                        "value": cell.get("value"), "quote": cell.get("quote", "")[:300],
                        "url": cell.get("url", "")})
        cell["value"] = [] if schema.BY_NAME[name].kind == "list" else "unknown"
        cell["quarantined"] = True
        unknown_reason[name] = "evidence-about-another-product" if check["verdict"] in (
            "unnamed-subject", "not-a-description", "off-topic-evidence"
        ) else "quote-failed-validation"
    return extracted, removed


def enforce_subject_checks(records: list[dict]) -> dict:
    """Run the subject check LAST, after every recorded verdict has been applied.

    Order matters and got this wrong once: the strict-grade and pass-2 patches overwrite
    verdicts wholesale, so a subject problem detected during grading was silently
    replaced by "valid" a few lines later. Fidelity and authority verdicts come from
    recorded runs; "is this even about the right product" is computable from the record
    itself, so it is re-derived here and allowed to override them.
    """
    import collections
    tally: collections.Counter = collections.Counter()
    for r in records:
        reasons = r.setdefault("unknown_reason", {})
        for field, check in r["quote_checks"].items():
            cell = r["extracted"].get(field) or {}
            problem = subject_check(field, r["app"], cell.get("value"),
                                    cell.get("quote", ""), check.get("tier", 5),
                                    cell.get("source", ""))
            if not problem:
                continue
            check["verdict"] = problem
            check["evidenced"] = False
            tally[problem] += 1
        r["extracted"], removed = quarantine(r["extracted"], r["quote_checks"], reasons)
        for item in removed:
            if item not in r.setdefault("quarantined", []):
                r["quarantined"].append(item)
    return dict(tally.most_common())


def summarise(checks: dict) -> dict:
    out: dict[str, int] = {}
    for c in checks.values():
        out[c["verdict"]] = out.get(c["verdict"], 0) + 1
        out[f"tier{c['tier']}"] = out.get(f"tier{c['tier']}", 0) + 1
    out["evidenced"] = sum(1 for c in checks.values() if c["evidenced"])
    return out
