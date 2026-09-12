"""Small typed control channel between the phone and the local Agent.

Artifacts deliberately do not travel on this socket. They are published by
``DownloadServer`` and this channel only carries chat, job descriptors, and
bounded device reports.
"""
from __future__ import annotations

import json
import queue
import socket
import threading
import time
from datetime import datetime, timezone
from uuid import uuid4
from capability_report import normalize_capabilities
from typing import Callable, Optional, Tuple

from trace import flow, kv


OnInbound = Callable[[str, str], Optional[str]]
OnConnected = Callable[[], None]

MAX_CONTROL_FRAME_BYTES = 64 * 1024
MAX_PENDING_MESSAGES = 32
INBOUND_TYPES = frozenset({"chat", "artifact_report", "runtime_error", "catalog_check", "task_check"})


class ConnectPhone:
    def __init__(self, host: str = "127.0.0.1", port: int = 17890) -> None:
        self.host = host
        self.port = port
        self._conn: Optional[socket.socket] = None
        self._server: Optional[socket.socket] = None
        self._write_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._inbound_queue: queue.Queue[Optional[Tuple[str, str]]] = queue.Queue(
            maxsize=MAX_PENDING_MESSAGES
        )
        self._worker: Optional[threading.Thread] = None
        self._capability_lock = threading.Lock()
        self._capability_pending = {}
        self._capability_cache = None

    def request_capabilities(self, timeout: float = 8.0) -> dict:
        """Query the current phone; never use another connection's or a stale report."""
        conn = self._conn
        unknown = {'status': 'unknown', 'reasonCode': 'PHONE_NOT_CONNECTED'}
        if conn is None:
            return unknown
        with self._capability_lock:
            cached = self._capability_cache
            if cached and cached[0] is conn and time.monotonic() - cached[1] < 60:
                return dict(cached[2], ageSeconds=round(time.monotonic() - cached[1], 1))
            existing = next(((key, value) for key, value in self._capability_pending.items()
                             if value['connection'] is conn), None)
            owner = existing is None
            if existing is not None:
                request_id, pending = existing
                event = pending['event']
            else:
                request_id = uuid4().hex
                event = threading.Event()
                pending = {'connection': conn, 'event': event, 'report': None}
                self._capability_pending[request_id] = pending
        try:
            if owner:
                self._send_typed('capability_probe', json.dumps({'requestId': request_id}))
            event.wait(timeout)
            with self._capability_lock:
                report = pending['report'] if self._conn is conn else None
            if report is None:
                return {'status': 'unknown', 'reasonCode': 'PROBE_UNAVAILABLE_OR_TIMED_OUT',
                        'nextAction': '自动重试或检查客户端版本；不要询问用户设备能否发音'}
            return dict(report, ageSeconds=0)
        except (OSError, RuntimeError):
            return unknown
        finally:
            if owner:
                with self._capability_lock:
                    self._capability_pending.pop(request_id, None)

    def _receive_capabilities(self, text: str) -> None:
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return
        report = normalize_capabilities(data)
        if report is None:
            return
        request_id = data.get('requestId')
        if not isinstance(request_id, str):
            return
        with self._capability_lock:
            pending = self._capability_pending.get(request_id)
            if pending is None or pending['connection'] is not self._conn:
                return
            result = dict(report, status='observed', observedAt=datetime.now(timezone.utc).isoformat())
            pending['report'] = result
            self._capability_cache = (self._conn, time.monotonic(), result)
            pending['event'].set()

    def _forget_capabilities(self) -> None:
        with self._capability_lock:
            self._capability_cache = None
            for pending in self._capability_pending.values():
                pending['report'] = None
                pending['event'].set()

    def send_to_phone(self, text: str) -> None:
        self._send_typed("chat", text)

    def send_job(self, job: dict) -> None:
        self._send_typed(
            "job",
            json.dumps(job, ensure_ascii=False, separators=(",", ":")),
        )

    def _send_typed(self, message_type: str, text: str) -> None:
        payload = json.dumps(
            {"type": message_type, "text": text},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        raw = (payload + "\n").encode("utf-8")
        if len(raw) > MAX_CONTROL_FRAME_BYTES:
            raise ValueError("control frame exceeds 64 KiB")
        with self._write_lock:
            conn = self._conn
            if conn is None:
                raise RuntimeError("phone is not connected")
            conn.sendall(raw)

    def receive_from_phone(
        self,
        on_inbound: OnInbound,
        on_connected: Optional[OnConnected] = None,
    ) -> None:
        """Serve phone connections until interrupted or :meth:`stop` is called.

        Only one phone is active at a time in the ADB demo, but a disconnect no
        longer terminates the whole Agent process. ``on_connected`` is invoked
        after the new socket becomes writable so callers can replay state.
        """

        self._stop_event.clear()
        self._inbound_queue = queue.Queue(maxsize=MAX_PENDING_MESSAGES)
        worker = threading.Thread(
            target=self._dispatch_loop,
            args=(on_inbound,),
            name="phone-inbound",
            daemon=True,
        )
        self._worker = worker
        worker.start()
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((self.host, self.port))
            server.listen(5)
            server.settimeout(0.5)
        except Exception:
            server.close()
            self._stop_event.set()
            self._stop_worker()
            raise
        self.port = int(server.getsockname()[1])
        self._server = server
        print("connect_phone listening on %s:%s" % (self.host, self.port), flush=True)
        print("waiting for phone via adb reverse", flush=True)
        try:
            while not self._stop_event.is_set():
                try:
                    conn, addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop_event.is_set():
                        break
                    raise
                self._forget_capabilities()
                self._conn = conn
                print("phone connected:", addr, flush=True)
                try:
                    if on_connected is not None:
                        try:
                            on_connected()
                        except Exception as exc:
                            flow("state replay failed: %s" % exc)
                    self._read_loop(conn, on_inbound)
                finally:
                    if self._conn is conn:
                        self._conn = None
                        self._forget_capabilities()
                    try:
                        conn.close()
                    except OSError:
                        pass
                    print("phone disconnected; waiting for reconnect", flush=True)
        except KeyboardInterrupt:
            pass
        finally:
            self._server = None
            try:
                server.close()
            except OSError:
                pass
            self._stop_worker()

    def stop(self) -> None:
        """Stop a running accept/read loop. Primarily useful for clean shutdown."""

        self._stop_event.set()
        self._forget_capabilities()
        conn = self._conn
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
        server = self._server
        if server is not None:
            try:
                server.close()
            except OSError:
                pass

    def _read_loop(self, conn: socket.socket, on_inbound: OnInbound) -> None:
        buffer = bytearray()
        try:
            while not self._stop_event.is_set():
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buffer.extend(chunk)
                while True:
                    newline = buffer.find(b"\n")
                    if newline < 0:
                        break
                    raw_line = bytes(buffer[:newline])
                    del buffer[: newline + 1]
                    if len(raw_line) > MAX_CONTROL_FRAME_BYTES:
                        self._send_protocol_error("FRAME_TOO_LARGE")
                        return
                    if not self._handle_line(raw_line, on_inbound):
                        return
                if len(buffer) > MAX_CONTROL_FRAME_BYTES:
                    self._send_protocol_error("FRAME_TOO_LARGE")
                    return
        except OSError as exc:
            if not self._stop_event.is_set():
                print("phone read ended:", exc, flush=True)

    def _handle_line(self, raw_line: bytes, on_inbound: OnInbound) -> bool:
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            self._send_protocol_error("INVALID_UTF8")
            return True
        message_type, text = self._parse_line(line)
        if message_type == "capability_report":
            self._receive_capabilities(text)
            return True
        if message_type == "ping":
            # Answer on the transport reader, never behind LLM/build jobs.
            # Echo only bounded probe tokens; heartbeat is not a user message.
            if text and len(text) <= 128:
                self._send_typed("pong", text)
            return True
        if not text:
            return True
        if message_type not in INBOUND_TYPES:
            self._send_protocol_error("UNSUPPORTED_MESSAGE_TYPE")
            return True
        flow("phone -> agent: [%s] %s" % (message_type, text[:400]))
        if message_type == "chat":
            try:
                self.send_to_phone("正在理解你的需求…")
                flow("agent -> phone: 正在理解你的需求…")
            except Exception as exc:
                flow("progress send failed: %s" % exc)
        try:
            self._inbound_queue.put_nowait((message_type, text))
        except queue.Full:
            self._send_protocol_error("BUSY")
        return True

    def _dispatch_loop(self, on_inbound: OnInbound) -> None:
        while True:
            if self._stop_event.is_set():
                return
            try:
                item = self._inbound_queue.get(timeout=0.25)
            except queue.Empty:
                if self._stop_event.is_set():
                    return
                continue
            try:
                if item is None:
                    return
                message_type, text = item
                try:
                    reply = on_inbound(message_type, text)
                except Exception as exc:
                    flow("inbound handler error: %s" % exc)
                    reply = "处理消息时出错，请再发一次。"
                if reply:
                    try:
                        self.send_to_phone(reply)
                        kv("agent -> phone", reply)
                    except ValueError as exc:
                        flow("reply rejected: %s" % exc)
                        self._send_protocol_error("RESPONSE_TOO_LARGE")
                    except (OSError, RuntimeError) as exc:
                        flow("reply queued state only; phone unavailable: %s" % exc)
            finally:
                self._inbound_queue.task_done()

    def _stop_worker(self) -> None:
        worker = self._worker
        if worker is None:
            return
        self._worker = None
        try:
            self._inbound_queue.put_nowait(None)
        except queue.Full:
            pass
        if worker is not threading.current_thread():
            worker.join(timeout=2)

    def _send_protocol_error(self, code: str) -> None:
        try:
            self._send_typed("error", code)
        except Exception as exc:
            flow("protocol error send failed: %s" % exc)

    @staticmethod
    def _parse_line(line: str) -> Tuple[str, str]:
        trimmed = line.strip()
        if not trimmed:
            return "chat", ""
        try:
            obj = json.loads(trimmed)
            if isinstance(obj, dict):
                message_type = str(obj.get("type") or "chat").strip() or "chat"
                body = obj.get("text")
                if body is None and "payload" in obj:
                    body = json.dumps(
                        obj["payload"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                return message_type, str(body or "").strip()
        except (TypeError, ValueError):
            pass
        # Plain text remains a development-only backwards-compatible chat
        # input. All server output is typed JSON.
        return "chat", trimmed
