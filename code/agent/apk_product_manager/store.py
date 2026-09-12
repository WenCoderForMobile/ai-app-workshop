"""Persist the user-confirmed intent. Later stages will read this file."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .archive import _safe_title


class IntentStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.latest_path = self.directory / "confirmed_intent.json"
        self.index_path = self.directory / "task_index.json"

    def save(self, intent: Dict[str, Any], utterances: list, task_id: str = '') -> Path:
        record = {
            "confirmedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "phone",
            "title": str(intent.get("title") or ""),
            "taskId": task_id or _safe_title(str(intent.get('title') or '')),
            "utterances": utterances,
            "intent": intent,
        }
        title = _safe_title(task_id or str(intent.get("title") or ""))
        titled = self.directory / ("%s.json" % title)
        payload = json.dumps(record, ensure_ascii=False, indent=2)
        titled.write_text(payload + "\n", encoding="utf-8")
        self.latest_path.write_text(payload + "\n", encoding="utf-8")
        return titled

    def latest(self) -> Optional[Dict[str, Any]]:
        if not self.latest_path.is_file():
            return None
        try:
            data = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        return data if isinstance(data, dict) else None

    def index_task(
        self,
        meta: Optional[Dict[str, Any]],
        intent: Optional[Dict[str, Any]],
        utterances: Optional[list] = None,
    ) -> None:
        meta = meta or {}
        intent = intent or {}
        rec = {
            "taskId": meta.get("taskId") or _safe_title(str(intent.get("title") or "")),
            "title": str(meta.get("title") or intent.get("title") or ""),
            "summary": str(meta.get("summary") or intent.get("summary") or ""),
            "packageName": meta.get("packageName") or "",
            "taskDir": meta.get("taskDir") or "",
            "productDir": meta.get("productDir") or "",
            "confirmedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "utterances": list(utterances or [])[-8:],
            "intent": intent,
        }
        items = [x for x in self.list_recent(40) if str(x.get("taskId") or "") != rec["taskId"]]
        items.insert(0, rec)
        self.index_path.write_text(
            json.dumps({"tasks": items[:40]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def list_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        seen = set()
        if self.index_path.is_file():
            try:
                raw = json.loads(self.index_path.read_text(encoding="utf-8"))
                items.extend(raw.get("tasks") or [])
            except Exception:
                pass
        latest = self.latest()
        if latest:
            items.insert(
                0,
                {
                    "taskId": latest.get('taskId') or _safe_title(str((latest.get("intent") or {}).get("title") or latest.get("title") or "")),
                    "title": latest.get("title") or (latest.get("intent") or {}).get("title") or "",
                    "summary": (latest.get("intent") or {}).get("summary") or "",
                    "intent": latest.get("intent") or {},
                    "utterances": latest.get("utterances") or [],
                    "confirmedAt": latest.get("confirmedAt") or "",
                },
            )
        out: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            key = str(item.get("taskId") or item.get("title") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
            if len(out) >= limit:
                break
        return out
