"""Behavioural evidence: ask the server instead of reading about it.

Three probes, all deterministic, none involving a model.

  api_probe      -- hit the API base unauthenticated and read what comes back.
                    Documentation can be stale; a live 401 cannot. Pass 1 only
                    looked at the WWW-Authenticate header, which just 9 of 196
                    responses set, and ignored 142 response bodies that said things
                    like "You did not provide an API key". Both are read now.

  pricing_probe  -- request /pricing and see where it lands. Pylon's pricing page
                    redirects to /schedule-demo: no public pricing, no self-serve
                    tier, and one page load settles a field the docs-only pass left
                    unknown. Deterministic, and it works on every app.

  spec_probe     -- look for a machine-readable API spec and count its paths. This
                    is the brief's "roughly how broad" for the 44 apps that are not
                    in Composio's registry and therefore have no tool count.
"""
from __future__ import annotations

import json
import re
import urllib.parse

from . import config

API_URL_RE = re.compile(r"https?://api[a-z0-9.-]*\.[a-z]{2,}(?:/[A-Za-z0-9._~/-]{0,40})?")
SPEC_URL_RE = re.compile(r"https?://[^\s\"'>)]+?(?:openapi|swagger)[^\s\"'>)]*?\.(?:json|ya?ml)")

# What the body says when a server is refusing you for lack of a credential.
BODY_SIGNALS = [
    (r"did not provide an api key", "api-key-required"),
    (r"api key (?:is )?(?:required|missing)", "api-key-required"),
    (r"missing authorization", "token-required"),
    (r"invalid (?:token|credentials)", "token-required"),
    (r"unauthorized", "unauthorized"),
    (r"authentication (?:is )?required", "auth-required"),
    (r"bearer", "bearer-transport"),
    (r"basic realm", "basic-transport"),
]
_BODY = [(re.compile(p), t) for p, t in BODY_SIGNALS]


def _get(url: str, timeout: int = config.PROBE_TIMEOUT, redirects: bool = True):
    import requests
    return requests.get(url, headers=config.USER_AGENT, timeout=timeout,
                        allow_redirects=redirects)


def _body_tags(text: str) -> list[str]:
    low = (text or "")[:1500].lower()
    return sorted({tag for rx, tag in _BODY if rx.search(low)})


def api_probe(hint: str, pages: list[dict], limit: int = 3) -> list[dict]:
    """Candidate API bases from the docs plus the obvious api.<domain> guess."""
    root = (hint or "").split("/")[0].replace("www.", "")
    candidates: list[str] = []
    for page in pages:
        candidates += API_URL_RE.findall(page.get("text") or "")[:4]
    if root:
        candidates.append(f"https://api.{root}")

    seen, out = set(), []
    for url in candidates:
        url = url.rstrip("/.,)")
        if url in seen:
            continue
        seen.add(url)
        try:
            r = _get(url)
            body = r.text or ""
            out.append({
                "url": url,
                "status": r.status_code,
                "www_authenticate": r.headers.get("WWW-Authenticate", ""),
                "body_hint": body[:200].replace("\n", " "),
                "body_tags": _body_tags(body),
            })
        except Exception as exc:
            out.append({"url": url, "status": 0, "err": type(exc).__name__})
        if len(out) >= limit:
            break
    return out


def pricing_probe(hint: str) -> dict:
    """The Pylon trick. A /pricing that lands on /demo, /contact or /sales is a
    sales gate stated by the server rather than inferred from prose."""
    root = (hint or "").split("/")[0].replace("www.", "")
    if not root:
        return {}
    url = f"https://{root}/pricing"
    try:
        r = _get(url)
    except Exception as exc:
        return {"url": url, "status": 0, "err": type(exc).__name__}

    final = r.url or url
    landed = urllib.parse.urlparse(final).path.lower()
    gate_words = ("demo", "contact", "sales", "schedule", "talk-to", "get-started", "signup")
    return {
        "url": url,
        "status": r.status_code,
        "final_url": final,
        "redirected": final.rstrip("/") != url.rstrip("/"),
        "hops": [h.url for h in (r.history or [])],
        "sales_gate": any(w in landed for w in gate_words) and "pricing" not in landed,
        "public_pricing": r.status_code == 200 and "pricing" in landed,
    }


WELL_KNOWN_SPECS = (
    "/openapi.json", "/openapi.yaml", "/swagger.json", "/v1/openapi.json",
    "/api/openapi.json", "/.well-known/openapi.json",
)


def spec_probe(hint: str, pages: list[dict]) -> dict:
    """Find a machine-readable spec and count its paths and operations. A non-LLM
    breadth measure, which is what the brief's 'roughly how broad' wants."""
    root = (hint or "").split("/")[0].replace("www.", "")
    candidates: list[str] = []
    for page in pages:
        candidates += SPEC_URL_RE.findall(page.get("text") or "")[:3]
    for suffix in WELL_KNOWN_SPECS:
        if root:
            candidates.append(f"https://api.{root}{suffix}")
            candidates.append(f"https://{root}{suffix}")

    for url in dict.fromkeys(candidates):
        try:
            r = _get(url, timeout=config.PROBE_TIMEOUT)
            if r.status_code != 200 or len(r.content) < 200:
                continue
            spec = _parse_spec(r.text)
            if not spec:
                continue
            paths = spec.get("paths") or {}
            ops = sum(1 for methods in paths.values() if isinstance(methods, dict)
                      for m in methods if m.lower() in
                      ("get", "post", "put", "patch", "delete"))
            if not paths:
                continue
            return {"spec_url": url, "path_count": len(paths), "operation_count": ops,
                    "spec_kind": "openapi3" if str(spec.get("openapi", "")).startswith("3")
                                 else "swagger2"}
        except Exception:
            continue
    return {}


def _parse_spec(text: str):
    try:
        return json.loads(text)
    except Exception:
        pass
    try:
        import yaml                                     # optional
        return yaml.safe_load(text)
    except Exception:
        return None


def breadth_bucket(count: int | None) -> str:
    """Shared vocabulary so registry tool counts and spec operation counts land in
    the same buckets and the column means one thing across all 100 rows."""
    if not count:
        return "unknown"
    if count < 25:
        return "narrow"
    if count < 100:
        return "medium"
    if count < 300:
        return "broad"
    return "very-broad"
