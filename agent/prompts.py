"""The extraction prompt.

The whole design rests on one idea: make abstention cheap and confident error
expensive, then tell the model that is the deal. The model is told an automated
checker re-reads every URL and verifies the quote literally appears there --
which is true, runs on every claim, and is why fabrication is a losing move
rather than a free guess.

Templates use string.Template ($app, $sources) rather than str.format, because
these prompts are mostly literal JSON and doubling every brace to escape it makes
the contract unreadable -- which matters when the contract is the thing a reviewer
most needs to understand.

Changes from pass 1, each traceable to a measured failure:
  * `primary_blocker` now requires quote + url. In pass 1 it asked for a free-text
    `reason`, so 88 of 100 blocker claims could not be validated at all.
  * `product_class` exists, so "this is a local CLI with no API" is sayable.
    Pass 1 gave Mermaid CLI OAuth2 + REST + an MCP server because the schema left
    it no honest way to say "not applicable".
  * `unknown_reason` is requested alongside every unknown, which separates "the
    vendor does not publish this" from "we failed to find it". Those are different
    findings and pass 1 reported them identically.
  * Detector hits are handed over as candidate evidence. The pipeline already found
    phrases like "contact sales" and "free tier" on 69 of 100 apps in pass 1 and
    then never showed them to the model.
  * one_liner is explicitly exempt from the verbatim requirement -- demanding a
    quotable sentence for "what does this do" is why 62 came back blank.
"""
from __future__ import annotations

from string import Template

from . import schema


def _field_block() -> str:
    lines = []
    for f in schema.FIELDS:
        shape = '{"value": [], "quote": "", "url": ""}' if f.kind == "list" \
                else '{"value": "", "quote": "", "url": ""}'
        allowed = ("\n      # allowed: " + " | ".join(f.values)) if f.values else ""
        unquoted = "\n      # no verbatim quote needed for this field; still give the url" \
                   if not f.quoted else ""
        lines.append(f'  "{f.name}": {shape},\n      # {f.prompt_hint}{allowed}{unquoted}')
    return "\n".join(lines)


FIELD_BLOCK = _field_block()
REASON_LIST = " | ".join(schema.UNKNOWN_REASONS)


EXTRACT = Template("""Extract facts for an agent-toolkit buildability audit from the SOURCES below.

These rules matter more than completeness:

1. Use ONLY the SOURCES. Treat everything you believe you know about $app as
   unavailable to you here. If the sources do not say it, you do not know it.
2. Every field needs `quote` -- text copied character-for-character from one
   source -- and `url`, that source's URL. Copy it, do not retype or tidy it.
3. If the sources do not support a field, set value to "unknown", quote to "",
   and name the reason in `unknown_reason`. An automated checker re-reads every URL
   and verifies your quote literally appears on that page, so an invented quote is
   detected and scores strictly worse than "unknown".
4. Access is FOUR separate questions, not one: can you sign up without contacting
   sales; which tier includes API access; can you mint the credential yourself; is
   there an approval step. Answer each only from what is actually stated, and do not
   infer one from another.
5. If this product has no public API -- a local CLI, a library, a desktop app, a
   product with only a UI -- say so in `product_class` and leave the API fields
   "unknown" with unknown_reason "not-applicable". That is a correct finding, not a
   failure.

APP: $app  ($category)
DOMAIN HINT: $hint

SIGNALS -- phrases a deterministic scanner already found on these pages. Treat them
as candidate evidence to check, not as conclusions:
$signals

SOURCES:
$sources

Return ONLY this JSON object, no prose:
{
$fields
  "unknown_reason": {}
}

`unknown_reason` maps each field you marked unknown to one of:
  $reasons

Guidance on the enums:
  signup_self_serve      can someone create an account WITHOUT contacting sales
  api_access_tier        which plan tier includes API access
  credential_self_issue  once inside, can the user generate the credential themselves
  approval_gate          any review, approval or admin-consent step before real API
                         calls work. admin-consent means a workspace or org
                         administrator must authorise it, not the vendor.
  primary_blocker        the single thing that stops a toolkit being built today
""")


# Pass 0: the deliberately naive baseline. No retrieval, no sources, answer from
# memory. This is the number the improvement is measured against, so it has to be a
# real attempt rather than a strawman -- same schema, same fields, nothing to read.
BASELINE = Template("""You are auditing apps for agent-toolkit buildability. Answer from your
own knowledge. You have no sources and no browser. Give your best single answer for
each field, and a plausible documentation URL for each.

APP: $app  ($category)
DOMAIN HINT: $hint

Return ONLY this JSON object, no prose:
{
$fields
}
""")


# Focused re-query for pass 2, used only on fields that came back blank. One field
# at a time, with sources selected for that question, beats re-running the whole
# extraction against the same pages that already failed to answer it.
REPAIR = Template("""You previously could not answer one field about $app from the sources you
had. Here are DIFFERENT sources, selected for this question specifically.

QUESTION: $question
FIELD: $field
ALLOWED VALUES: $allowed

Same rules: answer only from these sources, quote character-for-character, give the
url, and if they still do not support an answer return "unknown" with a reason. A
checker re-reads your URL.

SOURCES:
$sources

Return ONLY: {"value": ..., "quote": "", "url": "", "unknown_reason": ""}
""")


QUESTIONS = {
    "api_access_tier": "Which pricing plan or tier includes API access? Is the API "
                       "available on a free plan, only on a paid plan, or only to "
                       "enterprise customers?",
    "signup_self_serve": "Can a developer create an account without contacting sales "
                         "or booking a demo?",
    "credential_self_issue": "Once you have an account, can you generate the API "
                             "credential yourself in the UI?",
    "approval_gate": "Is there an app review, developer-token approval, business "
                     "verification, administrator consent, or partner approval step "
                     "before API calls work?",
    "existing_mcp": "Is there an MCP (Model Context Protocol) server for this app? Is "
                    "it published by the vendor (official) or by a third party "
                    "(community)?",
    "protocol": "What public API interface is documented -- REST, GraphQL, SOAP, gRPC?",
    "rate_limits_documented": "Are API rate limits documented publicly?",
    "one_liner": "In one sentence, what does this product do?",
}


def extract_prompt(app: dict, signals: str, sources: str) -> str:
    return EXTRACT.substitute(
        app=app["app"], category=app["category"], hint=app.get("hint", ""),
        signals=signals, sources=sources or "(no sources retrieved)",
        fields=FIELD_BLOCK, reasons=REASON_LIST,
    )


def baseline_prompt(app: dict) -> str:
    return BASELINE.substitute(
        app=app["app"], category=app["category"], hint=app.get("hint", ""),
        fields=FIELD_BLOCK,
    )


def repair_prompt(app: dict, field: str, sources: str) -> str:
    allowed = schema.BY_NAME[field].values or ["free text"]
    return REPAIR.substitute(
        app=app["app"], field=field, question=QUESTIONS.get(field, f"What is {field}?"),
        allowed=" | ".join(allowed), sources=sources,
    )
