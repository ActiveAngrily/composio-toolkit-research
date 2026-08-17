"""The record schema: fields, enums, normalisation, and citation tiering.

Design notes worth knowing before reading the code, because two of them are
corrections to the first version of this pipeline:

1.  `self_serve` used to be one field. It is four questions in a trenchcoat --
    can you sign up, which tier includes API access, can you mint the credential
    yourself, and is there an approval step. Each has documentary evidence
    somewhere; the blend has none, because no page is about the blend. So we ask
    the four separately and *derive* the blend with a rule (see derive.py).

2.  Every field carries `quote` + `url`, INCLUDING `primary_blocker`. In pass 1
    the blocker field asked for a free-text `reason` instead, which meant the
    single most consequential field in the dataset was structurally impossible to
    validate -- 88 of 100 records had no grade for it. Fixed here.
"""
from __future__ import annotations

import re
import urllib.parse

# --------------------------------------------------------------------------- enums

# "OAuth2, API key, Basic, token, or other" -- the brief's own list. OTHER exists
# because mTLS, HMAC-signed requests and session cookies are real and would
# otherwise be silently mangled into whatever the model typed.
AUTH_METHODS = ["OAUTH2", "API_KEY", "BEARER", "BASIC", "JWT", "MTLS", "NONE", "OTHER"]

AUTH_MAP = {
    "oauth": "OAUTH2", "oauth2": "OAUTH2", "oauth 2.0": "OAUTH2", "oauth2.0": "OAUTH2",
    "oauth 2": "OAUTH2", "oauth1": "OAUTH2", "s2s_oauth2": "OAUTH2",
    "api_key": "API_KEY", "api key": "API_KEY", "apikey": "API_KEY",
    "api_token": "API_KEY", "api token": "API_KEY", "token": "API_KEY",
    "personal access token": "API_KEY", "pat": "API_KEY", "secret key": "API_KEY",
    "bearer": "BEARER", "bearer_token": "BEARER", "bearer token": "BEARER",
    "basic": "BASIC", "basic auth": "BASIC", "http basic": "BASIC",
    "basic_with_jwt": "BASIC",
    "jwt": "JWT", "json web token": "JWT", "google_service_account": "JWT",
    "mtls": "MTLS", "mutual tls": "MTLS", "client certificate": "MTLS",
    "hmac": "OTHER", "signature": "OTHER", "cookie": "OTHER", "session": "OTHER",
    "custom": "OTHER", "other": "OTHER",
    "none": "NONE", "no_auth": "NONE", "no auth": "NONE",
    "unknown": "unknown",
}

PROTOCOLS = ["REST", "GraphQL", "SOAP", "gRPC", "WEBHOOK_ONLY", "CLI_ONLY", "NONE"]
PROTOCOL_MAP = {
    "rest": "REST", "restful": "REST", "rest api": "REST", "http": "REST", "json": "REST",
    "graphql": "GraphQL", "gql": "GraphQL",
    "soap": "SOAP", "xml": "SOAP",
    "grpc": "gRPC",
    "webhook": "WEBHOOK_ONLY", "webhooks": "WEBHOOK_ONLY",
    "cli": "CLI_ONLY", "command line": "CLI_ONLY",
    "none": "NONE", "unknown": "unknown",
}

# The brief names four gate kinds: paid plan, admin approval, partnership /
# contact-sales. ADMIN_CONSENT was missing from pass 1 and it is common --
# Slack workspace admins, Jira admins, Google Workspace domain admins,
# Salesforce org admins all sit in this box.
APPROVAL_GATES = [
    "none", "app-review", "developer-token", "business-verification",
    "admin-consent", "partner-approval", "unknown",
]

ACCESS_TIERS = ["free", "free-trial", "paid", "enterprise-only", "unknown"]
YES_NO = ["yes", "no", "unknown"]
MCP_STATES = ["official", "community", "none", "unknown"]

# Derived, never asked of the model.
SELF_SERVE = [
    "free", "free-trial", "paid-tier-required", "admin-consent",
    "app-review", "partner-or-sales-gate", "no-public-api", "unknown",
]
BUILDABILITY = ["build-now", "build-with-caveats", "needs-outreach", "not-buildable", "unknown"]
AUTH_FAMILIES = ["static-secret", "oauth-dance", "both", "none", "unknown"]

# Pass 1 could not express "this app has no API" -- Mermaid CLI came back with
# BEARER + JWT + OAUTH2 + REST + an official MCP for an npm package that renders
# diagrams locally. Every value invented, because the schema offered no other out.
PRODUCT_CLASSES = ["api", "cli-only", "no-public-api", "unknown"]

# The distinction that makes 400+ abstentions informative instead of embarrassing.
UNKNOWN_REASONS = [
    "not-stated-publicly",   # the vendor genuinely does not publish it
    "retrieval-failed",      # we did not find the page that says it
    "contradictory-sources", # sources disagree
    "not-applicable",        # the question does not apply to this product
    "quote-failed-validation",  # quarantined by the validator
]


# ------------------------------------------------------------------- field table

class F:
    """One extracted field. `quoted=False` means a URL is still required but the
    value need not be a character-for-character span."""

    def __init__(self, name, kind, values=None, quoted=True, prompt_hint=""):
        self.name, self.kind, self.values = name, kind, values
        self.quoted, self.prompt_hint = quoted, prompt_hint


FIELDS = [
    # one_liner is deliberately NOT quote-bound. Requiring a verbatim span for
    # "what does this product do" is why 62 of 100 descriptions came back blank
    # in pass 1 -- marketing pages phrase it in fragments, not sentences.
    F("one_liner", "text", quoted=False,
      prompt_hint="one sentence, what the product does, in your own words, from the sources"),
    F("auth_methods", "list", AUTH_METHODS,
      prompt_hint="every credential type the API accepts"),
    F("signup_self_serve", "enum", YES_NO,
      prompt_hint="can someone create an account WITHOUT contacting sales"),
    F("api_access_tier", "enum", ACCESS_TIERS,
      prompt_hint="which plan tier includes API access"),
    F("credential_self_issue", "enum", YES_NO,
      prompt_hint="once inside, can the user generate the credential themselves"),
    F("approval_gate", "enum", APPROVAL_GATES,
      prompt_hint="any review/approval/admin-consent step before real API calls work"),
    F("protocol", "list", PROTOCOLS,
      prompt_hint="the documented public interface"),
    F("rate_limits_documented", "enum", YES_NO,
      prompt_hint="are rate limits published"),
    F("existing_mcp", "enum", MCP_STATES,
      prompt_hint="is there an MCP server for this app, official or community"),
    F("product_class", "enum", PRODUCT_CLASSES,
      prompt_hint="api if it has a public API; cli-only for a local CLI/library with no service; "
                  "no-public-api if the product exists but exposes no public API"),
    # Now quote-bound, unlike pass 1.
    F("primary_blocker", "enum",
      ["none", "no-public-api", "paid-plan", "app-review", "admin-consent", "partner-gate", "unclear"],
      prompt_hint="the single thing that stops a toolkit being built today"),
]

FIELD_NAMES = [f.name for f in FIELDS]
BY_NAME = {f.name: f for f in FIELDS}


# ------------------------------------------------------------------ normalisation

def norm_ws(s: str | None) -> str:
    """Whitespace/case normalisation used identically at extraction and validation
    time, so a quote that passed here will pass there."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _norm_one(value, table, allowed):
    key = str(value).strip().lower().replace("-", "_")
    hit = table.get(key) or table.get(key.replace("_", " "))
    if hit:
        return hit
    upper = str(value).strip().upper()
    return upper if upper in allowed else "OTHER" if allowed is AUTH_METHODS else "unknown"


def norm_list(values, table, allowed) -> list[str]:
    if not isinstance(values, list):
        values = [values] if values not in (None, "") else []
    out = {_norm_one(v, table, allowed) for v in values if str(v).strip()}
    out.discard("unknown")
    return sorted(out)


def norm_enum(value, allowed) -> str:
    v = str(value or "unknown").strip().lower()
    return v if v in allowed else "unknown"


def normalise(extracted: dict) -> dict:
    """Coerce whatever the model returned into the schema. Applied to every field,
    not just auth -- pass 1 normalised auth only, which is why `protocol` carried
    both 'REST' and 'rest' as distinct values."""
    for f in FIELDS:
        cell = extracted.get(f.name)
        if not isinstance(cell, dict):
            extracted[f.name] = {"value": [] if f.kind == "list" else "unknown",
                                 "quote": "", "url": ""}
            continue
        cell.setdefault("quote", "")
        cell.setdefault("url", "")
        raw = cell.get("value")
        if f.kind == "list":
            table = AUTH_MAP if f.name == "auth_methods" else PROTOCOL_MAP
            cell["value"] = norm_list(raw, table, f.values)
        elif f.kind == "enum":
            cell["value"] = norm_enum(raw, f.values)
        else:
            cell["value"] = (str(raw).strip() or "unknown") if raw else "unknown"
    return extracted


def is_blank(field_name: str, value) -> bool:
    return value in (None, "", "unknown", [], ["unknown"])


# Some answers assert that something is NOT there: no approval gate, no MCP server,
# no published rate limits, no blocker. You cannot copy a verbatim sentence proving
# an absence -- no vendor writes "there is no app review for this API". Demanding a
# quote for these would quarantine every honest negative, so they are graded as
# absence claims instead: the value survives, but it is marked as resting on absence
# of evidence rather than on a citation, and counted separately on the page.
ABSENCE_VALUES = {
    "approval_gate": {"none"},
    "existing_mcp": {"none"},
    "rate_limits_documented": {"no"},
    "primary_blocker": {"none"},
}


def is_absence_claim(field_name: str, value) -> bool:
    return value in ABSENCE_VALUES.get(field_name, set())


# ------------------------------------------------------------------- source tiers

# A quote can be word-perfect and still worthless if it came from a third party's
# integration directory. Pass 1's validator checked fidelity but not authority --
# 79 of 483 citations pointed at n8n.io, apis.io, nexla.com and similar, and every
# one was graded "valid". Tier is recorded per citation; tier <= 2 counts as
# evidenced, and the distribution goes on the page.
VENDOR_HOSTS = ("docs.", "developer.", "developers.", "api.", "help.", "support.", "learn.")
CODE_HOSTS = ("github.com", "raw.githubusercontent.com", "gitlab.com", "npmjs.com", "pypi.org")
DIRECTORY_HOSTS = (
    "n8n.io", "apis.io", "nexla.com", "rollout.com", "zapier.com", "make.com",
    "nango.dev", "merge.dev", "paragon.so", "tray.io", "workato.com", "pipedream.com",
    "mintlify.com", "postman.com", "rapidapi.com", "cake.com",
)
_TWO_LEVEL_TLDS = {"co.uk", "com.au", "co.jp", "com.br", "co.in", "co.nz", "com.mx"}

# Same brand, different TLD is normally the same vendor -- stripe.com/stripe.dev,
# notion.so/notion.com, shopify.com/shopify.dev -- so brand matching is deliberately
# lenient. These are the exceptions: separately operated entities that share a brand
# and do NOT share API rules. Pass 1 cited binance.us for Binance's approval gate and
# graded it valid; binance.us is a distinct licensed entity with its own KYC and API
# terms, so a quote from it is not evidence about binance.com.
SEPARATE_ENTITIES = {"binance.us", "amazon.cn", "hsbc.us"}


def registrable_domain(host: str) -> str:
    host = (host or "").lower().split(":")[0].removeprefix("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_LEVEL_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _brand(domain: str) -> str:
    """The second-level label: 'stripe' from docs.stripe.com, 'highlevel' from
    highlevel.stoplight.io."""
    parts = registrable_domain(domain).split(".")
    return parts[0] if parts else ""


def source_tier(url: str, app_hint: str, app_name: str = "") -> int:
    """How much authority does this citation have?

    1  the vendor's own domain
    2  the vendor's code or package host (GitHub, npm, PyPI)
    3  a third-party integration directory -- n8n, apis.io, Zapier and friends.
       Often stale, occasionally wrong, and never authoritative about the vendor.
    4  anything else
    5  no URL at all

    Tier <= EVIDENCED_MAX_TIER is what counts as evidenced. Lower-tier citations are
    kept rather than deleted -- sometimes a directory is the only thing that exists,
    and recording that fact is more useful than pretending we found nothing.
    """
    if not url:
        return 5
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    here, base = registrable_domain(host), registrable_domain((app_hint or "").split("/")[0])

    if base and here == base:
        return 1
    if here in SEPARATE_ENTITIES:
        return 4

    # Brand-label match, across a different TLD or from the app's name.
    #
    # Two candidate brands, because the hint alone is not enough: 8 of the brief's
    # 100 hints point at a third-party documentation host rather than the vendor
    # (highlevel.stoplight.io, binance-docs.github.io, open.larksuite.com), so
    # deriving the vendor domain from the hint gives the wrong answer. The app's own
    # name is the second candidate -- "GoHighLevel" resolves gohighlevel.com even
    # when the hint is a Stoplight page.
    #
    # Containment rather than equality covers notion.so/notion.com and
    # highlevel/gohighlevel. Minimum 5 characters so short labels cannot collide.
    host_brand = _brand(here)
    candidates = {_brand(base)} | {re.sub(r"[^a-z0-9]", "", (app_name or "").lower())}
    for cand in candidates:
        if not (host_brand and cand) or len(min(host_brand, cand, key=len)) < 5:
            continue
        if host_brand in cand or cand in host_brand:
            return 1
    if any(host == h or host.endswith("." + h) for h in CODE_HOSTS):
        return 2
    if any(host == h or host.endswith("." + h) for h in DIRECTORY_HOSTS):
        return 3
    return 4


EVIDENCED_MAX_TIER = 2
