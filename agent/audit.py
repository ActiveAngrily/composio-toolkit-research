#!/usr/bin/env python3
"""The human check the brief asks for by name, and the scorer for it.

    python -m agent.audit --sheet                    # generate the blank sheet
    python -m agent.audit --score outputs/human_audit.csv

The brief says *"cross-check your agent's answers against real docs **by hand**"* and
*"verification loops … **plus human checks**"*. Everything else in this project's
verification is automated -- registry reconciliation, quote validation, refetching --
and an agent checking an agent does not answer that sentence.

THE SAMPLING RULE, written down before any result exists, so it cannot be accused of
being chosen to flatter the numbers:

    Stratify by buildability verdict, four strata. From each, take the app with the
    FEWEST evidenced fields and the app with the MOST. Ties break by lowest id.

That is deliberately not a random sample, and the consequence has to be stated
alongside any number it produces: half the sample is chosen to be the weakest claims
we made. **Accuracy measured here is a lower bound on the dataset's quality, not an
estimate of it.** A random sample would read better and mean less at n=8 -- the useful
question at this size is "do the claims we are least sure of hold up", not "what is the
mean".

The sheet deliberately does not contain the agent's answers. An auditor who has seen
them is not an independent check, and the accuracy number stops meaning anything.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import pathlib

from . import config, derive

# Three fields, not eleven. 8 apps x 11 fields is 88 lookups; nobody finishes that
# carefully, and a rushed audit is worse than a small one. These three are the ones a
# build decision actually turns on.
FIELDS = [
    ("auth_family", "Does the API accept a static secret (API key / token / basic), "
                    "an OAuth2 flow, or both?", "static-secret | oauth-dance | both | none"),
    ("access", "Can a developer get working API credentials without contacting sales? "
               "If not, what stops them?",
     "free | free-trial | paid-tier-required | admin-consent | app-review | "
     "partner-or-sales-gate | no-public-api"),
    ("existing_mcp", "Is there an MCP server for this app? Published by the vendor "
                     "(official) or a third party (community)?", "official | community | none"),
]


def evidenced_count(record: dict) -> int:
    return sum(1 for c in record["quote_checks"].values() if c.get("evidenced"))


def pick(records: list[dict], per_stratum: int = 2) -> list[dict]:
    """Apply the rule above. Deterministic: same dataset in, same sample out."""
    strata = {
        "already-built": [], "build-now": [],
        "gated": [],        # build-with-caveats + needs-outreach + not-buildable
        "unknown": [],
    }
    for r in records:
        verdict = r["buildability"]["value"]
        key = verdict if verdict in strata else \
            ("gated" if verdict in ("build-with-caveats", "needs-outreach", "not-buildable")
             else "unknown")
        strata[key].append(r)

    chosen: list[dict] = []
    for key, group in strata.items():
        if not group:
            continue
        ordered = sorted(group, key=lambda r: (evidenced_count(r), r["id"]))
        picks = [ordered[0]]                                   # weakest evidence
        if per_stratum > 1 and len(ordered) > 1:
            picks.append(ordered[-1])                          # strongest, as a control
        for r in picks:
            r = dict(r)
            r["_stratum"] = key
            r["_evidenced"] = evidenced_count(r)
            chosen.append(r)
    return sorted(chosen, key=lambda r: r["id"])


def _write(sample: list[dict], out: pathlib.Path) -> pathlib.Path:
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "app", "start_here", "field", "question", "allowed_values",
                    "your_answer", "your_source_url", "notes"])
        for r in sample:
            for name, question, allowed in FIELDS:
                w.writerow([r["id"], r["app"], r["hint"], name, question, allowed, "", "", ""])
    return out


def score(records: list[dict], filled: pathlib.Path) -> dict:
    """Compare the human answers to the agent's, per field, and name every mismatch."""
    by_id = {r["id"]: r for r in records}
    rows = [r for r in csv.DictReader(open(filled, encoding="utf-8"))
            if (r.get("your_answer") or "").strip()]
    if not rows:
        raise SystemExit(f"{filled} has no filled-in answers yet")

    per_field: dict[str, collections.Counter] = {}
    mismatches, checked = [], 0
    for row in rows:
        rec = by_id.get(int(row["id"]))
        if not rec:
            continue
        field = row["field"]
        human = row["your_answer"].strip().lower()
        agent = agent_value(rec, field)
        hit = human == str(agent).lower()
        per_field.setdefault(field, collections.Counter())["hit" if hit else "miss"] += 1
        checked += 1
        if not hit:
            mismatches.append({
                "app": rec["app"], "field": field, "human": human, "agent": agent,
                "human_source": row.get("your_source_url", ""),
                "agent_source": (rec["extracted"].get(_source_field(field)) or {}).get("url", ""),
                "notes": row.get("notes", ""),
            })

    hits = sum(c["hit"] for c in per_field.values())
    return {
        "sample_apps": len({row["id"] for row in rows}),
        "claims_checked": checked,
        "agreed": hits,
        "agreement_pct": round(100 * hits / checked, 1) if checked else 0.0,
        "per_field": {f: dict(c) for f, c in sorted(per_field.items())},
        "mismatches": mismatches,
        "sampling": "stress-weighted, not random: half the sample is the weakest-evidence "
                    "app in each buildability stratum. This is a lower bound on quality, "
                    "not an estimate of the mean.",
    }


def agent_value(record: dict, field: str):
    if field == "auth_family":
        return record["auth_family"]
    if field == "access":
        return record["self_serve"]["value"]
    return (record["extracted"].get(field) or {}).get("value", "unknown")


def _source_field(field: str) -> str:
    return {"auth_family": "auth_methods", "access": "api_access_tier"}.get(field, field)


def load(src: str) -> list[dict]:
    for name in (src, "outputs/dataset_v3.json", "outputs/dataset_v2.json"):
        p = pathlib.Path(name)
        if p.exists():
            return json.loads(p.read_text())["dataset"]
    raise SystemExit("no dataset found")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agent.audit", description=__doc__.split("\n")[0])
    p.add_argument("--sheet", action="store_true", help="write the blank audit sheet")
    p.add_argument("--score", metavar="CSV", help="score a filled-in sheet")
    p.add_argument("--in", dest="src", default="outputs/dataset_v3.json")
    p.add_argument("--out", default="outputs/human_audit.csv")
    args = p.parse_args(argv)

    records = load(args.src)
    if args.sheet:
        sample = pick(records)
        out = _write(sample, pathlib.Path(args.out))
        print(f"wrote {out}: {len(sample)} apps x {len(FIELDS)} fields "
              f"= {len(sample) * len(FIELDS)} checks")
        print("\nsample (rule: per buildability stratum, fewest evidenced fields + most):")
        for r in sample:
            print(f"  {r['id']:>3} {r['app']:<24} {r['_stratum']:<14} "
                  f"{r['_evidenced']} evidenced fields")
        print("\nThe sheet contains no agent answers. Fill it from the vendors' own docs.")
        return 0
    if args.score:
        result = score(records, pathlib.Path(args.score))
        dest = config.OUTPUTS / "human_audit_result.json"
        dest.write_text(json.dumps(result, indent=2))
        print(json.dumps({k: v for k, v in result.items() if k != "mismatches"}, indent=2))
        for m in result["mismatches"]:
            print(f"  MISMATCH {m['app']:<22} {m['field']:<14} you={m['human']} agent={m['agent']}")
        print(f"wrote {dest}")
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
