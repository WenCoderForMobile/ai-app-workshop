"""Talk to the active LLM. Phone never uses this module."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .config import CONFIG_PATH, active_provider, load_llm_config
from trace import flow, kv

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


class LlmClient:
    def __init__(
        self,
        provider_name: Optional[str] = None,
        config_path=None,
    ) -> None:
        self._config_path = config_path
        self._provider_override = provider_name
        self.reload()

    def reload(self) -> None:
        cfg = load_llm_config(self._config_path)
        if self._provider_override:
            if self._provider_override not in cfg["providers"]:
                raise ValueError("unknown provider: %s" % self._provider_override)
            cfg = dict(cfg)
            cfg["active"] = self._provider_override
        spec = active_provider(cfg)
        self.provider_name = spec["name"]
        self.label = spec["label"]
        self.base_url = spec["base_url"]
        self.api_key = spec["api_key"]
        self.model = spec["model"]
        self.timeout_seconds = spec["timeout_seconds"]
        if self.model in ("", "auto"):
            self.model = self._detect_local_model()

    def available(self) -> bool:
        if not self.base_url:
            return False
        if self.provider_name != "local" and not self.api_key:
            return False
        try:
            req = urllib.request.Request(
                self.base_url + "/models",
                headers=self._headers(),
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def chat_json(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        self.reload()
        started = time.monotonic()
        kv("config", str(self._config_path or CONFIG_PATH))
        kv("provider", self.provider_name)
        kv("model", self.model)
        kv("timeout", "%ss" % int(self.timeout_seconds))
        kv("api_key", "set" if self.api_key else "MISSING")
        if self.provider_name != "local" and not self.api_key:
            raise RuntimeError(
                "active=%s but API key is empty; check env.sh / DEEPSEEK_API_KEY"
                % self.provider_name
            )
        if self.provider_name == "local":
            content = self._ollama_chat(messages)
        else:
            content = self._openai_chat(messages)
        elapsed = time.monotonic() - started
        kv("elapsed", "%.1fs" % elapsed)
        kv("raw", content)
        parsed = _parse_json_content(content)
        flow("   parsed JSON ok")
        return parsed

    def _openai_chat(self, messages: List[Dict[str, str]]) -> str:
        url = self.base_url + "/chat/completions"
        kv("endpoint", url)
        flow("   waiting for model...")
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        payload = self._post_json(url, body)
        message = payload["choices"][0]["message"]
        return _message_text(message)

    def _ollama_chat(self, messages: List[Dict[str, str]]) -> str:
        # Native /api/chat: think=false so qwen3.6 does not spend minutes reasoning.
        root = self.base_url
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        url = root.rstrip("/") + "/api/chat"
        kv("endpoint", url)
        kv("think", "false")
        kv("format", "json")
        flow("   waiting for model...")
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "format": "json",
            "options": {"temperature": 0.2},
        }
        payload = self._post_json(url, body)
        return _message_text(payload.get("message") or {})

    def _post_json(self, url: str, body: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise RuntimeError("llm HTTP %s: %s" % (exc.code, detail)) from exc

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = "Bearer " + self.api_key
        return headers

    def _detect_local_model(self) -> str:
        if self.provider_name != "local":
            return "gpt-4o-mini"
        try:
            tags_url = self.base_url
            if tags_url.endswith("/v1"):
                tags_url = tags_url[: -len("/v1")] + "/api/tags"
            req = urllib.request.Request(tags_url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                tags = json.loads(resp.read().decode("utf-8"))
            names = [m.get("name") for m in tags.get("models", []) if m.get("name")]
            for preferred in ("qwen2.5:14b", "qwen3.6:27b", "qwen2.5:7b"):
                if preferred in names:
                    return preferred
            if names:
                return names[0]
        except Exception:
            pass
        return "qwen2.5:14b"


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        content = "".join(parts)
    text = str(content or "").strip()
    if not text:
        for key in ("reasoning_content", "reasoning", "thinking"):
            extra = message.get(key)
            if extra:
                text = str(extra).strip()
                break
    return _THINK_BLOCK.sub("", text).strip()


def _parse_json_content(content: str) -> Dict[str, Any]:
    text = _THINK_BLOCK.sub("", (content or "").strip()).strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                return obj
    raise ValueError("model did not return JSON: %r" % (text[:200],))
