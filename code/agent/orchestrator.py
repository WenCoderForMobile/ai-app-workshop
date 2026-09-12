"""Main agent: product archive → program APK → notify phone to download."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from download_server import DownloadServer
from apk_product_manager import ProductAgent
from program_agent import ProgramAgent
from program_agent.cli_supervisor import ASK_USER, DESIGN_INCOMPLETE, EXHAUSTED
from trace import flow, kv


ProgressFn = Callable[[str], None]


@dataclass
class AgentReply:
    text: str
    plugin: Optional[Dict[str, Any]] = None
    job: Optional[Dict[str, Any]] = None
    follow_up: Optional[Callable[[ProgressFn], "AgentReply"]] = None


class Orchestrator:
    def __init__(
        self,
        product: ProductAgent,
        program: ProgramAgent,
        downloads: DownloadServer,
    ) -> None:
        self.product = product
        self.program = program
        self.downloads = downloads
        self._repair_counts = {}

    def handle_inbound(self, msg_type: str, text: str) -> AgentReply:
        if msg_type == "runtime_error":
            return self.handle_runtime_error(text)
        return self.handle(text)

    def handle(self, text: str) -> AgentReply:
        reply = self.product.handle(text)
        if not getattr(self.product, "just_confirmed", False):
            return AgentReply(text=reply)
        meta = dict(self.product.last_archive or {})
        if not meta.get("taskId"):
            return AgentReply(text=reply + "\n产品归档失败，无法开始制作插件。")
        self._repair_counts.pop(meta["taskId"], None)
        job = _job_from_meta(meta, "making")
        flow("main agent: notify phone making")
        kv("taskId", job["taskId"])

        def _build(on_progress: ProgressFn) -> AgentReply:
            return self._after_program(meta, self.program.build(meta, on_progress=on_progress))

        return AgentReply(text=reply, job=job, follow_up=_build)

    def handle_runtime_error(self, text: str) -> AgentReply:
        try:
            payload = json.loads(text) if isinstance(text, str) else dict(text or {})
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        task_id = str(payload.get("taskId") or "").strip()
        meta = self._meta_for_task(task_id)
        if not meta:
            return AgentReply(text="收到运行错误，但找不到对应程序归档，无法在原代码上修复。")
        attempts = self._repair_counts.get(task_id, 0)
        if attempts >= 2:
            return AgentReply(text="该程序已自动修复两次，请补充问题现象后重新确认制作。")
        self._repair_counts[task_id] = attempts + 1
        writer = getattr(self.product, "archive_writer", None)
        if writer is not None:
            writer.reopen(meta)
            writer.append_alignment(
                kind="runtime_fix",
                user_text="手机%s失败：%s"
                % (payload.get("phase") or "runtime", payload.get("message") or ""),
                agent_reply="按堆栈修改现有 PluginMain，不从零重写。",
                extra={
                    "phase": payload.get("phase") or "",
                    "exceptionClass": payload.get("exceptionClass") or "",
                    "stackTrace": str(payload.get("stackTrace") or "")[:4000],
                },
            )
        title = meta.get("title") or task_id
        job = _job_from_meta(meta, "making")
        flow("main agent: runtime error repair")
        kv("taskId", job["taskId"])
        kv("phase", payload.get("phase") or "")

        def _repair(on_progress: ProgressFn) -> AgentReply:
            return self._after_program(
                meta,
                self.program.repair(meta, payload, on_progress=on_progress),
            )

        return AgentReply(
            text="收到「%s」运行错误，正在原代码上修复。" % title,
            job=job,
            follow_up=_repair,
        )

    def _after_program(
        self,
        meta: Dict[str, Any],
        result: Tuple[Optional[Dict[str, Any]], str, str],
    ) -> AgentReply:
        built, extra, outcome = result
        if outcome in (ASK_USER, DESIGN_INCOMPLETE, EXHAUSTED) or built is None:
            if outcome in (ASK_USER, DESIGN_INCOMPLETE, EXHAUSTED):
                self.product.reopen_for_dev_question(extra)
            failed = _job_from_meta(meta, "failed")
            failed["error"] = extra
            flow("main agent: notify phone failed (%s)" % outcome)
            return AgentReply(text=extra, job=failed)
        built["state"] = "downloading"
        flow("main agent: APK ready; publish task status (client downloads on manual sync)")
        # ApkDelivery snapshots and registers the built APK before transmission.
        return AgentReply(text=extra, job=built)

    def _meta_for_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        if not task_id or task_id in (".", "..") or "/" in task_id or "\\" in task_id:
            return None
        current = getattr(self.product, "last_archive", None) or {}
        if str(current.get("taskId") or "") == task_id:
            return dict(current)
        completed = getattr(self.product, "completed_archive", None) or {}
        if str(completed.get("taskId") or "") == task_id:
            return dict(completed)
        store = getattr(self.product, "store", None)
        if store is not None:
            for rec in store.list_recent(40):
                if str(rec.get("taskId") or "") != task_id:
                    continue
                task_dir = rec.get("taskDir") or ""
                loaded = _read_meta_file(Path(task_dir) / "meta.json") if task_dir else None
                if loaded:
                    return loaded
                if rec.get("taskDir") and rec.get("packageName"):
                    return {
                        "taskId": rec.get("taskId") or "",
                        "title": rec.get("title") or "",
                        "summary": rec.get("summary") or "",
                        "packageName": rec.get("packageName") or "",
                        "icon": rec.get("icon") or "📦",
                        "taskDir": rec.get("taskDir") or "",
                        "productDir": rec.get("productDir") or "",
                    }
        tasks_root = self.program.agent_root / "out" / "tasks"
        direct = _read_meta_file(tasks_root / task_id / "meta.json")
        if direct:
            return direct
        if tasks_root.is_dir():
            for folder in tasks_root.iterdir():
                loaded = _read_meta_file(folder / "meta.json")
                if loaded and str(loaded.get("taskId") or folder.name) == task_id:
                    return loaded
        return None


def _job_from_meta(meta: Dict[str, Any], state: str) -> Dict[str, Any]:
    return {
        "taskId": meta.get("taskId") or "",
        "title": meta.get("title") or "",
        "summary": meta.get("summary") or "",
        "icon": meta.get("icon") or "📦",
        "state": state,
        "packageName": meta.get("packageName") or "",
        "entryClass": (meta.get("packageName") or "") + ".PluginMain",
        "downloadUrl": "",
        "error": "",
    }


def _read_meta_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) else None
