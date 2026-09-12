"""Load LLM providers from one config file.

Switch model by changing `active`. Add a new provider by inserting a key
under `providers` with base_url / api_key / model — no code change.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict

_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"


def load_llm_config(path: Path | None = None) -> Dict[str, Any]:
    config_path = path or CONFIG_PATH
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    active = str(raw.get("active") or "").strip()
    providers = raw.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ValueError("llm/config.json needs a providers object")
    if active not in providers:
        raise ValueError("active=%r is not in providers: %s" % (active, list(providers)))
    return raw


def active_provider(config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = config or load_llm_config()
    name = str(cfg["active"])
    spec = dict(cfg["providers"][name])
    spec["name"] = name
    spec["base_url"] = _expand(str(spec.get("base_url") or "")).rstrip("/")
    spec["api_key"] = _expand(str(spec.get("api_key") or ""))
    spec["model"] = _expand(str(spec.get("model") or "auto"))
    spec["timeout_seconds"] = float(spec.get("timeout_seconds") or 60)
    spec["label"] = str(spec.get("label") or name)
    return spec


def _expand(value: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), "")

    return _ENV_PATTERN.sub(repl, value).strip()
