#!/usr/bin/env python3
"""CLI for the research agent.

    # one app, end to end -- the fastest way to see what this does
    python -m agent.run_research --app "Notion"

    # retrieval only, no LLM needed, just a Composio key
    python -m agent.run_research --app "Pylon" --sources-only

    # a batch, checkpointed, resumable
    python -m agent.run_research --range 0 10
    python -m agent.run_research --all --resume

    # pass 0: the deliberately naive no-retrieval baseline
    python -m agent.run_research --ids 1,11,21 --baseline

Batches are checkpointed to outputs/cache/ after every batch. That is not
belt-and-braces: Composio's workbench allows 180s per cell while the MCP client
that drives it times out at 60s, so a cell can complete server-side after the
caller has given up. It happened twice on the first run and the checkpoints are
the only reason those two batches were not lost.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import sys
import time

from . import config, pipeline, providers as providers_mod, registry


def load_apps() -> list[dict]:
    path = config.DATA / "apps.csv"
    if not path.exists():
        raise SystemExit(f"missing {path} -- the 100-app list from the brief")
    apps = []
    for row in csv.DictReader(open(path, encoding="utf-8")):
        apps.append({"id": int(row["id"]), "app": row["app"].strip(),
                     "category": row["category"].strip(), "hint": (row.get("hint") or "").strip()})
    return apps


def select(apps: list[dict], args) -> list[dict]:
    if args.app:
        wanted = args.app.strip().lower()
        hits = [a for a in apps if wanted in a["app"].lower()]
        if not hits:
            raise SystemExit(f"no app matching {args.app!r}. Try --list")
        return hits[:1]
    if args.ids:
        want = {int(x) for x in args.ids.replace(" ", "").split(",") if x}
        return [a for a in apps if a["id"] in want]
    if args.range:
        lo, hi = args.range
        return apps[lo:hi]
    return apps


def run(apps, provs, matches, args) -> list[dict]:
    fn = (lambda a: pipeline.baseline_app(a, provs)) if args.baseline else \
         (lambda a: pipeline.research_app(a, provs, matches, sources_only=args.sources_only))

    results: list[dict] = []
    for start in range(0, len(apps), args.batch):
        chunk = apps[start:start + args.batch]
        label = f"{chunk[0]['id']}-{chunk[-1]['id']}"
        ckpt = config.CACHE / f"{'pass0' if args.baseline else 'pass1'}_{label}.json"

        if args.resume and ckpt.exists():
            results += json.loads(ckpt.read_text())
            print(f"  = apps {label}: from checkpoint")
            continue

        t0 = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            done = []
            for app, res in zip(chunk, pool.map(_safe(fn), chunk)):
                done.append(res)
        ckpt.write_text(json.dumps(config.redact(done), indent=2))
        results += done

        graded = sum(1 for r in done for c in (r.get("quote_checks") or {}).values()
                     if c.get("verdict") == "valid")
        bad = sum(1 for r in done for c in (r.get("quote_checks") or {}).values()
                  if c.get("verdict") == "QUOTE_NOT_FOUND")
        print(f"  + apps {label}: {len(done)} records, {graded} verified quotes, "
              f"{bad} failed validation, {time.time() - t0:.0f}s")
    return results


def _safe(fn):
    """A single app blowing up must not take the run with it."""
    def wrapped(app):
        try:
            return fn(app)
        except Exception as exc:
            print(f"  ! {app['app']}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return {"id": app["id"], "app": app["app"], "category": app["category"],
                    "hint": app.get("hint", ""), "extracted": {},
                    "error": f"{type(exc).__name__}: {exc}"}
    return wrapped


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="agent.run_research",
        description="Research the 100 apps for agent-toolkit buildability.")
    sel = p.add_argument_group("what to run")
    sel.add_argument("--app", help='single app by name, e.g. --app "Notion"')
    sel.add_argument("--ids", help="comma-separated ids, e.g. --ids 1,11,21")
    sel.add_argument("--range", nargs=2, type=int, metavar=("LO", "HI"),
                     help="slice of the app list, e.g. --range 0 10")
    sel.add_argument("--all", action="store_true", help="all 100")
    sel.add_argument("--list", action="store_true", help="print the app list and exit")

    mode = p.add_argument_group("how to run")
    mode.add_argument("--backend", default="auto", choices=["auto", "workbench", "sdk"],
                      help="auto detects Composio's workbench, else uses the SDK")
    mode.add_argument("--llm", default="auto", choices=["auto", "openai", "anthropic"],
                      help="only used by the sdk backend")
    mode.add_argument("--sources-only", action="store_true",
                      help="retrieval + probes only, no LLM (needs just COMPOSIO_API_KEY)")
    mode.add_argument("--baseline", action="store_true",
                      help="pass 0: no retrieval, answer from model memory")
    mode.add_argument("--workers", type=int, default=8)
    mode.add_argument("--batch", type=int, default=10,
                      help="checkpoint every N apps (keep small: the workbench caller "
                           "times out at 60s)")
    mode.add_argument("--resume", action="store_true", help="reuse existing checkpoints")
    mode.add_argument("--out", help="write the combined dataset here")
    return _dispatch(p, p.parse_args(argv))


def _dispatch(parser, args) -> int:
    config.load_dotenv()
    apps = load_apps()

    if args.list:
        for a in apps:
            print(f"{a['id']:>3}  {a['app']:<28} {a['category']}")
        return 0
    if not (args.app or args.ids or args.range or args.all):
        parser.print_help()
        return 2

    chosen = select(apps, args)
    provs = providers_mod.build(args.backend, args.llm)
    matches = registry.load_matches()

    print(f"backend={provs.label}  apps={len(chosen)}  "
          f"registry={len([m for m in matches.values() if m['in_catalog']])} in catalog  "
          f"mode={'pass0' if args.baseline else 'sources-only' if args.sources_only else 'pass1'}")

    records = config.redact(run(chosen, provs, matches, args))

    out = args.out or (config.OUTPUTS /
                       f"{'pass0' if args.baseline else 'dataset'}_"
                       f"{'one' if len(records) == 1 else len(records)}.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(records, fh, indent=2)
    print(f"wrote {out}")

    if len(records) == 1:
        _print_one(records[0])
    elif not args.baseline and not args.sources_only:
        check = registry.cross_check_auth(records, matches)
        if check["sample"]:
            print(f"\nauth cross-check vs Composio's registry ({check['sample']} apps): "
                  f"{check['token_pct']}% token-level, {check['family_pct']}% family-level")
    return 0


def _print_one(rec: dict):
    """Human-readable single-app output, so `--app "Notion"` is a useful demo rather
    than a wall of JSON."""
    print(f"\n{'=' * 68}\n{rec['app']}  ({rec['category']})\n{'=' * 68}")
    if rec.get("error"):
        print("  failed:", rec["error"])
        return
    print(f"  sources used   : {len(rec.get('sources_used', []))} "
          f"(tiers {sorted(set(rec.get('source_tiers', {}).values()))})")
    for name, cell in (rec.get("extracted") or {}).items():
        check = (rec.get("quote_checks") or {}).get(name, {})
        value = cell.get("value")
        value = ", ".join(value) if isinstance(value, list) else value
        flag = {"valid": "ok", "near-miss": "paraphrase", "QUOTE_NOT_FOUND": "QUARANTINED",
                "abstained": "--", "wrong-url": "WRONG URL"}.get(check.get("verdict"),
                                                                 check.get("verdict", ""))
        reason = (rec.get("unknown_reason") or {}).get(name, "")
        print(f"  {name:<24} {str(value or 'unknown'):<26} [{flag}"
              f"{' t' + str(check.get('tier')) if check.get('tier') else ''}]"
              f"{'  ' + reason if reason else ''}")
    print(f"  auth family    : {rec.get('auth_family')}")
    print(f"  access         : {rec['self_serve']['value']}  <- {rec['self_serve']['basis']}")
    print(f"  buildability   : {rec['buildability']['value']} "
          f"(blocker: {rec['buildability']['blocker']})  <- {rec['buildability']['basis']}")
    print(f"  breadth        : {rec['breadth']['bucket']} "
          f"({rec['breadth']['count'] or '?'} via {rec['breadth']['source']})")
    if rec.get("pricing_probe", {}).get("sales_gate"):
        print(f"  pricing probe  : /pricing -> {rec['pricing_probe']['final_url']}  (sales gate)")
    for c in rec.get("contradictions") or []:
        print(f"  ! contradiction: {c}")
    print(f"  {rec['secs']}s")


if __name__ == "__main__":
    raise SystemExit(main())
