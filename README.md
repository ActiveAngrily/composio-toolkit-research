# 100 apps → an agent toolkit build queue

Composio take-home (AI Product Ops). 100 apps researched for agent-toolkit buildability
by an agent, with every claim quote-verified against the page it came from.

**Anant Jamuar** · jamuaranant@gmail.com · [github.com/ActiveAngrily](https://github.com/ActiveAngrily)

**Live page:** https://activeangrily.github.io/composio-toolkit-research/

Every number below is computed from `outputs/dataset_v3.json` and reproduced by
`python -m agent.build_site`; the same figures are in `docs/data.json`. If a number here
ever disagrees with the page, the page is right and this file is stale — which has already
happened once, and is why nothing in the page's prose is typed by hand any more.

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
  pass2.py          refetch truncated evidence, MCP discovery query, probes
  audit.py          the human-audit sheet and its scorer
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

- **No human verified any of these answers.** The brief asks for a by-hand cross-check and it
  was not done. The blank sheet is at `outputs/human_audit.csv`, with its sampling rule
  pre-registered in `agent/audit.py` before any result existed (stratify by verdict, then take
  the app with the fewest evidenced fields and the app with the most from each stratum —
  deliberately over-sampling the weakest claims, so any accuracy it produced would be a lower
  bound, not an estimate). It is unfilled. **Every accuracy number in this repo is
  machine-checked.** What stands in for the human check is the 56-app registry cross-check:
  wider than the planned hand-audit, and independent of the model in a way an audit run by
  this project's own author would not have been. What it cannot substitute for: quote
  *fidelity* is verified throughout; factual *correctness* where no page states a thing
  plainly is not.
- **Coverage, not correctness, is the weak spot.** Of the claims made, 79–100% carry a quote
  verified on a vendor-owned page. But the access fields — which tier includes API access —
  are answered for only 24–42% of apps, and that is the column the build queue most needs. The
  re-query for it was designed and cut for time.
- **The 75 claims we refused to guess about.** Pass 1 showed the model 5,000 characters per
  page and retained 2,500, so 75 claims became unverifiable offline. Calling them fabrications
  was tempting and wrong: `agent.pass2 --refetch` re-pulled the 53 pages behind them and found
  **53 verbatim, 21 reformatted, 1 fabricated**. Counting them as errors would have taken the
  published error count from 71 to 145 — a 2.0× overstatement of the problem being fixed.
- **`existing_mcp` went from 24% to 82% answered** once a query was written for it
  (`agent.pass2 --mcp`). Nobody asked in the first pass. Of the answers that arrived, 3 were
  *rejected* by validation rather than shipped.
- **A real sentence about the wrong product is the error that matters.** Quote validation
  cannot catch it — the quote is genuinely on the page it cites; the page is about something
  else. A name-mention check (`evidence.subject_check`) quarantines 29 such claims (24 whose
  evidence never names the app, 2 MCP claims whose evidence never mentions MCP, 3 one-liners
  that were authentication instructions): iPayX's description of iPaymu, Sherlock's MCP server
  for the Covertlabs platform, GoHighLevel's access story lifted from n8n's docs. It has a
  known blind spot: **Paygent Connect** and **Mermaid CLI** both share a brand with the right
  answer, so lookalike domains pass every test here. Both were caught by reading the rendered
  table, not by any checker.
- **311 abstentions are `unclassified`.** Pass 1 never recorded *why* it abstained, so
  "the vendor does not publish this" cannot be separated from "we did not find it".
- **No no-retrieval pass-0 baseline was run.** The improvement story runs pass 1 → pass 2,
  which is real, but would be starker against a naive anchor.
- **The deliverable broke its own deployment.** Brex's auth docs contain the literal string
  `Bearer {{your user_token here}}`. Quoted verbatim, as the contract requires, it reached
  GitHub Pages — where Jekyll parses `{{…}}` as a template tag, failed the build, and silently
  kept serving a stale commit. Fixed with `docs/.nojekyll` rather than by editing the quote:
  the evidence is the artefact and it stays byte-exact. `agent/build_site.py` now writes that
  file on every build so a fresh clone cannot lose it.
