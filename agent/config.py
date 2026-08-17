"""Paths, credentials, HTTP defaults, and the redaction rule that applies to
everything this pipeline writes to disk.

Standard library only. The pipeline has to run inside Composio's remote workbench,
where mid-run `pip install` is not something we want to depend on.
"""
from __future__ import annotations

import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUTS = ROOT / "outputs"
CACHE = OUTPUTS / "cache"

for _d in (OUTPUTS, CACHE):
    _d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- env

def load_dotenv(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Read a .env file into os.environ without clobbering existing values.

    Tolerates `KEY = value`, quoted values, blank lines and `#` comments, because
    hand-written .env files have all of those. Returns what it set.
    """
    p = pathlib.Path(path or ROOT / ".env")
    found: dict[str, str] = {}
    if not p.exists():
        return found
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key and val and key not in os.environ:
            os.environ[key] = val
            found[key] = val
    return found


def api_key(name: str, required: bool = False) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        raise SystemExit(
            f"{name} is not set. Put it in {ROOT / '.env'} as {name}=... "
            f"(that file is gitignored) or export it in your shell."
        )
    return val


COMPOSIO_BASE = os.environ.get("COMPOSIO_BASE", "https://backend.composio.dev/api/v3.1")


# -------------------------------------------------------------------------- http

USER_AGENT = {"User-Agent": "Mozilla/5.0 (compatible; composio-toolkit-research/2.0)"}
FETCH_TIMEOUT = 20
PROBE_TIMEOUT = 8
LLM_TIMEOUT = 90


# ---------------------------------------------------------------------- redaction

# Official documentation quotes example credentials verbatim. Committing one of
# those trips GitHub's secret scanner and blocks the push, which is exactly what
# happened on this project's first commit. Everything we serialise goes through
# redact() first.
SECRET_PATTERNS = [
    r"\b(?:sk|pk|rk)_(?:test|live)_[A-Za-z0-9]{8,}",     # Stripe-style
    r"\bAKIA[0-9A-Z]{16}\b",                              # AWS access key id
    r"\bgh[pousr]_[A-Za-z0-9]{20,}",                      # GitHub tokens
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}",                    # Slack tokens
    r"\bBearer\s+[A-Za-z0-9._\-]{24,}",                   # inline bearer values
    r"\bAIza[0-9A-Za-z_\-]{30,}",                         # Google API keys
    r"\b[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}\.[A-Za-z0-9_\-]{12,}\b",  # JWT
]
_SECRET_RE = [re.compile(p) for p in SECRET_PATTERNS]
REDACTION = "[REDACTED-EXAMPLE-CREDENTIAL]"


def redact(obj):
    """Recursively replace credential-shaped substrings. Called on every record
    before it is written, not at the end of the run."""
    if isinstance(obj, str):
        for rx in _SECRET_RE:
            obj = rx.sub(REDACTION, obj)
        return obj
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, tuple):
        return tuple(redact(x) for x in obj)
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items()}
    return obj
