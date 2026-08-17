# Implementation plan

Working document for the build. Plan v2 (`docs/PLAN.md`) is the *why*; this is the *do*.

---

## Current state

Repo initialized on `main` at `~/Documents/GitHub/composio_assignment`. Present:

```
data/apps.csv                     100 apps, machine-readable, parses clean
scripts/fetch_composio_registry.py Lane A step 1 — stdlib only, no pip install
docs/PLAN.md                      plan of execution
docs/ASSIGNMENT.md                the original brief
README.md  .gitignore  requirements.txt
agent/  site/  outputs/           empty, filled during the build
```

The registry script's matching logic was tested offline against a synthetic
registry (7/7 correct) — the only untested part is the HTTP call, which needs
your machine.

**One constraint to know about:** git commands run through the folder bridge fail
with `unable to unlink .git/index.lock: Operation not permitted` — the bridge
can't delete files, and git needs to. So **all git commands run in your own
Terminal**, not through me. Not a problem, just a division of labour.

---

## What I need from you

### 1. Three commands, now (~5 min)

```bash
cd ~/Documents/GitHub/composio_assignment
git add -A && git commit -m "scaffold: apps list, Lane A registry script, plan"

# create the repo (either the gh CLI, or make it on github.com and add the remote)
gh repo create composio-toolkit-research --public --source=. --remote=origin --push
```

### 2. The Composio registry dump (~5 min)

```bash
export COMPOSIO_API_KEY=ak_xxx      # platform.composio.dev → Settings → API Keys
python3 scripts/fetch_composio_registry.py
```

It prints a `GATE A` block at the end. **Paste that block back to me** — it tells us
how many of the 100 Composio already covers, which decides whether Lane A is the
ground-truth oracle or just a nice-to-have.

I never see your key. It stays in your shell.

### 3. Two decisions

**a) A GitHub token for me, or not?** With a fine-grained PAT scoped to this one repo
(contents: read/write, pages: write) I push and redeploy the page myself while iterating
at hours 5–7. Without one, you run `git push` each time I hand you changes — maybe
6–8 pushes over the evening. Your call; the token lives only in this sandbox, which is
destroyed when the session ends.

**b) Worth revisiting: a ~$10 Anthropic API key.** I said this was optional earlier.
The network probe changed the calculus and I'd rather flag it than quietly design around it.

Anthropic's API has server-side web search and fetch — the retrieval happens on
Anthropic's servers, not from this sandbox — and `api.anthropic.com` is one of the few
hosts the sandbox *can* reach. So with a key, `agent/run_research.py` genuinely runs
here end to end, and the repo ships one script that provably produced the dataset.

Without a key it still works, but the story splits: the research executes as Claude
subagents in the Cowork session while the repo ships the mirrored script, and the README
has to explain the difference honestly. That's defensible and I'll write it plainly — it's
just a paragraph of explaining that ~$10 makes disappear. Your judgement on whether
that's worth it.

---

## Build order

Owner column: **A** = Aayush, **C** = Claude. Times are elapsed from now.

### Phase 1 — Foundations (0:00–0:30)

| # | Owner | Task | Output |
|---|---|---|---|
| 1.1 | A | Commit, create GitHub repo, push | repo live |
| 1.2 | A | Run the registry script, paste GATE A | `data/composio_*.json`, `composio_match.csv` |
| 1.3 | C | Ingest registry, hand-check every fuzzy match | `outputs/lane_a.json` |
| 1.4 | C | Pre-register the audit sample **before any results exist** | `outputs/audit_sample.csv` |

**Gate A:** under 30 of 100 matched → Lane A drops to spec-discovery only, and the
"already in your catalog" column comes out of the page.

Note on 1.4: the 20 audit apps get picked by a fixed rule — 2 per category, one
well-known and one obscure — *written down before we know which apps the agent
struggles with*. That's what stops the sample being quietly cherry-picked, and saying
so on the page is worth more than the sample size.

### Phase 2 — Vertical slice, 10 apps end to end (0:30–2:00)

| # | Owner | Task | Output |
|---|---|---|---|
| 2.1 | C | Record schema + field enums + validation | `agent/schema.py` |
| 2.2 | C | Extraction prompt contract (quote-or-`unknown`, no memory answers) | `agent/prompts.py` |
| 2.3 | C | Orchestrator: plan queries → fetch docs → extract → structured record | `agent/run_research.py` |
| 2.4 | C | Run 10 apps, 1 per category | `outputs/dataset_v1.json` |
| 2.5 | C | Citation validator: URL 200s, official domain, quote literally present | `agent/validate_citations.py` |
| 2.6 | C | Ugly-but-real HTML page rendering those 10 | `site/index.html` |
| 2.7 | A | Push, enable GitHub Pages | **live URL exists** |

**Gate B (2:00):** if 10 apps aren't producing clean, citation-validated records,
cut Lane C's browser pass and ship the simpler pipeline. A deployed page at hour 2 is
the whole point of doing it in this order — the failure mode to avoid is a beautiful
pipeline and a rushed page.

### Phase 3 — Scale + baseline (2:00–3:00)

| # | Owner | Task | Output |
|---|---|---|---|
| 3.1 | C | Run the remaining 90 | `outputs/dataset_v1.json` (100) |
| 3.2 | C | **Pass 0**: no-retrieval baseline on the 20 audit apps | `outputs/pass0.json` |
| 3.3 | C | Spec discovery: OpenAPI/GraphQL, count endpoints | `outputs/specs.json` |

### Phase 4 — Verification (3:00–4:00)

| # | Owner | Task | Output |
|---|---|---|---|
| 4.1 | C | Reconcile Lane A vs Lane B, flag conflicts | `outputs/conflicts.json` |
| 4.2 | C | Re-research every conflict with a targeted prompt | `outputs/dataset_v2.json` |
| 4.3 | C | Citation validation across all 100 | `outputs/citation_report.json` |
| 4.4 | C | Browser pass on the hard tail — the apps where "self-serve or not" needs walking a real signup flow | `outputs/dataset_v3.json` |

### Phase 5 — Your audit, in parallel (3:00–4:30)

| # | Owner | Task | Output |
|---|---|---|---|
| 5.1 | A | Hand-check 20 apps against real docs, fill the sheet | `outputs/human_audit.csv` |

I'll hand you a pre-filled CSV: app, field, agent's answer hidden, a URL column and a
verdict column. **You fill it without seeing the agent's answers** — otherwise it's
not an independent check and the accuracy number means nothing. 60–90 min.

### Phase 6 — Scoring and patterns (4:30–5:15)

| # | Owner | Task | Output |
|---|---|---|---|
| 6.1 | C | Score passes 0→3 per field against your audit | `outputs/accuracy.json` |
| 6.2 | C | Error taxonomy: what kind of wrong, how often | `outputs/errors.json` |
| 6.3 | C | Cluster: auth × category, blockers, build queue ordering | `outputs/patterns.json` |

### Phase 7 — The deliverable (5:15–7:15)

**Gate C at 5:15: data freezes regardless of coverage.** Gaps ship as `unknown` with a
note. Per the brief that's the correct finding, not a failure.

| # | Owner | Task | Output |
|---|---|---|---|
| 7.1 | C | Build the real page: headline, patterns, matrix, build queue, agent, verification, proof | `site/index.html` |
| 7.2 | C | Machine-readable outputs | `site/data.json`, `site/llms.txt` |
| 7.3 | C | Rewrite README: how to run, what ran where, honest limitations | `README.md` |
| 7.4 | A | Final read-through — flag anything you couldn't defend under questioning | |
| 7.5 | A/C | Deploy, verify the live link, submit | |

---

## Definition of done

- [ ] 100 apps, every field either evidenced or explicitly `unknown`
- [ ] Every citation mechanically validated — URL resolves, quote actually on the page
- [ ] Accuracy reported per field across 4 passes against a held-out human audit
- [ ] Error taxonomy published, misses named, apps that defeated us named
- [ ] Patterns stated as headline claims with numbers, above the fold
- [ ] Build queue: build-now / caveats / outreach / skip, ordered
- [ ] Page readable in 2 minutes with no narration
- [ ] Machine-readable data alongside the page
- [ ] README explains how to run it, and states honestly what ran where
- [ ] Live link works in a private window

---

## Standing risks

| Risk | Trigger | Response |
|---|---|---|
| Lane A thin | Gate A < 30 | Drop the catalog column, lean on spec discovery |
| Slice not clean by 2:00 | Gate B | Cut the browser pass |
| Running late | Gate C at 5:15 | Freeze data, ship gaps as `unknown` |
| Docs bot-blocked or JS-only | per app | Record `blocked — manual check`; never work around a block. An honest "couldn't retrieve" is legitimate under the brief's own honesty constraint |
| Audit slips | 4:30 | Fall back to 10 apps and publish the wider error bars rather than hiding them |
