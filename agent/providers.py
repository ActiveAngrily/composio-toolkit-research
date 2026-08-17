"""The three capabilities the pipeline needs from the outside world -- search,
fetch, and an LLM -- behind one interface, with two backends.

Why two backends, stated plainly because the README has to be honest about it:

  * `workbench` is what produced the shipped dataset. The pipeline runs inside
    Composio's remote workbench, where `run_composio_tool` and `invoke_llm` are
    preloaded and the sandbox has open internet. Search is Composio's
    COMPOSIO_SEARCH_WEB (Exa, returns citations with URLs) and fetch is
    COMPOSIO_SEARCH_FETCH_URL_CONTENT. The LLM there is a GPT-family model, which
    is a different vendor from the Claude session that wrote this code -- so the
    cross-check in verify.py is genuinely cross-vendor rather than a model
    grading itself.

  * `sdk` is for anyone reproducing this on their own machine. Search and fetch go
    through the official Composio SDK with a COMPOSIO_API_KEY. Composio does not
    expose a generic completion endpoint, so extraction needs OPENAI_API_KEY or
    ANTHROPIC_API_KEY. `--sources-only` runs the whole retrieval half with just
    the Composio key and no LLM at all, which is enough to see it work.
"""
from __future__ import annotations

import json
import re
from typing import Callable

from . import config

URL_RE = re.compile(r"https?://[^\s\)\]\"'>]+")


class Providers:
    """search(query) -> [(url, title)] · fetch(urls) -> [{url, text}] · llm(prompt) -> (text, err)"""

    def __init__(self, search_fn: Callable, fetch_fn: Callable, llm_fn: Callable, label: str):
        self._search, self._fetch, self._llm, self.label = search_fn, fetch_fn, llm_fn, label

    def search(self, query: str) -> list[tuple[str, str]]:
        try:
            return self._search(query) or []
        except Exception as exc:                       # a dead query must not kill an app
            print(f"  ! search failed ({type(exc).__name__}): {query[:60]}")
            return []

    def fetch(self, urls: list[str], max_chars: int = 9000) -> list[dict]:
        if not urls:
            return []
        try:
            return self._fetch(urls, max_chars) or []
        except Exception as exc:
            print(f"  ! fetch failed ({type(exc).__name__}) for {len(urls)} urls")
            return []

    def llm(self, prompt: str) -> tuple[str, str]:
        if self._llm is None:
            return "", "no-llm-backend"
        try:
            return self._llm(prompt)
        except Exception as exc:
            return "", f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------- workbench mode

    @classmethod
    def from_workbench(cls, run_composio_tool, invoke_llm=None) -> "Providers":
        """Build from the helpers Composio's workbench preloads into the kernel.

        Usage inside a workbench cell:
            from agent.providers import Providers
            P = Providers.from_workbench(run_composio_tool, invoke_llm)
        """

        def search(query: str):
            res, err = run_composio_tool("COMPOSIO_SEARCH_WEB", {"query": query},
                                         print_schema_for_tool=False)
            if err:
                return []
            cites = (res.get("data", {}) or {}).get("citations") or []
            return [(c.get("url", ""), c.get("title", "")) for c in cites if c.get("url")]

        def fetch(urls: list[str], max_chars: int):
            res, err = run_composio_tool(
                "COMPOSIO_SEARCH_FETCH_URL_CONTENT",
                {"urls": urls, "text": True, "max_characters": max_chars},
                print_schema_for_tool=False,
            )
            if err:
                return []
            data = res.get("data", {}) or {}
            ok = {s.get("url") or s.get("id")
                  for s in (data.get("statuses") or []) if s.get("status") == "success"}
            out = []
            for row in (data.get("results") or []):
                text = (row.get("text") or "").strip()
                url = row.get("url", "")
                if not text or row.get("content_quality_warning"):
                    continue
                if ok and url not in ok:
                    continue
                out.append({"url": url, "text": text})
            return out

        def llm(prompt: str):
            out, err = invoke_llm(prompt)
            return (out or ""), (err or "")

        return cls(search, fetch, (llm if invoke_llm else None), "workbench")

    @classmethod
    def autodetect_workbench(cls) -> "Providers | None":
        """When run_research.py is executed inside the workbench, the helpers live in
        the __main__ module rather than in this module's globals."""
        try:
            import __main__
            rct = getattr(__main__, "run_composio_tool", None)
            if rct is None:
                return None
            return cls.from_workbench(rct, getattr(__main__, "invoke_llm", None))
        except Exception:
            return None

    # ------------------------------------------------------------------- sdk mode

    @classmethod
    def from_sdk(cls, llm_backend: str = "auto") -> "Providers":
        key = config.api_key("COMPOSIO_API_KEY", required=True)
        try:
            from composio import Composio               # type: ignore
        except ImportError:
            raise SystemExit(
                "The sdk backend needs the Composio SDK: pip install composio\n"
                "Or run inside Composio's workbench, where search/fetch/LLM are provided."
            )
        client = Composio(api_key=key)

        def _execute(slug: str, arguments: dict):
            res = client.tools.execute(slug, arguments=arguments)
            if isinstance(res, dict):
                return res
            return json.loads(res.model_dump_json()) if hasattr(res, "model_dump_json") else {}

        def search(query: str):
            res = _execute("COMPOSIO_SEARCH_WEB", {"query": query})
            cites = ((res.get("data") or {}).get("citations")) or []
            return [(c.get("url", ""), c.get("title", "")) for c in cites if c.get("url")]

        def fetch(urls: list[str], max_chars: int):
            res = _execute("COMPOSIO_SEARCH_FETCH_URL_CONTENT",
                           {"urls": urls, "text": True, "max_characters": max_chars})
            rows = ((res.get("data") or {}).get("results")) or []
            return [{"url": r.get("url", ""), "text": (r.get("text") or "").strip()}
                    for r in rows if (r.get("text") or "").strip()]

        return cls(search, fetch, _pick_llm(llm_backend), "sdk")


def _pick_llm(backend: str):
    """OpenAI or Anthropic over plain HTTPS. Returns None when no key is present,
    which is a supported state -- `--sources-only` needs no LLM."""
    import os
    if backend in ("auto", "openai") and os.environ.get("OPENAI_API_KEY"):
        return _openai_llm
    if backend in ("auto", "anthropic") and os.environ.get("ANTHROPIC_API_KEY"):
        return _anthropic_llm
    return None


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    import requests
    r = requests.post(url, headers=headers, json=payload, timeout=config.LLM_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _openai_llm(prompt: str) -> tuple[str, str]:
    import os
    model = os.environ.get("OPENAI_MODEL", "gpt-4.1")
    body = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
        {"model": model, "temperature": 0,
         "messages": [{"role": "user", "content": prompt}]},
    )
    return body["choices"][0]["message"]["content"], ""


def _anthropic_llm(prompt: str) -> tuple[str, str]:
    import os
    model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    body = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
        {"model": model, "max_tokens": 2000, "temperature": 0,
         "messages": [{"role": "user", "content": prompt}]},
    )
    return "".join(b.get("text", "") for b in body.get("content", [])), ""


def build(backend: str = "auto", llm_backend: str = "auto") -> Providers:
    if backend in ("auto", "workbench"):
        found = Providers.autodetect_workbench()
        if found:
            return found
        if backend == "workbench":
            raise SystemExit("Not running inside Composio's workbench "
                             "(run_composio_tool not found). Try --backend sdk.")
    return Providers.from_sdk(llm_backend)
