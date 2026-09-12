import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apk_delivery import ApkDelivery
from download_server import DownloadServer
from apk_product_manager import ProductAgent, IntentStore
from apk_product_manager.product_agent import _is_confirm
from program_agent.apk_builder import ApkBuilder, ApkBuildError
from product_manager import ProductAgent as DeclarativeAgent, IntentStore as DeclarativeStore


class AlignmentRegressionTest(unittest.TestCase):
    def test_approval_does_not_swallow_feedback(self):
        for text in ('确认，但是按钮放大', '确认后再加计时', '确认？', '好的，把按钮放大'):
            self.assertFalse(_is_confirm(text), text)
        for text in ('确认。', 'OK!', '好的', '可以'):
            self.assertTrue(_is_confirm(text), text)

    def test_cancel_cannot_generate_and_repeated_confirm_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent = ProductAgent(IntentStore(Path(tmp)))
            agent.handle('俄罗斯方块')
            agent.handle('取消')
            agent.handle('确认')
            self.assertFalse(agent.just_confirmed)
            self.assertEqual(agent.phase, 'intake')
            self.assertFalse(agent.store.latest_path.exists())

    def test_new_model_response_cannot_approve_stale_intent(self):
        for agent_type, store_type in ((ProductAgent, IntentStore), (DeclarativeAgent, DeclarativeStore)):
            with tempfile.TemporaryDirectory() as tmp:
                llm = Mock(provider_name='test')
                llm.chat_json.side_effect = [
                    {'status': 'WAITING_APPROVAL', 'reply': '旧方案', 'intent': {'title': '计数', 'summary': '点击计数'}},
                    {'status': 'WAITING_APPROVAL', 'reply': '确认吧'},
                ]
                agent = agent_type(store_type(Path(tmp)), llm=llm)
                agent.handle('计数器')
                agent.handle('按钮放大')
                self.assertEqual(agent.phase, 'intake')
                self.assertIsNone(agent.last_intent)

    def test_no_fake_game_when_codegen_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            builder = ApkBuilder(root)
            builder._from_codex_or_llm = Mock(return_value=None)
            meta = {'packageName': 'com.demo.game', 'title': '俄罗斯方块', 'summary': '下落方块', 'productDir': str(root)}
            with self.assertRaises(ApkBuildError):
                builder._write_entry(root / 'src', meta, 'com.demo.game.PluginMain')

    def test_apk_delivery_is_immutable_and_replays_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'plugin.apk'
            source.write_bytes(b'first-apk')
            bridge = Mock()
            server = DownloadServer(root, port=17891)
            delivery = ApkDelivery(bridge, server, root / 'jobs.json')
            delivery.publish({'taskId': '俄罗斯方块', 'state': 'downloading', 'apkPath': str(source)})
            first = bridge.send_job.call_args.args[0]
            self.assertNotIn('apkPath', first)
            self.assertEqual(first['sha256'], hashlib.sha256(b'first-apk').hexdigest())
            source.write_bytes(b'second-apk')
            self.assertEqual(server._resolve_request(first['downloadUrl'].split(':17891')[1]).read_bytes(), b'first-apk')
            restarted = ApkDelivery(bridge, DownloadServer(root, port=12345), root / 'jobs.json')
            restarted.replay()
            self.assertIn(':12345/', bridge.send_job.call_args.args[0]['downloadUrl'])

    def test_interrupted_build_is_failed_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'jobs.json'
            path.write_text(json.dumps({'one': {'taskId': 'one', 'state': 'making'}}))
            delivery = ApkDelivery(Mock(), DownloadServer(root), path)
            self.assertEqual(delivery.jobs['one']['state'], 'failed')
