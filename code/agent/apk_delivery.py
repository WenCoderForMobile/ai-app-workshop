"""Durable APK job delivery over the existing bounded control channel."""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from download_server import MAX_ARTIFACT_BYTES


class ApkDelivery:
    def __init__(self, bridge, downloads, state_path: Path):
        self.bridge = bridge
        self.downloads = downloads
        self.path = state_path
        self.lock = threading.RLock()
        self.jobs = {}
        if self.path.is_file():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, job in raw.items():
                if job.get("state") == "downloading":
                    artifact = self.downloads.root / "artifacts" / (job["sha256"] + ".apk")
                    if artifact.is_file():
                        job["downloadUrl"] = self.downloads.register(job["sha256"], artifact)
                    else:
                        job.update(state="failed", error="制品文件缺失，请重新制作。")
                elif job.get("state") == "making":
                    job.update(state="failed", error="服务已重启，可以继续原任务。")
                self.jobs[key] = job

    def publish(self, job):
        job = dict(job)
        if job.get("state") == "downloading":
            path = Path(job.pop("apkPath")).resolve(strict=True)
            path.relative_to(self.downloads.root)
            if not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
                raise ValueError("APK exceeds size limit")
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            dest = self.downloads.root / "artifacts" / (digest + ".apk")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                if dest.read_bytes() != data:
                    raise ValueError("artifact content conflict")
            else:
                temp = dest.with_suffix(".tmp")
                temp.write_bytes(data)
                temp.replace(dest)
            job.update(sha256=digest, size=len(data), launchType="apk",
                       downloadUrl=self.downloads.register(digest, dest))
        with self.lock:
            self.jobs[job["taskId"]] = job
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(self.jobs, ensure_ascii=False), encoding="utf-8")
            temp.replace(self.path)
        self._send(job)

    def replay(self):
        with self.lock:
            jobs = list(self.jobs.values())
        for job in jobs:
            self._send(job)

    def send_catalog(self, request_id):
        """One bounded frame per artifact, then a completion marker, only on client request."""
        if not re.fullmatch(r'[a-f0-9]{32}', request_id):
            return
        with self.lock:
            jobs = [dict(job) for job in self.jobs.values()
                    if job.get('state') == 'downloading' and job.get('sha256') and job.get('downloadUrl')]
        # Do not swallow transport errors and emit a misleading partial completion.
        for job in jobs:
            self.bridge.send_job({'kind': 'catalog_entry', 'requestId': request_id, 'job': job})
        self.bridge.send_job({'kind': 'catalog_complete', 'requestId': request_id, 'count': len(jobs)})

    def _send(self, job):
        try:
            self.bridge.send_job(job)
        except (OSError, RuntimeError):
            pass  # Client recovers its waiting tasks by ID after reconnect.

    def send_task_statuses(self, text):
        """Recover only client-known tasks; never replay unrelated cloud programs."""
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            return
        ids = payload.get('taskIds') if isinstance(payload, dict) else None
        if not isinstance(ids, list) or not 0 < len(ids) <= 50:
            return
        if any(not isinstance(key, str) or not key.strip() or len(key) > 160 for key in ids):
            return
        with self.lock:
            jobs = [dict(self.jobs[key]) if key in self.jobs else
                    {'kind': 'task_missing', 'taskId': key} for key in dict.fromkeys(ids)]
        for job in jobs:
            self.bridge.send_job(job)

    def chat(self, text):
        if text:
            try:
                self.bridge.send_to_phone(text)
            except (OSError, RuntimeError):
                pass

    def dispatch(self, reply):
        self.chat(reply.text)
        if reply.job:
            self.publish(reply.job)
        if reply.follow_up:
            try:
                self.dispatch(reply.follow_up(self.chat))
            except Exception as exc:
                failed = dict(reply.job or {})
                failed.update(state="failed", error=str(exc)[:500])
                self.publish(failed)
                self.chat('制作或发布失败，可以回复“继续制作%s”重试。' % (failed.get('title') or failed.get('taskId') or '原程序'))
