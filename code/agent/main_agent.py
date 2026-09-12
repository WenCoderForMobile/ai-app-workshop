"""Trusted publication hand-off between product/program agents and the phone."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Protocol

from download_server import DownloadServer
from trace import flow


class JobNotifier(Protocol):
    def send_job(self, job: dict) -> None: ...


_JOB_STATES = frozenset({"RUNNING", "SUCCEEDED", "FAILED"})
_REPORT_RESULTS = frozenset(
    {"PASSED", "FAILED", "REJECTED", "INCOMPATIBLE", "CORRUPTED", "QUARANTINED"}
)
_SAFE_ID = re.compile(r"[A-Za-z0-9._:-]{1,160}")
_SAFE_ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_SHA256 = re.compile(r"(?:sha256:)?([0-9a-f]{64})")


class MainAgent:
    """Publish descriptors, replay job state, and store safe device reports."""

    def __init__(
        self,
        notifier: JobNotifier,
        downloads: DownloadServer,
        state_path: Path,
        report_directory: Path,
    ) -> None:
        self.notifier = notifier
        self.downloads = downloads
        self.state_path = Path(state_path)
        self.report_directory = Path(report_directory)
        self._lock = threading.RLock()
        self._published_jobs: Dict[str, Dict[str, Any]] = {}
        self._load_published_jobs()

    def publish_job(self, job: Mapping[str, Any]) -> None:
        normalized = self._normalize_job(job)
        task_id = normalized["taskId"]
        with self._lock:
            self._published_jobs[task_id] = normalized
            self._persist_published_jobs_locked()
        self._try_send(normalized)

    def publish_plugin(self, result: Mapping[str, Any]) -> Dict[str, Any]:
        """Register a ProgramAgent result and return its small descriptor.

        ``ProductAgent`` emits the corresponding SUCCEEDED job through
        :meth:`publish_job`, keeping all three lifecycle states on one path.
        """

        if not isinstance(result, Mapping):
            raise TypeError("plugin result must be an object")
        manifest = result.get("manifest")
        if not isinstance(manifest, Mapping):
            raise ValueError("plugin manifest is missing")
        artifact_path = Path(result.get("path") or "").resolve(strict=True)
        embedded_manifest, manifest_bytes = _read_manifest(artifact_path)
        artifact_id = _required_id(manifest.get("artifactId") or result.get("artifactId"), "artifactId")
        task_id = _required_id(manifest.get("taskId"), "taskId")
        program_id = _required_id(manifest.get("programId") or result.get("programId"), "programId")
        version_id = _required_id(manifest.get("versionId"), "versionId")
        revision = _required_id(manifest.get("revision"), "revision")
        for field, expected in (
            ("artifactId", artifact_id),
            ("taskId", task_id),
            ("programId", program_id),
            ("versionId", version_id),
            ("revision", revision),
        ):
            if embedded_manifest.get(field) != expected:
                raise ValueError("artifact manifest %s mismatch" % field)
        transport_sha256 = _sha256_file(artifact_path)
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        download_url = self.downloads.register(artifact_id, artifact_path)
        descriptor = {
            "taskId": task_id,
            "artifactId": artifact_id,
            "programId": program_id,
            "versionId": version_id,
            "revision": revision,
            "downloadUrl": download_url,
            "transportSha256": transport_sha256,
            "manifestSha256": manifest_sha256,
            "size": artifact_path.stat().st_size,
        }
        return descriptor

    def replay_published(self) -> None:
        """Replay the latest durable state for each task after a reconnect."""

        with self._lock:
            jobs = [dict(job) for job in self._published_jobs.values()]
        for job in jobs:
            try:
                self.notifier.send_job(job)
            except (OSError, RuntimeError) as exc:
                flow("job replay stopped: %s" % exc)
                break

    def handle_artifact_report(self, text: str) -> str:
        """Validate and persist a deliberately small, redacted device report."""

        try:
            payload = json.loads(text)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("artifact report is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("artifact report must be an object")
        result = str(payload.get("result") or payload.get("state") or "").upper()
        if result not in _REPORT_RESULTS:
            raise ValueError("unsupported artifact report result")
        record: Dict[str, Any] = {
            "reportId": uuid.uuid4().hex,
            "receivedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "result": result,
            "taskId": _required_id(payload.get("taskId"), "taskId"),
            "artifactId": _required_id(payload.get("artifactId"), "artifactId"),
        }
        for key in ("programId", "versionId", "revision"):
            value = payload.get(key)
            if value not in (None, ""):
                record[key] = _required_id(value, key)
        phase = str(payload.get("phase") or "").upper()
        if phase:
            record["phase"] = _required_id(phase, "phase")
        digest = payload.get("transportSha256")
        if digest not in (None, ""):
            match = _SHA256.fullmatch(str(digest))
            if match is None:
                raise ValueError("invalid transportSha256")
            record["transportSha256"] = match.group(1)
        error_code = str(payload.get("errorCode") or "").upper()
        if error_code:
            record["errorCode"] = (
                error_code if _SAFE_ERROR_CODE.fullmatch(error_code) else "DEVICE_ERROR"
            )
        error_message = payload.get("errorMessage") or payload.get("message")
        if error_message:
            record["errorMessage"] = _redact(_bounded_text(error_message, 500))
        self.report_directory.mkdir(parents=True, exist_ok=True)
        path = self.report_directory / (
            "%s-%s.json" % (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
                record["reportId"][:12],
            )
        )
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        flow(
            "artifact report stored: task=%s artifact=%s result=%s"
            % (record["taskId"], record["artifactId"], result)
        )
        return "已收到插件验证结果。"

    def _try_send(self, job: Dict[str, Any]) -> None:
        try:
            self.notifier.send_job(job)
        except (OSError, RuntimeError) as exc:
            # The durable state is replayed when the phone reconnects.
            flow("job queued for replay: %s" % exc)

    def _normalize_job(self, job: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(job, Mapping):
            raise TypeError("job must be an object")
        status = str(job.get("status") or job.get("state") or "").upper()
        if status not in _JOB_STATES:
            raise ValueError("unsupported job state")
        normalized: Dict[str, Any] = {
            "taskId": _required_id(job.get("taskId"), "taskId"),
            "status": status,
        }
        allowed_text = ("title", "phase", "artifactId", "programId", "versionId", "revision", "downloadUrl", "transportSha256", "manifestSha256", "errorCode", "error")
        for key in allowed_text:
            value = job.get(key)
            if value not in (None, ""):
                normalized[key] = _bounded_text(value, 1000 if key in ("downloadUrl", "error") else 200)
        if "size" in job:
            size = int(job["size"])
            if size < 0:
                raise ValueError("negative artifact size")
            normalized["size"] = size
        return normalized

    def _load_published_jobs(self) -> None:
        if not self.state_path.is_file():
            return
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            values = raw.values() if isinstance(raw, dict) else raw
            loaded = {}
            changed = False
            for value in values:
                normalized = self._normalize_job(value)
                if isinstance(value, Mapping) and "status" not in value and "state" in value:
                    changed = True
                if normalized["status"] == "RUNNING":
                    normalized = {
                        "taskId": normalized["taskId"],
                        "status": "FAILED",
                        "phase": normalized.get("phase", "COMPILING"),
                        "title": normalized.get("title", ""),
                        "errorCode": "AGENT_RESTARTED",
                        "error": "agent restarted before the task completed",
                    }
                    changed = True
                elif normalized["status"] == "SUCCEEDED":
                    if not self._restore_artifact_registration(normalized):
                        normalized = {
                            "taskId": normalized["taskId"],
                            "status": "FAILED",
                            "title": normalized.get("title", ""),
                            "errorCode": "ARTIFACT_UNAVAILABLE",
                            "error": "published artifact is unavailable",
                        }
                    changed = True
                loaded[normalized["taskId"]] = normalized
        except Exception as exc:
            flow("published job state ignored: %s" % exc)
            return
        self._published_jobs = loaded
        if changed:
            self._persist_published_jobs_locked()

    def _restore_artifact_registration(self, job: Dict[str, Any]) -> bool:
        try:
            artifact_id = _required_id(job.get("artifactId"), "artifactId")
            program_id = _required_id(job.get("programId"), "programId")
            version_id = _required_id(job.get("versionId"), "versionId")
            expected_size = int(job.get("size"))
            expected_transport = _required_sha256(job.get("transportSha256"), "transportSha256")
            expected_manifest = _required_sha256(job.get("manifestSha256"), "manifestSha256")
            candidate = self.downloads.root / program_id / (version_id + ".apkg")
            if candidate.stat().st_size != expected_size:
                return False
            if _sha256_file(candidate) != expected_transport:
                return False
            embedded, manifest_bytes = _read_manifest(candidate)
            if hashlib.sha256(manifest_bytes).hexdigest() != expected_manifest:
                return False
            for field, expected in (
                ("artifactId", artifact_id),
                ("taskId", job["taskId"]),
                ("programId", program_id),
                ("versionId", version_id),
                ("revision", job.get("revision")),
            ):
                if embedded.get(field) != expected:
                    return False
            job["downloadUrl"] = self.downloads.register(artifact_id, candidate)
            job["transportSha256"] = expected_transport
            job["manifestSha256"] = expected_manifest
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _persist_published_jobs_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_name(".%s.%s.tmp" % (self.state_path.name, uuid.uuid4().hex))
        temporary.write_text(
            json.dumps(self._published_jobs, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)


def _required_id(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not _SAFE_ID.fullmatch(text):
        raise ValueError("invalid %s" % field)
    return text


def _bounded_text(value: Any, limit: int) -> str:
    return str(value).replace("\x00", "")[:limit]


def _required_sha256(value: Any, field: str) -> str:
    match = _SHA256.fullmatch(str(value or ""))
    if match is None:
        raise ValueError("invalid %s" % field)
    return match.group(1)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> tuple[Dict[str, Any], bytes]:
    try:
        with zipfile.ZipFile(path) as package:
            manifest = package.read("manifest.json")
    except (KeyError, OSError, zipfile.BadZipFile) as exc:
        raise ValueError("artifact has no readable manifest") from exc
    try:
        parsed = json.loads(manifest)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("artifact manifest is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("artifact manifest must be an object")
    return parsed, manifest


def _manifest_sha256(path: Path) -> str:
    _, manifest = _read_manifest(path)
    return hashlib.sha256(manifest).hexdigest()


def _redact(value: str) -> str:
    value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", value)
    return re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret|authorization)\b\s*[:=]\s*[^\s,;]+",
        lambda match: "%s=[REDACTED]" % match.group(1),
        value,
    )
