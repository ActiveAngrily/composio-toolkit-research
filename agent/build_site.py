#!/usr/bin/env python3
"""Build the deliverable: one self-contained HTML page.

    python -m agent.build_site

Reads outputs/dataset_v2.json and writes docs/index.html with the data inlined, plus
docs/data.json and docs/llms.txt so an agent can consume the same findings a human
reads. Nothing is fetched at page load, so the page works from a file:// URL, offline,
and in a private window.
"""
from __future__ import annotations

import collections
import html
import json
import pathlib

from . import config

# GitHub Pages serves from the repo root or /docs only, so the deliverable lives in
# docs/ -- "Deploy from a branch -> main -> /docs" needs no workflow and no waiting.
SITE = config.ROOT / "docs"
REPO_URL = "https://github.com/ActiveAngrily/composio-toolkit-research"

CATS = ["CRM and Sales", "Support and Helpdesk", "Communications and Messaging",
        "Marketing, Ads, Email and Social", "Ecommerce", "Data, SEO and Scraping",
        "Developer, Infra and Data platforms", "Productivity and Project Management",
        "Finance and Fintech", "AI, Research and Media-native"]

VERDICT_LABEL = {
    "unnamed-subject": "evidence never names this app, from a non-vendor source — quarantined",
    "not-a-description": "an authentication instruction, not a description — quarantined",
    "off-topic-evidence": "evidence never mentions MCP — quarantined",
    "valid": "quote verified on the cited page",
    "near-miss": "real sentence, reformatted",
    "QUOTE_NOT_FOUND": "quote not on the page — quarantined",
    "wrong-url": "real quote, wrong page cited",
    "unverifiable-url": "cited a page we never fetched",
    "truncated-evidence": "unverifiable offline: only half the page text was retained",
    "absence-claim": "asserts an absence \u2014 an absence cannot be quoted",
    "abstained": "no answer claimed",
    "no-quote": "claimed without a quote — quarantined",
    "not-asked-pass1": "derived by rule \u2014 nothing to quote",
    "unquoted-ok": "paraphrase allowed for this field",
    "no-retained-text": "no page text retained",
}


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


def row_view(r: dict) -> dict:
    """Trim a record to what the table renders. Keeps every citation so evidence is one
    click from any cell, drops the raw probe and detector payloads."""
    ex = r["extracted"]
    fields = {}
    for name, cell in ex.items():
        check = r["quote_checks"].get(name, {})
        val = cell.get("value")
        fields[name] = {
            "v": ", ".join(val) if isinstance(val, list) else val,
            "q": (cell.get("quote") or "")[:400],
            "u": cell.get("url") or "",
            "g": check.get("verdict", ""),
            "t": check.get("tier", 5),
            "e": bool(check.get("evidenced")),
            "r": (r.get("unknown_reason") or {}).get(name, ""),
        }
    return {
        "id": r["id"], "app": r["app"], "cat": r["category"], "hint": r["hint"],
        "fam": r["auth_family"],
        "acc": r["self_serve"]["value"], "accWhy": r["self_serve"]["basis"],
        "bld": r["buildability"]["value"], "blk": r["buildability"]["blocker"],
        "bldWhy": r["buildability"]["basis"],
        "brd": r["breadth"]["bucket"], "brdN": r["breadth"]["count"],
        "brdSrc": r["breadth"]["source"],
        "brdReg": r["breadth"].get("registry_tools"),
        "brdSpec": r["breadth"].get("spec_operations"),
        "gateFinal": (r.get("pricing_probe") or {}).get("final_url", ""),
        "pubPricing": bool((r.get("pricing_probe") or {}).get("public_pricing")),
        "cat_in": bool(r["registry"].get("in_catalog")),
        "slug": r["registry"].get("composio_slug", ""),
        "tools": r["registry"].get("composio_tools_count", 0),
        "f": fields,
        "dis": r.get("contradictions", []),
        "qn": len(r.get("quarantined", [])),
        "gate": bool((r.get("pricing_probe") or {}).get("sales_gate")),
    }


def latest_dataset() -> pathlib.Path:
    """Newest pass wins. v3 = pass 2 repairs applied, v2 = offline re-derivation only."""
    for name in ("dataset_v3.json", "dataset_v2.json"):
        path = config.OUTPUTS / name
        if path.exists():
            return path
    raise SystemExit("no dataset in outputs/ -- run agent.upgrade first")


def build() -> pathlib.Path:
    src = latest_dataset()
    print(f"building from {src.name}")
    payload = json.loads(src.read_text())
    ds, pat = payload["dataset"], payload["patterns"]
    cov, delta, xc = payload["coverage"], payload["delta_vs_pass1"], payload["auth_cross_check"]
    ua = payload["unknown_audit"]
    rows = [row_view(r) for r in ds]

    SITE.mkdir(parents=True, exist_ok=True)
    machine = {"generated_from": f"outputs/{src.name}", "repo": REPO_URL,
               "apps": rows, "patterns": pat, "coverage": cov,
               "delta_vs_pass1": delta, "auth_cross_check": xc, "unknown_audit": ua}
    (SITE / "data.json").write_text(json.dumps(machine, indent=2))
    (SITE / "llms.txt").write_text(llms_txt(pat, cov, xc, delta, ua, rows))
    (SITE / "index.html").write_text(page(rows, pat, cov, delta, xc, ua,
                                          payload.get("pass2_report")))
    return SITE / "index.html"


# ------------------------------------------------------------------------ llms.txt

def llms_txt(pat, cov, xc, delta, ua, rows) -> str:
    missing = pat["catalog"]
    lines = [
        "# 100 apps -> an agent toolkit build queue",
        "",
        f"Source repo: {REPO_URL}",
        "Machine-readable dataset: ./data.json",
        "",
        "## What this is",
        "100 apps researched for agent-toolkit buildability by an agent running in",
        "Composio's remote workbench. Every claim carries a verbatim quote, that",
        "quote's URL, a source-authority tier, and a validation verdict from",
        "re-reading the page. Claims whose quote could not be verified are",
        "quarantined to unknown rather than reported.",
        "",
        "## Headline",
        f"- Composio already covers {missing['in_catalog']} of the 100. "
        f"{missing['missing']} are not in the catalog.",
        f"- {pat['auth']['headline_static_secret_path']} of 100 accept a static secret, "
        f"so most need no OAuth app registered.",
        f"- {missing['missing_with_official_mcp']} of the {missing['missing']} missing apps "
        f"already have an official MCP server built by someone else.",
        f"- Auth cross-check against Composio's own registry on {xc['sample']} apps: "
        f"{xc['token_pct']}% token-level, {xc['family_pct']}% family-level.",
        "",
        "## Patterns",
        f"- auth families: {json.dumps(pat['auth']['families'])}",
        f"- access: {json.dumps(pat['access_overall'])}",
        f"- blockers: {json.dumps(pat['blockers'])}",
        f"- buildability: {json.dumps(pat['buildability'])}",
        "",
        "## Honest limits",
        f"- {delta['needing_refetch']} claims cannot be verified offline because only "
        f"half of each page's text was retained. They are labelled, not counted as errors.",
        f"- {ua['overall'].get('unclassified', 0)} abstentions are unclassified: pass 1 never "
        f"recorded WHY it abstained, so 'the vendor does not publish this' cannot be "
        f"separated from 'we did not find it'.",
        f"- {ua['overall'].get('retrieval-failed', 0)} abstentions are provably our own gap.",
        "",
        "## Per-app data",
        "app | category | auth_family | access | buildability | blocker | in_composio_catalog",
    ]
    for r in rows:
        lines.append(f"{r['app']} | {r['cat']} | {r['fam']} | {r['acc']} | "
                     f"{r['bld']} | {r['blk']} | {'yes' if r['cat_in'] else 'no'}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- page

def page(rows, pat, cov, delta, xc, ua, pass2=None) -> str:
    sc = (pass2 or {}).get("subject_check") or {}
    cat = pat["catalog"]
    # Pass-2 refetch outcome, however it was produced: live or replayed from the patch.
    p2 = pass2 or {}
    rf = (p2.get("refetch") or {}).get("verdicts") or {}
    if not rf and p2.get("replayed"):
        rf = {k.split(":", 1)[1]: v for k, v in p2["replayed"].items() if k.startswith("refetch:")}
    fam = pat["auth"]["families"]
    static_path = pat["auth"]["headline_static_secret_path"]
    bld = pat["buildability"]

    # Access x category, for the heatmap. The brief asks which categories are
    # self-serve versus gated, so the grid answers exactly that question.
    gated_kinds = ["free", "free-trial", "paid-tier-required", "app-review",
                   "admin-consent", "partner-or-sales-gate", "no-public-api", "unknown"]

    # Apps where Composio's tool count and the app's own OpenAPI spec both exist. The
    # two numbers are not the same measurement and the gap says so loudly.
    both = [(r["app"], r["brdReg"], r["brdSpec"]) for r in rows
            if r.get("brdReg") and r.get("brdSpec")]
    both.sort(key=lambda x: -(x[2] / max(x[1], 1)))

    # Access x category -- one of the four pattern questions the brief names.
    grid: dict[str, collections.Counter] = {}
    for r in rows:
        grid.setdefault(r["cat"], collections.Counter())[r["acc"]] += 1

    exhibits = []
    for r in rows:
        for name, f in r["f"].items():
            if f["g"] in ("QUOTE_NOT_FOUND", "no-quote") and f["r"] == "quote-failed-validation":
                exhibits.append((r["app"], name, f["q"], f["u"]))

    tiles = [
        (f"{cat['in_catalog']}", "already in Composio's catalog", "of the 100 researched"),
        (f"{cat['missing']}", "not in the catalog", "the actual build queue"),
        (f"{static_path}", "accept a static secret", "no OAuth app to register"),
        (f"{xc['token_pct']}% &rarr; {xc['family_pct']}%", "auth agreement with Composio's registry",
         f"{xc['sample']} apps, no human involved"),
        (f"{delta['quarantined_claims']}", "claims quarantined", "their quote did not check out"),
    ]

    findings = [
        ("Which auth dominates is the wrong question.",
         f"{fam.get('both', 0)} of the 100 accept <em>both</em> an OAuth dance and a static secret, "
         f"{fam.get('static-secret', 0)} accept only a static secret, and just "
         f"{fam.get('oauth-dance', 0)} force OAuth. "
         f"<strong>{static_path} of 100 have a static-secret path</strong>, so for most of this "
         f"list the expensive part &mdash; registering an OAuth app per vendor &mdash; is optional."),
        ("Composio's coverage gap is shaped, not random.",
         "Productivity is 9/10 in the catalog and Developer/Infra 8/10, against "
         "<strong>Ecommerce 2/10 and AI-native 3/10</strong>. The thin categories are the "
         "newest ones, where the vendors themselves are newest."),
        ("The most common blocker is money, not partnership.",
         f"Of the apps with a blocker: {pat['blockers'].get('paid-plan', 0)} need a paid plan, "
         f"{pat['blockers'].get('app-review', 0)} need vendor app review, "
         f"{pat['blockers'].get('partner-gate', 0)} need a sales or partner conversation, and "
         f"{pat['blockers'].get('no-public-api', 0)} have no public API at all. "
         "A paid seat is a purchase order; a partner gate is a quarter."),
        ("The dangerous error is not a made-up sentence. It is a real sentence about the "
         "wrong product.",
         f"Quote validation cannot catch these, because the quote is genuinely on the page "
         f"it cites &mdash; the page is just about something else. A name-mention check "
         f"caught <strong>{sc.get('unnamed-subject', 0)}</strong> claims sourced from "
         f"non-vendor pages that never name the app (iPayX's description welcomes you to "
         f"iPaymu; Sherlock's &lsquo;official MCP server&rsquo; belongs to the Covertlabs "
         f"infostealer platform), <strong>{sc.get('off-topic-evidence', 0)}</strong> MCP "
         f"claims whose evidence never mentions MCP, and "
         f"<strong>{sc.get('not-a-description', 0)}</strong> one-liners that were "
         f"authentication instructions. All quarantined. This is the same failure mode as "
         f"matching Plaid to a toolkit called <span class=mono>placid</span>."),
        ("Composio's tool count measures Composio, not the app.",
         "On the five apps where both a Composio tool count and the app's own OpenAPI spec "
         "could be read, the two disagree wildly: Notion 53 tools against 49 operations and "
         "Attio 99 against 79 are close, but Apify is 112 against 229, Close 6 against 300, "
         "and <strong>Cloudflare 20 tools against 3,319 operations</strong>. "
         "A tool count is a <em>coverage decision</em>, not a measure of surface area \u2014 which "
         "means it answers &lsquo;how much of this have we wrapped&rsquo;, not &lsquo;how big is "
         "this&rsquo;. We had been using it for the second. It is labelled correctly below."),
        ("Someone else has already built most of the missing integrations.",
         f"<strong>{cat['missing_with_official_mcp']} of the {cat['missing']}</strong> apps missing "
         f"from Composio's catalog ship an <em>official</em> MCP server today, and "
         f"{pat['mcp'].get('official', 0)} of the full 100 do. Nobody asked this question in the "
         f"first pass &mdash; the column was 24% answered until a query was written for it, and "
         f"it is now 86%. The gap in the catalog is smaller than it looks, because the vendors "
         f"have been closing it themselves."),
    ]

    data_json = json.dumps({"apps": rows, "cov": cov, "cats": CATS,
                            "labels": VERDICT_LABEL}, separators=(",", ":"))

    cov_rows = "".join(
        f"<tr><td class=mono>{esc(k)}</td>"
        f"<td class=num>{v['coverage_pct']}%</td>"
        f"<td class=num>{v['evidenced_pct_of_answered']}%</td>"
        f"<td class=bar><span style='width:{v['coverage_pct']}%'></span></td></tr>"
        for k, v in cov.items())

    exhibit_rows = "".join(
        f"<tr><td>{esc(a)}</td><td class=mono>{esc(f)}</td>"
        f"<td class=q>&ldquo;{esc(q[:150])}&hellip;&rdquo;</td>"
        f"<td class=mono><a href='{esc(u)}' target=_blank rel=noopener>source</a></td></tr>"
        for a, f, q, u in exhibits[:12])

    miss_rows = "".join(
        f"<tr><td>{esc(d['app'])}</td><td class=mono>{esc(', '.join(d['registry']))}</td>"
        f"<td class=mono>{esc(', '.join(d['agent']) or '&mdash;')}</td>"
        f"<td>{'our taxonomy' if d['cause'] == 'transport-vs-credential' else 'real recall miss'}</td></tr>"
        for d in xc["disagreements"])

    return f"""<!doctype html>
<html lang=en><head>
<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>100 apps &rarr; an agent toolkit build queue</title>
<meta name=description content="100 apps researched for agent-toolkit buildability by an
agent, with every claim quote-verified and the failures published.">
<style>
:root{{
  --bg:#0d1014; --panel:#141920; --panel2:#1a2029; --line:#242c37;
  --ink:#e8ecf1; --dim:#98a4b3; --faint:#697687;
  --ok:#4ea88a; --warn:#c79a3e; --bad:#c86a5e; --info:#5b8ec9; --accent:#7c9fd4;
}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
  font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}}
.wrap{{max-width:1180px;margin:0 auto;padding:0 22px}}
a{{color:var(--accent)}} a:hover{{color:#a9c4e8}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}}
h1{{font-size:31px;line-height:1.2;margin:0 0 10px;letter-spacing:-.02em}}
h2{{font-size:19px;margin:0 0 4px;letter-spacing:-.01em}}
h3{{font-size:15px;margin:0 0 6px}}
p{{margin:0 0 12px}}
header{{border-bottom:1px solid var(--line);padding:46px 0 30px;background:
  radial-gradient(1100px 320px at 12% -30%,#1b2735 0%,transparent 70%)}}
.lede{{color:var(--dim);max-width:76ch;font-size:16px}}
.meta{{color:var(--faint);font-size:12.5px;margin-top:16px}}
.meta a{{color:var(--dim)}}
section{{padding:34px 0;border-bottom:1px solid var(--line)}}
.eyebrow{{font-size:11px;letter-spacing:.14em;text-transform:uppercase;
  color:var(--faint);margin-bottom:14px}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(178px,1fr));gap:12px;
  margin:22px 0 0}}
.tile{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}}
.tile b{{display:block;font-size:27px;font-weight:650;letter-spacing:-.02em;line-height:1.1}}
.tile span{{display:block;color:var(--dim);font-size:13px;margin-top:6px}}
.tile i{{display:block;color:var(--faint);font-size:11.5px;font-style:normal;margin-top:3px}}
.finding{{display:grid;grid-template-columns:26px 1fr;gap:12px;
  padding:15px 0;border-top:1px solid var(--line)}}
.finding:first-of-type{{border-top:none}}
.finding .n{{color:var(--faint);font-size:12px;padding-top:3px}}
.finding p{{color:var(--dim);margin:4px 0 0;max-width:82ch}}
.finding strong{{color:var(--ink)}} .finding em{{color:var(--ink);font-style:normal}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:var(--faint);font-weight:600;font-size:11px;
  text-transform:uppercase;letter-spacing:.07em;padding:8px 9px;
  border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg)}}
td{{padding:7px 9px;border-bottom:1px solid #1b222c;vertical-align:top}}
tbody tr:hover{{background:#171d25}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.bar{{width:130px}} .bar span{{display:block;height:7px;border-radius:4px;background:var(--info)}}
.stack{{width:180px;white-space:nowrap;line-height:0}}
.stack span{{display:inline-block;height:9px}}
.s-free{{background:var(--ok)}} .s-paid{{background:var(--warn)}}
.s-rev{{background:var(--info)}} .s-sale{{background:var(--bad)}}
.s-none{{background:#2a333f}}
.pill{{display:inline-block;padding:1.5px 7px;border-radius:20px;font-size:11px;
  border:1px solid transparent;white-space:nowrap}}
.p-already{{background:#132a24;color:#7fd0b0;border-color:#1e4438}}
.p-now{{background:#12251c;color:#6ec898;border-color:#1d4130}}
.p-caveat{{background:#2a2415;color:#dfbc6a;border-color:#463a1d}}
.p-outreach{{background:#2b1a17;color:#e5928a;border-color:#4a2a25}}
.p-not{{background:#241a1a;color:#cf8b84;border-color:#3d2724}}
.p-unknown{{background:#1c222b;color:var(--dim);border-color:#2a333f}}
.ev{{cursor:help;font-size:9.5px;padding:1px 4px;border-radius:3px;margin-left:4px;
  font-family:ui-monospace,Menlo,monospace}}
.g-ok{{background:#13291f;color:#79c39c}} .g-para{{background:#242a1a;color:#c9c07a}}
.g-bad{{background:#2a1a1a;color:#e0847a}}
.src-ok{{background:#182430;color:#8fb4d8}} .src-dir{{background:#2a2415;color:#dfbc6a}}
.src-other{{background:#232a33;color:var(--faint)}}
.g-rule{{background:#1c2430;color:#8fa7c4}}
.why{{color:var(--faint);font-size:11.5px;font-style:italic}}
.controls{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 14px}}
button,select{{background:var(--panel2);color:var(--ink);border:1px solid var(--line);
  border-radius:7px;padding:6px 11px;font-size:12.5px;cursor:pointer;font-family:inherit}}
button:hover{{border-color:#38455a}} button[aria-pressed=true]{{border-color:var(--accent);
  background:#1c2836;color:#cfe0f5}}
.q{{color:var(--dim);font-style:italic}}
.oneline{{color:var(--dim);font-size:12px;line-height:1.45;max-width:36ch;margin:2px 0 0}}
.catchip{{color:var(--faint);font-size:10.5px;margin-left:6px;letter-spacing:.03em;
  text-transform:uppercase}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:26px}}
.card{{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:17px}}
.card h3{{margin-bottom:8px}} .card p{{color:var(--dim);font-size:13.5px}}
.flow{{font-size:12px;line-height:2;color:var(--dim)}}
.flow b{{color:var(--ink);font-weight:600}}
.warnbox{{border-left:2px solid var(--warn);padding:2px 0 2px 15px;margin:16px 0;
  color:var(--dim);font-size:13.5px}}
.detail{{display:none;background:#101620}} .detail td{{padding:0}}
.detail .inner{{padding:13px 16px 17px}}
.dgrid{{display:grid;grid-template-columns:auto 1fr;gap:5px 14px;font-size:12.5px}}
.dgrid dt{{color:var(--faint);white-space:nowrap}} .dgrid dd{{margin:0;color:var(--dim)}}
.dgrid a{{word-break:break-all}}
footer{{padding:30px 0 60px;color:var(--faint);font-size:12.5px}}
@media(max-width:820px){{.two{{grid-template-columns:1fr}} h1{{font-size:25px}}}}
</style></head><body>

<header><div class=wrap>
  <h1>Composio already covers 56 of these 100.<br>Here is what the other 44 cost you.</h1>
  <p class=lede>100 apps researched for agent-toolkit buildability by an agent running in
  Composio's own remote workbench. Every claim carries a verbatim quote, that quote's URL,
  a source-authority tier, and a verdict from re-reading the page. Claims whose quote did
  not check out are quarantined rather than reported &mdash; there were
  {delta['quarantined_claims']}, and they are named below.</p>
  <div class=meta>
    <a href="{REPO_URL}">source repo</a> &middot;
    <a href="./data.json">data.json</a> &middot;
    <a href="./llms.txt">llms.txt</a> &middot;
    run: 100 apps, {delta['citations_total']} citations, ~25 min of compute
  </div>
  <div class=tiles>
    {''.join(f'<div class=tile><b>{t}</b><span>{s}</span><i>{i}</i></div>' for t, s, i in tiles)}
  </div>
</div></header>

<section><div class=wrap>
  <div class=eyebrow>The patterns</div>
  <h2>{len(findings)} findings, with their numbers</h2>
  {''.join(f'<div class=finding><div class=n>{n + 1}</div><div><h3>{t}</h3><p>{b}</p></div></div>'
           for n, (t, b) in enumerate(findings))}
</div></section>

<section><div class=wrap>
  <div class=eyebrow>The build queue</div>
  <h2>The 44 apps Composio does not have, ordered by what they cost</h2>
  <p class=lede style="font-size:14px">Grouped by verdict. <strong>Build&nbsp;now</strong> means
  self-serve credentials and a documented API today. <strong>Caveats</strong> means buildable
  but something must be bought, reviewed, or consented to.
  <strong>Outreach</strong> means a human conversation before any code.</p>
  <div id=queue></div>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>Pattern 2, in full</div>
  <h2>Which categories are self-serve, and which are gated</h2>
  <table style="margin-top:6px"><thead><tr><th>Category</th>
    <th class=num>self-serve</th><th class=num>pay first</th><th class=num>review / consent</th>
    <th class=num>sales gate</th><th class=num>no API</th><th class=num>unknown</th>
    <th>shape</th></tr></thead><tbody>
  """ + "".join(
      f"<tr><td>{esc(c)}</td>"
      f"<td class=num>{g['free'] + g['free-trial']}</td>"
      f"<td class=num>{g['paid-tier-required']}</td>"
      f"<td class=num>{g['app-review'] + g['admin-consent']}</td>"
      f"<td class=num>{g['partner-or-sales-gate']}</td>"
      f"<td class=num>{g['no-public-api']}</td>"
      f"<td class=num style='color:var(--faint)'>{g['unknown']}</td>"
      f"<td class=stack>"
      f"<span class=s-free style='width:{10 * (g['free'] + g['free-trial'])}%'></span>"
      f"<span class=s-paid style='width:{10 * g['paid-tier-required']}%'></span>"
      f"<span class=s-rev style='width:{10 * (g['app-review'] + g['admin-consent'])}%'></span>"
      f"<span class=s-sale style='width:{10 * g['partner-or-sales-gate']}%'></span>"
      f"<span class=s-none style='width:{10 * (g['no-public-api'] + g['unknown'])}%'></span>"
      f"</td></tr>"
      for c, g in sorted(grid.items(), key=lambda kv: -(kv[1]['free'] + kv[1]['free-trial']))
  ) + f"""</tbody></table>
  <p class=meta>Ten apps per category. Access is derived by rule from four separately
  evidenced sub-answers, never asked of the model as one question &mdash; the basis for
  every value is in the row detail below.</p>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>The findings</div>
  <h2>All 100, with the evidence behind every cell</h2>
  <div class=controls>
    <button data-f=all aria-pressed=true>all 100</button>
    <button data-f=missing>not in catalog (44)</button>
    <button data-f=build-now>build now</button>
    <button data-f=needs-outreach>needs outreach</button>
    <button data-f=quarantined>has a quarantined claim</button>
    <select id=catsel></select>
    <button id=more>show all</button>
    <span class=meta style="margin:0;align-self:center" id=count></span>
  </div>
  <table id=matrix><thead><tr>
    <th style="min-width:250px">App &amp; what it does</th><th>Auth</th><th>Access</th><th>Verdict</th><th>Blocker</th>
    <th>Breadth</th><th>MCP</th><th>Composio</th>
  </tr></thead><tbody></tbody></table>
  <p class=meta>Click any row for every claim, its quote, its source and its verdict.
  Two marks, two different facts: <span class="ev g-ok">&#10003;</span> quoted verbatim
  &middot; <span class="ev g-para">&#8776;</span> paraphrased &middot;
  <span class="ev src-ok">V</span> vendor's own domain &middot;
  <span class="ev src-dir">D</span> third-party directory &middot;
  <span class="ev src-other">?</span> other. A claim needs both to count as evidenced.</p>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>The agent</div>
  <h2>What it does, and where it needed a human</h2>
  <div class=two>
    <div class=card><h3>The pipeline</h3>
      <div class=flow>
        <b>plan</b> four queries per app &mdash; auth, pricing, signup, MCP<br>
        <b>search</b> Composio's <span class=mono>COMPOSIO_SEARCH_WEB</span> (returns URLs)<br>
        <b>rank</b> by source authority, then by docs-shaped paths<br>
        <b>fetch</b> <span class=mono>COMPOSIO_SEARCH_FETCH_URL_CONTENT</span><br>
        <b>scan</b> deterministic gate phrases &rarr; fed in as candidate evidence<br>
        <b>extract</b> quote-or-<span class=mono>unknown</span>, every field cited<br>
        <b>validate</b> re-read the cited page; is the quote literally there?<br>
        <b>quarantine</b> unverified claims &rarr; <span class=mono>unknown</span> + a reason<br>
        <b>probe</b> API base, <span class=mono>/pricing</span> redirect, OpenAPI spec<br>
        <b>derive</b> access, buildability, auth family &mdash; by rule, never by model<br>
        <b>reconcile</b> against Composio's registry: 56 apps of ground truth
      </div>
      <p style="margin-top:12px">Built on Composio's own SDK and tools, running in its remote
      workbench. The extraction model is a GPT-family model, a different vendor from the
      Claude session that wrote the code &mdash; so the cross-checks are cross-vendor, not a
      model grading itself.</p>
    </div>
    <div class=card><h3>Where a human was needed</h3>
      <p><strong>Catching the deterministic lane lying.</strong> The registry matcher paired
      Plaid with a Composio toolkit slugged <span class=mono>placid</span> at 0.909 similarity.
      Placid is an image-generation product. One false positive in 57, caught only because
      fuzzy matches were flagged for human review rather than trusted. The lane without an
      LLM in it was the one that produced a wrong answer.</p>
      <p><strong>Deciding what a schema may not say.</strong> Mermaid CLI came back with
      Bearer, JWT, OAuth2, REST and an official MCP server. It is an npm package that renders
      diagrams on your laptop. Every value invented &mdash; because the schema offered
      <span class=mono>unknown</span> or a guess, and nothing for &ldquo;not applicable&rdquo;.
      A person had to notice the shape of the error, not the error.</p>
      <p><strong>Refusing a flattering number.</strong> The first unknown-accounting run
      reported &ldquo;98% of our gaps are genuine non-disclosure&rdquo;. That was an artifact
      of a default, not a measurement. See the box below.</p>
      <p><strong>Everything git.</strong> The sandbox running the analysis cannot reach
      <span class=mono>composio.dev</span>, and the file bridge to the laptop has no network
      and cannot delete files &mdash; so it cannot clear
      <span class=mono>.git/index.lock</span>. Every commit was run by hand.</p>
    </div>
  </div>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>The verification</div>
  <h2>Two numbers per field, because one of them can be gamed</h2>
  <p class=lede style="font-size:14px">A pipeline that answers nothing scores 100% on
  accuracy. So coverage &mdash; how often we answered at all &mdash; is published beside
  precision, and the honest read of this table is that
  <strong>coverage is the weakness, not correctness</strong>.</p>
  <div class=two style="margin-top:18px">
    <div>
      <table><thead><tr><th>Field</th><th class=num>Answered</th>
        <th class=num>Evidenced</th><th></th></tr></thead>
        <tbody>{cov_rows}</tbody></table>
      <p class=meta>&ldquo;Evidenced&rdquo; = of the claims we made, how many carry a quote
      verified on a page owned by the vendor (tier&nbsp;1&ndash;2).<br><br>
      <strong>Two rows read like holes and are not.</strong>
      <span class=mono>product_class</span> (0%) and <span class=mono>primary_blocker</span>
      (7%) are <em>derived by rule</em> from the evidenced fields above them, not claimed by
      the model &mdash; so there is no quote to verify, by design. The blocker shown in the
      table is the derived one, and every row carries the rule that produced it. Pass 1 did
      ask the model for a blocker and asked for a free-text reason instead of a quote, which
      is why the 7% exists at all rather than being 0.</p>
    </div>
    <div>
      <div class=card><h3>The delta pass 1 &rarr; pass 2</h3>
        <dl class=dgrid>
          <dt>field-slots with no grade at all</dt><dd>{delta['pass1_ungraded_slots']} &rarr; 0</dd>
          <dt>fabricated claims shipped</dt>
          <dd>{delta['pass1_verdicts'].get('QUOTE_NOT_FOUND', 0)} &rarr; 0 (quarantined)</dd>
          <dt>citations below tier 2</dt>
          <dd>unmeasured &rarr; {delta['citations_below_tier2']} of {delta['citations_total']}, flagged</dd>
          <dt>rows contradicting themselves</dt>
          <dd>{delta['contradictions_pass1']} shipped &rarr; 0; {delta['contradictions_now']} disagreements recorded</dd>
          <dt>registry auth agreement</dt>
          <dd>{xc['token_pct']}% token &rarr; {xc['family_pct']}% family</dd>
          <dt>claims unjudgeable offline</dt>
          <dd>{rf.get('valid', 0) + rf.get('near-miss', 0) + rf.get('QUOTE_NOT_FOUND', 0)} &rarr; 0 (53 pages re-pulled)</dd>
          <dt>MCP column answered</dt>
          <dd>24 &rarr; {cov['existing_mcp']['answered']} of 100</dd>
        </dl>
      </div>
      <div class=card style="margin-bottom:14px"><h3>A third lane: ask the server, not the docs</h3>
      <p>Every app got an unauthenticated probe of its API base and a
      <span class=mono>GET /pricing</span> that follows redirects. The redirect probe
      generalises the one case documents could not settle &mdash;
      <span class=mono>usepylon.com/pricing</span> lands on
      <span class=mono>/schedule-demo</span>, so there is no public pricing and no
      self-serve tier.</p>
      <p><strong>Across 100 apps it found exactly one sales gate: Pylon, the case it was
      designed from.</strong> That is a negative result and it belongs here. A technique
      built from one vivid example generalised to nothing, which is itself the finding
      &mdash; hidden pricing is rarer on this list than that example implies. What it did
      establish: <strong>50 apps publish pricing at a predictable URL</strong> (a real
      self-serve signal), 28 have no <span class=mono>/pricing</span> at all, and 5 block
      automated requests. Spec discovery found 9 machine-readable OpenAPI documents.</p></div>
      <div class=warnbox><strong>The number that got worse.</strong> Agreement with Composio's
      registry <em>fell</em> from 82.1% to {xc['token_pct']}% once fabricated auth claims were
      quarantined. Pass 1's score was partly propped up by answers that were right without
      being evidenced. Precision up, recall down. A verification pass where every number
      improves is a verification pass nobody should trust.</div>
    </div>
  </div>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>Hits and misses</div>
  <h2>Where the agent was wrong</h2>
  <div class=two>
    <div>
      <h3>Auth, against Composio's own registry &mdash; {xc['sample']} apps, no human</h3>
      <p class=lede style="font-size:13.5px">Composio has shipped a working connector for 56 of
      these apps, so its recorded auth scheme is ground truth. Every disagreement, and its
      cause:</p>
      <table><thead><tr><th>App</th><th>Composio</th><th>Agent</th><th>Cause</th></tr></thead>
        <tbody>{miss_rows}</tbody></table>
      <p class=meta><strong>Six of the {len(xc['disagreements'])} are our own schema</strong>,
      not the model: we recorded the envelope (Bearer, Basic) where Composio recorded the
      credential kind (API key). Collapsing that distinction moved agreement from
      {xc['token_pct']}% to {xc['family_pct']}% &mdash; the first fix that improved accuracy
      was to the measuring instrument.</p>
    </div>
    <div>
      <h3>Fabricated quotes, caught and removed</h3>
      <p class=lede style="font-size:13.5px">Claims where the cited page does not contain the
      quoted sentence. Every one was in the committed dataset at full confidence, looking
      exactly like a verified claim.</p>
      <table><thead><tr><th>App</th><th>Field</th><th>Claimed quote</th><th></th></tr></thead>
        <tbody>{exhibit_rows}</tbody></table>
    </div>
  </div>
  <div class=warnbox style="border-color:var(--bad)"><strong>The finding we deleted.</strong>
  An early run announced that 98% of our blanks were genuine vendor non-disclosure and 0% were
  our own retrieval failure. It was false. Pass 1's prompt never asked the model <em>why</em> it
  abstained, so the accounting was stamping &ldquo;not stated publicly&rdquo; on anything where
  we had pages and no answer &mdash; a claim about a vendor, manufactured from our own silence.
  The honest version: <strong>{ua['overall'].get('retrieval-failed', 0)} of
  {sum(ua['overall'].values())} blanks are provably our own gap</strong> (every missing MCP
  answer, because pass 1 issued three queries per app and none of them was an MCP query), and
  <strong>{ua['overall'].get('unclassified', 0)} stay unclassified</strong> until the model is
  asked to name its reason. That is worse for us and truer.</div>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>The proof</div>
  <h2>Run it yourself</h2>
  <p class=lede style="font-size:14px">Clone the repo, add a Composio API key, and every
  number on this page regenerates. The tests need no key and no network at all.</p>
  <div class=card style="margin-top:14px"><div class=flow>
    <span class=mono>python -m agent.run_research --app "Notion"</span>
      &nbsp;&mdash;&nbsp;one app end to end: search, fetch, extract, validate, probe, derive<br>
    <span class=mono>python -m agent.run_research --app "Pylon" --sources-only</span>
      &nbsp;&mdash;&nbsp;retrieval and probes only, no model needed<br>
    <span class=mono>python3 scripts/fetch_composio_registry.py</span>
      &nbsp;&mdash;&nbsp;Composio's 1,222-toolkit registry, standard library only<br>
    <span class=mono>python -m agent.upgrade</span>
      &nbsp;&mdash;&nbsp;re-derive all 100 records offline from the recorded evidence<br>
    <span class=mono>python -m agent.pass2</span>
      &nbsp;&mdash;&nbsp;replay the refetch, MCP and probe repairs<br>
    <span class=mono>python -m agent.build_site</span>
      &nbsp;&mdash;&nbsp;rebuild this page<br>
    <span class=mono>python tests/test_agent.py</span>
      &nbsp;&mdash;&nbsp;every rule described on this page, checked. No dependencies.
  </div></div>
  <p class=meta>The expensive steps ran once where they could &mdash; the URL-strict quote
  re-grade and the pass-2 repairs need Composio's workbench &mdash; and their results are
  committed as <span class=mono>data/pass1_strict_grades.txt</span>,
  <span class=mono>data/pass2_patch.json</span> and
  <span class=mono>data/probe_patch.json</span>, so any environment reproduces the identical
  dataset without a key.</p>
</div></section>

<section><div class=wrap>
  <div class=eyebrow>Honest limits</div>
  <h2>What is not here, and what defeated us</h2>
  <div class=two>
    <div class=card><h3>The 75 claims we refused to guess about</h3>
      <p>Pass 1 showed the model 5,000 characters per page and retained 2,500, so 75 claims
      became unverifiable offline &mdash; a quote from the back half of a page simply is not
      there any more. The tempting move was to call them fabrications. Instead they were
      labelled <span class=mono>truncated-evidence</span>, excluded from both sides of the
      ratio, and the 53 pages behind them were re-pulled in full.</p>
      <p><strong>{rf.get('valid', 0)} were verbatim, {rf.get('near-miss', 0)} were real
      sentences reformatted, and {rf.get('QUOTE_NOT_FOUND', 0)} was fabricated.</strong>
      Counting them as errors would have taken our published error count from
      {delta['quarantined_claims']} to {delta['quarantined_claims'] + 74} &mdash; a 3.5&times;
      overstatement of the problem we were claiming to have fixed. That is the single clearest
      argument for labelling uncertainty rather than resolving it in whichever direction
      flatters the story.</p>
      <p><strong>Coverage on access is still 28&ndash;45%.</strong> Which pricing tier includes
      API access is the field vendors publish least and the field the build queue most needs.
      The re-query for it was designed and cut for time.</p>
      <p><strong>Breadth is unknown for 40 apps.</strong> The 56 in Composio's catalog have a
      tool count, and 4 more have a readable OpenAPI spec. And per finding 3 those two numbers
      are not the same measurement, so the column is labelled by source rather than merged
      into a single fake scale.</p>
    </div>
    <div class=card><h3>Apps that defeated us</h3>
      <p><strong>Mermaid CLI, Sherlock, higgsfield.</strong> Not APIs. Local command-line
      tools. The correct finding is &ldquo;wrap the CLI&rdquo;, and the first schema could not
      express it, so the model invented an API for one of them.</p>
      <p><strong>Two name collisions our own validator cannot catch.</strong> Mermaid CLI's
      MCP server resolves to <span class=mono>mcp.mermaid.ai</span> &mdash; that is Mermaid
      Chart, a commercial product, not the npm package the brief points at. And Paygent
      Connect's description (&ldquo;real-time cost visibility for your AI product&rdquo;)
      belongs to something else entirely, on a lookalike domain that passes every authority
      test here. Both share a brand name with the right answer, which is exactly the case a
      name-mention check is blind to. Found by reading the rendered table, not by any
      checker &mdash; which is the argument for presenting data where a person can see it.</p>
      <p><strong>Pylon.</strong> <span class=mono>usepylon.com/pricing</span> redirects to
      <span class=mono>/schedule-demo</span>. No public pricing, no self-serve tier. Documents
      alone returned <span class=mono>unknown</span>; a browser resolved it in one page load,
      which is why a deterministic redirect probe now runs for every app.</p>
      <p><strong>Stripe.</strong> Its docs say API key, its server answers
      <span class=mono>WWW-Authenticate: Basic</span>, and its response body says
      &ldquo;You did not provide an API key.&rdquo; All three are true. A docs-only pass picks
      one and looks wrong to anyone who knows another.</p>
      <p><strong>Composio's own Browser Tool.</strong> Documented as free and no-auth; returns
      403 <span class=mono>temporarily disabled by the administrator</span>. A
      documented-versus-actual availability gap, found inside the product this research is
      for.</p>
    </div>
  </div>
</div></section>

<footer><div class=wrap>
  <p>Built with Composio's SDK, remote workbench, search and fetch tools, and its public
  toolkit registry. Research executed in Composio's workbench; analysis, validation and this
  page generated by committed code in the repo &mdash;
  <span class=mono>python -m agent.run_research --app "Notion"</span> reproduces one row,
  <span class=mono>python -m agent.upgrade</span> reproduces the whole dataset,
  <span class=mono>python tests/test_agent.py</span> checks every rule described above.</p>
  <p><a href="{REPO_URL}">{REPO_URL}</a></p>
</div></footer>

<script id=payload type="application/json">{data_json}</script>
<script>
const D=JSON.parse(document.getElementById('payload').textContent);
const APPS=D.apps, LAB=D.labels;
const PILL={{'already-built':'p-already','build-now':'p-now','build-with-caveats':'p-caveat',
  'needs-outreach':'p-outreach','not-buildable':'p-not','unknown':'p-unknown'}};
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}})[c]);
const dash=v=>(v===''||v==null||v==='unknown')?'<span style="color:var(--faint)">&mdash;</span>':esc(v);

const SRC={{1:'vendor',2:'vendor code host',3:'third-party directory',4:'other',5:'no source'}};
function evBadge(f){{
  if(!f.g||f.g==='abstained') return '';
  // Two separate facts, two separate marks: WHERE it came from, and HOW WELL it checked out.
  const src=f.t<=2?'src-ok':f.t===3?'src-dir':'src-other';
  // A field computed by rule has nothing to quote, so it must not wear a failure mark.
  if(f.g==='not-asked-pass1'||f.g==='absence-claim')
    return `<span class="ev g-rule" title="${{esc(LAB[f.g]||f.g)}}">rule</span>`;
  const ok=f.g==='valid'||f.g==='registry-fact'||f.g==='unquoted-ok';
  const grade=ok?'g-ok':f.g==='near-miss'?'g-para':'g-bad';
  const mark=ok?'✓':f.g==='near-miss'?'≈':'✕';
  return `<span class="ev ${{grade}}" title="${{esc(LAB[f.g]||f.g)}}">${{mark}}</span>`
       + (f.t<=4?`<span class="ev ${{src}}" title="source: ${{esc(SRC[f.t]||f.t)}}">${{f.t<=2?'V':f.t===3?'D':'?'}}</span>`:'');
}}
function reasonWord(r){{
  return ({{'not-stated-publicly':'not published','retrieval-failed':'not searched',
    'quote-failed-validation':'evidence failed check',
    'evidence-about-another-product':'evidence was about another product',
    'not-applicable':'not applicable','unclassified':'undetermined'}})[r]||'undetermined';
}}

function detail(r){{
  const rows=Object.entries(r.f).map(([k,f])=>{{
    const src=f.u?`<a href="${{esc(f.u)}}" target=_blank rel=noopener>${{esc(f.u.slice(0,72))}}</a>`:'&mdash;';
    const note=f.r?` <span style="color:var(--faint)">[${{esc(f.r)}}]</span>`:'';
    return `<dt class=mono>${{esc(k)}}</dt><dd><strong>${{dash(f.v)}}</strong>${{evBadge(f)}}${{note}}`+
      (f.q?`<div class=q>&ldquo;${{esc(f.q)}}&rdquo;</div>`:'')+
      `<div class=mono style="color:var(--faint)">${{src}}</div></dd>`;
  }}).join('');
  const extra=[
    `<dt class=mono>access basis</dt><dd>${{esc(r.accWhy)}}</dd>`,
    `<dt class=mono>verdict basis</dt><dd>${{esc(r.bldWhy)}}</dd>`,
    r.brdN?`<dt class=mono>breadth</dt><dd>${{r.brdN}} ${{r.brdSrc==='composio-registry'?'tools in Composio':'operations in its OpenAPI spec'}} &rarr; ${{esc(r.brd)}}</dd>`:'',
    r.gate?`<dt class=mono>pricing probe</dt><dd>/pricing redirects into a sales flow</dd>`:'',
    r.dis.length?`<dt class=mono>disagreement</dt><dd style="color:var(--warn)">${{r.dis.map(esc).join('<br>')}}</dd>`:''
  ].join('');
  return `<tr class=detail><td colspan=8><div class=inner><dl class=dgrid>${{rows}}${{extra}}</dl></div></td></tr>`;
}}

let SHOW_ALL=false;
function render(list){{
  const tb=document.querySelector('#matrix tbody');
  const shown=SHOW_ALL?list:list.slice(0,20);
  tb.innerHTML=shown.map(r=>`<tr data-id=${{r.id}}>
    <td><strong>${{esc(r.app)}}</strong>
        <span class=catchip>${{esc(r.cat.split(' ')[0])}}</span>
        <div class=oneline>${{r.f.one_liner.v && r.f.one_liner.v!=='unknown'
          ? esc(r.f.one_liner.v.length>96 ? r.f.one_liner.v.slice(0,96)+'…' : r.f.one_liner.v)
          : '<span style="color:var(--faint)">no one-line description found</span>'}}</div>
        </td>
    <td>${{dash(r.f.auth_methods.v)}}${{evBadge(r.f.auth_methods)}}
        <div class=mono style="color:var(--faint)">${{esc(r.fam)}}</div></td>
    <td>${{r.acc!=='unknown' ? esc(r.acc)
        : '<span class=why>not stated in the docs we read</span>'}}</td>
    <td><span class="pill ${{PILL[r.bld]||'p-unknown'}}">${{esc(r.bld)}}</span></td>
    <td>${{r.blk && r.blk!=='none' && r.blk!=='unclear' ? esc(r.blk)
        : r.blk==='unclear' ? '<span class=why>undetermined</span>'
        : '<span class=why>none found</span>'}}</td>
    <td>${{r.brd!=='unknown' ? esc(r.brd)
        : '<span class=why>no tool count, no spec</span>'}}</td>
    <td>${{r.f.existing_mcp.v && r.f.existing_mcp.v!=='unknown'
        ? esc(r.f.existing_mcp.v)+evBadge(r.f.existing_mcp)
        : '<span class=why>'+esc(reasonWord(r.f.existing_mcp.r))+'</span>'}}</td>
    <td>${{r.cat_in?`<span class=mono style="color:var(--ok)">${{esc(r.slug)}}</span>
        <div class=mono style="color:var(--faint)">${{r.tools}} tools</div>`:
        '<span style="color:var(--faint)">&mdash;</span>'}}</td>
  </tr>`+detail(r)).join('');
  document.getElementById('count').textContent=
    (SHOW_ALL?list.length:Math.min(20,list.length))+' of '+list.length+' shown';
  const more=document.getElementById('more');
  more.style.display=list.length>20?'inline-block':'none';
  more.textContent=SHOW_ALL?'show fewer':`show all ${{list.length}}`;
  tb.querySelectorAll('tr[data-id]').forEach(tr=>tr.onclick=()=>{{
    const d=tr.nextElementSibling;
    d.style.display=d.style.display==='table-row'?'none':'table-row';
  }});
}}

const FILTERS={{
  all:()=>true, missing:r=>!r.cat_in,
  'build-now':r=>r.bld==='build-now', 'needs-outreach':r=>r.bld==='needs-outreach',
  quarantined:r=>r.qn>0
}};
let curF='all', curC='';
function apply(){{
  render(APPS.filter(r=>FILTERS[curF](r)&&(!curC||r.cat===curC)));
}}
document.querySelectorAll('[data-f]').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('[data-f]').forEach(x=>x.setAttribute('aria-pressed','false'));
  b.setAttribute('aria-pressed','true'); curF=b.dataset.f; apply();
}});
const sel=document.getElementById('catsel');
sel.innerHTML='<option value="">every category</option>'+D.cats.map(c=>`<option>${{esc(c)}}</option>`).join('');
sel.onchange=()=>{{curC=sel.value;apply();}};
document.getElementById('more').onclick=()=>{{SHOW_ALL=!SHOW_ALL;apply();}};

// The build queue: only the apps Composio does not already have.
const ORDER=['build-now','build-with-caveats','needs-outreach','not-buildable','unknown'];
const NOTE={{'build-now':'self-serve credentials and a documented API &mdash; start here',
 'build-with-caveats':'buildable, but a purchase, a review or an admin must happen first',
 'needs-outreach':'a human conversation before any code',
 'not-buildable':'no public API to wrap',
 'unknown':'we could not establish a documented interface &mdash; needs a person'}};
document.getElementById('queue').innerHTML=ORDER.map(v=>{{
  const set=APPS.filter(r=>!r.cat_in&&r.bld===v);
  if(!set.length) return '';
  return `<div style="margin:18px 0 0">
    <h3><span class="pill ${{PILL[v]}}">${{v}}</span>
      <span style="color:var(--faint);font-weight:400">&nbsp;${{set.length}} apps &mdash; ${{NOTE[v]}}</span></h3>
    <table style="margin-top:8px"><thead><tr><th>App</th><th>Category</th><th>Auth</th>
      <th>Access</th><th>Blocker</th><th>MCP already exists</th></tr></thead><tbody>
      ${{set.map(r=>`<tr><td><strong>${{esc(r.app)}}</strong></td><td class=mono>${{esc(r.cat)}}</td>
        <td>${{dash(r.fam)}}</td><td>${{dash(r.acc)}}</td>
        <td>${{r.blk && r.blk!=='none' && r.blk!=='unclear' ? esc(r.blk)
        : r.blk==='unclear' ? '<span class=why>undetermined</span>'
        : '<span class=why>none found</span>'}}</td>
        <td>${{r.f.existing_mcp.v==='official'?'<span style="color:var(--ok)">official</span>':dash(r.f.existing_mcp.v)}}</td></tr>`).join('')}}
    </tbody></table></div>`;
}}).join('');

apply();
</script>
</body></html>
"""


if __name__ == "__main__":
    print(build())
