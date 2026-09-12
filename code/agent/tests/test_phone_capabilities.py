import copy
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capability_report import normalize_capabilities
from connect_phone import ConnectPhone
from runtime_environment import RuntimeEnvironment
from apk_product_manager import ProductAgent, IntentStore
from apk_product_manager.feasibility import FeasibilityChecker
from apk_product_manager.archive import ProductArchive


REPORT = {'schemaVersion': 1, 'host': {'packageName': 'com.autoprocedure.plat', 'versionCode': 6, 'versionName': '0.1.5'},
          'audioOutput': {'status': 'supported', 'mediaVolume': 0, 'muted': True},
          'offlineEnglishTts': {'status': 'supported', 'reasonCode': 'OFFLINE_SYNTHESIS_VERIFIED',
                               'scope': 'default_engine', 'engine': 'test.engine', 'voice': 'english-local',
                               'locale': 'en-US', 'networkRequired': False, 'synthesizedBytes': 16044}}


class CapabilityTest(unittest.TestCase):
    def responder(self, bridge, report=REPORT):
        def respond(kind, text):
            self.assertEqual(kind, 'capability_probe')
            value = dict(report, requestId=json.loads(text)['requestId'])
            bridge._handle_line(json.dumps({'type': 'capability_report', 'text': json.dumps(value)}).encode(),
                                lambda *_: self.fail('report must bypass LLM work queue'))
        return respond

    def test_valid_report_is_observed_and_cached_on_same_connection(self):
        bridge = ConnectPhone()
        bridge._conn = object()
        with patch.object(bridge, '_send_typed', side_effect=self.responder(bridge)) as send:
            first = bridge.request_capabilities()
            second = bridge.request_capabilities()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(first['status'], 'observed')
        self.assertEqual(second['offlineEnglishTts']['status'], 'supported')
        self.assertTrue(second['audioOutput']['muted'])  # Muted does not erase API/voice capability.
        self.assertEqual(second['audioOutput']['status'], 'supported')

    def test_cache_is_not_reused_after_reconnect_or_expiry(self):
        bridge = ConnectPhone()
        bridge._conn = object()
        with patch.object(bridge, '_send_typed', side_effect=self.responder(bridge)) as send:
            bridge.request_capabilities()
            bridge._conn = object()
            bridge.request_capabilities()
            conn, timestamp, report = bridge._capability_cache
            bridge._capability_cache = (conn, timestamp - 61, report)
            bridge.request_capabilities()
        self.assertEqual(send.call_count, 3)

    def test_concurrent_callers_share_one_probe(self):
        bridge = ConnectPhone()
        bridge._conn = object()
        sent = threading.Event()
        request = {}
        results = []
        def send(kind, text):
            request.update(json.loads(text))
            sent.set()
        with patch.object(bridge, '_send_typed', side_effect=send) as sender:
            first = threading.Thread(target=lambda: results.append(bridge.request_capabilities(timeout=1)))
            second = threading.Thread(target=lambda: results.append(bridge.request_capabilities(timeout=1)))
            first.start()
            self.assertTrue(sent.wait(1))
            second.start()
            bridge._receive_capabilities(json.dumps(dict(REPORT, requestId=request['requestId'])))
            first.join(2)
            second.join(2)
        self.assertEqual(sender.call_count, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r['status'] == 'observed' for r in results))

    def test_wrong_request_id_and_unknown_client_remain_unknown(self):
        bridge = ConnectPhone()
        bridge._conn = object()
        def wrong(*_):
            bridge._receive_capabilities(json.dumps(dict(REPORT, requestId='wrong')))
        with patch.object(bridge, '_send_typed', side_effect=wrong):
            self.assertEqual(bridge.request_capabilities(timeout=.001)['status'], 'unknown')
        self.assertTrue(bridge._inbound_queue.empty())
        self.assertFalse(bridge._capability_pending)

    def test_disconnected_probe_and_invalid_report_never_claim_support(self):
        self.assertEqual(ConnectPhone().request_capabilities()['status'], 'unknown')
        for bad in (None, [], {}, dict(REPORT, schemaVersion=True), dict(REPORT, host={'packageName': 'other'})):
            self.assertIsNone(normalize_capabilities(bad))
        for field, value in (('networkRequired', True), ('synthesizedBytes', 44), ('locale', 'zh-CN'), ('voice', '')):
            report = copy.deepcopy(REPORT)
            report['offlineEnglishTts'][field] = value
            self.assertEqual(normalize_capabilities(report)['offlineEnglishTts']['status'], 'unknown')

    def test_probe_failure_preserves_unknown_in_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = RuntimeEnvironment(Path(tmp), capability_provider=Mock(side_effect=TimeoutError()))
            self.assertEqual(json.loads(env.snapshot())['phoneCapabilities']['status'], 'unknown')

    def test_missing_reviewer_does_not_delegate_probe_to_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            llm = Mock(provider_name='test')
            llm.chat_json.return_value = {
                'status': 'NEED_CLARIFY', 'clarificationKind': 'technical_verification',
                'reply': '请确认手机能否发音', 'intent': {'title': '单词', 'summary': '英文朗读'}}
            agent = ProductAgent(IntentStore(root / 'store'), llm,
                                 environment=RuntimeEnvironment(root))
            reply = agent.handle('需要英文朗读')
            self.assertEqual(agent.phase, 'intake')
            self.assertIn('自动核对', reply)
            self.assertNotIn('请确认', reply)
            self.assertEqual(agent.last_intent['runtimeCapabilities']['status'], 'unknown')

    def test_live_report_reaches_both_llms_and_generation_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            report = dict(REPORT, status='observed')
            env = RuntimeEnvironment(root, capability_provider=lambda: report)
            llm = Mock(provider_name='test')
            llm.chat_json.side_effect = [
                {'status': 'NEED_CLARIFY', 'clarificationKind': 'technical_verification',
                 'needsTechnicalReview': False, 'reply': '需要验证朗读',
                 'intent': {'title': '单词', 'summary': '英文朗读'}},
                {'feasible': True, 'assessment': 'supported', 'plan': '使用 english-local 朗读',
                 'effect': '按轮次记忆单词并朗读。'}]
            checker = Mock(spec=FeasibilityChecker)
            checker.review_dir = None
            checker.ask.return_value = {'feasible': True, 'assessment': 'supported', 'plan': 'english-local'}
            agent = ProductAgent(IntentStore(root / 'store'), llm, feasibility=checker, environment=env)
            agent.handle('托业单词分四轮练习，需要离线英文朗读')
            self.assertEqual(agent.phase, 'waiting_approval')
            checker.ask.assert_called_once()
            for call in llm.chat_json.call_args_list:
                self.assertIn('english-local', call.args[0][0]['content'])
            self.assertEqual(agent.last_intent['runtimeCapabilities'], report)
            archive = ProductArchive(root / 'tasks', root)
            meta = archive.create(agent.last_intent, ['学习单词'])
            self.assertIn('english-local', (Path(meta['productDir']) / 'runtime.md').read_text())

    def test_capability_request_during_chat_does_not_deadlock_reader(self):
        bridge = ConnectPhone(port=0)
        observed = []
        completed = threading.Event()
        def handler(*_):
            observed.append(bridge.request_capabilities(timeout=1))
            completed.set()
        worker = threading.Thread(target=bridge.receive_from_phone, args=(handler,), daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + 2
            while bridge._server is None and time.monotonic() < deadline:
                time.sleep(.01)
            with socket.create_connection(('127.0.0.1', bridge.port), timeout=2) as phone:
                phone.sendall(b'{"type":"chat","text":"hello"}\n')
                with phone.makefile('rb') as reader:
                    while True:
                        request = json.loads(reader.readline())
                        if request['type'] == 'capability_probe': break
                    request_id = json.loads(request['text'])['requestId']
                    response = {'type': 'capability_report', 'text': json.dumps(dict(REPORT, requestId=request_id))}
                    phone.sendall((json.dumps(response) + '\n').encode())
                    self.assertTrue(completed.wait(1))
                    self.assertEqual(observed[0]['offlineEnglishTts']['status'], 'supported')
        finally:
            bridge.stop()
            worker.join(3)
        self.assertFalse(worker.is_alive())


if __name__ == '__main__':
    unittest.main()
