"""Derived fields: computed by rule, never asked of the model.

Anything the model would have to blend from several facts gets computed here
instead, for two reasons. It is auditable -- every derived value carries the
`basis` that produced it, so a reviewer can disagree with the rule rather than
with a black box. And it is consistent -- pass 1 computed `primary_blocker` in the
prompt and `self_serve` in code, never compared them, and shipped 7 rows where the
blocker said "none" while the access field said "gated". Google Ads was one of
them, and its developer token needs Google's approval.
"""
from __future__ import annotations

from . import schema

STATIC_SECRETS = {"API_KEY", "BEARER", "BASIC", "JWT", "MTLS"}


def _val(extracted: dict, field: str):
    return ((extracted or {}).get(field) or {}).get("value")


# ------------------------------------------------------------------- auth family

def auth_family(auth_methods: list[str]) -> str:
    """The split that matters for building a toolkit: does this need an OAuth dance
    with redirect URIs and refresh tokens, or is a static secret enough?

    Pass 1 listed OAUTH2, BEARER, API_KEY, BASIC and JWT as coequal values, which
    conflates a credential kind with the envelope it travels in. Attio came out as
    four separate auth methods, which is noise rather than a finding -- and six of
    the ten disagreements against Composio's own registry were nothing but this.
    """
    methods = set(auth_methods or [])
    if not methods or methods == {"unknown"}:
        return "unknown"
    if methods == {"NONE"}:
        return "none"
    has_oauth = "OAUTH2" in methods
    has_static = bool(methods & STATIC_SECRETS)
    if has_oauth and has_static:
        return "both"
    if has_oauth:
        return "oauth-dance"
    if has_static:
        return "static-secret"
    return "unknown"


# ----------------------------------------------------------------------- access

def derive_access(extracted: dict, detectors: list[dict] | None = None,
                  pricing: dict | None = None) -> dict:
    """Blend the four access sub-answers into one verdict, with a stated basis.

    Order matters: the hardest gate wins. An app with a free tier that still needs
    Meta-style app review is not self-serve, and reporting it as free would be the
    more damaging error.
    """
    tags = {d["tag"] for d in (detectors or [])}
    tier = _val(extracted, "api_access_tier")
    gate = _val(extracted, "approval_gate")
    signup = _val(extracted, "signup_self_serve")
    issue = _val(extracted, "credential_self_issue")
    product = _val(extracted, "product_class")

    def out(value, basis):
        return {"value": value, "basis": basis}

    if product in ("cli-only", "no-public-api"):
        return out("no-public-api", f"product_class={product}")

    if tier == "enterprise-only" or gate == "partner-approval":
        return out("partner-or-sales-gate", f"tier={tier}, gate={gate}")
    if signup == "no":
        return out("partner-or-sales-gate", "signup_self_serve=no")
    if gate == "admin-consent":
        return out("admin-consent", "approval_gate=admin-consent")
    if gate in ("app-review", "developer-token", "business-verification"):
        return out("app-review", f"approval_gate={gate}")
    if tier == "paid":
        return out("paid-tier-required", "api_access_tier=paid")
    if tier == "free" and issue == "yes":
        return out("free", "api_access_tier=free + credential_self_issue=yes")
    if tier == "free-trial":
        return out("free-trial", "api_access_tier=free-trial")
    if tier == "free":
        return out("free-trial", "api_access_tier=free, self-issue unconfirmed")

    # Nothing the model answered settles it. Fall back to deterministic signals --
    # which in pass 1 were collected and then never consulted.
    if pricing and pricing.get("sales_gate"):
        return out("partner-or-sales-gate", "pricing page redirects to a sales flow")
    if tags & {"enterprise-only", "invite-only"}:
        return out("partner-or-sales-gate", "page signal: enterprise/invite only")
    if tags & {"contact-sales", "demo-gate"} and not (tags & {"free-tier", "free-account", "no-cc"}):
        return out("partner-or-sales-gate", "page signal: sales/demo gate, no free-tier signal")
    if "admin-consent" in tags:
        return out("admin-consent", "page signal: admin consent")
    if tags & {"app-review", "developer-token", "business-verification"}:
        return out("app-review", "page signal: review/approval step")
    if tags & {"free-tier", "free-account", "no-cc"} and issue == "yes":
        return out("free", "page signal: free tier + credential_self_issue=yes")
    if "paid-plan" in tags:
        return out("paid-tier-required", "page signal: paid plan required")
    if tags & {"free-tier", "free-account", "no-cc"}:
        return out("free-trial", "page signal: free tier, self-issue unconfirmed")
    return out("unknown", "no sufficient signal")


# ----------------------------------------------------------------- buildability

def derive_buildability(extracted: dict, access: str, registry: dict | None = None) -> dict:
    """The brief asks for a buildability verdict; pass 1 had no such field at all.

    Composio having already shipped a toolkit is the strongest possible evidence
    that this is buildable -- but note what it does NOT prove. Google Ads, Meta Ads
    and LinkedIn Ads are all in the catalog and all three are approval- or
    partner-gated, so catalog membership means "the credential is obtainable",
    not "the credential is self-serve".
    """
    product = _val(extracted, "product_class")
    protocol = _val(extracted, "protocol") or []
    in_catalog = bool((registry or {}).get("in_catalog"))

    def out(value, blocker, basis):
        return {"value": value, "blocker": blocker, "basis": basis}

    if product == "cli-only":
        return out("build-with-caveats", "no-public-api",
                   "local CLI -- wrap the command line, not an API")
    if product == "no-public-api":
        return out("not-buildable", "no-public-api", "no public API exists")
    if in_catalog:
        return out("build-now", "none",
                   f"already in Composio's catalog as {registry.get('composio_slug')}")
    if access in ("partner-or-sales-gate",):
        return out("needs-outreach", "partner-gate", f"access={access}")
    if access == "admin-consent":
        return out("build-with-caveats", "admin-consent",
                   "buildable, but each customer's admin must consent")
    if access == "app-review":
        return out("build-with-caveats", "app-review",
                   "buildable after the vendor's review process")
    if access == "paid-tier-required":
        return out("build-with-caveats", "paid-plan",
                   "buildable, but testing needs a paid seat")
    if access in ("free", "free-trial") and protocol:
        return out("build-now", "none", f"self-serve access, documented {'/'.join(protocol)}")
    if not protocol:
        return out("unknown", "unclear", "no documented public interface found")
    return out("unknown", "unclear", f"access={access}")


# ---------------------------------------------------------------- reconciliation

def reconcile(extracted: dict, access: dict, buildability: dict) -> list[str]:
    """Return the contradictions rather than silently preferring one field. A row
    that disagrees with itself should be visible, not smoothed over."""
    problems = []
    model_blocker = _val(extracted, "primary_blocker")
    derived_blocker = buildability["blocker"]
    gated = {"paid-tier-required", "app-review", "admin-consent", "partner-or-sales-gate"}

    if model_blocker == "none" and access["value"] in gated:
        problems.append(
            f"model said primary_blocker=none but access derives to {access['value']} "
            f"({access['basis']})")
    if model_blocker not in ("unclear", "unknown", None) and derived_blocker != model_blocker:
        problems.append(f"model blocker={model_blocker}, derived blocker={derived_blocker}")
    if _val(extracted, "product_class") == "api" and not (_val(extracted, "protocol") or []):
        problems.append("product_class=api but no protocol found")
    return problems


# ------------------------------------------------------------- unknown accounting

def fill_unknown_reasons(extracted: dict, reasons: dict, pages_fetched: int) -> dict:
    """Every blank field ends up with a reason. This is what turns several hundred
    abstentions from an embarrassment into a finding: 'the vendor does not publish
    this' and 'our crawler missed it' are different results, and pass 1 reported
    them identically as `unknown`."""
    reasons = dict(reasons or {})
    product = _val(extracted, "product_class")
    api_fields = {"auth_methods", "protocol", "rate_limits_documented",
                  "api_access_tier", "credential_self_issue", "approval_gate"}
    for field in schema.FIELDS:
        if not schema.is_blank(field.name, _val(extracted, field.name)):
            reasons.pop(field.name, None)
            continue
        if field.name in reasons and reasons[field.name] in schema.UNKNOWN_REASONS:
            continue
        if product in ("cli-only", "no-public-api") and field.name in api_fields:
            reasons[field.name] = "not-applicable"
        elif pages_fetched == 0:
            reasons[field.name] = "retrieval-failed"
        else:
            reasons[field.name] = "not-stated-publicly"
    return reasons
