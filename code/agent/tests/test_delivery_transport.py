from __future__ import annotations

import hashlib
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from connect_phone import ConnectPhone
from connect_phone.connect_phone import MAX_CONTROL_FRAME_BYTES, MAX_PENDING_MESSAGES
from download_server import DownloadServer
from main_agent import MainAgent


class FakeNotifier:
    def __init__(self) -> None:
        self.jobs = []

    def send_job(self, job: dict) -> None:
        self.jobs.append(json.loads(json.dumps(job)))


def make_artifact(root: Path) -> tuple[dict, bytes, bytes]:
    manifest = {
        "artifactId": "artifact-0123456789abcdef",
        "taskId": "task-0123456789abcdef",
        "programId": "program-demo",
        "versionId": "0123456789abcdef",
        "revision": "0123456789abcdef",
    }
    manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
    path = root / manifest["programId"] / (manifest["versionId"] + ".apkg")
    path.parent.mkdir(parents=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("manifest.json", manifest_bytes)
        package.writestr("program/main.json", b"{}")
        package.writestr("tests/smoke.json", b"{}")
    package_bytes = path.read_bytes()
    return (
        {
            "path": path,
            "bytes": package_bytes,
            "programId": manifest["programId"],
            "title": "Demo",
            "artifactId": manifest["artifactId"],
            "manifest": manifest,
        },
        package_bytes,
        manifest_bytes,
    )


class DeliveryTest(unittest.TestCase):
    def test_descriptor_uses_http_and_control_job_has_no_artifact_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            result, package_bytes, manifest_bytes = make_artifact(artifact_root)
            downloads = DownloadServer(artifact_root, port=0)
            downloads.start()
            self.addCleanup(downloads.stop)
            notifier = FakeNotifier()
            agent = MainAgent(
                notifier,
                downloads,
                root / "published.json",
                root / "reports",
            )

            descriptor = agent.publish_plugin(result)
            agent.publish_job({"status": "SUCCEEDED", "title": "Demo", **descriptor})

            self.assertEqual(
                descriptor["transportSha256"], hashlib.sha256(package_bytes).hexdigest()
            )
            self.assertEqual(
                descriptor["manifestSha256"], hashlib.sha256(manifest_bytes).hexdigest()
            )
            self.assertEqual(descriptor["size"], len(package_bytes))
            self.assertEqual(urllib.request.urlopen(descriptor["downloadUrl"]).read(), package_bytes)
            wire = json.dumps(notifier.jobs[-1])
            self.assertNotIn("bytes", wire)
            self.assertNotIn("packageBase64", wire)
            self.assertEqual(notifier.jobs[-1]["status"], "SUCCEEDED")
            with self.assertRaises(urllib.error.HTTPError) as failure:
                urllib.request.urlopen(
                    "http://127.0.0.1:%s/not-registered" % downloads.port
                )
            self.assertEqual(failure.exception.code, 404)

    def test_succeeded_job_is_registered_again_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            result, package_bytes, _ = make_artifact(artifact_root)
            state_path = root / "published.json"
            first_server = DownloadServer(artifact_root, port=0)
            first_server.start()
            first = MainAgent(FakeNotifier(), first_server, state_path, root / "reports")
            descriptor = first.publish_plugin(result)
            first.publish_job({"status": "SUCCEEDED", "title": "Demo", **descriptor})
            first_server.stop()

            second_server = DownloadServer(artifact_root, port=0)
            second_server.start()
            self.addCleanup(second_server.stop)
            notifier = FakeNotifier()
            restored = MainAgent(notifier, second_server, state_path, root / "reports")
            restored.replay_published()

            self.assertEqual(len(notifier.jobs), 1)
            replay = notifier.jobs[0]
            self.assertEqual(replay["status"], "SUCCEEDED")
            self.assertIn(":%s/" % second_server.port, replay["downloadUrl"])
            self.assertEqual(urllib.request.urlopen(replay["downloadUrl"]).read(), package_bytes)
            self.assertNotIn("artifactPath", state_path.read_text(encoding="utf-8"))

    def test_running_job_becomes_failed_after_process_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads = DownloadServer(root / "artifacts", port=17891)
            state_path = root / "published.json"
            first = MainAgent(FakeNotifier(), downloads, state_path, root / "reports")
            first.publish_job(
                {
                    "taskId": "task-running",
                    "status": "RUNNING",
                    "phase": "COMPILING",
                    "title": "Demo",
                }
            )
            notifier = FakeNotifier()
            restored = MainAgent(notifier, downloads, state_path, root / "reports")
            restored.replay_published()
            self.assertEqual(notifier.jobs[0]["status"], "FAILED")
            self.assertEqual(notifier.jobs[0]["errorCode"], "AGENT_RESTARTED")

    def test_legacy_state_field_is_loaded_as_canonical_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_path = root / "published.json"
            state_path.write_text(
                '{"task-old":{"taskId":"task-old","state":"FAILED","errorCode":"OLD"}}',
                encoding="utf-8",
            )
            notifier = FakeNotifier()
            agent = MainAgent(
                notifier,
                DownloadServer(root / "artifacts"),
                state_path,
                root / "reports",
            )
            agent.replay_published()
            self.assertEqual(notifier.jobs[0]["status"], "FAILED")
            self.assertNotIn("state", notifier.jobs[0])

    def test_tampered_published_artifact_is_not_replayed_as_succeeded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifact_root = root / "artifacts"
            result, _, _ = make_artifact(artifact_root)
            state_path = root / "published.json"
            downloads = DownloadServer(artifact_root)
            first = MainAgent(FakeNotifier(), downloads, state_path, root / "reports")
            descriptor = first.publish_plugin(result)
            first.publish_job({"status": "SUCCEEDED", "title": "Demo", **descriptor})
            result["path"].write_bytes(b"tampered")

            notifier = FakeNotifier()
            restored = MainAgent(notifier, downloads, state_path, root / "reports")
            restored.replay_published()
            self.assertEqual(notifier.jobs[0]["status"], "FAILED")
            self.assertEqual(notifier.jobs[0]["errorCode"], "ARTIFACT_UNAVAILABLE")

    def test_artifact_report_is_canonical_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = MainAgent(
                FakeNotifier(),
                DownloadServer(root / "artifacts"),
                root / "published.json",
                root / "reports",
            )
            reply = agent.handle_artifact_report(
                json.dumps(
                    {
                        "result": "passed",
                        "state": "INSTALLED",
                        "phase": "INSTALLED",
                        "taskId": "task-1",
                        "artifactId": "artifact-1",
                        "transportSha256": "sha256:" + "a" * 64,
                        "errorMessage": "token=very-secret Bearer abc.def",
                        "userPrompt": "must not be persisted",
                    }
                )
            )
            self.assertIn("收到", reply)
            files = list((root / "reports").glob("*.json"))
            self.assertEqual(len(files), 1)
            raw = files[0].read_text(encoding="utf-8")
            report = json.loads(raw)
            self.assertEqual(report["result"], "PASSED")
            self.assertEqual(report["phase"], "INSTALLED")
            self.assertEqual(report["transportSha256"], "a" * 64)
            self.assertNotIn("very-secret", raw)
            self.assertNotIn("abc.def", raw)
            self.assertNotIn("userPrompt", raw)
            with self.assertRaisesRegex(ValueError, "result"):
                agent.handle_artifact_report(
                    '{"result":"INSTALLED","taskId":"task-1","artifactId":"artifact-1"}'
                )


class ControlChannelTest(unittest.TestCase):
    def test_parser_preserves_type_and_supports_payload_object(self) -> None:
        message_type, text = ConnectPhone._parse_line(
            '{"type":"artifact_report","payload":{"result":"PASSED"}}'
        )
        self.assertEqual(message_type, "artifact_report")
        self.assertEqual(json.loads(text), {"result": "PASSED"})

    def test_oversized_inbound_frame_is_rejected_before_dispatch(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.chunks = [b"x" * (MAX_CONTROL_FRAME_BYTES + 1)]
                self.sent = []

            def recv(self, _size: int) -> bytes:
                return self.chunks.pop(0) if self.chunks else b""

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

        bridge = ConnectPhone()
        fake = FakeSocket()
        bridge._conn = fake  # exercise the same bounded reader used by real sockets
        received = []
        bridge._read_loop(fake, lambda message_type, text: received.append((message_type, text)))
        self.assertEqual(received, [])
        response = json.loads(fake.sent[0].decode("utf-8"))
        self.assertEqual(response, {"type": "error", "text": "FRAME_TOO_LARGE"})

    def test_full_inbound_queue_returns_busy(self) -> None:
        class FakeSocket:
            def __init__(self) -> None:
                self.sent = []

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

        bridge = ConnectPhone()
        fake = FakeSocket()
        bridge._conn = fake
        raw = b'{"type":"artifact_report","text":"{}"}'
        for _ in range(MAX_PENDING_MESSAGES + 1):
            bridge._handle_line(raw, lambda _message_type, _text: None)
        response = json.loads(fake.sent[-1].decode("utf-8"))
        self.assertEqual(response, {"type": "error", "text": "BUSY"})

    def test_slow_handler_does_not_block_reconnect_or_second_read(self) -> None:
        bridge = ConnectPhone(port=0)
        slow_started = threading.Event()
        release_slow = threading.Event()
        second_handled = threading.Event()
        connected = []
        received = []
        latest_job = {}

        def handler(message_type: str, text: str):
            received.append((message_type, text))
            if text == "slow":
                latest_job.update(
                    {"taskId": "task-slow", "status": "RUNNING", "phase": "COMPILING"}
                )
                bridge.send_job(latest_job)
                slow_started.set()
                release_slow.wait(timeout=3)
                latest_job.update({"status": "SUCCEEDED", "phase": "PUBLISHING"})
                bridge.send_job(latest_job)
            if text == "second":
                second_handled.set()
            return "done:" + text

        def on_connected() -> None:
            connected.append(True)
            if latest_job:
                bridge.send_job(latest_job)

        thread = threading.Thread(
            target=bridge.receive_from_phone,
            args=(handler, on_connected),
            daemon=True,
        )
        thread.start()
        deadline = time.monotonic() + 3
        while bridge._server is None and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertIsNotNone(bridge._server)

        first = socket.create_connection(("127.0.0.1", bridge.port), timeout=2)
        first.sendall(b'{"type":"chat","text":"slow"}\n')
        self.assertTrue(slow_started.wait(timeout=2))
        first.close()

        second = socket.create_connection(("127.0.0.1", bridge.port), timeout=2)
        deadline = time.monotonic() + 2
        while len(connected) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(connected), 2)
        second.sendall(b'{"type":"chat","text":"second"}\n')
        release_slow.set()
        self.assertTrue(second_handled.wait(timeout=2))
        second.settimeout(2)
        statuses = []
        reader = second.makefile("rb")
        deadline = time.monotonic() + 2
        while "SUCCEEDED" not in statuses and time.monotonic() < deadline:
            outer = json.loads(reader.readline().decode("utf-8"))
            if outer.get("type") == "job":
                statuses.append(json.loads(outer["text"])["status"])
        self.assertIn("RUNNING", statuses)
        self.assertIn("SUCCEEDED", statuses)
        reader.close()
        second.close()
        bridge.stop()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(received, [("chat", "slow"), ("chat", "second")])


if __name__ == "__main__":
    unittest.main()
