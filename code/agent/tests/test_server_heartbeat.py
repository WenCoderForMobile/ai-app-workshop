from pathlib import Path
import json
import socket
import sys
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connect_phone import ConnectPhone
from connect_phone.connect_phone import MAX_PENDING_MESSAGES


class ServerHeartbeatTest(unittest.TestCase):
    def test_ping_bypasses_even_a_full_business_queue(self):
        bridge = ConnectPhone()
        for _ in range(MAX_PENDING_MESSAGES):
            bridge._inbound_queue.put_nowait(('chat', 'queued'))
        left, right = socket.socketpair()
        try:
            bridge._conn = left
            right.settimeout(1)
            bridge._handle_line(b'{"type":"ping","text":"nonce-123"}',
                                lambda *_: self.fail('ping reached business handler'))
            self.assertEqual(json.loads(right.recv(1024)), {'type': 'pong', 'text': 'nonce-123'})
            self.assertEqual(bridge._inbound_queue.qsize(), MAX_PENDING_MESSAGES)
        finally:
            left.close()
            right.close()

    def test_long_probe_is_not_echoed_or_queued(self):
        from unittest.mock import patch
        bridge = ConnectPhone()
        with patch.object(bridge, '_send_typed') as send:
            bridge._handle_line(json.dumps({'type': 'ping', 'text': 'x' * 129}).encode(), lambda *_: None)
        send.assert_not_called()
        self.assertTrue(bridge._inbound_queue.empty())

    def test_busy_agent_still_replies_to_live_socket_heartbeat(self):
        bridge = ConnectPhone(port=0)
        busy = threading.Event()
        release = threading.Event()
        handled = []

        def handler(kind, text):
            handled.append((kind, text))
            busy.set()
            release.wait(3)

        worker = threading.Thread(target=bridge.receive_from_phone, args=(handler,), daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while bridge._server is None and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertIsNotNone(bridge._server)
            with socket.create_connection(('127.0.0.1', bridge.port), timeout=1) as phone:
                phone.sendall(b'{"type":"chat","text":"slow"}\n')
                self.assertTrue(busy.wait(1))
                phone.sendall(b'{"type":"ping","text":"during-generation"}\n')
                with phone.makefile('rb') as reader:
                    response = json.loads(reader.readline())
                    if response['type'] != 'pong':
                        response = json.loads(reader.readline())
                self.assertEqual(response, {'type': 'pong', 'text': 'during-generation'})
                self.assertFalse(release.is_set())
                self.assertEqual(handled, [('chat', 'slow')])
        finally:
            release.set()
            bridge.stop()
            worker.join(3)
        self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
