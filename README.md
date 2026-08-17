# 100 apps → an agent toolkit build queue

Composio take-home (AI Product Ops). 100 apps researched for agent-toolkit buildability
by an agent, with every claim quote-verified against the page it came from.

**Live page:** https://activeangrily.github.io/composio-toolkit-research/

---

## Run it

```bash
git clone https://github.com/ActiveAngrily/composio-toolkit-research
cd composio-toolkit-research
cp .env.example .env        # add your COMPOSIO_API_KEY

# one app, end to end — the fastest way to see what this does
python -m agent.run_research --app "Notion"

# retrieval + probes only, no LLM needed
python -m agent.run_research --app "Pylon" --sources-only

# Composio's own toolkit registry: which of the 100 they already cover
python3 scripts/fetch_composio_registry.py

# re-derive the whole dataset from the recorded pass-1 evidence, offline
python -m agent.upgrade

# replay the pass-2 repairs (offline), or re-run them live against the workbench
python -m agent.pass2
python -m agent.pass2 --refetch --mcp

# rebuild the page
python -m agent.build_site

# every rule described on the page, checked — no network, no deps
python tests/test_agent.py
```

Search, fetch and the registry need only `COMPOSIO_API_KEY`. Extraction needs a model:
inside Composio's remote workbench one is provided (`--backend workbench`, the default);
outside it, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` (`--backend sdk`).

## What ran where

- **Research** ran in **Composio's remote workbench** — open internet, `COMPOSIO_SEARCH_WEB`
  for search, `COMPOSIO_SEARCH_FETCH_URL_CONTENT` for pages, and a GPT-family model for
  extraction. That model is a different vendor from the Claude session that wrote this code,
  so the cross-checks are cross-vendor rather than a model grading itself.
- **The registry pull** ran on a laptop (`scripts/fetch_composio_registry.py`, stdlib only).
- **Validation, scoring and the page** are pure compute over committed files and run anywhere.
- `data/pass1_strict_grades.txt` holds the URL-strict quote verdicts, computed once in the
  workbench where the retained page text lives, so `agent/upgrade.py` reproduces the identical
  dataset in any environment.

## Layout

```
agent/
  run_research.py   CLI: search → fetch → extract → validate → probe → derive
  schema.py         fields, enums, normalisation, source-authority tiers
  prompts.py        the quote-or-unknown contract
  providers.py      search / fetch / LLM — one interface, two backends
  evidence.py       phrase scanner, quote grading, quarantine
  probe.py          API-base probe, /pricing redirect probe, OpenAPI spec discovery
  derive.py         auth family, access, buildability — by rule, never by model
  registry.py       Composio's registry: ground truth + a 56-app accuracy check
  upgrade.py        re-derive the dataset offline
  pass2.py          refetch truncated evidence, MCP discovery query
  build_site.py     the deliverable page
data/               the 100 apps, the registry dump, the strict grades
outputs/            datasets, coverage, patterns, the pass-1 → pass-2 delta
tests/              every rule above, runnable with no dependencies
docs/               GitHub Pages source AND the written record
  index.html          the deliverable, self-contained
  data.json           the same findings, machine-readable
  llms.txt            the same findings, for an agent
  PLAN.md             plan of execution, written before the results existed
  IMPLEMENTATION.md   build order and the constraints that shaped it
  ASSIGNMENT.md       the brief
```

## Honest limits

- **Coverage, not correctness, is the weak spot.** Of the claims made, 78–97% carry a quote
  verified on a vendor-owned page. But the access fields — which tier includes API access —
  are answered for only 28–45% of apps, and that is the column the build queue most needs. The
  re-query for it was designed and cut for time.
- **The 75 claims we refused to guess about.** Pass 1 showed the model 5,000 characters per
  page and retained 2,500, so 75 claims became unverifiable offline. Calling them fabrications
  was tempting and wrong: `agent.pass2 --refetch` re-pulled the 53 pages behind them and found
  **53 verbatim, 21 reformatted, 1 fabricated**. Counting them as errors would have taken the
  published error count from 30 to 104 — a 3.5× overstatement of the problem being fixed.
- **`existing_mcp` went from 24% to 86% answered** once a query was written for it
  (`agent.pass2 --mcp`). Nobody asked in the first pass. 14 answers arrived and were *rejected*
  for failing quote validation rather than shipped.
- **347 abstentions are `unclassified`.** Pass 1 never recorded *why* it abstained, so
  "the vendor does not publish this" cannot be separated from "we did not find it".
- **The human audit was cut for time**, along with the no-retrieval pass-0 baseline. The
  accuracy claims here rest on the 56-app registry cross-check and mechanical quote validation,
  both automated. The page says so.
