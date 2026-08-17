"""The research agent behind `composio-toolkit-research`.

Reads the 100-app list, researches each one against live documentation, and emits a
record per app where every claim carries a verbatim quote, that quote's URL, a
source-authority tier, and a validation verdict from re-reading the page.

Module map, in the order data flows through it:

    config.py     paths, credentials, HTTP defaults, credential redaction
    schema.py     fields, enums, normalisation, source tiering
    prompts.py    the extraction contract (quote-or-unknown), pass-0 baseline, repair
    providers.py  search / fetch / LLM, behind one interface, two backends
    pipeline.py   research one app end to end
    evidence.py   phrase scanner, quote grading, quarantine
    probe.py      behavioural probes: API base, /pricing redirect, OpenAPI spec
    derive.py     auth family, access verdict, buildability, contradiction checks
    registry.py   Composio's own toolkit registry -- ground truth, and a free
                  56-app accuracy check
    run_research.py  CLI

One idea holds it together: an LLM will answer any of these questions confidently
and be wrong a meaningful fraction of the time, and a wrong answer looks exactly
like a right one. So the model is never the last word. It may only answer with text
it copied from a page it actually opened; a validator re-reads that page; claims
that fail are quarantined rather than reported; and anything that can be settled by
Composio's registry, an HTTP response, or a redirect is settled that way instead.
"""

__version__ = "2.0.0"
