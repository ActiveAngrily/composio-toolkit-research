"""Re-derive the pass-1 dataset under the v2 schema, without touching the network.

    python -m agent.upgrade --batches /mnt/files/research
    python -m agent.upgrade --batches outputs/pass1_batches --refetch   # resolve truncation

Everything here is arithmetic over files that already exist. No model, no search, no
human. It exists as a module rather than a one-off script because otherwise the repo
and the dataset tell different stories -- which was the single worst problem with the
first pass.

What it fixes, all measurable against pass 1:

    auth_family            one fact instead of five overlapping enum values
    source_tier            authority on all 483 citations, not just fidelity
    URL-strict re-grade    a quote must be on the page it was attributed to
    quarantine             values whose quote failed validation stop being reported
    unknown_reason         "not published" separated from "we did not find it"
    product_class          "this is a CLI with no API" becomes sayable
    buildability           the verdict the brief asks for and pass 1 had no field for
    breadth                registry tool counts, bucketed
    contradictions         rows that disagree with themselves get surfaced

ONE MEASUREMENT HAZARD, handled rather than ignored. Pass 1 fed the model 5,000
characters per page but only retained 2,500 in `_texts_kept`. So a quote lifted from
the back half of a page is genuinely unverifiable offline, and grading it naively
would report it as fabricated. Any claim that pass 1 graded valid and that we now
cannot find in the retained text is therefore labelled `truncated-evidence`, not
fabrication, and queued for `--refetch`. Counting those as model errors would inflate
our own improvement, which is the failure mode this whole project is supposed to be
guarding against.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import pathlib
import re
import urllib.parse

from . import config, derive, evidence, probe, registry, schema

# Fields the pass-1 prompt never asked for in a gradable form. `primary_blocker`
# asked for a free-text `reason` instead of a quote, and `product_class` did not
# exist. Their values are kept but cannot be validated, and calling that a model
# failure would be dishonest -- it is a schema failure, and ours.
NOT_ASKED_IN_PASS1 = {"primary_blocker", "product_class"}

# Pass 1 planned three queries per app: auth, pricing, signup. There was no MCP query,
# so a blank `existing_mcp` is a provable gap in our retrieval rather than a fact about
# the vendor -- and it is 75 of the 100 rows. Recording that honestly is what makes the
# rest of the unknown accounting believable.
NEVER_QUERIED_IN_PASS1 = {"existing_mcp"}

# The URL-strict re-grade needs the page text pass 1 retained, and that text lives in
# Composio's workbench. So the verdicts are computed there once and committed here, and
# every other environment reproduces the identical dataset from them. Only exceptions
# are recorded; a quote-bearing field absent from the file graded `valid`.
GRADE_CODES = {"T": "truncated-evidence", "F": "QUOTE_NOT_FOUND", "N": "near-miss",
               "W": "wrong-url", "U": "unverifiable-url", "Q": "no-quote"}


def load_strict_grades(path: pathlib.Path) -> dict[tuple[int, str], str]:
    if not path.exists():
        return {}
    out: dict[tuple[int, str], str] = {}
    for token in path.read_text().split():
        if token.startswith("#") or "=" not in token or "." not in token:
            continue
        left, code = token.split("=", 1)
        app_id, field = left.split(".", 1)
        if code in GRADE_CODES:
            out[(int(app_id), field)] = GRADE_CODES[code]
    return out


def apply_strict_grades(record_id: int, extracted: dict, checks: dict,
                        grades: dict[tuple[int, str], str]) -> dict:
    """Replace the offline verdict with the workbench's URL-strict one wherever the
    field carries a quote. Fields with no quote keep the locally derived verdict --
    abstained, absence-claim or not-asked-pass1 need no page text to decide."""
    for field, check in checks.items():
        if not (extracted.get(field) or {}).get("quote"):
            continue
        if check["verdict"] == "not-asked-pass1":
            continue
        verdict = grades.get((record_id, field), "valid")
        check["verdict"] = verdict
        check["evidenced"] = verdict in ("valid", "near-miss") and \
            check.get("tier", 5) <= schema.EVIDENCED_MAX_TIER
        check["needs_refetch"] = verdict == "truncated-evidence"
    return checks


# --------------------------------------------------------------------------- input

def load_pass1(batches: pathlib.Path) -> list[dict]:
    """Prefer the batch files: they retain per-page text, which is what makes an
    offline re-grade possible at all. Fall back to the slim committed dataset."""
    files = sorted(glob.glob(str(batches / "batch_*.json")))
    records: list[dict] = []
    for path in files:
        records += json.loads(pathlib.Path(path).read_text())
    if not records:
        slim = config.OUTPUTS / "dataset_v1.json"
        if slim.exists():
            records = json.loads(slim.read_text())
            print(f"  ! no batch files in {batches}; using {slim.name} "
                  f"(no page text -> re-grade will be skipped)")
    by_id = {r["id"]: r for r in records}
    return [by_id[i] for i in sorted(by_id)]


# ------------------------------------------------------------------ product class

GITHUB_REPO_RE = re.compile(r"^github\.com/[^/]+/[^/]+", re.I)


def infer_product_class(record: dict) -> tuple[str, str]:
    """Deterministic, from the brief's own hint. When the hint for an app is a GitHub
    repository rather than a product domain, or the path is a CLI page, the thing is
    a local tool and not a hosted API.

    This is what pass 1 could not express, and the cost was concrete: Mermaid CLI came
    back with BEARER + JWT + OAUTH2, REST and an official MCP server, for an npm
    package that renders diagrams on your laptop. Every value invented, because the
    schema left no honest alternative.

    Marked `needs_confirmation` rather than asserted -- three apps is a small enough
    set for a person or a browser pass to check.
    """
    hint = (record.get("hint") or "").strip()
    if GITHUB_REPO_RE.match(hint.replace("https://", "").replace("www.", "")):
        return "cli-only", "hint is a GitHub repository, not a product domain"
    if re.search(r"/cli\b", hint):
        return "cli-only", "hint points at a CLI page"
    return "unknown", ""


# ---------------------------------------------------------------------- re-probing

def reparse_probe_bodies(record: dict) -> list[dict]:
    """Pass 1 stored 142 response bodies and read only the 9 WWW-Authenticate headers.
    The bodies are still there, so the signals can be extracted now at zero cost --
    Stripe's own body says "You did not provide an API key" while its header says
    Basic, and both are true."""
    out = []
    for p in (record.get("probes") or []):
        p = dict(p)
        body = p.get("body_hint") or ""
        low = body[:1500].lower()
        p["body_tags"] = sorted({tag for rx, tag in probe._BODY if rx.search(low)})
        out.append(p)
    return out


# ------------------------------------------------------------------------ re-grade

def regrade(record: dict, app_name: str, app_hint: str) -> tuple[dict, dict, list]:
    """URL-strict grading against retained page text, with the truncation caveat."""
    extracted = record.get("extracted") or {}
    texts = record.get("_texts_kept") or {}
    pass1 = record.get("quote_checks") or {}

    checks = evidence.grade_record(extracted, texts, app_hint, app_name)

    for name, check in checks.items():
        old = pass1.get(name)
        if name in NOT_ASKED_IN_PASS1 and not (extracted.get(name) or {}).get("quote"):
            # Not the model's failure: pass 1 never asked for a quote here.
            check["verdict"] = "not-asked-pass1"
            check["evidenced"] = False
            continue
        if not texts:
            check["verdict"] = "no-retained-text"
            check["evidenced"] = False
            continue
        # Pass 1 graded against 5,000 chars; we retained 2,500. A claim it verified
        # that we now cannot find is a truncation artefact, not a fabrication.
        if check["verdict"] in ("QUOTE_NOT_FOUND", "unverifiable-url") and \
                old in ("valid", "near-miss"):
            check["verdict"] = "truncated-evidence"
            check["evidenced"] = False
            check["needs_refetch"] = True

    quarantined: list[dict] = []
    reasons: dict = {}
    extracted, quarantined = evidence.quarantine(extracted, checks, reasons)
    return extracted, checks, quarantined, reasons


# ------------------------------------------------------------------------- upgrade

def upgrade_record(record: dict, matches: dict,
                   strict: dict[tuple[int, str], str] | None = None) -> dict:
    app_name, app_hint = record["app"], record.get("hint", "")
    reg = matches.get(record["id"], {})

    extracted = schema.normalise(dict(record.get("extracted") or {}))

    # product_class did not exist in pass 1.
    inferred, why = infer_product_class(record)
    cell = extracted.setdefault("product_class", {"value": "unknown", "quote": "", "url": ""})
    if inferred != "unknown":
        cell["value"], cell["derived"], cell["needs_confirmation"] = inferred, why, True
    elif not schema.is_blank("auth_methods", extracted["auth_methods"]["value"]) \
            or not schema.is_blank("protocol", extracted["protocol"]["value"]):
        cell["value"], cell["derived"] = "api", "auth or protocol documented"

    staged = dict(record)
    staged["extracted"] = extracted
    extracted, checks, quarantined, reasons = regrade(staged, app_name, app_hint)
    if strict:
        checks = apply_strict_grades(record["id"], extracted, checks, strict)
        extracted, more = evidence.quarantine(extracted, checks, reasons)
        quarantined += [q for q in more if q not in quarantined]

    detectors = record.get("detectors") or []
    probes = reparse_probe_bodies(record)
    pricing = record.get("pricing_probe") or {}
    spec = record.get("spec_probe") or {}

    access = derive.derive_access(extracted, detectors, pricing)
    buildability = derive.derive_buildability(extracted, access["value"], reg)
    breadth_count = reg.get("composio_tools_count") or spec.get("operation_count")

    return {
        "id": record["id"], "app": app_name, "category": record["category"], "hint": app_hint,
        "extracted": extracted,
        "auth_family": derive.auth_family(extracted["auth_methods"]["value"]),
        "self_serve": access,
        "buildability": buildability,
        "breadth": {"count": breadth_count, "bucket": probe.breadth_bucket(breadth_count),
                    "source": "composio-registry" if reg.get("composio_tools_count")
                              else ("openapi-spec" if spec.get("operation_count") else "none")},
        "registry": reg,
        "quote_checks": checks,
        "quote_checks_pass1": record.get("quote_checks") or {},
        "quote_summary": evidence.summarise(checks),
        "quarantined": quarantined,
        "unknown_reason": derive.fill_unknown_reasons(
            extracted, {**(record.get("unknown_reason") or {}), **reasons},
            len(record.get("sources_used") or []), NEVER_QUERIED_IN_PASS1),
        "contradictions": derive.reconcile(extracted, access, buildability),
        "contradictions_pass1": _pass1_contradiction(record),
        "sources_used": record.get("sources_used") or [],
        "source_tiers": {u: schema.source_tier(u, app_hint, app_name)
                         for u in (record.get("sources_used") or [])},
        "detectors": detectors,
        "probes": probes,
        "pricing_probe": pricing,
        "spec_probe": spec,
        "provenance": {"pass": 2, "from": "pass-1 records re-derived offline",
                       "pass1_self_serve": record.get("self_serve_derived")},
    }


def _pass1_contradiction(record: dict) -> list[str]:
    """What pass 1 shipped: the model's blocker and the code's access verdict, never
    compared. Kept so the fix is measurable rather than asserted."""
    blocker = ((record.get("extracted") or {}).get("primary_blocker") or {}).get("value")
    old_access = record.get("self_serve_derived")
    if blocker == "none" and old_access in ("app-review", "paid-tier-required",
                                            "partner-or-sales-gate"):
        return [f"pass1: blocker=none but self_serve_derived={old_access}"]
    return []


# -------------------------------------------------------------------- the patterns

def patterns(records: list[dict], matches: dict) -> dict:
    """The brief names four pattern questions. Answer each one separately, with its
    number, rather than producing a generic list of insights."""
    def count(fn):
        return dict(collections.Counter(fn(r) for r in records).most_common())

    in_catalog = [r for r in records if r["registry"].get("in_catalog")]
    missing = [r for r in records if not r["registry"].get("in_catalog")]

    by_cat_access: dict[str, dict] = {}
    for r in records:
        by_cat_access.setdefault(r["category"], collections.Counter())[r["self_serve"]["value"]] += 1

    return {
        # 1. which auth dominates
        "auth": {
            "families": count(lambda r: r["auth_family"]),
            "raw_methods": dict(collections.Counter(
                m for r in records
                for m in (r["extracted"]["auth_methods"]["value"] or [])).most_common()),
            "headline_static_secret_path": sum(
                1 for r in records if r["auth_family"] in ("static-secret", "both")),
        },
        # 2. which categories are self-serve vs gated
        "access_by_category": {c: dict(v) for c, v in sorted(by_cat_access.items())},
        "access_overall": count(lambda r: r["self_serve"]["value"]),
        # 3. the most common blocker
        "blockers": count(lambda r: r["buildability"]["blocker"]),
        # 4. easy wins vs outreach
        "buildability": count(lambda r: r["buildability"]["value"]),
        "catalog": {
            "in_catalog": len(in_catalog),
            "missing": len(missing),
            "by_category": {c: sum(1 for r in in_catalog if r["category"] == c)
                            for c in sorted({r["category"] for r in records})},
            "missing_with_official_mcp": sum(
                1 for r in missing if r["extracted"]["existing_mcp"]["value"] == "official"),
            "missing_build_now": [r["app"] for r in missing
                                  if r["buildability"]["value"] == "build-now"],
            "missing_needs_outreach": [r["app"] for r in missing
                                       if r["buildability"]["value"] == "needs-outreach"],
        },
        "mcp": count(lambda r: r["extracted"]["existing_mcp"]["value"]),
        "breadth": count(lambda r: r["breadth"]["bucket"]),
    }


# ------------------------------------------------------------------------- scoring

def coverage_report(records: list[dict]) -> dict:
    """Precision and coverage, per field, reported separately.

    Pass 1's headline said "3.7% fabricated", computed over 912 field-slots including
    425 abstentions. Dividing by your own silence flatters the number: among claims
    actually answered the rate was 7.0%. A pipeline that answers nothing scores 100%
    on accuracy, which is why coverage has to be published beside precision.
    """
    out = {}
    for field in schema.FIELDS:
        answered = evidenced = 0
        verdicts: collections.Counter = collections.Counter()
        for r in records:
            check = r["quote_checks"].get(field.name, {})
            verdicts[check.get("verdict", "?")] += 1
            if not schema.is_blank(field.name, (r["extracted"].get(field.name) or {}).get("value")):
                answered += 1
                evidenced += bool(check.get("evidenced"))
        out[field.name] = {
            "answered": answered,
            "coverage_pct": round(100 * answered / len(records), 1),
            "evidenced": evidenced,
            "evidenced_pct_of_answered": round(100 * evidenced / answered, 1) if answered else 0.0,
            "verdicts": dict(verdicts.most_common()),
        }
    return out


def delta_vs_pass1(records: list[dict]) -> dict:
    """What moved, and why. Grouped by cause so the improvement is attributable
    instead of just larger."""
    old_v = collections.Counter()
    new_v = collections.Counter()
    for r in records:
        old_v.update(r["quote_checks_pass1"].values())
        new_v.update(c.get("verdict", "?") for c in r["quote_checks"].values())
    return {
        "pass1_verdicts": dict(old_v.most_common()),
        "pass2_verdicts": dict(new_v.most_common()),
        "pass1_ungraded_slots": len(records) * len(schema.FIELDS) - sum(old_v.values()),
        "quarantined_claims": sum(len(r["quarantined"]) for r in records),
        # Only slots that actually cite something. tier 5 == no URL == an abstention,
        # and counting abstentions as weak citations inflates this eightfold.
        "citations_total": sum(
            1 for r in records for f, c in r["quote_checks"].items()
            if (r["extracted"].get(f) or {}).get("url")),
        "citations_below_tier2": sum(
            1 for r in records for f, c in r["quote_checks"].items()
            if (r["extracted"].get(f) or {}).get("url") and c.get("tier", 5) > 2),
        "needing_refetch": sum(
            1 for r in records for c in r["quote_checks"].values() if c.get("needs_refetch")),
        "contradictions_pass1": sum(len(r["contradictions_pass1"]) for r in records),
        "contradictions_now": sum(len(r["contradictions"]) for r in records),
    }


def unknown_audit(records: list[dict]) -> dict:
    """The experiment that decides how much of pass 2 is worth running.

    Pass 1 abstained on 425 field-slots and reported them all identically as
    `unknown`. But "the vendor does not publish which tier includes API access" is a
    finding, while "we never searched for an MCP server" is our own gap wearing the
    same label. If most blanks are `not-stated-publicly`, more retrieval buys little
    and the honest move is to say so per field. If most are `retrieval-failed`, a
    targeted second pass earns its slot.
    """
    per_field: dict[str, collections.Counter] = {}
    overall: collections.Counter = collections.Counter()
    for r in records:
        for field, reason in (r["unknown_reason"] or {}).items():
            if schema.is_blank(field, (r["extracted"].get(field) or {}).get("value")):
                per_field.setdefault(field, collections.Counter())[reason] += 1
                overall[reason] += 1
    return {"overall": dict(overall.most_common()),
            "per_field": {f: dict(c.most_common()) for f, c in sorted(per_field.items())}}


def refetch_queue(records: list[dict]) -> list[dict]:
    """Claims that need the full page text again to be judged fairly."""
    queue = []
    for r in records:
        for field, check in r["quote_checks"].items():
            if check.get("needs_refetch"):
                queue.append({"id": r["id"], "app": r["app"], "field": field,
                              "url": (r["extracted"].get(field) or {}).get("url", "")})
    return queue


# ----------------------------------------------------------------------------- cli

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agent.upgrade",
                               description="Re-derive the pass-1 dataset under the v2 schema, offline.")
    p.add_argument("--batches", default="outputs/pass1_batches",
                   help="directory of pass-1 batch_*.json files (they retain page text)")
    p.add_argument("--out", default=None)
    p.add_argument("--grades", default="data/pass1_strict_grades.txt",
                   help="committed URL-strict verdicts from the workbench re-grade")
    args = p.parse_args(argv)

    config.load_dotenv()
    records1 = load_pass1(pathlib.Path(args.batches))
    if not records1:
        raise SystemExit("no pass-1 records found")
    matches = registry.load_matches()
    print(f"pass 1: {len(records1)} records | registry: "
          f"{sum(1 for m in matches.values() if m['in_catalog'])} of 100 in catalog")

    strict = load_strict_grades(pathlib.Path(args.grades))
    print(f"strict grades: {len(strict)} recorded exceptions"
          f"{' (none found -- run with --batches in the workbench)' if not strict else ''}")
    records2 = [upgrade_record(r, matches, strict) for r in records1]

    out = pathlib.Path(args.out or config.OUTPUTS / "dataset_v2.json")
    payload = {
        "dataset": config.redact(records2),
        "patterns": patterns(records2, matches),
        "coverage": coverage_report(records2),
        "delta_vs_pass1": delta_vs_pass1(records2),
        "auth_cross_check": registry.cross_check_auth(records2, matches),
        "unknown_audit": unknown_audit(records2),
        "refetch_queue": refetch_queue(records2),
    }
    out.write_text(json.dumps(payload, indent=2))
    for name in ("patterns", "coverage", "delta_vs_pass1", "auth_cross_check", "unknown_audit"):
        (config.OUTPUTS / f"{name}.json").write_text(json.dumps(payload[name], indent=2))
    print(f"wrote {out} and 4 companion files in {config.OUTPUTS}")

    _report(payload)
    return 0


def _report(payload: dict):
    d, c, x = payload["delta_vs_pass1"], payload["coverage"], payload["auth_cross_check"]
    print("\n--- auth cross-check vs Composio's registry (no human involved) ---")
    print(f"  {x['sample']} apps | token-level {x['token_pct']}% | family-level {x['family_pct']}%")
    causes = collections.Counter(row["cause"] for row in x["disagreements"])
    for cause, n in causes.items():
        print(f"    {n} x {cause}")

    print("\n--- validation, pass 1 -> now ---")
    print(f"  pass-1 ungraded field-slots : {d['pass1_ungraded_slots']}")
    print(f"  claims quarantined          : {d['quarantined_claims']}")
    print(f"  citations below tier 2      : {d['citations_below_tier2']} of {d['citations_total']}")
    print(f"  queued for refetch          : {d['needing_refetch']}  (truncation, not fabrication)")
    print(f"  pass-1 rows contradicting themselves and shipped anyway : "
          f"{d['contradictions_pass1']}")
    print(f"  rows where the model's blocker loses to page evidence    : "
          f"{d['contradictions_now']}  (derived value wins, disagreement recorded)")

    print("\n--- coverage and evidence, per field ---")
    print(f"  {'field':<24} {'answered':>8} {'evidenced':>10}")
    for name, row in c.items():
        print(f"  {name:<24} {row['coverage_pct']:>7}% {row['evidenced_pct_of_answered']:>9}%")

    pat = payload["patterns"]
    print("\n--- the four pattern questions ---")
    print(f"  1 auth families          : {pat['auth']['families']}")
    print(f"    static-secret path     : {pat['auth']['headline_static_secret_path']}/100")
    print(f"  2 access overall         : {pat['access_overall']}")
    print(f"  3 blockers               : {pat['blockers']}")
    print(f"  4 buildability           : {pat['buildability']}")
    print(f"    catalog                : {pat['catalog']['in_catalog']} in, "
          f"{pat['catalog']['missing']} missing, "
          f"{pat['catalog']['missing_with_official_mcp']} of the missing have an official MCP")

    ua = payload["unknown_audit"]
    print("\n--- unknown reasons: is the gap the world's or ours? ---")
    print(f"  overall: {ua['overall']}")
    for field, reasons in ua["per_field"].items():
        print(f"    {field:<24} {reasons}")
    failed = ua["overall"].get("retrieval-failed", 0)
    unclass = ua["overall"].get("unclassified", 0)
    total = sum(ua["overall"].values()) or 1
    print(f"\n  provable retrieval failures : {failed}/{total} ({100*failed//total}%)")
    print(f"  unclassified                : {unclass}/{total} ({100*unclass//total}%)")
    print("  Pass 1 never recorded why it abstained, so the split between genuine")
    print("  non-disclosure and our own gaps CANNOT be measured from it. Pass 2 asks")
    print("  the model to name the reason; until then unclassified stays unclassified.")


if __name__ == "__main__":
    raise SystemExit(main())
