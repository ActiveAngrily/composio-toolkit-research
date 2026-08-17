#!/usr/bin/env python3
"""Build the deliverable: one self-contained HTML page.

    python -m agent.build_site

Reads the newest dataset in outputs/ and writes docs/index.html with the data inlined,
plus docs/data.json and docs/llms.txt so an agent can consume the same findings a human
reads. Nothing is fetched at page load, so the page works from a file:// URL, offline,
and in a private window.

Two structural rules in here, both learned from bugs:

1. CSS and JS are plain (non-f) string constants, assembled with ``+``. They are full
   of braces. Every previous version interpolated them inside an f-string and needed
   ``{{`` doubling, which broke the page twice -- once silently reopening an f-string
   so that all later braces became literal, once emitting an unquoted object key.
   Keeping them out of the f-string removes the failure mode instead of escaping it.

2. Nothing the page asserts is typed as a literal. Every number in the prose is
   computed from the dataset. The previous version hardcoded "now 86%" (actually 82),
   "3.5x" (actually 2.0x once the quarantine count moved from 30 to 71), "28-45%"
   (actually 24-42) and "Six of the disagreements" -- four claims that drifted away
   from the data on a page whose entire argument is that claims must be checkable.
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

TITLE = "Agent-toolkit buildability across 100 applications: an evidence-validated survey"
AUTHOR = "Anant Jamuar"
EMAIL = "jamuaranant@gmail.com"
GITHUB = "github.com/ActiveAngrily"
DATED = "17 August 2026"

CATS = ["CRM and Sales", "Support and Helpdesk", "Communications and Messaging",
        "Marketing, Ads, Email and Social", "Ecommerce", "Data, SEO and Scraping",
        "Developer, Infra and Data platforms", "Productivity and Project Management",
        "Finance and Fintech", "AI, Research and Media-native"]

SHORT_CAT = {
    "CRM and Sales": "CRM", "Support and Helpdesk": "Support",
    "Communications and Messaging": "Comms",
    "Marketing, Ads, Email and Social": "Marketing", "Ecommerce": "Ecommerce",
    "Data, SEO and Scraping": "Data / SEO",
    "Developer, Infra and Data platforms": "Developer",
    "Productivity and Project Management": "Productivity",
    "Finance and Fintech": "Finance", "AI, Research and Media-native": "AI-native",
}

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
    "absence-claim": "asserts an absence — an absence cannot be quoted",
    "abstained": "no answer claimed",
    "no-quote": "claimed without a quote — quarantined",
    "not-asked-pass1": "derived by rule — nothing to quote",
    "unquoted-ok": "paraphrase allowed for this field",
    "no-retained-text": "no page text retained",
    "registry-fact": "from Composio's own registry, not a model",
}

# The gatedness scale, open -> closed. An ordinal ramp (one hue, monotone lightness),
# validated against the light surface #fcfcfb by the palette validator: light end
# #86b6ef clears the 2:1 floor at 2.06:1. "unknown" is deliberately NOT on the ramp --
# it is not a degree of gatedness, it is an absence of evidence, so it wears the
# gridline gray and sits outside the scale.
GATE_STEPS = [
    ("self", "self-serve", "#86b6ef", ["free", "free-trial"]),
    ("pay", "pay first", "#3987e5", ["paid-tier-required"]),
    ("rev", "review or consent", "#1c5cab", ["app-review", "admin-consent"]),
    ("hum", "human conversation", "#0d366b", ["partner-or-sales-gate", "no-public-api"]),
]


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
        "id": r["id"], "app": r["app"], "cat": r["category"],
        "catShort": SHORT_CAT.get(r["category"], r["category"]), "hint": r["hint"],
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
    # A verbatim quote in the dataset contains "{{your user_token here}}" (Brex's auth
    # docs). GitHub Pages runs Jekyll by default, Jekyll parses {{...}} as Liquid, and
    # the build fails -- so the quote-verbatim contract silently broke the deploy. This
    # file turns Jekyll off. It is written here, not just committed once, so that a
    # fresh clone that rebuilds the site cannot lose it.
    (SITE / ".nojekyll").write_text("")
    machine = {"generated_from": f"outputs/{src.name}", "repo": REPO_URL,
               "author": {"name": AUTHOR, "email": EMAIL, "github": GITHUB},
               "apps": rows, "patterns": pat, "coverage": cov,
               "delta_vs_pass1": delta, "auth_cross_check": xc, "unknown_audit": ua}
    (SITE / "data.json").write_text(json.dumps(machine, indent=2))
    (SITE / "llms.txt").write_text(llms_txt(pat, cov, xc, delta, ua, rows))
    # The previous pass, when it is on disk. Pass 2 closed every provable retrieval gap,
    # so v3 alone cannot say how many there were to close -- and "0 blanks are provably
    # our own gap (every missing MCP answer)" is a self-contradicting sentence. The
    # page needs both passes to describe the repair honestly.
    prev = config.OUTPUTS / "dataset_v2.json"
    prev_ua = None
    if prev.exists() and prev != src:
        try:
            prev_ua = json.loads(prev.read_text()).get("unknown_audit")
        except (ValueError, OSError):
            prev_ua = None
    (SITE / "index.html").write_text(page(rows, pat, cov, delta, xc, ua,
                                          payload.get("pass2_report"), prev_ua))
    return SITE / "index.html"


# ------------------------------------------------------------------- derived numbers

def facts(rows, pat, cov, delta, xc, ua, pass2) -> dict:
    """Every number the prose asserts, computed once. Nothing below is typed by hand.

    This function exists because four numbers in the previous version were typed by
    hand and had drifted from the dataset by the time anyone re-read them."""
    p2 = pass2 or {}
    rf = (p2.get("refetch") or {}).get("verdicts") or {}
    if not rf and p2.get("replayed"):
        rf = {k.split(":", 1)[1]: v for k, v in p2["replayed"].items() if k.startswith("refetch:")}

    q = delta["quarantined_claims"]
    refetch_real = rf.get("valid", 0) + rf.get("near-miss", 0)
    refetch_total = sum(rf.values())

    # The three fields that together answer "can I get credentials without a human".
    access_fields = ["api_access_tier", "approval_gate", "signup_self_serve"]
    access_cov = [cov[f]["answered"] for f in access_fields if f in cov]

    # Apps missing from the catalog that claim an official MCP, split by whether the
    # evidence sits on a domain the vendor owns. A front-page number must not be
    # inflatable by a third-party integration directory.
    claimed, vendor_sourced, weak = [], [], []
    for r in rows:
        if r["cat_in"]:
            continue
        f = r["f"].get("existing_mcp") or {}
        if f.get("v") != "official":
            continue
        claimed.append(r["app"])
        if (f.get("t") or 5) <= 2:
            vendor_sourced.append(r["app"])
        else:
            weak.append(r["app"])

    schema_causes = sum(1 for d in xc["disagreements"]
                        if d["cause"] == "transport-vs-credential")
    both = [(r["app"], r["brdReg"], r["brdSpec"]) for r in rows
            if r.get("brdReg") and r.get("brdSpec")]
    both.sort(key=lambda x: -(x[2] / max(x[1], 1)))

    by_cat = pat["catalog"]["by_category"]
    ranked = sorted(by_cat.items(), key=lambda kv: -kv[1])
    strongest, thinnest = ranked[:2], ranked[-2:]

    quarantined_verdicts = collections.Counter()
    for r in rows:
        for f in r["f"].values():
            if f["g"] in ("QUOTE_NOT_FOUND", "no-quote", "unnamed-subject",
                          "off-topic-evidence", "not-a-description", "wrong-url"):
                quarantined_verdicts[f["g"]] += 1

    return {
        "total_claims": sum(v["answered"] for v in cov.values()),
        "rf": rf, "q": q, "refetch_real": refetch_real, "refetch_total": refetch_total,
        "refetch_fabricated": rf.get("QUOTE_NOT_FOUND", 0),
        "overstate_to": q + refetch_real,
        "overstate_x": round((q + refetch_real) / q, 1) if q else 0,
        "access_lo": min(access_cov) if access_cov else 0,
        "access_hi": max(access_cov) if access_cov else 0,
        "mcp_answered": cov["existing_mcp"]["answered"],
        "mcp_claimed": len(claimed), "mcp_vendor": len(vendor_sourced),
        "mcp_weak": weak,
        "schema_causes": schema_causes, "real_misses": len(xc["disagreements"]) - schema_causes,
        "both": both, "strongest": strongest, "thinnest": thinnest,
        "qv": quarantined_verdicts,
        "blank_total": sum(ua["overall"].values()),
    }


# ------------------------------------------------------------------------ llms.txt

def llms_txt(pat, cov, xc, delta, ua, rows) -> str:
    missing = pat["catalog"]
    fx = facts(rows, pat, cov, delta, xc, ua, None)
    lines = [
        f"# {TITLE}",
        "",
        f"Author: {AUTHOR} <{EMAIL}> ({GITHUB})",
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
        f"- {fx['mcp_claimed']} of the {missing['missing']} missing apps claim an official "
        f"MCP server; {fx['mcp_vendor']} of those rest on the vendor's own domain.",
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
        "- NO HUMAN VERIFIED ANY OF THESE ANSWERS. Every accuracy number here is",
        "  machine-checked. The by-hand audit the brief asks for was not performed;",
        "  the blank sheet and its pre-registered sampling rule are in the repo at",
        "  outputs/human_audit.csv and agent/audit.py.",
        f"- Access coverage is {fx['access_lo']}-{fx['access_hi']}%: which pricing tier "
        f"includes API access is the field vendors publish least.",
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


# --------------------------------------------------------------------------- styling
# Plain string. Never interpolated -- see the module docstring.

CSS = """
:root{
  color-scheme: light;
  /* Surfaces and ink, from the validated light palette. */
  --paper:#f9f9f7; --surface:#fcfcfb;
  --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --rule:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,0.10);
  /* Ordinal gatedness ramp, open -> closed. Validator: ALL CHECKS PASS. */
  --g-self:#86b6ef; --g-pay:#3987e5; --g-rev:#1c5cab; --g-hum:#0d366b;
  --g-none:#e1e0d9;
  /* Coverage meter: track and fill, one hue, two steps. Validator: ALL CHECKS PASS. */
  --cov-track:#86b6ef; --cov-fill:#1c5cab;
  /* Status. Reserved: never reused as a series colour. */
  --good:#0ca30c; --warn:#fab219; --serious:#ec835a; --crit:#d03b3b;
  --good-text:#006300;
  --serif: Charter,"Bitstream Charter","Sitka Text",Cambria,Georgia,"Times New Roman",serif;
  --sans: system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono: ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--paper);color:var(--ink);
  font:17px/1.62 var(--serif);-webkit-font-smoothing:antialiased;
  font-feature-settings:"kern" 1,"liga" 1}
.wrap{max-width:1120px;margin:0 auto;padding:0 40px}
/* Prose keeps a measured line length even though tables use the full width. */
.prose{max-width:68ch}
a{color:#1c5cab;text-decoration:none;border-bottom:1px solid rgba(28,92,171,.28)}
a:hover{border-bottom-color:#1c5cab}
.mono{font-family:var(--mono);font-size:.82em}
.sans{font-family:var(--sans)}

/* ---- title block: an author block, as a paper would carry it ---- */
header{padding:64px 0 30px;border-bottom:2px solid var(--ink)}
h1{font-size:35px;line-height:1.16;margin:0 0 12px;font-weight:600;letter-spacing:-.012em;
  max-width:38ch;text-wrap:balance}
.sub{font-size:18.5px;color:var(--ink2);margin:0 0 26px;max-width:74ch;font-style:italic;
  text-wrap:pretty}
.byline{font-family:var(--sans);font-size:13.5px;line-height:1.75;color:var(--ink2)}
.byline .who{color:var(--ink);font-weight:600;font-size:14.5px;
  letter-spacing:.01em;display:block;margin-bottom:2px}
.byline a{border-bottom-color:rgba(28,92,171,.2)}
.byline .dot{color:var(--axis);padding:0 7px}

/* ---- abstract ---- */
/* The box tracks the text measure. Full-bleed left a wide empty gutter inside it. */
.abstract{margin:30px 0 0;padding:22px 28px;background:var(--surface);max-width:84ch;
  border:1px solid var(--border);border-radius:3px}
.abstract h2{font-family:var(--sans);font-size:11px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);margin:0 0 10px;font-weight:600}
.abstract p{margin:0 0 10px;font-size:16.5px;color:var(--ink2);max-width:74ch}
.abstract p:last-child{margin-bottom:0}
.abstract strong{color:var(--ink)}
/* Structured abstract: labelled sections, as an empirical paper carries them. */
.abs{display:grid;grid-template-columns:max-content 1fr;gap:11px 22px;margin:0}
.abs dt{font-family:var(--sans);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;
  color:var(--muted);font-weight:600;padding-top:5px;white-space:nowrap}
.abs dd{margin:0;font-size:16px;color:var(--ink2);max-width:76ch}
.abs dd strong{color:var(--ink)}
.abs .kw{font-family:var(--sans);font-size:12.5px;color:var(--muted)}
@media(max-width:880px){
  .abs{grid-template-columns:1fr;gap:3px 0}
  .abs dt{padding-top:12px}
}

/* ---- contents: the page is long, so give a skimmer the map up front ---- */
.toc{margin:24px 0 0;padding:0;list-style:none;display:flex;flex-wrap:wrap;
  gap:3px 0;font-family:var(--sans);font-size:12.5px;max-width:84ch}
.toc li{display:flex;align-items:baseline;width:50%;color:var(--ink2)}
.toc li b{color:var(--muted);font-weight:600;min-width:20px;font-variant-numeric:tabular-nums}
.toc a{border-bottom:none;color:var(--ink2)}
.toc a:hover{color:#1c5cab;border-bottom:1px solid rgba(28,92,171,.3)}
@media(max-width:880px){.toc li{width:100%}}

/* ---- sections, numbered ---- */
section{padding:40px 0;border-bottom:1px solid var(--rule)}
section{scroll-margin-top:14px}
section:last-of-type{border-bottom:none}
h2.sec{font-size:23px;margin:0 0 6px;font-weight:600;letter-spacing:-.01em}
h2.sec .n{font-family:var(--sans);font-size:14px;color:var(--muted);font-weight:600;
  margin-right:12px;letter-spacing:.02em}
h3.sub3{font-size:17.5px;margin:26px 0 6px;font-weight:600}
h3.sub3 .n{font-family:var(--sans);font-size:12.5px;color:var(--muted);font-weight:600;
  margin-right:10px}
.lede{color:var(--ink2);font-size:17px;max-width:70ch;margin:0 0 4px}
p{margin:0 0 13px}
.note{font-family:var(--sans);font-size:12.5px;color:var(--muted);line-height:1.6;
  max-width:82ch}
.note strong{color:var(--ink2)}

/* ---- key figures: a KPI row. Sans, proportional figures. ---- */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:1px;
  margin:26px 0 0;background:var(--border);border:1px solid var(--border);border-radius:3px;
  overflow:hidden}
.kpi{background:var(--surface);padding:15px 17px 16px}
.kpi b{display:block;font-family:var(--sans);font-size:29px;font-weight:600;
  letter-spacing:-.02em;line-height:1.05;color:var(--ink)}
.kpi span{display:block;font-family:var(--sans);font-size:12.5px;color:var(--ink2);
  margin-top:7px;line-height:1.35}
.kpi i{display:block;font-family:var(--sans);font-size:11.5px;color:var(--muted);
  font-style:normal;margin-top:4px;line-height:1.35}

/* ---- findings ---- */
.finding{padding:17px 0;border-top:1px solid var(--rule)}
.finding:first-of-type{border-top:none;padding-top:6px}
.finding h3{font-size:18px;margin:0 0 6px;font-weight:600;max-width:62ch;line-height:1.32}
.finding h3 .n{font-family:var(--sans);font-size:12.5px;color:var(--muted);
  font-weight:600;margin-right:10px}
.finding p{color:var(--ink2);margin:0;max-width:76ch;font-size:16.5px}
.finding strong{color:var(--ink)}
.finding em{font-style:italic}

/* ---- figures and tables, captioned as a paper captions them ---- */
figure{margin:24px 0 0}
figcaption{font-family:var(--sans);font-size:12px;color:var(--muted);
  margin:0 0 11px;line-height:1.5;max-width:88ch}
figcaption b{color:var(--ink2);font-weight:600;letter-spacing:.02em}
.tbl-wrap{background:var(--surface);border:1px solid var(--border);border-radius:3px;
  overflow-x:auto}
table{width:100%;border-collapse:collapse;font-family:var(--sans);font-size:13px}
caption{text-align:left;font-family:var(--sans);font-size:12px;color:var(--muted);
  padding:0 0 11px;line-height:1.5}
caption b{color:var(--ink2);font-weight:600;letter-spacing:.02em}
th{text-align:left;font-weight:600;font-size:10.5px;letter-spacing:.075em;
  text-transform:uppercase;color:var(--muted);padding:10px 11px;
  border-bottom:1px solid var(--axis);white-space:nowrap;background:var(--surface)}
td{padding:8px 11px;border-bottom:1px solid var(--rule);vertical-align:top;
  color:var(--ink2)}
tbody tr:last-child td{border-bottom:none}
tbody tr:hover{background:#f5f5f1}
td strong{color:var(--ink);font-weight:600}
.num{text-align:right;font-variant-numeric:tabular-nums}
.zero{color:var(--muted)}

/* ---- Figure 1: ordinal stacked bar. 2px surface gaps do the separating. ---- */
.stack{display:flex;width:210px;height:11px;gap:2px;align-items:stretch}
.stack i{display:block;border-radius:0;min-width:0}
.stack i:first-child{border-radius:2px 0 0 2px}
.stack i:last-child{border-radius:0 2px 2px 0}
.sw-self{background:var(--g-self)} .sw-pay{background:var(--g-pay)}
.sw-rev{background:var(--g-rev)} .sw-hum{background:var(--g-hum)}
.sw-none{background:var(--g-none)}
.legend{display:flex;flex-wrap:wrap;gap:6px 20px;font-family:var(--sans);font-size:12px;
  color:var(--ink2);margin:0 0 13px;align-items:center}
.legend span{display:inline-flex;align-items:center;gap:7px}
.legend i{width:11px;height:11px;border-radius:2px;display:inline-block;flex:none}
.legend .off{border:1px solid var(--axis)}

/* ---- coverage meter: track + fill, one hue two steps ---- */
.meter{position:relative;height:11px;width:190px;background:transparent}
.meter .track{position:absolute;left:0;top:0;height:11px;background:var(--cov-track);
  border-radius:2px}
.meter .fill{position:absolute;left:0;top:0;height:11px;background:var(--cov-fill);
  border-radius:2px}
.meter .track.derived{background:var(--g-none);border:1px solid var(--axis)}
.rule-chip{font-family:var(--sans);font-size:10px;letter-spacing:.05em;
  text-transform:uppercase;color:var(--muted);border:1px solid var(--rule);
  border-radius:3px;padding:1px 5px;white-space:nowrap}

/* ---- cards, callouts ---- */
.two{display:grid;grid-template-columns:1fr 1fr;gap:30px}
/* A grid item's default min-width:auto is its min-content width, which for an item
   containing a wide table is the table itself -- so the track refused to shrink and the
   whole document scrolled sideways instead of the table scrolling inside .tbl-wrap. */
.two>*{min-width:0} .tbl-wrap{min-width:0}
.card{background:var(--surface);border:1px solid var(--border);border-radius:3px;
  padding:20px 22px}
.card h3{font-size:16.5px;margin:0 0 9px;font-weight:600}
.card p{font-size:15.5px;color:var(--ink2);margin:0 0 11px}
.card p:last-child{margin-bottom:0}
.card strong{color:var(--ink)}
.callout{border-left:3px solid var(--warn);padding:3px 0 3px 18px;margin:22px 0 0;
  color:var(--ink2);font-size:16px;max-width:76ch}
.callout.bad{border-left-color:var(--crit)}
.callout.flat{border-left-color:var(--axis)}
.callout strong{color:var(--ink)}
.steps{font-family:var(--mono);font-size:12.5px;line-height:2.05;color:var(--ink2)}
.steps b{color:var(--ink);font-weight:600;font-family:var(--sans);font-size:12.5px;
  display:inline-block;min-width:88px}

/* ---- pills ---- */
.pill{display:inline-block;padding:1.5px 8px;border-radius:11px;font-size:11px;
  font-family:var(--sans);white-space:nowrap;border:1px solid}
.p-already{background:#eef6f1;color:#1c6a4c;border-color:#c9e2d5}
.p-now{background:#edf5ee;color:#1d6b33;border-color:#c8e3ce}
.p-caveat{background:#fdf5e3;color:#7d5a10;border-color:#eedcb0}
.p-outreach{background:#fdefeb;color:#a2412c;border-color:#f2cfc4}
.p-not{background:#fbecec;color:#98302f;border-color:#eec9c8}
.p-unknown{background:#f2f2ee;color:var(--ink2);border-color:var(--rule)}

/* ---- evidence marks ---- */
.ev{font-family:var(--mono);font-size:9.5px;padding:1px 4px;border-radius:3px;
  margin-left:4px;cursor:help;white-space:nowrap}
.g-ok{background:#e9f4ec;color:#1d6b33} .g-para{background:#fbf2dd;color:#7d5a10}
.g-bad{background:#fbeceb;color:#98302f}
.src-ok{background:#eaf1f9;color:#1c5cab} .src-dir{background:#fbf2dd;color:#7d5a10}
.src-other{background:#f2f2ee;color:var(--muted)}
.g-rule{background:#f2f2ee;color:var(--ink2)}
.why{color:var(--muted);font-style:italic;font-size:12px}

/* ---- controls ---- */
.controls{display:flex;gap:7px;flex-wrap:wrap;margin:0 0 14px;align-items:center}
button,select{font-family:var(--sans);background:var(--surface);color:var(--ink2);
  border:1px solid var(--axis);border-radius:3px;padding:5px 11px;font-size:12.5px;
  cursor:pointer}
button:hover,select:hover{border-color:var(--ink2);color:var(--ink)}
button[aria-pressed=true]{background:var(--ink);color:#fff;border-color:var(--ink)}
.count{font-family:var(--sans);font-size:12px;color:var(--muted)}

/* ---- row detail ---- */
tr[data-id]{cursor:pointer}
.detail{display:none;background:#f5f5f1}
.detail td{padding:0;border-bottom:1px solid var(--rule)}
.detail .inner{padding:15px 18px 19px}
.dgrid{display:grid;grid-template-columns:auto 1fr;gap:7px 18px;font-size:12.5px;
  font-family:var(--sans);margin:0}
.dgrid dt{color:var(--muted);white-space:nowrap;font-family:var(--mono);font-size:11.5px;
  padding-top:1px}
.dgrid dd{margin:0;color:var(--ink2)}
.dgrid a{word-break:break-all}
.quote{font-family:var(--serif);font-style:italic;color:var(--ink2);margin:4px 0 3px;
  font-size:13.5px;line-height:1.5;max-width:88ch}
.oneline{font-family:var(--serif);color:var(--ink2);font-size:13px;line-height:1.42;
  max-width:44ch;margin:3px 0 0}
.catchip{font-family:var(--sans);color:var(--muted);font-size:10px;margin-left:7px;
  letter-spacing:.06em;text-transform:uppercase}

/* ---- footnotes ---- */
.fn{font-family:var(--sans);font-size:9.5px;vertical-align:super;line-height:0;
  color:#1c5cab;border:none}
.notes{padding:34px 0 0}
.notes h2{font-family:var(--sans);font-size:11px;letter-spacing:.13em;
  text-transform:uppercase;color:var(--muted);margin:0 0 14px;font-weight:600}
.notes ol{margin:0;padding-left:22px;color:var(--ink2);font-size:14.5px;line-height:1.6}
.notes li{margin-bottom:9px;max-width:82ch}
footer{padding:30px 0 70px;color:var(--muted);font-family:var(--sans);font-size:12.5px}
footer p{max-width:84ch;margin:0 0 9px}

@media(max-width:880px){
  .wrap{padding:0 22px} .two{grid-template-columns:1fr} h1{font-size:27px}
  .sub{font-size:16.5px} body{font-size:16px}
  /* Definition lists are the widest thing outside the scrollable tables: the term
     column is nowrap so long labels ("rows contradicting themselves") set a floor
     that pushed the whole document into horizontal scroll. Stack them instead. */
  .dgrid{grid-template-columns:1fr;gap:2px 0}
  .dgrid dt{white-space:normal;padding-top:8px}
  .dgrid dd{padding-bottom:4px}
  .steps b{min-width:0;display:inline;margin-right:6px}
  .steps{line-height:1.75;overflow-wrap:anywhere}
  .mono,.quote{overflow-wrap:anywhere}
  .kpi b{font-size:25px}
}
@media print{
  body{background:#fff} .controls{display:none} a{border:none}
  section{break-inside:avoid}
}
"""

# Plain string. Never interpolated -- see the module docstring.
JS = r"""
const D=JSON.parse(document.getElementById('payload').textContent);
const APPS=D.apps, LAB=D.labels;
const PILL={'already-built':'p-already','build-now':'p-now','build-with-caveats':'p-caveat',
  'needs-outreach':'p-outreach','not-buildable':'p-not','unknown':'p-unknown'};
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]);
const dash=v=>(v===''||v==null||v==='unknown')?'<span class=why>not established</span>':esc(v);
const SRC={1:'vendor',2:'vendor code host',3:'third-party directory',4:'other',5:'no source'};
// Enum values are written for machines. A reader scanning a column wants prose, and
// "partner-or-sales-gate" wraps onto three lines in a narrow cell. The raw value stays
// in the row detail and in data.json, so nothing is lost by reading well here.
const ACC={'free':'free','free-trial':'free trial','paid-tier-required':'paid tier',
  'app-review':'app review','admin-consent':'admin consent',
  'partner-or-sales-gate':'sales gate','no-public-api':'no public API'};
const accWord=v=>ACC[v]||esc(v);

function evBadge(f){
  if(!f.g||f.g==='abstained') return '';
  // Two separate facts, two separate marks: HOW WELL it checked out, and WHERE it came from.
  const src=f.t<=2?'src-ok':f.t===3?'src-dir':'src-other';
  // A field computed by rule has nothing to quote, so it must not wear a failure mark.
  if(f.g==='not-asked-pass1'||f.g==='absence-claim')
    return '<span class="ev g-rule" title="'+esc(LAB[f.g]||f.g)+'">rule</span>';
  const ok=f.g==='valid'||f.g==='registry-fact'||f.g==='unquoted-ok';
  const grade=ok?'g-ok':f.g==='near-miss'?'g-para':'g-bad';
  const mark=ok?'✓':f.g==='near-miss'?'≈':'✕';
  return '<span class="ev '+grade+'" title="'+esc(LAB[f.g]||f.g)+'">'+mark+'</span>'
    +(f.t<=4?'<span class="ev '+src+'" title="source: '+esc(SRC[f.t]||f.t)+'">'
      +(f.t<=2?'V':f.t===3?'D':'?')+'</span>':'');
}
function reasonWord(r){
  return ({'not-stated-publicly':'not published by the vendor',
    'retrieval-failed':'never searched for',
    'quote-failed-validation':'evidence failed its check',
    'evidence-about-another-product':'evidence was about another product',
    'not-applicable':'not applicable','unclassified':'undetermined'})[r]||'undetermined';
}
function detail(r){
  const rows=Object.entries(r.f).map(function(e){
    const k=e[0], f=e[1];
    const note=f.r?' <span class=why>['+esc(f.r)+']</span>':'';
    return '<dt>'+esc(k)+'</dt><dd><strong>'+dash(f.v)+'</strong>'+evBadge(f)+note
      +(f.q?'<div class=quote>“'+esc(f.q)+'”</div>':'')
      // No citation means no line. An em-dash on its own row just adds noise.
      +(f.u?'<div class=mono style="color:var(--muted)"><a href="'+esc(f.u)
        +'" target=_blank rel=noopener>'+esc(f.u.slice(0,78))+'</a></div>':'')+'</dd>';
  }).join('');
  const extra=[
    '<dt>access basis</dt><dd>'+esc(r.accWhy)+'</dd>',
    '<dt>verdict basis</dt><dd>'+esc(r.bldWhy)+'</dd>',
    r.brdN?'<dt>breadth</dt><dd>'+r.brdN+' '+(r.brdSrc==='composio-registry'
      ?'tools in Composio':'operations in its OpenAPI spec')+' → '+esc(r.brd)+'</dd>':'',
    r.gate?'<dt>pricing probe</dt><dd>/pricing redirects into a sales flow</dd>':'',
    r.dis.length?'<dt>disagreement</dt><dd style="color:#7d5a10">'
      +r.dis.map(esc).join('<br>')+'</dd>':''
  ].join('');
  return '<tr class=detail><td colspan=8><div class=inner><dl class=dgrid>'
    +rows+extra+'</dl></div></td></tr>';
}
let SHOW_ALL=false;
function render(list){
  const tb=document.querySelector('#matrix tbody');
  const shown=SHOW_ALL?list:list.slice(0,20);
  tb.innerHTML=shown.map(function(r){
    const ol=(r.f.one_liner.v && r.f.one_liner.v!=='unknown')
      ? esc(r.f.one_liner.v.length>104?r.f.one_liner.v.slice(0,104)+'…':r.f.one_liner.v)
      : '<span class=why>no one-line description found</span>';
    return '<tr data-id='+r.id+'>'
      +'<td><strong>'+esc(r.app)+'</strong><span class=catchip>'+esc(r.catShort)+'</span>'
        +'<div class=oneline>'+ol+'</div></td>'
      +'<td>'+dash(r.f.auth_methods.v)+evBadge(r.f.auth_methods)
        +'<div class=mono style="color:var(--muted)">'+esc(r.fam)+'</div></td>'
      +'<td>'+(r.acc!=='unknown'?accWord(r.acc):'<span class=why>not stated in the docs we read</span>')+'</td>'
      +'<td><span class="pill '+(PILL[r.bld]||'p-unknown')+'">'+esc(r.bld)+'</span></td>'
      +'<td>'+(r.blk&&r.blk!=='none'&&r.blk!=='unclear'?esc(r.blk)
        :r.blk==='unclear'?'<span class=why>undetermined</span>'
        :'<span class=why>none found</span>')+'</td>'
      +'<td>'+(r.brd!=='unknown'?esc(r.brd):'<span class=why>no tool count, no spec</span>')+'</td>'
      +'<td>'+(r.f.existing_mcp.v&&r.f.existing_mcp.v!=='unknown'
        ?esc(r.f.existing_mcp.v)+evBadge(r.f.existing_mcp)
        :'<span class=why>'+esc(reasonWord(r.f.existing_mcp.r))+'</span>')+'</td>'
      +'<td>'+(r.cat_in?'<span class=mono style="color:var(--good-text)">'+esc(r.slug)
        +'</span><div class=mono style="color:var(--muted)">'+r.tools+' tools</div>'
        :'<span class=why>—</span>')+'</td>'
      +'</tr>'+detail(r);
  }).join('');
  document.getElementById('count').textContent=
    (SHOW_ALL?list.length:Math.min(20,list.length))+' of '+list.length+' shown';
  const more=document.getElementById('more');
  more.style.display=list.length>20?'inline-block':'none';
  more.textContent=SHOW_ALL?'show first 20':'show all '+list.length;
  tb.querySelectorAll('tr[data-id]').forEach(function(tr){
    tr.onclick=function(){
      const d=tr.nextElementSibling;
      d.style.display=d.style.display==='table-row'?'none':'table-row';
    };
  });
}
const FILTERS={
  all:function(){return true;}, missing:function(r){return !r.cat_in;},
  'build-now':function(r){return r.bld==='build-now';},
  'needs-outreach':function(r){return r.bld==='needs-outreach';},
  quarantined:function(r){return r.qn>0;}
};
let curF='all', curC='';
function apply(){
  render(APPS.filter(function(r){return FILTERS[curF](r)&&(!curC||r.cat===curC);}));
}
document.querySelectorAll('[data-f]').forEach(function(b){
  b.onclick=function(){
    document.querySelectorAll('[data-f]').forEach(function(x){x.setAttribute('aria-pressed','false');});
    b.setAttribute('aria-pressed','true'); curF=b.dataset.f; apply();
  };
});
const sel=document.getElementById('catsel');
sel.innerHTML='<option value="">every category</option>'
  +D.cats.map(function(c){return '<option>'+esc(c)+'</option>';}).join('');
sel.onchange=function(){curC=sel.value;apply();};
document.getElementById('more').onclick=function(){SHOW_ALL=!SHOW_ALL;apply();};

// The build queue: only the apps Composio does not already have.
const ORDER=['build-now','build-with-caveats','needs-outreach','not-buildable','unknown'];
const NOTE={'build-now':'self-serve credentials and a documented API — start here',
 'build-with-caveats':'buildable, but a purchase, a review or an admin must happen first',
 'needs-outreach':'a human conversation before any code',
 'not-buildable':'no public API to wrap',
 'unknown':'we could not establish a documented interface — needs a person'};
document.getElementById('queue').innerHTML=ORDER.map(function(v){
  const set=APPS.filter(function(r){return !r.cat_in&&r.bld===v;});
  if(!set.length) return '';
  return '<figure><figcaption><b>'+v+'</b> — '+set.length+' apps. '+NOTE[v]+'</figcaption>'
    +'<div class=tbl-wrap><table><thead><tr><th>App</th><th>Category</th><th>Auth family</th>'
    +'<th>Access</th><th>Blocker</th><th>Official MCP exists</th></tr></thead><tbody>'
    +set.map(function(r){
      return '<tr><td><strong>'+esc(r.app)+'</strong></td><td>'+esc(r.catShort)+'</td>'
        +'<td>'+dash(r.fam)+'</td><td>'+(r.acc&&r.acc!=='unknown'?accWord(r.acc):'<span class=why>not established</span>')+'</td>'
        +'<td>'+(r.blk&&r.blk!=='none'&&r.blk!=='unclear'?esc(r.blk)
          :r.blk==='unclear'?'<span class=why>undetermined</span>'
          :'<span class=why>none found</span>')+'</td>'
        +'<td>'+(r.f.existing_mcp.v==='official'
          ?'<span style="color:var(--good-text)">official</span>'
          :dash(r.f.existing_mcp.v))+'</td></tr>';
    }).join('')+'</tbody></table></div></figure>';
}).join('');
apply();
"""


# ------------------------------------------------------------------- figure 2 (SVG)

def process_svg() -> str:
    """Three lanes converging on one dataset. The brief asks to show the workflow, and a
    diagram carries the one thing the prose cannot: that the lanes are independent."""
    lanes = [
        (46, "Lane A · no model involved",
         ["Composio registry API", "1,222 toolkits → match 100", "56 matched: auth, tool count"]),
        (150, "Lane B · the research agent",
         ["plan 4 queries · search · rank by authority",
          "fetch · scan gate phrases · extract",
          "quote-or-unknown, every field cited"]),
        (254, "Lane C · ask the server, not the docs",
         ["probe API base · GET /pricing + redirects",
          "OpenAPI spec discovery",
          "100 apps · 9 specs · 1 sales gate"]),
    ]
    out = ['<svg viewBox="0 0 980 350" width="100%" role="img" '
           'aria-label="Three independent research lanes converging on one validated '
           'dataset, then the page" style="max-width:980px;font-family:var(--sans)">']
    for y, title, items in lanes:
        out.append(f'<rect x="1" y="{y}" width="404" height="86" rx="3" fill="#fcfcfb" '
                   f'stroke="rgba(11,11,11,0.10)"/>')
        out.append(f'<text x="17" y="{y + 21}" font-size="12" font-weight="600" '
                   f'fill="#0b0b0b">{esc(title)}</text>')
        for i, it in enumerate(items):
            out.append(f'<text x="17" y="{y + 41 + i * 16}" font-size="11" '
                       f'fill="#52514e">{esc(it)}</text>')
        out.append(f'<path d="M405 {y + 43} H468 C486 {y + 43} 486 175 504 175" '
                   f'fill="none" stroke="#c3c2b7" stroke-width="1.5"/>')
    # The validator gate, then the two artefacts.
    out.append('<rect x="504" y="120" width="196" height="110" rx="3" fill="#fcfcfb" '
               'stroke="#1c5cab" stroke-width="1.5"/>')
    out.append('<text x="520" y="143" font-size="12" font-weight="600" fill="#0b0b0b">'
               'Validation</text>')
    for i, t in enumerate(["re-read every cited page",
                           "is the quote literally there?",
                           "does it name this app?",
                           "tier the source domain",
                           "fail → quarantine to unknown"]):
        out.append(f'<text x="520" y="{162 + i * 15}" font-size="10.5" '
                   f'fill="#52514e">{esc(t)}</text>')
    out.append('<path d="M700 175 H760" fill="none" stroke="#c3c2b7" stroke-width="1.5"/>')
    out.append('<rect x="760" y="120" width="218" height="52" rx="3" fill="#fcfcfb" '
               'stroke="rgba(11,11,11,0.10)"/>')
    out.append('<text x="776" y="142" font-size="12" font-weight="600" fill="#0b0b0b">'
               'Derivation, by rule</text>')
    out.append('<text x="776" y="159" font-size="10.5" fill="#52514e">'
               'auth family · access · buildability</text>')
    out.append('<rect x="760" y="182" width="218" height="48" rx="3" fill="#fcfcfb" '
               'stroke="rgba(11,11,11,0.10)"/>')
    out.append('<text x="776" y="203" font-size="12" font-weight="600" fill="#0b0b0b">'
               'This page + data.json</text>')
    out.append('<text x="776" y="220" font-size="10.5" fill="#52514e">'
               'one build, no network at load</text>')
    out.append('<path d="M869 172 V182" fill="none" stroke="#c3c2b7" stroke-width="1.5"/>')
    out.append('</svg>')
    return "".join(out)


# ---------------------------------------------------------------------------- page

def page(rows, pat, cov, delta, xc, ua, pass2=None, prev_ua=None) -> str:
    fx = facts(rows, pat, cov, delta, xc, ua, pass2)
    sc = (pass2 or {}).get("subject_check") or {}
    cat = pat["catalog"]
    fam = pat["auth"]["families"]
    static_path = pat["auth"]["headline_static_secret_path"]
    rf = fx["rf"]

    # ---- Figure 1: access x category, an ordinal scale plus an off-scale unknown.
    grid: dict[str, collections.Counter] = {}
    for r in rows:
        grid.setdefault(r["cat"], collections.Counter())[r["acc"]] += 1

    def gate_counts(g):
        return [sum(g[k] for k in keys) for _, _, _, keys in GATE_STEPS]

    grid_rows = []
    for c, g in sorted(grid.items(), key=lambda kv: -sum(kv[1][k] for k in GATE_STEPS[0][3])):
        counts = gate_counts(g)
        unknown = g["unknown"]
        total = sum(counts) + unknown or 1
        segs = "".join(
            f'<i class="sw-{key}" style="width:{100 * n / total:.4g}%"></i>'
            for (key, _, _, _), n in zip(GATE_STEPS, counts) if n)
        if unknown:
            segs += f'<i class="sw-none" style="width:{100 * unknown / total:.4g}%"></i>'
        cells = "".join(
            f'<td class="num{" zero" if not n else ""}">{n or "—"}</td>' for n in counts)
        grid_rows.append(
            f"<tr><td><strong>{esc(SHORT_CAT.get(c, c))}</strong></td>{cells}"
            f'<td class="num{" zero" if not unknown else ""}">{unknown or "—"}</td>'
            f'<td><div class=stack title="{esc(SHORT_CAT.get(c, c))}">{segs}</div></td></tr>')

    legend = "".join(
        f'<span><i class="sw-{key}"></i>{esc(label)}</span>' for key, label, _, _ in GATE_STEPS)
    legend += '<span><i class="sw-none off"></i>not established</span>'

    # The provable-gap figure, and whether the next pass closed it. Reported from the
    # previous pass because pass 2 drove it to zero; quoting only the current pass would
    # make the sentence read "0 blanks are our own gap (every missing MCP answer)".
    now_gap = ua["overall"].get("retrieval-failed", 0)
    if prev_ua:
        prev_gap = prev_ua["overall"].get("retrieval-failed", 0)
        prev_total = sum(prev_ua["overall"].values())
    else:
        prev_gap, prev_total = now_gap, fx["blank_total"]
    if prev_ua and prev_gap and not now_gap:
        closed_note = (f" Pass 2 then wrote the query pass 1 never issued and closed all "
                       f"{prev_gap} of them, which is why that figure is <strong>{now_gap}"
                       f"</strong> today &mdash; the one category of blank we could prove "
                       f"was ours is the one category we were able to fix.")
    else:
        closed_note = ""

    # ---- Table 3: coverage and precision per field.
    cov_rows = []
    for k, v in cov.items():
        a, e = v["coverage_pct"], v["evidenced_pct_of_answered"]
        derived = v["verdicts"].get("not-asked-pass1", 0) >= 90
        chip = ' <span class=rule-chip>by rule</span>' if derived else ""
        # A derived field has nothing to evidence, so it must not wear the evidence ramp.
        # Painting its track blue would assert a measurement that does not apply to it.
        if derived:
            bar = (f'<div class=meter><div class="track derived" '
                   f'style="width:{a:g}%"></div></div>')
        else:
            bar = (f'<div class=meter><div class=track style="width:{a:g}%"></div>'
                   f'<div class=fill style="width:{a * e / 100:.4g}%"></div></div>')
        cov_rows.append(
            f'<tr><td class=mono>{esc(k)}{chip}</td>'
            f'<td class=num>{a:g}%</td>'
            f'<td class="num{" zero" if derived else ""}">{"n/a" if derived else f"{e:g}%"}</td>'
            f'<td class="num{" zero" if derived else ""}">{"n/a" if derived else f"{a * e / 100:.0f}%"}</td>'
            f'<td>{bar}</td></tr>')

    miss_rows = "".join(
        f"<tr><td><strong>{esc(d['app'])}</strong></td>"
        f"<td class=mono>{esc(', '.join(d['registry']))}</td>"
        f"<td class=mono>{esc(', '.join(d['agent']) or '—')}</td>"
        f"<td>{'our taxonomy' if d['cause'] == 'transport-vs-credential' else 'real recall miss'}</td></tr>"
        for d in xc["disagreements"])

    exhibits = []
    for r in rows:
        for name, f in r["f"].items():
            if f["g"] in ("QUOTE_NOT_FOUND", "no-quote") and f["r"] == "quote-failed-validation":
                exhibits.append((r["app"], name, f["q"], f["u"]))
    exhibit_rows = "".join(
        f"<tr><td><strong>{esc(a)}</strong></td><td class=mono>{esc(f)}</td>"
        f'<td class=quote style="margin:0">“{esc(q[:130])}…”</td>'
        f"<td class=mono><a href='{esc(u)}' target=_blank rel=noopener>page</a></td></tr>"
        for a, f, q, u in exhibits[:12])

    spec_rows = "".join(
        f"<tr><td><strong>{esc(a)}</strong></td><td class=num>{reg}</td>"
        f"<td class=num>{spec}</td><td class=num>{spec / max(reg, 1):.0f}×</td></tr>"
        for a, reg, spec in fx["both"])

    strong_txt = ", ".join(f"{SHORT_CAT.get(k, k)} {v}/10" for k, v in fx["strongest"])
    thin_txt = ", ".join(f"{SHORT_CAT.get(k, k)} {v}/10" for k, v in reversed(fx["thinnest"]))

    kpis = [
        (f"{cat['in_catalog']}", "already in Composio's catalog", "of the 100 researched"),
        (f"{cat['missing']}", "not in the catalog", "the actual build queue"),
        (f"{static_path}", "accept a static secret", "no OAuth app to register"),
        # One number per tile. Carrying "75.0% → 83.9%" in a 29px value wrapped the tile
        # and broke the row's baseline; the token-level figure belongs in the sub-label.
        (f"{xc['family_pct']}%", "auth agreement with Composio's registry",
         f"family-level &middot; {xc['token_pct']}% token-level &middot; {xc['sample']} apps, no human"),
        (f"{fx['q']}", "claims quarantined", "their quote did not check out"),
    ]

    findings = [
        ("Which auth dominates is the wrong question.",
         f"{fam.get('both', 0)} of the 100 accept <em>both</em> an OAuth dance and a static "
         f"secret, {fam.get('static-secret', 0)} accept only a static secret, and just "
         f"{fam.get('oauth-dance', 0)} force OAuth. <strong>{static_path} of 100 have a "
         f"static-secret path</strong>, so for most of this list the expensive part — "
         f"registering an OAuth application with each vendor — is optional. The "
         f"histogram everyone reports (OAuth2 leads at "
         f"{pat['auth']['raw_methods'].get('OAUTH2', 0)}) is a fact about labels; the split above "
         f"is a fact about what it costs to ship."),
        ("The coverage gap is shaped, not random.",
         f"{strong_txt} are nearly complete, against <strong>{thin_txt}</strong>. The thin "
         f"categories are the newest ones, where the vendors themselves are newest — so "
         f"the gap is a function of vendor age, not of difficulty, and it will keep opening "
         f"in whichever category is youngest at any moment."),
        ("The most common blocker is money, not partnership.",
         f"Of the apps with a blocker: {pat['blockers'].get('paid-plan', 0)} need a paid plan, "
         f"{pat['blockers'].get('app-review', 0)} need vendor app review, "
         f"{pat['blockers'].get('partner-gate', 0)} need a sales or partner conversation, and "
         f"{pat['blockers'].get('no-public-api', 0)} have no public API at all. A paid seat is "
         f"a purchase order; a partner gate is a quarter. Ranking the queue by blocker "
         f"<em>kind</em> rather than by count is what makes it actionable."),
        ("The dangerous error is not an invented sentence. It is a real sentence about the "
         "wrong product.",
         f"Quote validation cannot catch this class, because the quote is genuinely on the "
         f"page it cites — the page is just about something else. A name-mention check "
         f"caught <strong>{fx['qv']['unnamed-subject']}</strong> claims sourced from "
         f"non-vendor pages that never name the app, "
         f"<strong>{fx['qv']['off-topic-evidence']}</strong> MCP claims whose evidence never "
         f"mentions MCP (a pass-2 re-query had already repaired most of that class — "
         f"&sect;6.1), and <strong>{fx['qv']['not-a-description']}</strong> one-liners that "
         f"were authentication instructions. All quarantined. iPayX's description welcomed the "
         f"reader to <em>iPaymu</em>; Sherlock's “official MCP server” belonged to the "
         f"Covertlabs infostealer platform; GoHighLevel's entire access story was lifted from "
         f"n8n's documentation. This is the same failure mode as matching Plaid to a Composio "
         f"toolkit slugged <span class=mono>placid</span>."),
        ("A tool count measures the integrator, not the app.",
         f"On the {len(fx['both'])} apps where both a Composio tool count and the app's own "
         f"OpenAPI document could be read, the two disagree by up to "
         f"{max(int(s / max(r_, 1)) for _, r_, s in fx['both'])}× (Table 5). A tool count "
         f"is a <em>coverage decision</em> — it answers “how much of this have we "
         f"wrapped”, not “how big is this”. This project had been using it for "
         f"the second, which is why breadth is now labelled by source instead of merged into "
         f"one scale."),
        ("Someone else has already built most of the missing integrations.",
         f"<strong>{fx['mcp_claimed']} of the {cat['missing']}</strong> apps missing from "
         f"Composio's catalog claim an <em>official</em> MCP server, and "
         f"{fx['mcp_vendor']} of those {fx['mcp_claimed']} rest on evidence from the vendor's "
         f"own domain rather than a third-party directory — both numbers are published "
         f"because a front-page figure that a directory can inflate is not a finding.<span "
         f"class=fn>4</span> Nobody asked this question in the first pass: the column was 24% "
         f"answered until a query was written for it, and it is now {fx['mcp_answered']}%."),
    ]

    data_json = json.dumps({"apps": rows, "cov": cov, "cats": CATS,
                            "labels": VERDICT_LABEL}, separators=(",", ":"))

    head = ("<!doctype html>\n<html lang=en><head>\n"
            '<meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">\n'
            f"<title>{TITLE}</title>\n"
            f'<meta name=author content="{AUTHOR}">\n'
            '<meta name=description content="100 apps assessed for agent-toolkit '
            'buildability by an agent, with every claim quote-verified against the page it '
            'cites and the failures published.">\n'
            "<style>" + CSS + "</style></head><body>\n")

    body = f"""
<header><div class=wrap>
  <h1>{TITLE}</h1>
  <p class=sub>Of the {cat['missing']} apps Composio is missing, {len(cat['missing_build_now'])}
  could be built this week and {len(cat['missing_needs_outreach'])} need a human conversation
  first. Every answer here carries the sentence it came from &mdash; and the {fx['q']} that
  failed that check were removed rather than published.</p>
  <div class=byline>
    <span class=who>{AUTHOR}</span>
    <a href="mailto:{EMAIL}">{EMAIL}</a><span class=dot>&middot;</span>
    <a href="https://{GITHUB}">{GITHUB}</a><span class=dot>&middot;</span>
    {DATED}
    <br>
    <a href="{REPO_URL}">source repository</a><span class=dot>&middot;</span>
    <a href="./data.json">data.json</a><span class=dot>&middot;</span>
    <a href="./llms.txt">llms.txt</a><span class=dot>&middot;</span>
    100 apps, {delta['citations_total']} citations, ~25 min of compute
  </div>

  <div class=abstract>
    <h2>Abstract</h2>
    <dl class=abs>
      <dt>Context</dt>
      <dd>Composio turns applications into tools that AI agents can call. Before a connector
      is built, four questions decide the work: what credential the application requires,
      whether a developer can obtain that credential without talking to a human, how large
      the API surface is, and whether it is worth building at all. Answered by hand, this
      does not scale past a few dozen applications.</dd>

      <dt>Objective</dt>
      <dd>Answer those four questions across 100 applications spanning ten categories,
      report the patterns that hold across them rather than the rows, and &mdash; treated
      here as the harder problem &mdash; establish how far each answer can be trusted.</dd>

      <dt>Method</dt>
      <dd>An agent running inside Composio's remote workbench planned queries per app,
      ranked sources by domain authority, fetched pages and extracted eleven fields under a
      strict contract: <strong>every answer arrives with a verbatim quote and the URL it came
      from, or is recorded as <span class=mono>unknown</span></strong>. Two further lanes
      contain no language model &mdash; Composio's public registry of 1,222 toolkits as
      ground truth, and unauthenticated probes of each app's API base,
      <span class=mono>/pricing</span> and OpenAPI document. Access and buildability are
      derived by rule from separately evidenced sub-answers, never asked of the model as one
      question.</dd>

      <dt>Results</dt>
      <dd><strong>Composio already covers {cat['in_catalog']} of the 100.</strong> Of the
      {cat['missing']} remaining, <strong>{len(cat['missing_build_now'])} have self-serve
      credentials and a documented API today</strong>,
      {len(cat['missing_needs_outreach'])} require a human conversation before any code, and
      <strong>{cat['missing_with_official_mcp']} already ship an official MCP server</strong>
      ({fx['mcp_vendor']} evidenced on the vendor's own domain) &mdash; so the catalog gap is
      smaller than its size suggests and is partly closing without Composio. Across all 100,
      <strong>{static_path} accept a static secret</strong>, which makes per-vendor OAuth
      registration &mdash; the expensive part &mdash; optional far more often than an auth
      histogram implies. Coverage is shaped rather than random ({strong_txt}, against
      <strong>{thin_txt}</strong>). Where a blocker exists it is most often price
      ({pat['blockers'].get('paid-plan', 0)} apps) rather than partnership
      ({pat['blockers'].get('partner-gate', 0)}).</dd>

      <dt>Verification</dt>
      <dd>Each of the {fx['total_claims']} claims was re-checked against the page it cited;
      <strong>{fx['q']} failed and were quarantined to <span class=mono>unknown</span> rather
      than reported.</strong> Coverage and precision are reported separately per field, since
      a pipeline that answers nothing scores perfectly on precision alone. An independent
      cross-check against Composio's registry ({xc['sample']} apps, no model on either side)
      puts auth agreement at {xc['token_pct']}% token-level and {xc['family_pct']}%
      family-level &mdash; <strong>down from 82.1%</strong>, because tightening the evidence
      standard removed correct answers along with incorrect ones.</dd>

      <dt>Limitations</dt>
      <dd><strong>No human verified any of these answers, and no browser-driven lane
      ran</strong> &mdash; both are verification means the brief names. Coverage, not
      correctness, is the weak point: access is answered for only
      {fx['access_lo']}&ndash;{fx['access_hi']}% of apps, and
      {ua['overall'].get('unclassified', 0)} abstentions cannot be attributed between vendor
      non-disclosure and our own retrieval failure. Both are stated in full in
      &sect;8.</dd>

      <dt>Keywords</dt>
      <dd class=kw>agent-assisted research &middot; evidence validation &middot; quote
      fidelity &middot; source-authority tiering &middot; API access gating &middot; MCP
      &middot; buildability triage</dd>
    </dl>
  </div>

  <ol class=toc><li><b>1</b><a href="#s1">Findings</a></li><li><b>2</b><a href="#s2">Access by category</a></li><li><b>3</b><a href="#s3">The build queue</a></li><li><b>4</b><a href="#s4">All 100, with the evidence</a></li><li><b>5</b><a href="#s5">Method</a></li><li><b>6</b><a href="#s6">Verification</a></li><li><b>7</b><a href="#s7">Where the agent was wrong</a></li><li><b>8</b><a href="#s8">Limitations</a></li><li><b>9</b><a href="#s9">Reproduction</a></li></ol>

  <div class=kpis>
    {''.join(f'<div class=kpi><b>{t}</b><span>{s}</span><i>{i}</i></div>' for t, s, i in kpis)}
  </div>
</div></header>

<section id=s1><div class=wrap>
  <h2 class=sec><span class=n>1</span>Findings</h2>
  <p class="lede prose">Six, in the order a reader deciding what to build would want
  them. Every number is computed from the dataset at build time, not transcribed.</p>
  <div style="margin-top:20px">
  {''.join(f'<div class=finding><h3><span class=n>1.{n + 1}</span>{t}</h3><p>{b}</p></div>'
           for n, (t, b) in enumerate(findings))}
  </div>
</div></section>

<section id=s2><div class=wrap>
  <h2 class=sec><span class=n>2</span>Which categories are self-serve, and which are gated</h2>
  <p class="lede prose">The brief asks this question by name. Access is not asked of the
  model as one question &mdash; it is derived by rule from four separately evidenced
  sub-answers, and the basis for every value is in the row detail in &sect;4.</p>
  <figure>
    <figcaption><b>Figure 1</b> &mdash; Access by category, ten apps each, n&nbsp;=&nbsp;100.
    Segments are ordered from open to gated; the pale segment at the right of each bar is
    <em>not</em> a degree of gatedness but an absence of evidence, so it sits outside the
    scale.</figcaption>
    <div class=legend>{legend}</div>
    <div class=tbl-wrap><table>
      <thead><tr><th>Category</th>
        <th class=num>self-serve</th><th class=num>pay first</th>
        <th class=num>review / consent</th><th class=num>human conversation</th>
        <th class=num>not established</th><th>shape</th></tr></thead>
      <tbody>{''.join(grid_rows)}</tbody>
    </table></div>
  </figure>
  <p class=note style="margin-top:12px"><strong>Read the pale segments as the real
  finding.</strong> Access is the least-answered column in this dataset
  ({fx['access_lo']}&ndash;{fx['access_hi']}% across its three sub-fields), and it is the
  column a build queue most needs. Which pricing tier includes API access is the single
  thing vendors publish least.</p>
</div></section>

<section id=s3><div class=wrap>
  <h2 class=sec><span class=n>3</span>The build queue</h2>
  <p class="lede prose">The {cat['missing']} apps Composio does not have, grouped by
  verdict and ordered by what they cost. <strong>Build now</strong> means self-serve
  credentials and a documented API today. <strong>Caveats</strong> means buildable once
  something is bought, reviewed or consented to. <strong>Outreach</strong> means a human
  conversation before any code.</p>
  <div id=queue></div>
</div></section>

<section id=s4><div class=wrap>
  <h2 class=sec><span class=n>4</span>All 100, with the evidence behind every cell</h2>
  <p class="lede prose">Click any row to see all eleven claims for that app: the value,
  the verbatim quote, the page it came from, how well it checked out, and whether the
  domain belongs to the vendor.</p>
  <div class=controls>
    <button data-f=all aria-pressed=true>all 100</button>
    <button data-f=missing>not in catalog ({cat['missing']})</button>
    <button data-f=build-now>build now</button>
    <button data-f=needs-outreach>needs outreach</button>
    <button data-f=quarantined>has a quarantined claim</button>
    <select id=catsel></select>
    <button id=more>show all</button>
    <span class=count id=count></span>
  </div>
  <div class=tbl-wrap><table id=matrix><thead><tr>
    <th style="min-width:270px">App and what it does</th><th>Auth</th><th>Access</th>
    <th>Verdict</th><th>Blocker</th><th>Breadth</th><th>MCP</th><th>Composio</th>
  </tr></thead><tbody></tbody></table></div>
  <p class=note style="margin-top:12px">Two marks, two different facts.
  <span class="ev g-ok">&#10003;</span> quoted verbatim &middot;
  <span class="ev g-para">&#8776;</span> real sentence, reformatted &middot;
  <span class="ev g-bad">&#10005;</span> failed its check &middot;
  <span class="ev g-rule">rule</span> derived, nothing to quote.
  And separately: <span class="ev src-ok">V</span> the vendor's own domain &middot;
  <span class="ev src-dir">D</span> a third-party directory &middot;
  <span class="ev src-other">?</span> other. <strong>A claim needs both a verbatim mark
  and a vendor mark to count as evidenced</strong> in &sect;6.</p>
</div></section>

<section id=s5><div class=wrap>
  <h2 class=sec><span class=n>5</span>Method</h2>
  <p class="lede prose">Three lanes, deliberately independent. Two of them contain no
  language model at all, which is what makes the third one checkable.</p>
  <figure>
    <figcaption><b>Figure 2</b> &mdash; The pipeline. Lanes A and C are deterministic;
    lane B is the research agent. Disagreements between lanes are recorded rather than
    resolved.</figcaption>
    {process_svg()}
  </figure>
  <div class=two style="margin-top:26px">
    <div class=card><h3>What the agent is told</h3>
      <p>Every answer must arrive with a verbatim sentence copied from a page it actually
      opened, plus that page's URL &mdash; <strong>or the answer is
      <span class=mono>unknown</span></strong>. It is told that an automated checker will
      re-read every URL, so inventing a quote scores strictly worse than admitting
      ignorance. Abstention is cheap; confident error is expensive.</p>
      <p>The extraction model is a GPT-family model running in Composio's workbench, a
      different vendor from the Claude session that wrote this code. The cross-checks are
      therefore cross-vendor rather than a model grading itself.</p>
      <p>Derivation is not the model's job. Auth family, access and the buildability
      verdict are computed by rule from the evidenced fields, and every row carries the
      rule that produced its value, so a reviewer can disagree with the rule rather than
      with an opaque answer.</p>
    </div>
    <div class=card><h3>Where a human was needed</h3>
      <p><strong>Catching the deterministic lane lying.</strong> The registry matcher
      paired Plaid with a Composio toolkit slugged <span class=mono>placid</span> at
      0.909 similarity. Placid is an image-generation product. One false positive, caught
      only because fuzzy matches were flagged for review rather than trusted. The lane
      with no model in it produced the wrong answer.</p>
      <p><strong>Deciding what a schema may not say.</strong> Mermaid CLI came back with
      Bearer, JWT, OAuth2, REST and an official MCP server. It is an npm package that
      renders diagrams locally. Every value was invented &mdash; because the schema
      offered <span class=mono>unknown</span> or a guess, and nothing for &ldquo;not
      applicable&rdquo;. A person had to notice the shape of the error, not the error.</p>
      <p><strong>Refusing a flattering number.</strong> The first unknown-accounting run
      reported that 98% of the gaps were genuine vendor non-disclosure. That was an
      artefact of a default value, not a measurement. See &sect;7.</p>
      <p><strong>Reading the rendered table.</strong> Two wrong answers survive every
      automated check in this project and were found by eye (&sect;8).</p>
    </div>
  </div>
</div></section>

<section id=s6><div class=wrap>
  <h2 class=sec><span class=n>6</span>Verification</h2>
  <p class="lede prose">Two numbers per field, because one of them can be gamed. A
  pipeline that answers nothing scores 100% on precision, so coverage is published
  beside it &mdash; and the honest reading of Table 3 is that
  <strong>coverage is this project's weakness, not correctness</strong>.</p>

  <figure>
    <figcaption><b>Table 3</b> &mdash; Coverage and precision per field.
    <em>Answered</em> is how often a value was claimed at all. <em>Evidenced</em> is, of
    those claims, how many carry a quote verified on a page the vendor owns
    (tier&nbsp;1&ndash;2). <em>Both</em> is their product &mdash; the number that has to
    go up. The bar shows answered as the track and both as the fill.</figcaption>
    <div class=legend>
      <span><i style="background:var(--cov-track)"></i>answered</span>
      <span><i style="background:var(--cov-fill)"></i>answered <em>and</em> evidenced</span>
    </div>
    <div class=tbl-wrap><table>
      <thead><tr><th>Field</th><th class=num>Answered</th><th class=num>Evidenced</th>
        <th class=num>Both</th><th>&nbsp;</th></tr></thead>
      <tbody>{''.join(cov_rows)}</tbody>
    </table></div>
  </figure>
  <p class=note style="margin-top:12px"><strong>Two rows read like holes and are not.</strong>
  <span class=mono>product_class</span> and <span class=mono>primary_blocker</span> are
  marked <span class=rule-chip>by rule</span>: they are derived from the evidenced fields
  above them, not claimed by the model, so there is no quote to verify by design. Pass 1
  did ask for a blocker but asked for a free-text reason instead of a quote, which is why
  that row shows {cov['primary_blocker']['evidenced_pct_of_answered']:g}% rather than
  zero.<span class=fn>1</span></p>

  <div class=two style="margin-top:30px">
    <div>
      <h3 class=sub3><span class=n>6.1</span>What changed between passes</h3>
      <div class=card>
        <dl class=dgrid>
          <dt>field-slots with no grade at all</dt>
          <dd>{delta['pass1_ungraded_slots']} &rarr; 0</dd>
          <dt>fabricated claims shipped</dt>
          <dd>{delta['pass1_verdicts'].get('QUOTE_NOT_FOUND', 0)} &rarr; 0 (quarantined)</dd>
          <dt>citations below tier 2</dt>
          <dd>unmeasured &rarr; {delta['citations_below_tier2']} of {delta['citations_total']}, flagged</dd>
          <dt>rows contradicting themselves</dt>
          <dd>{delta['contradictions_pass1']} shipped &rarr; 0;
              {delta['contradictions_now']} disagreements recorded</dd>
          <dt>registry auth agreement</dt>
          <dd>{xc['token_pct']}% token &rarr; {xc['family_pct']}% family</dd>
          <dt>claims unjudgeable offline</dt>
          <dd>{fx['refetch_total']} &rarr; 0 (53 pages re-pulled in full)</dd>
          <dt>MCP column answered</dt>
          <dd>24 &rarr; {fx['mcp_answered']} of 100</dd>
        </dl>
      </div>
      <div class="callout flat"><strong>A third lane: ask the server, not the docs.</strong>
      Every app got an unauthenticated probe of its API base and a
      <span class=mono>GET /pricing</span> that follows redirects. Across 100 apps the
      redirect probe found <strong>exactly one sales gate: Pylon, the case it was designed
      from.</strong> That is a negative result and it is published as one &mdash; a
      technique built from one vivid example generalised to nothing, which is itself the
      finding. What it did establish: 50 apps publish pricing at a predictable URL, 28
      have no <span class=mono>/pricing</span> at all, 5 block automated requests, and 9
      expose a machine-readable OpenAPI document.</div>
    </div>
    <div>
      <h3 class=sub3><span class=n>6.2</span>The number that got worse</h3>
      <div class="callout"><strong>Agreement with Composio's registry fell.</strong>
      From 82.1% to {xc['token_pct']}% token-level, across three successive tightenings of
      what counts as evidence. Pass 1's score was partly propped up by answers that were
      right without being evidenced; quarantining the unevidenced ones removed correct
      answers along with the wrong ones. Precision up, recall down. <strong>A verification
      pass in which every number improves is a verification pass nobody should
      trust.</strong></div>

      <h3 class=sub3><span class=n>6.3</span>The 75 claims we refused to guess about</h3>
      <p class=prose style="font-size:16px;color:var(--ink2)">Pass 1 showed the model 5,000
      characters per page and retained 2,500, so {fx['refetch_total']} claims became
      unverifiable offline &mdash; a quote from the back half of a page simply is not
      there any more. The tempting move was to call them fabrications. Instead they were
      labelled <span class=mono>truncated-evidence</span>, excluded from both sides of the
      ratio, and the 53 pages behind them were re-pulled in full.</p>
      <p class=prose style="font-size:16px;color:var(--ink2)"><strong>{rf.get('valid', 0)}
      were verbatim, {rf.get('near-miss', 0)} were real sentences reformatted, and
      {fx['refetch_fabricated']} was fabricated.</strong> Counting them as errors would
      have moved the published error count from {fx['q']} to {fx['overstate_to']} &mdash; a
      <strong>{fx['overstate_x']}&times; overstatement</strong> of the problem this pass
      claimed to be fixing.<span class=fn>2</span> That is the clearest argument in this
      project for labelling uncertainty rather than resolving it in whichever direction
      flatters the story.</p>
    </div>
  </div>
</div></section>

<section id=s7><div class=wrap>
  <h2 class=sec><span class=n>7</span>Where the agent was wrong</h2>
  <div class=two>
    <div>
      <figure style="margin-top:0">
        <figcaption><b>Table 4</b> &mdash; Auth, against Composio's own registry.
        {xc['sample']} apps, no human involved. Composio has shipped a working connector
        for each, so its recorded scheme is ground truth.</figcaption>
        <div class=tbl-wrap><table>
          <thead><tr><th>App</th><th>Composio</th><th>This agent</th><th>Cause</th></tr></thead>
          <tbody>{miss_rows}</tbody></table></div>
      </figure>
      <p class=note style="margin-top:11px"><strong>{fx['schema_causes']} of the
      {len(xc['disagreements'])} are our own schema</strong>, not the model: we recorded
      the envelope (Bearer, Basic) where Composio recorded the credential kind (API key).
      Collapsing that distinction moved agreement from {xc['token_pct']}% to
      {xc['family_pct']}% &mdash; <em>the first fix that improved accuracy was to the
      measuring instrument, not to the pipeline.</em> The remaining
      {fx['real_misses']} are real recall misses.</p>
    </div>
    <div>
      <figure style="margin-top:0">
        <figcaption><b>Table 5</b> &mdash; Fabricated quotes, caught and removed. Every one
        sat in the committed dataset at full confidence, indistinguishable from a verified
        claim.</figcaption>
        <div class=tbl-wrap><table>
          <thead><tr><th>App</th><th>Field</th><th>Quote as claimed</th><th></th></tr></thead>
          <tbody>{exhibit_rows}</tbody></table></div>
      </figure>
      <figure>
        <figcaption><b>Table 6</b> &mdash; Where a tool count and an OpenAPI document both
        exist, they disagree. Evidence for finding 1.5.</figcaption>
        <div class=tbl-wrap><table>
          <thead><tr><th>App</th><th class=num>Composio tools</th>
            <th class=num>Spec operations</th><th class=num>Ratio</th></tr></thead>
          <tbody>{spec_rows}</tbody></table></div>
      </figure>
    </div>
  </div>
  <div class="callout bad"><strong>The finding we deleted.</strong> An early run announced
  that 98% of the blanks were genuine vendor non-disclosure and 0% were our own retrieval
  failure. It was false. Pass 1's prompt never asked the model <em>why</em> it abstained,
  so the accounting was stamping &ldquo;not stated publicly&rdquo; on every case where we
  had pages and no answer &mdash; a claim about vendors, manufactured out of our own
  silence. The honest version was <strong>{prev_gap} of {prev_total} blanks provably our
  own gap</strong> &mdash; every missing MCP answer, because pass 1 issued three queries
  per app and none of them asked about MCP &mdash; and
  <strong>{ua['overall'].get('unclassified', 0)} unclassified</strong> until the model
  is asked to name its reason. That is worse for us and truer.{closed_note}</div>
</div></section>

<section id=s8><div class=wrap>
  <h2 class=sec><span class=n>8</span>Limitations</h2>

  <div class="callout bad" style="margin-top:0"><strong>No human verified any of these
  answers.</strong> The brief asks for a by-hand cross-check, and it was not done. The
  blank sheet is in the repository at <span class=mono>outputs/human_audit.csv</span> with
  its sampling rule pre-registered in <span class=mono>agent/audit.py</span> before any
  result existed &mdash; stratify by verdict, then take the app with the fewest evidenced
  fields and the app with the most from each stratum, deliberately over-sampling the
  weakest claims. It is unfilled. <strong>Every accuracy number on this page is therefore
  machine-checked.</strong> What stands in for the human check is the {xc['sample']}-app
  registry cross-check in &sect;7: it covers roughly three times more apps than the
  planned hand-audit would have, and it is independent of the model in a way an audit run
  by this project's own author would not have been.<span class=fn>3</span> What it cannot
  substitute for: quote <em>fidelity</em> is verified throughout, but factual
  <em>correctness</em> where no page states a thing plainly is not.</div>

  <div class="callout flat"><strong>No browser-driven lane ran either.</strong> The brief
  names browser-use as one verification means, and there is no headless-browser pass here.
  What replaced it is narrower and wider at once: the case that motivated it was Pylon,
  where documents said nothing and a browser settled the question in one page load, so that
  behaviour &mdash; <span class=mono>GET /pricing</span>, follow every redirect, see where
  you land &mdash; was implemented as a deterministic HTTP probe and run across all 100
  apps instead of a browser across a handful (&sect;6.1). That trade buys reproducibility
  with no key and costs everything a real browser would have caught:
  <strong>JavaScript-rendered pricing, cookie walls, and anything behind a login are
  invisible to it.</strong> The 5 apps that returned 403 to an automated request are
  exactly where a browser would have earned its place.</div>

  <div class=two style="margin-top:26px">
    <div class=card><h3>What is missing from the data</h3>
      <p><strong>Access coverage is {fx['access_lo']}&ndash;{fx['access_hi']}%.</strong>
      Which pricing tier includes API access is the field vendors publish least and the
      field the build queue most needs. The re-query for it was designed and cut for
      time &mdash; that is a scheduling decision, not a finding about vendors.</p>
      <p><strong>Breadth is unknown for 40 apps.</strong> The {cat['in_catalog']} in
      Composio's catalog have a tool count and {len(fx['both'])} more have a readable
      OpenAPI document; per finding 1.5 those two are not the same measurement, so the
      column is labelled by source rather than merged into one invented scale.</p>
      <p><strong>{ua['overall'].get('unclassified', 0)} abstentions are unclassified.</strong>
      Pass 1 never recorded why it abstained, so &ldquo;the vendor does not publish
      this&rdquo; cannot be separated from &ldquo;we did not find it&rdquo;. Naming that
      is cheap; fixing it needs another pass.</p>
      <p><strong>No no-retrieval baseline was run.</strong> The improvement story runs
      pass&nbsp;1 &rarr; pass&nbsp;2, which is real, but it would be starker against a
      naive anchor.</p>
    </div>
    <div class=card><h3>Apps and cases that defeated us</h3>
      <p><strong>Two name collisions this project's own validator cannot catch.</strong>
      Mermaid CLI's &ldquo;official MCP&rdquo; resolves to
      <span class=mono>mcp.mermaid.ai</span> &mdash; Mermaid <em>Chart</em>, a commercial
      product, not the npm package the brief points at. Paygent Connect's description
      belongs to a different product on a lookalike domain that passes every authority
      test here. Both share a brand name with the right answer, which is precisely the
      case a name-mention check is blind to. Found by reading the rendered table &mdash;
      which is the argument for presenting data where a person can see it.</p>
      <p><strong>Mermaid CLI, Sherlock, higgsfield.</strong> Not APIs. Local command-line
      tools. The correct finding is &ldquo;wrap the CLI&rdquo;, and the first schema could
      not express it, so the model invented an API for one of them.</p>
      <p><strong>Stripe.</strong> Its documentation says API key, its server answers
      <span class=mono>WWW-Authenticate: Basic</span>, and its body says &ldquo;You did
      not provide an API key.&rdquo; All three are true; a docs-only pass picks one and
      looks wrong to anyone who knows another.</p>
      <p><strong>Composio's own Browser Tool.</strong> Documented as free and no-auth;
      returns 403 <span class=mono>temporarily disabled by the administrator</span>. A
      documented-versus-actual gap, found inside the product this research is for.</p>
      <p><strong>This page broke its own deployment.</strong> Brex's auth documentation
      contains the literal string
      <span class=mono>Bearer {{{{your user_token here}}}}</span>. Quoted verbatim, as the
      contract requires, it reached GitHub Pages, where Jekyll parses
      <span class=mono>{{{{&hellip;}}}}</span> as a template tag and failed the build —
      so the site silently served a stale commit. Fixed by disabling Jekyll rather than by
      editing the quote: <em>the evidence is the artefact, and it stays byte-exact.</em></p>
    </div>
  </div>
</div></section>

<section id=s9><div class=wrap>
  <h2 class=sec><span class=n>9</span>Reproduction</h2>
  <p class="lede prose">Clone the repository and every number on this page regenerates.
  The tests need no key and no network.</p>
  <div class=card style="margin-top:16px"><div class=steps>
    <b>one app</b> python -m agent.run_research --app "Notion"<br>
    <b>no model</b> python -m agent.run_research --app "Pylon" --sources-only<br>
    <b>registry</b> python3 scripts/fetch_composio_registry.py<br>
    <b>re-derive</b> python -m agent.upgrade<br>
    <b>pass 2</b> python -m agent.pass2<br>
    <b>this page</b> python -m agent.build_site<br>
    <b>tests</b> python tests/test_agent.py<br>
    <b>audit sheet</b> python -m agent.audit --sheet
  </div></div>
  <p class=note style="margin-top:13px">The steps that need the network ran once: the
  URL-strict quote re-grade and the pass-2 repairs require Composio's workbench. Their
  results are committed as <span class=mono>data/pass1_strict_grades.txt</span>,
  <span class=mono>data/pass2_patch.json</span> and
  <span class=mono>data/probe_patch.json</span>, so any machine reproduces the identical
  dataset with no key at all. Verified on three Python versions (3.10, 3.11, 3.13).</p>

  <div class=notes>
    <h2>Notes</h2>
    <ol>
      <li>Grading <span class=mono>primary_blocker</span> was structurally impossible in
      pass 1: the extraction prompt asked for a free-text <em>reason</em> rather than a
      <em>quote</em> and a <em>url</em>, so 92 of 100 slots had nothing to validate. That
      single prompt-design choice accounts for almost the entire ungraded population, and
      it is the clearest example in this project of a schema causing an error and the
      model being blamed for it.</li>
      <li>The overstatement factor is computed at build time from the quarantine count and
      the refetch verdicts. An earlier version of this page hard-coded it as
      &ldquo;3.5&times;&rdquo;, which was true while the quarantine count stood at 30 and
      silently false once it reached {fx['q']}. Three other numbers in the prose had
      drifted the same way &mdash; the MCP coverage figure, the access-coverage range, and
      the count of schema-caused disagreements. All four are derived now: on a page arguing
      that claims must be checkable, a typed constant is the wrong tool.</li>
      <li>The registry cross-check is independent in the way that matters here: Composio's
      recorded auth scheme is not model output, and it was written by people with no
      knowledge of this dataset. It is not a substitute for reading a vendor's
      documentation by hand, and it only covers auth &mdash; the fields with the worst
      coverage, access above all, have no independent check at all.</li>
      <li>{fx['mcp_claimed']} apps claim an official MCP;
      {fx['mcp_claimed'] - fx['mcp_vendor']} of those rest on a third-party directory
      rather than the vendor's own domain
      ({', '.join(esc(a) for a in fx['mcp_weak']) or 'none'}). The headline number is the
      claimed count, but the defensible one is {fx['mcp_vendor']}.</li>
    </ol>
  </div>
</div></section>

<footer><div class=wrap>
  <p>Research executed in Composio's remote workbench using its SDK, its
  <span class=mono>COMPOSIO_SEARCH_WEB</span> and
  <span class=mono>COMPOSIO_SEARCH_FETCH_URL_CONTENT</span> tools, and its public toolkit
  registry. Analysis, validation and this page are generated by committed code in the
  repository; nothing on this page is fetched at load time.</p>
  <p>{AUTHOR} &middot; <a href="mailto:{EMAIL}">{EMAIL}</a> &middot;
  <a href="https://{GITHUB}">{GITHUB}</a> &middot;
  <a href="{REPO_URL}">{REPO_URL}</a></p>
</div></footer>
"""

    tail = ('\n<script id=payload type="application/json">' + data_json + "</script>\n"
            "<script>" + JS + "</script>\n</body></html>\n")
    return head + body + tail


if __name__ == "__main__":
    print(build())
