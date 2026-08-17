# CHECKOUT — session state, decisions, and findings

**Assignment:** Composio take-home, AI Product Ops Intern. Research 100 apps for agent-toolkit
buildability, find the patterns, do it with an agent, verify accuracy, ship one self-explanatory
HTML page plus a source repo.

**Clock:** received ~16:32 IST 2026-08-17. Budget 6–8h → soft deadline ~00:30 IST.
This checkout written at ~18:40 IST, roughly 2h05 elapsed.

**Repo:** `ActiveAngrily/composio-toolkit-research` (⚠ still **private** — must be public before submission)
**Local:** `~/Documents/GitHub/composio_assignment`
**Last commit:** `55f3433` — pass 1 dataset, 100 apps

---

## 1. What the assignment is actually testing

The surface ask is 100 apps × 6 columns. The brief's own weighting says otherwise:

> "**Accuracy is what matters most.**" · "Insight over raw table." · "Clarity and presentation are the point."
> "show how accuracy moved from a lower first pass to a higher one because of those loops."

So the real test is twofold: can you build a machine that does the research, and **can you tell when
that machine is lying to you**. An LLM will answer any of these questions confidently, cite a URL,
and be wrong a meaningful fraction of the time, and a wrong answer is visually identical to a right
one. Everything in the design below exists to make that difference visible and measurable.

**Framing decision:** ship a **prioritised toolkit build queue**, not a research report. "Of your 100,
N are already in your catalog, M are build-now, K need BD outreach, here's the order and the blocker."
That's the question Composio's product-ops function exists to answer, and the data supports it for free.

---

## 2. The three-machine topology (source of most early confusion)

| Machine | Network | Role |
|---|---|---|
| **Aayush's Mac** | full | git repo, Terminal commands, Chrome |
| **Cowork cloud sandbox** (Claude's) | **allowlisted only** — pypi/npm/crates, `api.github.com`, `raw.githubusercontent.com`, `api.anthropic.com`. Everything else blocked | analysis, scoring, file authoring, page build |
| **Composio remote workbench** | **open internet** | all research execution |

Discovered by probing, not assumed. `composio.dev`, `vercel.com`, and every vendor doc domain are
unreachable from the Cowork sandbox. The folder bridge to the Mac (`device_bash`) has **no network at
all**, and cannot delete files — which is why `git` fails there with
`unable to unlink .git/index.lock: Operation not permitted`, and why all git runs in Aayush's own Terminal.

This topology is why the architecture landed where it did. It wasn't a preference.

---

## 3. Decisions taken, in order

| # | Decision | Rationale |
|---|---|---|
| 1 | Run in the cloud sandbox, not the Mac | Bridge has no network; Mac would bottleneck on Aayush |
| 2 | Python | Fastest for the data work; Composio's mature SDK |
| 3 | 20-app human audit, 2 per category | Smallest sample supporting an honest claim about the other 80 |
| 4 | Research executes in **Composio's workbench** | Superseded decision 4a below |
| ~~4a~~ | ~~Research via Claude subagents~~ | Superseded once the workbench turned out to have open internet, Exa search, and an LLM |
| 5 | **No Anthropic API key needed** | Was recommended twice; the workbench made it unnecessary. Retracted |
| 6 | GitHub Pages over Vercel | Vercel unreachable from sandbox; GitHub is reachable |
| 7 | GitHub auth via **Composio OAuth**, not a PAT | Simpler, nothing to paste, and one more genuinely Composio-native component |
| 8 | Decompose `self_serve` into four sub-fields | It was four questions in a trenchcoat; see §6 |

---

## 4. The Composio registry — Lane A ground truth

`scripts/fetch_composio_registry.py` (stdlib only, runs on the Mac) pulls
`GET https://backend.composio.dev/api/v3.1/toolkits`. Returned **1,222 toolkits**.

**Gate A result: 56/100** of the assignment's apps exist in Composio's catalog.
Reported as 57 by the script; **one was a false positive** — see §5.

Across the 56 matched, Composio's own authoritative data:

- Auth: 41 OAUTH2 · 33 API_KEY · 4 S2S_OAUTH2 · 2 BASIC · 1 BEARER (sums >56, apps support multiple)
- Tools: median 84 per toolkit, max 871, **7,240 total**

**Coverage by category — already a headline pattern:**

| Category | In catalog |
|---|---|
| Productivity & Project Management | 9/10 |
| Marketing, Ads, Email & Social | 8/10 |
| Developer, Infra & Data platforms | 8/10 |
| Support & Helpdesk | 6/10 |
| Data, SEO & Scraping | 6/10 |
| Finance & Fintech | 6/10 |
| CRM & Sales | 5/10 |
| Communications & Messaging | 4/10 |
| AI, Research & Media-native | 3/10 |
| **Ecommerce** | **2/10** |

Composio is deep in developer/productivity and thin in ecommerce and AI-native. The absences are the
interesting half.

---

## 5. Errors found so far (keep these — they belong on the page)

**Plaid → "Placid".** The fuzzy matcher matched Plaid to a Composio toolkit slugged `placid`
at 0.909 similarity. Placid is an image-generation product, entirely unrelated. Verified against the
raw registry: **there is no Plaid toolkit**. One false positive in 57 matches, caught because the
script flagged fuzzy matches for human eyeballing. GoHighLevel → `highlevel` is a genuine match.

*Why it matters:* the deterministic lane is not automatically the trustworthy one.

**Stripe: docs and server disagree, and both are right.** Probing `api.stripe.com` unauthenticated
returns `WWW-Authenticate: Basic realm="Stripe"` while the body says "You did not provide an API key."
The credential is an API key; the transport is HTTP Basic. A docs-only pass picks one and looks wrong
to anyone who knows the other.

**Gumroad:** `WWW-Authenticate: Bearer realm="Doorkeeper"` — Doorkeeper is a Rails OAuth2 provider,
so the server identifies its own auth implementation.

**Pylon: pricing page is a sales gate.** `usepylon.com/pricing` **redirects to `/schedule-demo`**.
No public pricing, no self-serve tier — only "Schedule a personalized 30-minute demo." The docs-only
pass returned `unknown` for this field; the browser resolved it in one page load. Pylon's API is
publicly documented with Bearer auth, but you cannot get an account without going through sales.

**GitHub secret scanning rejected our first commit.** Quoting Stripe's docs verbatim captured one of
their example `sk_test_` keys. Verbatim quoting has that failure mode. The pipeline now redacts
credential-shaped patterns before writing anything.

**Composio's own BROWSER_TOOL is documented as free/no-auth but 403s on execution:**
`Execution of toolkit 'BROWSER_TOOL' is temporarily disabled by the administrator` (code 10403).
Retried; the dashboard shows it enabled, so the block is at Composio's execution layer, not project
config. *This is itself a documented-versus-actual availability gap, found inside Composio's own
product — exactly the phenomenon this assignment is about. It goes on the page.*

---

## 6. The architecture as built

### Composio workbench capabilities (all confirmed by probing)

| Helper | What it is |
|---|---|
| `web_search(q)` | Exa — returns a **synthesized answer with no URLs**. Not usable for citations |
| `COMPOSIO_SEARCH_WEB` | Exa — returns `data.citations[].url`. **This is the one we use** |
| `COMPOSIO_SEARCH_FETCH_URL_CONTENT` | Exa — clean page text, multi-URL. No auth required |
| `invoke_llm(q)` | GPT-family model. **Different vendor from Claude** → real cross-vendor checking |
| `run_composio_tool` | Execute any Composio tool |
| `proxy_execute` | Authenticated API calls through Composio connections |
| `requests` + open internet | Direct HTTP probing |
| `/mnt/files` | Persists across restarts → checkpointing |

**Operational limits learned the hard way:** the workbench allows 180s per cell, but the **MCP client
times out at 60s**. Cells must finish in ~55s. Batches of 10 apps at 8-way parallelism run 14–28s.
Twice the MCP call timed out while the cell completed server-side anyway — **the checkpoint-to-disk
design saved both batches**. Keep it.

### Evidence classes (the trust model)

Not every fact deserves equal confidence, so each is tagged by origin:

1. **Registry** — Composio's own catalog. Fact, not inference. Strongest.
2. **Documentary** — a quote from official docs, mechanically verified present on the cited page.
3. **Behavioural** — we probed and the server answered. Can't be stale marketing copy.
4. **Cross-vendor** — a different model on a different index agreed independently.

The page will show, per field, the distribution across these. That converts "trust me" into something
inspectable.

### The `self_serve` decomposition

The single most important design fix. "Self-serve vs gated" is four questions:

- `signup_self_serve` — can you make an account without contacting sales?
- `api_access_tier` — which plan tier includes API access?
- `credential_self_issue` — can you generate the credential yourself once inside?
- `approval_gate` — app review / developer token / business verification / partner approval?

Each has documentary evidence *somewhere*; the blend has none, because no page is about the blend.
`self_serve` is then **derived by a deterministic rule** rather than judged by the model.

### Prompt contract

Answer only from the supplied sources; every field carries a verbatim `quote` + `url`; unsupported →
`unknown` with empty quote. The prompt states explicitly that an automated checker re-reads every URL,
so an invented quote is detected and scores worse than an abstention. **Abstention is cheap, confident
error is expensive** — and the model is told why.

---

## 7. Pass 1 results (committed, `55f3433`)

100/100 apps returned usable sources. Zero empty.

**Quote validation across 912 field-level claims:**

| Grade | Count | % |
|---|---|---|
| abstained (honest "unknown") | 425 | 46.6% |
| **valid** (quote literally on cited page) | 385 | 42.2% |
| near-miss (real sentence, reformatted) | 60 | 6.6% |
| **QUOTE_NOT_FOUND (fabricated)** | 34 | **3.7%** |
| no-quote but non-unknown value | 8 | 0.9% |

Without the validator those 34 fabrications ship looking identical to the 385 good ones.
Near-miss is tracked separately from fabrication deliberately — paraphrasing and inventing are
different failure modes and conflating them hides which one the model actually commits.

**Extracted distributions:**

- auth: OAUTH2 58 · BEARER 45 · API_KEY 43 · BASIC 14 · JWT 8
- `self_serve` derived: unknown 59 · free 13 · app-review 13 · paid-tier-required 10 · sales-gate 5
- `api_access_tier`: unknown 71 · free 15 · paid 11 · enterprise-only 3
- `existing_mcp`: unknown 75 · official 23 · community 2
- 71 apps answered an HTTP probe; only 5 sent a `WWW-Authenticate` header
- 48 apps triggered ≥1 gate-phrase detector

**A mid-run fix that worked:** batch 1 initially produced 6 fabrications. Cause — pricing pages were
ranked below API docs and cut before the model saw them, and enum values weren't normalised. Forcing
pricing pages into the source set dropped it to 1. That fix is in the code.

---

## 8. Known weaknesses going into pass 2

| Weakness | Cause | Pass-2 fix |
|---|---|---|
| `self_serve` unknown 59% | access info isn't in API docs | access-specific re-queries on unknowns only |
| `existing_mcp` unknown 75% | **I never wrote an MCP search.** My omission | dedicated "<app> MCP server" query for all 100 |
| 48 gate-detector hits unused | detectors ran but weren't shown to the model | feed hits in as explicit signals |
| Only 5 `WWW-Authenticate` headers | auth info is often in the response **body** | parse bodies too |
| No registry cross-check yet | not run | reconcile 56 apps, re-research disagreements |
| No redirect signal | not yet built | **new:** `GET /pricing`, see if it lands on `/demo`, `/contact`, `/schedule` — the Pylon trick, deterministic, all 100 |

---

## 9. How the accuracy number will be produced

Aayush hand-checks **20 apps, 2 per category, one well-known + one obscure** — a rule written down in
`docs/IMPLEMENTATION.md` *before* any results existed, so the sample can't be accused of being picked
to flatter. He works **without seeing the agent's answers**; his sheet is the held-out answer key.

Four versions scored against it:

| Pass | What it is |
|---|---|
| **0** | deliberately naive — LLM from memory, no retrieval. The version most people would ship |
| **1** | evidence-grounded extraction (done) |
| **2** | + registry reconciliation, detector signals, redirect probes, quote validation |
| **3** | + browser resolution of the ambiguous residue |

Scored **per field, not per row** — an app has ~20 fields, so "row correct" is a meaningless metric.
Reported per field type, so we can say "auth 96%, self-serve 78%" rather than hiding the weak one in
an average.

---

## 10. Verification stack, current status

| Lane | Status |
|---|---|
| Composio registry | ✅ 56 apps, authoritative |
| Documentary + quote validation | ✅ 912 claims graded |
| Behavioural — HTTP probe | ✅ 71 apps responding |
| Behavioural — `/pricing` redirect | ⏳ built next, all 100 |
| Browser — Claude in Chrome | ✅ connected as `composio-browser-bench`, validated on Pylon |
| Browser — Composio BROWSER_TOOL | ❌ 403 disabled. On retry list; if it returns we get two independent browser agents and can report their agreement |
| Cross-vendor (Claude vs GPT-family) | ⏳ pending |
| Human audit | ⏳ pending — Aayush, ~60–90 min |

---

## 11. Open items

1. **Make the repo public** before submission.
2. Pass 2 — the escalation loop in §8.
3. Pass 0 baseline on the 20 audit apps.
4. Generate the audit sheet; Aayush fills it blind.
5. Browser lane on the ambiguous residue.
6. Score, error taxonomy, patterns.
7. Build the page; deploy to GitHub Pages.
8. Rewrite README; state honestly what ran where.

**Gate C: data freezes at ~22:00 IST regardless of coverage.** Gaps ship as `unknown` with a note —
per the brief that is the correct finding, not a failure.

---

## 12. Things to say on the page because they're true

- The deterministic lane produced a false positive (Plaid/Placid) and a human caught it.
- 3.7% of first-pass claims were fabricated quotes; here is the mechanism that caught them.
- Stripe's docs and Stripe's server disagree, and both are right.
- Composio's own Browser Tool is documented as free but returns 403.
- Committing verbatim documentation quotes tripped GitHub's secret scanner.
- Self-serve remains the weakest field, and here is its measured accuracy, separately.
