# Composio take-home — plan of execution (v2, decisions locked)

Assignment read. Clock started ~16:32 IST 2026-08-17; 8h lands ~00:30 IST.
**Changes in v2:** decisions locked, and a sandbox network constraint I found during the spike that rewrites part of the architecture. §2 is the important one.

---

## 0. Locked decisions

| Decision | Choice |
|---|---|
| Keys | Composio only |
| Runtime | This cloud sandbox |
| Stack | Python |
| LLM | Research runs as Claude subagents in this session; repo ships the equivalent standalone script |
| Human audit | You — 20 apps, 2 per category, ~hour 3–4 |
| Repo / deploy | Neither set up yet → see §6, this is now the top deadline risk |

---

## 1. What is actually being graded

The surface ask is "research 100 apps, 6 columns each." Almost every candidate will do that. The brief's own weighting says something different:

> "**Accuracy is what matters most.**" · "Insight over raw table." · "Clarity and presentation are the point." · "show how accuracy moved from a lower first pass to a higher one because of those loops."

| # | Criterion | Weak submission | What separates a strong one |
|---|---|---|---|
| 1 | **Measured accuracy** | "spot-checked a few, looked right" | Held-out human-labelled sample, field-level accuracy per pass, an honest 6x% → 9x% delta with the error taxonomy behind it |
| 2 | **Presentation** | Long scrolling table | 2-minute page: headline → patterns → matrix → proof |
| 3 | **The agent** | One prompt in a loop | Real orchestration, escalation, citation validation, honest "where I stepped in" |
| 4 | **Patterns** | "OAuth2 is most common" | Clusters that imply a decision, numbers attached |
| 5 | **Honesty** | Silent gaps | Named failures, named apps that defeated it |

**The reframe I think wins it:** this is literally Composio's internal job — decide which toolkits to build next. So don't ship a research report, ship a **prioritised toolkit build queue**: "of your 100, N are already in your catalog, M are build-now, K need BD outreach, here's the order and the blocker for each." Free to do, and it's the difference between "did the homework" and "already thinking like the team."

---

## 2. ⚠ The constraint I found, and how it rewrites the architecture

I probed the sandbox's outbound network before planning around it. It has an **egress allowlist**:

| Reachable | Blocked |
|---|---|
| pypi, npm, crates · `api.github.com` · `raw.githubusercontent.com` · `api.anthropic.com` | `composio.dev` · `vercel.com` · every vendor doc domain · search engines · essentially the whole open web |

So: **no bulk HTTP fetching of vendor docs from the sandbox, no Composio API call from the sandbox, no Vercel deploy from the sandbox.** Your Mac's bridge doesn't rescue this either — `device_bash` has no network at all.

What *does* work, verified just now: my own fetch tooling is proxied outside the sandbox and reaches arbitrary vendor docs. I test-pulled Notion and DataForSEO and got exactly the span-grounded evidence Lane B needs — DataForSEO returned *"DataForSEO is using the Basic Authentication…"* and *"Create a free account… then go to the API Access tab"*, which is the auth field and the self-serve field, both quoted, both citable.

**Consequences:**

1. **Lane B must run as Claude subagents in this session.** The option you picked isn't just the convenient one — under this constraint it's the only one that works today. Good outcome.
2. **Lane A needs ~2 minutes of your hands.** I'll write `scripts/fetch_composio_registry.py`; you run it once in your own Terminal (not through me — the bridge has no network), it drops `data/composio_toolkits.json` into the repo folder, I stage it from there. Your Composio key never leaves your machine, which is also the right way to handle it.
3. **GitHub Pages beats Vercel now.** GitHub is reachable from the sandbox, so I can push and deploy end-to-end. Vercel is blocked, so it becomes you clicking through a browser at hour 7 — exactly when you don't want new setup. See §6.
4. This constraint is itself a legitimate "where a human was needed" line on the page. Say it plainly; it reads as engineering awareness, not excuse.

---

## 3. Data model (the "and more..")

The six fields are the floor; `and more..` is an invitation. Every field carries provenance.

**Identity** — `app`, `category_assigned`, `category_true`, `one_liner`, `domain`
**Auth** — `auth_methods[]` (OAUTH2/API_KEY/BEARER/BASIC/JWT/MTLS/NONE/CUSTOM), `oauth_app_review_required` (the real blocker at Meta, LinkedIn, Google Ads), `scopes_granular`, `admin_consent_required`
**Access** — `self_serve` (free / free-trial / paid-tier-required / admin-approval / app-review / partner-or-sales-gate / no-public-api), `credential_ceiling`, **`time_to_first_call`** (minutes/hours/days/weeks/never — the most product-ops-native metric here), `sandbox_available`
**API surface** — `protocol[]`, `spec_url`, **`endpoint_count`** (counted from the spec, not guessed), `breadth_bucket`, `rate_limits_documented`, `webhooks_triggers`
**Agent-readiness** — `existing_mcp` (official/community/none)+url, `in_composio_catalog`, `composio_tools_count`, `composio_triggers_count`, `composio_auth_schemes[]`
**Verdict** — `buildability` (build-now / build-with-caveats / needs-outreach / not-buildable), `primary_blocker`, `effort_estimate`, `notes`
**Provenance on every claim** — `evidence_url`, `evidence_quote` (verbatim span), `source_tier`, `confidence`, `verification_state`

`unknown` is first-class. **Abstention scores better than a confident wrong answer** — and saying that on the page is itself the signal.

---

## 4. Architecture

```
              ┌──── LANE A · deterministic, no LLM ──────────────────────────────┐
              │ Composio registry (you run one script locally, 2 min)            │
100 apps ────►│ → auth_schemes, no_auth, categories, tools_count, triggers_count │
   │          │ + OpenAPI/GraphQL spec discovery, endpoint counting, MCP registry│
   │          └──────────────────────────────────────────────────────────────────┘
   │
   │          ┌──── LANE B · evidence-grounded, Claude subagents ────────────────┐
   ├─────────►│ query planner (5–6 targeted searches/app) → fetch official docs   │
   │          │ → span-grounded extraction: every field needs a verbatim quote +  │
   │          │   URL, or it is `unknown`. Answering from model memory is banned. │
   │          └──────────────────────────────────────────────────────────────────┘
   ▼
┌─── RECONCILER (pure compute, sandbox) ───┐   ┌──── LANE C · verification ───────┐
│ merge A+B · flag disagreements · score   │◄─►│ 1 cross-lane conflict → re-research│
│ confidence · route conflicts to re-run   │   │ 2 second pass, different queries,  │
└──────────────────┬───────────────────────┘   │   different lens → agreement rate  │
                   │                           │ 3 citation validator: does the     │
                   │                           │   quote literally appear on a       │
                   │                           │   200-ing official-domain page?     │
                   │                           │   ← kills fabricated citations      │
                   │                           │ 4 browser-use on the hard tail:     │
                   │                           │   walk the real signup/pricing flow │
                   │                           │ 5 human audit: your 20, held out    │
                   ▼                           └─────────────────────────────────────┘
       dataset.json ──► patterns ──► single-file HTML ──► GitHub Pages
```

**Lane A caveat, stated honestly:** its value rests on how many of your 100 apps resolve to Composio slugs. My guess is 60–75. We find out in the first 20 minutes, from the JSON you generate. If it comes back under 30, Lane A shrinks to spec-discovery only and we lose maybe 20% of the edge — not the plan.

### How the accuracy delta gets measured honestly

Run a deliberately naive **Pass 0** — one LLM call per app, no retrieval, answering from memory — on the same 20 apps you audit. That's the low number, and it's real rather than manufactured.

| Pass | What it is | Expected field accuracy |
|---|---|---|
| 0 | LLM from memory, no retrieval | ~55–70%, and most citations fabricated or stale |
| 1 | Evidence-grounded extraction | ~80–88% |
| 2 | + cross-lane reconcile + citation validation | ~90–94% |
| 3 | + browser-use on the flagged tail | ~93–96% |

Scored **per field**, not per row — a row is 20 fields, so "row correct" is a meaningless metric that inflates or deflates arbitrarily. Ground truth is your audit, **held out**: the pipeline never sees it.

Deliberately building the weak baseline is the step most people skip, and it's what makes the improvement claim credible instead of decorative.

---

## 5. The page

Single self-contained HTML. Dark, dense, skimmable, no framework, inline JSON.

1. **Headline band** — `N/100 buildable today` · `X% OAuth2` · `Y gated behind sales/partnership` · `Z already in Composio` · `accuracy 6x% → 9x%`
2. **The patterns** — 5–7 one-line findings, each with its number, above the fold
3. **The matrix** — 100 rows, sortable/filterable, colour-coded by verdict, evidence one click from any cell; category × auth heatmap alongside
4. **The build queue** — the reframe: build-now / caveats / outreach / skip, ordered
5. **The agent** — inline architecture diagram, what it does, and a plainly-worded "where a human was needed"
6. **The verification** — accuracy per pass, error taxonomy, misses named
7. **Proof** — repo + runnable trigger
8. **Machine-readable** — embedded `<script type="application/json">` plus `data.json` and `llms.txt`. The brief says "easy for both an agent and a human to consume" and most people will read straight past that line.

---

## 6. Deploy — decide this now, not at hour 7

Neither GitHub repo nor Vercel exists yet, and Vercel is unreachable from the sandbox. Options:

| | **GitHub Pages** ⭐ | Vercel | Both |
|---|---|---|---|
| Who does it | Me, end-to-end, if you give me a repo-scoped PAT | You, in a browser, ~5 min | Me for Pages now, you add Vercel later if you want the live endpoint |
| Setup cost | One token | Signup + import + connect | One token now |
| Live serverless demo | No — proof is a runnable CLI + recorded run + machine-readable data | Yes | Yes, if you get to it |
| Deadline risk | Lowest | Real: new setup at the worst hour | Lowest, with upside |

**Recommendation: GitHub Pages as the guaranteed floor, Vercel as a stretch if we're ahead at hour 6.** The brief asks for "a live link… or runnable trigger" — Pages plus a documented CLI satisfies it. A hosted live agent is a nice-to-have that shouldn't be allowed to threaten the actual deliverable.

If you'd rather not hand me a PAT: you create an empty public repo, I build everything into your folder, you run `git push` once. Costs ~3 minutes of your time and no token.

---

## 7. Timeline with kill gates

| Hour | Work | Gate |
|---|---|---|
| 0:00–0:30 | Repo scaffold in your folder. **You: create the repo, run the Composio registry script (~5 min total).** I check how many of the 100 resolve | **Gate A:** <30 resolve → Lane A drops to spec-discovery only |
| 0:30–2:00 | Orchestrator built. **Vertical slice: 10 apps end-to-end → dataset.json → a real (ugly) page → deployed** | **Gate B:** no clean cited records by 2:00 → cut Lane C's browser pass, ship the simpler pipeline |
| 2:00–3:00 | Scale to 100. Pass 0 baseline in parallel | |
| 3:00–4:00 | Reconcile, citation validation, browser-use on the flagged tail | |
| 3:00–4:30 | **You, in parallel: hand-audit your 20 into `human_audit.csv`** | The one part that can't be delegated — the brief says "by hand" |
| 4:30–5:15 | Score every pass against your audit. Error taxonomy. Patterns | |
| 5:15–6:45 | Build the real page. Deploy | **Gate C at 5:15: data freezes regardless of coverage.** Gaps ship as `unknown` with a note — per the brief that's the correct finding, not a failure |
| 6:45–7:15 | README, repo tidy, final honesty read-through | |
| 7:15–8:00 | Buffer / Vercel stretch | |

---

## 8. Known traps (flagged now, not at hour 6)

Needs human eyes: **fanbasis**, **iPayX**, **Paygent Connect (NMI-powered)**, **Waterfall.io**, **higgsfield**, **Grain**, **Pumble**, **systeme.io**.
Category traps: **Sherlock** is an OSS CLI, not an API — correct finding is "no API, wrap the CLI". **Mermaid CLI** likewise. **NotebookLM** has no public API; the Gemini Enterprise API is a different product, and a submission claiming "NotebookLM API" has walked into a trap.
Looks self-serve, isn't: **Meta Ads**, **WhatsApp Business**, **Threads**, **LinkedIn Ads**, **Google Ads** (developer token approval), **Amazon SP-API**.

Naming these on the page with what we concluded and why is worth more than quietly getting them right.

---

## 9. Risks

| Risk | Mitigation |
|---|---|
| Composio slug coverage thin | Found out by hour 0:30; graceful degrade |
| Doc sites JS-only or bot-blocked | Record as `blocked — manual check needed`; browser-use for the ones that matter. We don't work around blocks — an honest "couldn't retrieve" is legitimate under the brief's own honesty constraint |
| Deploy setup at hour 7 | Do it at hour 0 instead — §6 |
| Model invents plausible doc URLs | Deterministic citation validator: quote must literally appear on a 200-ing official-domain page |
| Pipeline eats the budget, page ships weak | Vertical slice forces a deployed page by hour 2 |
| "Self-serve vs gated" often isn't answerable from docs | The field that genuinely needs browser-use plus your judgement. Budget for it; don't let the model guess |
| Repo script can't be run end-to-end in the sandbox | Validate its logic against recorded fixtures, and say so in the README rather than implying a run we didn't do |

---

## 10. What I need from you to start

1. **Composio registry dump** — I write the script, you run it once in your own Terminal, it writes JSON into the repo folder. ~2 min. Your key stays on your machine.
2. **Repo** — either a repo-scoped GitHub PAT so I push directly, or you create an empty public repo and push once at the end.
3. **Confirm GitHub Pages** as the deploy floor, Vercel as a stretch.
