# 100 apps → an agent toolkit build queue

Composio take-home (AI Product Ops). Research 100 apps for agent-toolkit buildability,
find the patterns, do it with an agent, and prove the answers are trustworthy.

**Live page:** _(link once deployed)_

> Status: scaffolding. This README gets rewritten once the pipeline exists.

---

## Quick start

```bash
git clone <this repo>
cd composio_assignment

# Lane A — Composio's own registry (standard library only, no install needed)
export COMPOSIO_API_KEY=ak_xxx
python3 scripts/fetch_composio_registry.py
```

---

## What's here

```
data/
  apps.csv                    the 100 apps from the brief, machine-readable
  composio_toolkits.json      Composio's toolkit registry (generated)
  composio_match.csv          the 100 apps ↔ Composio slugs (generated)
  composio_toolkit_details.json  per-toolkit auth detail (generated)
scripts/
  fetch_composio_registry.py  Lane A step 1 — runs on your machine
agent/                        the research pipeline
site/                         the deliverable HTML page
outputs/                      datasets, scores, audit results
docs/
  PLAN.md                     plan of execution
```

## Approach in one paragraph

Three independent lanes produce every answer, then a reconciler compares them.
**Lane A** asks sources that cannot hallucinate — Composio's own toolkit registry
and machine-readable OpenAPI specs. **Lane B** is an LLM that may only answer with a
verbatim quote copied from a documentation page it actually opened, plus that page's
URL; if it can't find supporting text, the answer is `unknown`. **Lane C** verifies:
cross-lane disagreements get re-researched, every citation is mechanically checked
(does that sentence really appear on that URL?), the hard tail gets a browser pass,
and a held-out 20-app human audit is the ground truth everything is scored against.

Accuracy is reported per field, across four passes, starting from a deliberately
naive no-retrieval baseline so the improvement is measured rather than asserted.
