<div align="center">

# Agent-toolkit buildability across 100 applications: an evidence-validated survey

**Of the 44 apps Composio is missing, 10 could be built this week and 5 need a human
conversation first. Every answer carries the sentence it came from — and the 71 that
failed that check were removed rather than published.**

[![live page](https://img.shields.io/badge/live_page-activeangrily.github.io-1c5cab?style=flat-square)](https://activeangrily.github.io/composio-toolkit-research/)
[![data.json](https://img.shields.io/badge/machine_readable-data.json-52514e?style=flat-square)](https://activeangrily.github.io/composio-toolkit-research/data.json)
[![llms.txt](https://img.shields.io/badge/for_agents-llms.txt-52514e?style=flat-square)](https://activeangrily.github.io/composio-toolkit-research/llms.txt)
![python](https://img.shields.io/badge/python-3.10%2B-1c5cab?style=flat-square)
[![tests](https://img.shields.io/badge/tests-no_network_no_deps-0ca30c?style=flat-square)](tests/test_agent.py)

Composio take-home · AI Product Ops
<br>
**Anant Jamuar** · [jamuaranant@gmail.com](mailto:jamuaranant@gmail.com) · [github.com/ActiveAngrily](https://github.com/ActiveAngrily)

</div>

---

Composio already covers **56** of these 100 apps. This project is an account of the other
**44** — built by an agent running inside Composio's own remote workbench, where every
claim carries a verbatim quote, that quote's URL, a source-authority tier, and a verdict
from re-reading the page. Claims whose quote did not check out are **quarantined to
`unknown` rather than reported**.

> **Where the numbers live.** Every figure is computed from `outputs/dataset_v3.json` at
> build time and published on the page and in `docs/data.json`. Nothing is transcribed by
> hand — four numbers once drifted that way, which is why the generator now derives all of
> them. **If this file ever disagrees with the page, the page is right.**

## Quickstart

```bash
git clone https://github.com/ActiveAngrily/composio-toolkit-research
cd composio-toolkit-research
cp .env.example .env          # add COMPOSIO_API_KEY

python -m agent.run_research --app "Notion"   # one app, end to end
```

That single command is the whole pipeline on one app: plan queries → search → rank by
source authority → fetch → scan for gate phrases → extract with a quote per field →
re-read the cited page to validate → probe the live API → derive the verdict by rule.

**No key? The dataset still reproduces.** The steps that need the network ran once, and
their results are committed — so `upgrade` → `pass2` → `build_site` rebuilds the identical
dataset and the identical page on any machine, offline.

```bash
python -m agent.upgrade && python -m agent.pass2 && python -m agent.build_site
```

## Every command

| Command | What it does | Needs |
| :-- | :-- | :-- |
| `agent.run_research --app "Notion"` | One app, end to end | key + model |
| `agent.run_research --app "Pylon" --sources-only` | Retrieval and probes only | key |
| `scripts/fetch_composio_registry.py` | Composio's 1,222-toolkit registry | key |
| `agent.upgrade` | Re-derive all 100 records from recorded evidence | — |
| `agent.pass2` | Replay the pass-2 repairs | — |
| `agent.pass2 --refetch --mcp` | Re-run those repairs live instead | key + model |
| `agent.build_site` | Rebuild the page, `data.json` and `llms.txt` | — |
| `agent.audit --sheet` | Regenerate the blind human-audit sheet | — |
| `tests/test_agent.py` | Every rule the page describes | — |

Search, fetch and the registry need only `COMPOSIO_API_KEY`. Extraction needs a model:
inside Composio's workbench one is provided (`--backend workbench`, the default); outside
it, set `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` (`--backend sdk`).

## Three lanes, deliberately independent

Two of them contain no language model, which is what makes the third one checkable.

| Lane | What it is | Model? |
| :-- | :-- | :-- |
| **A** | Composio's public toolkit registry — 1,222 toolkits, 56 of the 100 matched, with their recorded auth and tool counts | no |
| **B** | The research agent: `COMPOSIO_SEARCH_WEB`, `COMPOSIO_SEARCH_FETCH_URL_CONTENT`, GPT-family extraction | yes |
| **C** | Behavioural probes: API base, `GET /pricing` following redirects, OpenAPI spec discovery | no |

Disagreements between the lanes are **recorded, not resolved**. Lane A is also the
accuracy check on lane B: 56 apps of non-model ground truth, no human effort.

<details>
<summary><b>Repo map</b></summary>

```
agent/
  run_research.py   CLI: search → fetch → extract → validate → probe → derive
  schema.py         fields, enums, normalisation, source-authority tiers
  prompts.py        the quote-or-unknown contract
  providers.py      search / fetch / LLM — one interface, two backends
  evidence.py       phrase scanner, quote grading, subject check, quarantine
  probe.py          API-base probe, /pricing redirect probe, spec discovery
  derive.py         auth family, access, buildability — by rule, never by model
  registry.py       Composio's registry: ground truth + the 56-app cross-check
  upgrade.py        re-derive the dataset offline
  pass2.py          refetch truncated evidence, MCP query, probes
  audit.py          the human-audit sheet and its scorer
  build_site.py     the deliverable page
data/               the 100 apps, the registry dump, the recorded patches
outputs/            datasets v1→v3, coverage, patterns, the pass-1 → pass-2 delta
tests/              every rule above, no dependencies
docs/               GitHub Pages source AND the written record
  index.html          the deliverable, self-contained, nothing fetched at load
  data.json           the same findings, machine-readable
  llms.txt            the same findings, for an agent
  PLAN.md             written before the results existed
  IMPLEMENTATION.md   build order and the constraints that shaped it
  ASSIGNMENT.md       the brief
```

</details>

## Honest limits

The page carries these in full ([§8](https://activeangrily.github.io/composio-toolkit-research/#s8)).
The three that matter most:

- **No human verified any of these answers.** The brief asks for a by-hand cross-check and
  it was not done. The blank sheet is at `outputs/human_audit.csv`, its sampling rule
  pre-registered in `agent/audit.py` before any result existed. Every accuracy number here
  is machine-checked. The 56-app registry cross-check stands in for it — wider, and
  independent of the model in a way an author-run audit would not have been.
- **No browser-driven lane ran.** The brief names browser-use; the Pylon case it was needed
  for became a deterministic redirect probe run across all 100 instead. That buys
  reproducibility and loses everything only a real browser sees.
- **Coverage, not correctness, is the weak spot.** Of the claims made, 79–100% carry a
  quote verified on a vendor-owned page — but the access fields are answered for only
  24–42% of apps, and that is the column a build queue most needs.

**The number worth arguing with:** agreement with Composio's registry *fell* from 82.1% to
75.0% as evidence standards tightened, because quarantining unevidenced claims removed
correct answers along with wrong ones. A verification pass in which every number improves
is a verification pass nobody should trust.
