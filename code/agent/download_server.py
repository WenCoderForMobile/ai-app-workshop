"""Restricted HTTP server for immutable declarative ``.apkg`` artifacts."""
from __future__ import annotations

import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional
from urllib.parse import quote, unquote, urlsplit


_SAFE_ARTIFACT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
MAX_ARTIFACT_BYTES = 10 * 1024 * 1024


class DownloadServer:
    """Serve only explicitly registered files located below ``root``.

    There is no directory listing and arbitrary relative paths are never mapped
    to the filesystem. The default bind address is loopback because this server
    is reached through ``adb reverse`` in the development flow.
    """

    def __init__(self, root: Path, host: str = "127.0.0.1", port: int = 17891) -> None:
        self.root = Path(root).resolve()
        self.host = host
        self.port = port
        self._artifacts: Dict[str, Path] = {}
        self._lock = threading.RLock()
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def register(self, artifact_id: str, path: Path) -> str:
        """Register one immutable artifact and return its download URL."""

        if not _SAFE_ARTIFACT_ID.fullmatch(str(artifact_id)):
            raise ValueError("unsafe artifact id")
        candidate = Path(path).resolve(strict=True)
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("artifact is outside the configured root") from exc
        if not candidate.is_file():
            raise ValueError("artifact is not a regular file")
        if not 1 <= candidate.stat().st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("artifact size is outside the allowed range")
        with self._lock:
            previous = self._artifacts.get(artifact_id)
            if previous is not None and previous != candidate:
                raise ValueError("artifact id is already registered")
            self._artifacts[artifact_id] = candidate
        return self.url_for(artifact_id)

    def url_for(self, artifact_id: str) -> str:
        if not _SAFE_ARTIFACT_ID.fullmatch(str(artifact_id)):
            raise ValueError("unsafe artifact id")
        advertised_host = "127.0.0.1" if self.host in ("0.0.0.0", "::") else self.host
        return "http://%s:%s/artifacts/%s" % (
            advertised_host,
            self.port,
            quote(artifact_id, safe=""),
        )

    def start(self) -> None:
        if self._httpd is not None:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
                self._serve(send_body=True)

            def do_HEAD(self) -> None:  # noqa: N802 - stdlib callback name
                self._serve(send_body=False)

            def _serve(self, send_body: bool) -> None:
                artifact = owner._resolve_request(self.path)
                if artifact is None:
                    self.send_error(404)
                    return
                try:
                    size = artifact.stat().st_size
                    stream = artifact.open("rb")
                except OSError:
                    self.send_error(404)
                    return
                with stream:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.android.package-archive" if artifact.suffix == ".apk" else "application/vnd.auto-procedure.apkg")
                    self.send_header("Content-Length", str(size))
                    self.send_header("Cache-Control", "private, no-transform")
                    self.send_header("X-Content-Type-Options", "nosniff")
                    self.end_headers()
                    if not send_body:
                        return
                    while True:
                        chunk = stream.read(64 * 1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)

            def log_message(self, fmt: str, *args) -> None:
                print("artifact download %s" % (fmt % args), flush=True)

        httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        httpd.daemon_threads = True
        self.port = int(httpd.server_address[1])
        self._httpd = httpd
        thread = threading.Thread(
            target=httpd.serve_forever,
            name="artifact-download",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        print(
            "artifact download server %s:%s root=%s" % (self.host, self.port, self.root),
            flush=True,
        )

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def _resolve_request(self, target: str) -> Optional[Path]:
        request_path = urlsplit(target).path
        prefix = "/artifacts/"
        if not request_path.startswith(prefix):
            return None
        encoded_id = request_path[len(prefix) :]
        if not encoded_id or "/" in encoded_id:
            return None
        artifact_id = unquote(encoded_id)
        if not _SAFE_ARTIFACT_ID.fullmatch(artifact_id):
            return None
        with self._lock:
            artifact = self._artifacts.get(artifact_id)
        if artifact is None:
            return None
        try:
            resolved = artifact.resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None
