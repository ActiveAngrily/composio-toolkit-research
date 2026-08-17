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

        checks[field.name] = {
            "verdict": verdict,
            "tier": tier,
            "evidenced": verdict in ("valid", "near-miss", "unquoted-ok")
                         and tier <= schema.EVIDENCED_MAX_TIER,
        }
    return checks


QUARANTINE_VERDICTS = {"QUOTE_NOT_FOUND", "no-quote", "unverifiable-url", "no-source"}


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
        unknown_reason[name] = "quote-failed-validation"
    return extracted, removed


def summarise(checks: dict) -> dict:
    out: dict[str, int] = {}
    for c in checks.values():
        out[c["verdict"]] = out.get(c["verdict"], 0) + 1
        out[f"tier{c['tier']}"] = out.get(f"tier{c['tier']}", 0) + 1
    out["evidenced"] = sum(1 for c in checks.values() if c["evidenced"])
    return out
