"""Terminal trace for the LLM / agent pipeline."""
from __future__ import annotations

from typing import Any


def flow(message: str) -> None:
    print(message, flush=True)


def begin(title: str) -> None:
    print("", flush=True)
    print("======== %s ========" % title, flush=True)


def end() -> None:
    print("======== done ========", flush=True)


def kv(label: str, value: Any) -> None:
    text = str(value)
    print("  %s: %s" % (label, text), flush=True)
