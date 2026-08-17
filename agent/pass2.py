#!/usr/bin/env python3
"""Pass 2: two targeted repairs, each aimed at a gap we can name and measure.

    python -m agent.pass2 --refetch --mcp

**--refetch** resolves the claims that cannot be judged offline. Pass 1 showed the
model 5,000 characters per page and retained 2,500, so 75 claims sit behind
`truncated-evidence` -- verified once, unverifiable since. They live on only 53
distinct URLs. Re-pull those pages in full and the verdict becomes real: valid,
near-miss, or fabricated. Until this runs, `evidenced%` is understated by construction
and we cannot honestly say how many of the 75 were good.

**--mcp** fills the column the brief names and pass 1 never queried. `existing_mcp` is
answered for 24 of 100, and 75 of the blanks are provably ours: pass 1 planned three
queries per app -- auth, pricing, signup -- and none of them was an MCP query. This
issues that query, one focused question per app, and grades the answer the same way as
everything else.

Both write checkpoints per batch, because the workbench allows 180s per cell while the
client driving it times out at 60s. A cell can finish server-side after the caller has
given up; that happened twice in pass 1 and the checkpoints are why nothing was lost.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import pathlib
import re
import time

from . import config, derive, evidence, probe, prompts, registry, schema, upgrade
from . import providers as providers_mod


# ---------------------------------------------------------------------- refetching

def refetch_targets(records: list[dict]) -> dict[str, list[tuple[int, str]]]:
    """url -> [(app_id, field)]. Grouped by URL so each page is pulled once even when
    several claims on it need judging."""
    by_url: dict[str, list[tuple[int, str]]] = {}
    for r in records:
        for field, check in r["quote_checks"].items():
            if not check.get("needs_refetch"):
                continue
            url = (r["extracted"].get(field) or {}).get("url", "")
            if url:
                by_url.setdefault(url, []).append((r["id"], field))
    return by_url


def run_refetch(records: list[dict], provs, batch: int = 12) -> dict:
    """Re-pull the pages behind truncated claims and grade the quotes for real."""
    by_id = {r["id"]: r for r in records}
    targets = refetch_targets(records)
    urls = list(targets)
    print(f"refetch: {sum(len(v) for v in targets.values())} claims on {len(urls)} urls")

    resolved: dict[tuple[int, str], str] = {}
    fetched_chars = 0
    for start in range(0, len(urls), batch):
        chunk = urls[start:start + batch]
        pages = {p["url"]: p["text"] for p in provs.fetch(chunk, max_chars=30000)}
        fetched_chars += sum(len(t) for t in pages.values())
        for url in chunk:
            text = pages.get(url)
            for app_id, field in targets[url]:
                cell = (by_id[app_id]["extracted"].get(field) or {})
                if not text:
                    # The page would not load now. That is link rot, not a model error,
                    # and it is a finding in its own right.
                    resolved[(app_id, field)] = "unreachable-now"
                    continue
                resolved[(app_id, field)] = evidence.grade_quote(cell.get("quote", ""), text)
        print(f"  + urls {start}-{start + len(chunk)}: {len(pages)}/{len(chunk)} fetched")
    return {"resolved": resolved, "urls": len(urls), "chars": fetched_chars}


def apply_refetch(records: list[dict], resolved: dict[tuple[int, str], str]) -> dict:
    """Replace `truncated-evidence` with the verdict the full page supports, then
    quarantine anything that turns out to be fabricated after all."""
    tally: collections.Counter = collections.Counter()
    for r in records:
        reasons = r.setdefault("unknown_reason", {})
        for field, check in list(r["quote_checks"].items()):
            verdict = resolved.get((r["id"], field))
            if not verdict:
                continue
            check["verdict"] = verdict
            check["needs_refetch"] = False
            check["resolved_by"] = "refetch"
            check["evidenced"] = verdict in ("valid", "near-miss") and \
                check.get("tier", 5) <= schema.EVIDENCED_MAX_TIER
            tally[verdict] += 1
        extracted, removed = evidence.quarantine(r["extracted"], r["quote_checks"], reasons)
        r["extracted"] = extracted
        for item in removed:
            if item not in r["quarantined"]:
                r["quarantined"].append(item)
    return dict(tally.most_common())


# ------------------------------------------------------------------- mcp discovery

MCP_HINT_RE = re.compile(r"(mcp|model[- ]context[- ]protocol)", re.I)


def _mcp_one(record: dict, provs) -> dict:
    app = {"app": record["app"], "category": record["category"], "hint": record["hint"]}
    citations = provs.search(f"{record['app']} MCP server model context protocol integration")
    citations += provs.search(f"{record['app']} official MCP server github")

    # Only pages that actually mention MCP are worth spending the model on.
    ranked = [u for u, _t in citations if u and MCP_HINT_RE.search(u)]
    ranked += [u for u, _t in citations if u and u not in ranked]
    urls = list(dict.fromkeys(ranked))[:5]
    pages = provs.fetch(urls, max_chars=6000)
    pages = [p for p in pages if MCP_HINT_RE.search(p["text"][:4000] or "")] or pages
    if not pages:
        return {"id": record["id"], "ok": False, "why": "no pages"}

    block = "\n\n".join(
        f"--- SOURCE {p['url']}  (tier {schema.source_tier(p['url'], record['hint'], record['app'])})\n"
        f"{p['text'][:4500]}" for p in pages[:3])
    raw, err = provs.llm(prompts.repair_prompt(app, "existing_mcp", block))
    if err:
        return {"id": record["id"], "ok": False, "why": err}

    match = re.search(r"\{.*\}", raw or "", re.S)
    if not match:
        return {"id": record["id"], "ok": False, "why": "unparseable"}
    try:
        answer = json.loads(match.group(0))
    except Exception as exc:
        return {"id": record["id"], "ok": False, "why": f"json: {exc}"}

    return {"id": record["id"], "ok": True,
            "cell": {"value": schema.norm_enum(answer.get("value"), schema.MCP_STATES),
                     "quote": (answer.get("quote") or "")[:600],
                     "url": answer.get("url") or ""},
            "reason": answer.get("unknown_reason") or "",
            "texts": {p["url"]: p["text"] for p in pages}}


def run_mcp(records: list[dict], provs, batch: int = 10, workers: int = 8,
            only_blank: bool = True) -> dict:
    todo = [r for r in records
            if not only_blank or schema.is_blank("existing_mcp",
                                                 r["extracted"]["existing_mcp"]["value"])]
    print(f"mcp: querying {len(todo)} apps (of {len(records)})")
    by_id = {r["id"]: r for r in records}
    tally: collections.Counter = collections.Counter()

    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        ckpt = config.CACHE / f"pass2_mcp_{chunk[0]['id']}_{chunk[-1]['id']}.json"
        if ckpt.exists():
            results = json.loads(ckpt.read_text())
            print(f"  = apps {chunk[0]['id']}-{chunk[-1]['id']}: from checkpoint")
        else:
            t0 = time.time()
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(lambda r: _safe(_mcp_one, r, provs), chunk))
            ckpt.write_text(json.dumps(config.redact(results), indent=2))
            print(f"  + apps {chunk[0]['id']}-{chunk[-1]['id']}: "
                  f"{sum(1 for x in results if x.get('ok'))}/{len(chunk)} answered, "
                  f"{time.time() - t0:.0f}s")

        for res in results:
            rec = by_id[res["id"]]
            if not res.get("ok"):
                tally["failed"] += 1
                continue
            cell, texts = res["cell"], res.get("texts") or {}
            grade = evidence.grade_quote(cell["quote"], texts.get(cell["url"], "")) \
                if cell["quote"] and cell["url"] in texts else \
                ("abstained" if not cell["quote"] else "unverifiable-url")
            tier = schema.source_tier(cell["url"], rec["hint"], rec["app"])
            if grade in ("valid", "near-miss") or \
                    (cell["value"] == "none" and not cell["quote"]):
                rec["extracted"]["existing_mcp"] = {**cell, "source": "pass2-mcp-query"}
                rec["quote_checks"]["existing_mcp"] = {
                    "verdict": "absence-claim" if cell["value"] == "none" and not cell["quote"]
                               else grade,
                    "tier": tier,
                    "evidenced": grade in ("valid", "near-miss") and tier <= schema.EVIDENCED_MAX_TIER,
                    "resolved_by": "pass2-mcp-query"}
                rec["unknown_reason"].pop("existing_mcp", None)
                tally[cell["value"]] += 1
            else:
                # Answer arrived but its quote does not check out. An unverified answer
                # is worth less than an admitted gap, so it does not get recorded.
                rec["unknown_reason"]["existing_mcp"] = \
                    res.get("reason") if res.get("reason") in schema.UNKNOWN_REASONS \
                    else "quote-failed-validation"
                tally[f"rejected:{grade}"] += 1
    return dict(tally.most_common())


def _safe(fn, record, provs):
    try:
        return fn(record, provs)
    except Exception as exc:
        return {"id": record["id"], "ok": False, "why": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------ recorded patch

def apply_patch(records: list[dict], patch: dict) -> dict:
    """Apply a recorded pass-2 result instead of re-running it.

    Both repairs need the network and one needs a model, so they run once in the
    workbench and the outcome is committed to `data/pass2_patch.json`. Every other
    environment then reproduces the identical dataset from it -- same reasoning as
    `data/pass1_strict_grades.txt`. Re-running with --refetch --mcp regenerates the
    patch from scratch; this path just replays it.
    """
    tally: collections.Counter = collections.Counter()
    by_id = {r["id"]: r for r in records}

    for key, verdict in (patch.get("refetch") or {}).items():
        app_id, field = key.split(".", 1)
        rec = by_id.get(int(app_id))
        if not rec or field not in rec["quote_checks"]:
            continue
        check = rec["quote_checks"][field]
        check.update(verdict=verdict, needs_refetch=False, resolved_by="refetch",
                     evidenced=verdict in ("valid", "near-miss")
                     and check.get("tier", 5) <= schema.EVIDENCED_MAX_TIER)
        tally[f"refetch:{verdict}"] += 1

    for app_id, cell in (patch.get("mcp") or {}).items():
        rec = by_id.get(int(app_id))
        if not rec:
            continue
        if cell.get("v"):
            rec["extracted"]["existing_mcp"] = {
                "value": cell["v"], "quote": cell.get("q", ""), "url": cell.get("u", ""),
                **({"source": cell["src"]} if cell.get("src") else {})}
            rec["quote_checks"]["existing_mcp"] = {
                "verdict": cell.get("g", "valid"), "tier": cell.get("t", 5),
                "evidenced": bool(cell.get("e")), "resolved_by": "pass2-mcp-query"}
            (rec.setdefault("unknown_reason", {})).pop("existing_mcp", None)
            tally[f"mcp:{cell['v']}"] += 1
        elif cell.get("rsn"):
            rec.setdefault("unknown_reason", {})["existing_mcp"] = cell["rsn"]
            tally["mcp:rejected"] += 1

    for r in records:
        reasons = r.setdefault("unknown_reason", {})
        r["extracted"], removed = evidence.quarantine(r["extracted"], r["quote_checks"], reasons)
        for item in removed:
            if item not in r["quarantined"]:
                r["quarantined"].append(item)
    return dict(tally.most_common())


# ------------------------------------------------------- probes and registry facts

def apply_probes(records: list[dict], patch: dict) -> dict:
    """Fold in the behavioural probes and Composio's own product descriptions.

    The `/pricing` redirect probe is the generalisation of the one case documents could
    not settle: usepylon.com/pricing lands on /schedule-demo, so there is no public
    pricing and no self-serve tier. Running it on all 100 produced a NEGATIVE result --
    exactly one sales gate, the one it was designed from. Publishing that matters more
    than the gate it found: a technique built from a single vivid example generalised to
    nothing, and the honest conclusion is that hidden pricing is rarer on this list than
    that example suggests. What it did establish positively is 50 apps with public
    pricing at a predictable URL, which is a real self-serve signal.

    `one_liner` comes from Composio's registry description where the app is in the
    catalog. Non-LLM and authoritative, so it is marked `registry-fact` rather than
    dressed up as a vendor quote -- it is Composio describing the app, which is exactly
    what the field needs and nothing more.
    """
    tally: collections.Counter = collections.Counter()
    by_id = {r["id"]: r for r in records}

    for app_id, pr in (patch.get("probe") or {}).items():
        rec = by_id.get(int(app_id))
        if not rec:
            continue
        rec["pricing_probe"] = {"sales_gate": bool(pr.get("gate")),
                                "public_pricing": bool(pr.get("pub")),
                                "status": pr.get("st", 200),
                                "final_url": pr.get("fin", ""),
                                "redirected": bool(pr.get("fin"))}
        if pr.get("ops"):
            rec["spec_probe"] = {"operation_count": pr["ops"], "spec_url": pr.get("spec", "")}
            tally["spec_found"] += 1
        tally["sales_gate" if pr.get("gate") else
              "public_pricing" if pr.get("pub") else "pricing_inconclusive"] += 1

    for app_id, one in (patch.get("oneliner") or {}).items():
        rec = by_id.get(int(app_id))
        if not rec or not schema.is_blank("one_liner", rec["extracted"]["one_liner"]["value"]):
            continue
        rec["extracted"]["one_liner"] = {
            "value": one["v"],
            "quote": "", "url": f"https://platform.composio.dev/toolkits/{one['slug']}",
            "source": "composio-registry"}
        rec["quote_checks"]["one_liner"] = {"verdict": "registry-fact", "tier": 1,
                                           "evidenced": True, "resolved_by": "composio-registry"}
        (rec.setdefault("unknown_reason", {})).pop("one_liner", None)
        tally["one_liner_from_registry"] += 1
    return dict(tally.most_common())


# ----------------------------------------------------------------------- rederive

def rederive(records: list[dict], matches: dict) -> list[dict]:
    """Re-run every derivation after the repairs, so access, buildability and the
    unknown accounting reflect the new evidence rather than the old."""
    for r in records:
        ex = r["extracted"]
        access = derive.derive_access(ex, r.get("detectors"), r.get("pricing_probe"))
        buildability = derive.derive_buildability(ex, access["value"], r.get("registry") or {})
        r["self_serve"], r["buildability"] = access, buildability
        r["auth_family"] = derive.auth_family(ex["auth_methods"]["value"])
        reg_tools = (r.get("registry") or {}).get("composio_tools_count")
        spec_ops = (r.get("spec_probe") or {}).get("operation_count")
        count = reg_tools or spec_ops
        r["breadth"] = {"count": count, "bucket": probe.breadth_bucket(count),
                        "source": "composio-registry" if reg_tools
                                  else ("openapi-spec" if spec_ops else "none"),
                        # Where both exist they are a free cross-check on each other.
                        "registry_tools": reg_tools, "spec_operations": spec_ops}
        r["contradictions"] = derive.reconcile(ex, access, buildability)
        r["quote_summary"] = evidence.summarise(r["quote_checks"])
        r["unknown_reason"] = derive.fill_unknown_reasons(
            ex, r.get("unknown_reason") or {}, len(r.get("sources_used") or []),
            set() if r["extracted"]["existing_mcp"].get("source") else upgrade.NEVER_QUERIED_IN_PASS1)
        r["provenance"] = {**(r.get("provenance") or {}), "pass": 3}
    return records


# ---------------------------------------------------------------------------- cli

def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="agent.pass2",
                               description="Targeted repairs on the pass-2 dataset.")
    p.add_argument("--refetch", action="store_true",
                   help="re-pull pages behind truncated-evidence claims and grade for real")
    p.add_argument("--mcp", action="store_true",
                   help="issue the MCP query pass 1 never issued")
    p.add_argument("--in", dest="src", default="outputs/dataset_v2.json")
    p.add_argument("--out", default="outputs/dataset_v3.json")
    p.add_argument("--backend", default="auto", choices=["auto", "workbench", "sdk"])
    p.add_argument("--batch", type=int, default=10)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--all-apps", action="store_true",
                   help="query MCP for all 100, not only the blanks")
    p.add_argument("--probe-patch", default="data/probe_patch.json",
                   help="recorded pricing-redirect / OpenAPI-spec probes and registry "
                        "descriptions")
    p.add_argument("--patch", default="data/pass2_patch.json",
                   help="replay the recorded pass-2 result instead of re-running it "
                        "(default when neither --refetch nor --mcp is given)")
    args = p.parse_args(argv)

    config.load_dotenv()
    payload = json.loads(pathlib.Path(args.src).read_text())
    records = payload["dataset"]
    matches = registry.load_matches()
    report: dict = {}
    live = args.refetch or args.mcp

    if not live:
        patch_path = pathlib.Path(args.patch)
        if not patch_path.exists():
            p.print_help()
            return 2
        print(f"replaying {patch_path} (no network) -- pass --refetch --mcp to re-run live")
        report["replayed"] = apply_patch(records, json.loads(patch_path.read_text()))
        print(f"  {report['replayed']}")
        probe_path = pathlib.Path(args.probe_patch)
        if probe_path.exists():
            report["probes"] = apply_probes(records, json.loads(probe_path.read_text()))
            print(f"  probes: {report['probes']}")

    provs = providers_mod.build(args.backend) if live else None
    if live:
        print(f"backend={provs.label}  records={len(records)}")
    if args.refetch:
        out = run_refetch(records, provs)
        report["refetch"] = {"urls": out["urls"], "chars": out["chars"],
                             "verdicts": apply_refetch(records, out["resolved"])}
        print(f"  refetch verdicts: {report['refetch']['verdicts']}")
    if args.mcp:
        report["mcp"] = run_mcp(records, provs, args.batch, args.workers,
                                only_blank=not args.all_apps)
        print(f"  mcp results: {report['mcp']}")

    report["subject_check"] = evidence.enforce_subject_checks(records)
    print(f"  subject check: {report['subject_check'] or 'nothing flagged'}")
    records = rederive(records, matches)
    payload["dataset"] = config.redact(records)
    payload["patterns"] = upgrade.patterns(records, matches)
    payload["coverage"] = upgrade.coverage_report(records)
    payload["delta_vs_pass1"] = upgrade.delta_vs_pass1(records)
    payload["auth_cross_check"] = registry.cross_check_auth(records, matches)
    payload["unknown_audit"] = upgrade.unknown_audit(records)
    payload["refetch_queue"] = upgrade.refetch_queue(records)
    payload["pass2_report"] = report

    pathlib.Path(args.out).write_text(json.dumps(payload, indent=2))
    print(f"wrote {args.out}")
    upgrade._report(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
