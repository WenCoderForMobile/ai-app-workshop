import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apk_product_manager import ProductAgent, IntentStore
from apk_product_manager.feasibility import FeasibilityChecker, _load_json, apply_verdict
from runtime_environment import RuntimeEnvironment


class AlignmentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = Mock()
        self.env.snapshot.return_value = 'ENV-EVIDENCE: PluginEntry; debug-only; ASR is host-only'
        self.intent = {'title': '旋律', 'summary': '点击播放合成旋律', 'acceptance': ['点击播放，再次点击停止']}
        self.initial = {'status': 'UNSUPPORTED', 'reply': '目前音乐做不了', 'intent': self.intent,
                        'technicalReason': '不确定有无音频文件支持'}
        self.developer = {'feasible': True, 'assessment': 'supported', 'title': '旋律',
                          'plan': '生成 PCM 旋律，销毁时释放', 'evidence': ['Java 代码可合成声音']}
        self.final = dict(self.developer, effect='🎵 点击播放简单旋律，可随时停止。')

    def agent(self, initial=None, developer=None, final=None):
        llm = Mock(provider_name='test')
        llm.chat_json.side_effect = [initial or self.initial, final or self.final]
        checker = Mock(spec=FeasibilityChecker)
        checker.review_dir = None
        checker.ask.return_value = developer or self.developer
        agent = ProductAgent(IntentStore(self.root), llm, feasibility=checker, environment=self.env)
        return agent, llm, checker

    def test_initial_refusal_is_reviewed_by_developer_then_main_llm(self):
        agent, llm, checker = self.agent()
        reply = agent.handle('播放一段合成旋律')
        checker.ask.assert_called_once()
        self.assertIn('不确定', checker.ask.call_args.kwargs['product_assessment']['technicalReason'])
        self.assertEqual(llm.chat_json.call_count, 2)
        for call in llm.chat_json.call_args_list:
            self.assertIn('ENV-EVIDENCE', call.args[0][0]['content'])
        self.assertIn('programmingAgentAssessment', llm.chat_json.call_args_list[1].args[0][1]['content'])
        self.assertEqual(agent.phase, 'waiting_approval')
        self.assertFalse(agent.just_confirmed)
        self.assertNotIn('做不了', reply)
        self.assertIn('PCM', agent.last_intent['devPlan'])
        self.assertEqual(agent.last_intent['acceptance'], self.intent['acceptance'])
        self.assertEqual(json.loads(agent.history[-1]['content'])['status'], 'WAITING_APPROVAL')
        agent.handle('确认')
        self.assertTrue(agent.just_confirmed)
        self.assertEqual(llm.chat_json.call_count, 2)

    def test_main_llm_can_reject_developer_optimism(self):
        final = {'feasible': False, 'assessment': 'unsupported', 'reason': '当前无法访问指定的在线歌曲服务',
                 'effect': '目前无法播放该服务中的歌曲。', 'evidence': ['当前约束无该服务接入']}
        agent, llm, checker = self.agent(final=final)
        self.assertIn('无法播放', agent.handle('在线歌曲'))
        self.assertEqual(agent.phase, 'intake')
        checker.record_decision.assert_called_once_with(final, 'decided')

    def test_host_extension_is_not_a_buildable_proposal(self):
        final = {'feasible': False, 'assessment': 'requires_host_changes',
                 'requiredHostChanges': ['提供音频素材读取通路'],
                 'effect': '需要先增加音乐文件导入，当前不能直接制作。'}
        agent, _, _ = self.agent(final=final)
        agent.handle('播放我手机上的歌曲')
        self.assertEqual(agent.phase, 'intake')
        agent.handle('确认')
        self.assertFalse(agent.just_confirmed)
        self.assertEqual(agent.last_intent['feasibility']['requiredHostChanges'], ['提供音频素材读取通路'])

    def test_developer_failure_does_not_turn_into_impossible_or_auto_approval(self):
        agent, llm, checker = self.agent()
        checker.ask.return_value = None
        reply = agent.handle('做小游戏')
        self.assertIn('暂未完成', reply)
        self.assertEqual(agent.phase, 'intake')
        self.assertEqual(llm.chat_json.call_count, 1)

    def test_final_model_error_or_malformed_result_stays_pending(self):
        for bad in (RuntimeError('offline'), {'feasible': 'true'}, {'feasible': True, 'assessment': 'unsupported'}):
            agent, llm, _ = self.agent()
            llm.chat_json.side_effect = [self.initial, bad]
            self.assertIn('暂未完成', agent.handle('做个旋律'))
            self.assertEqual(agent.phase, 'intake')

    def test_technical_uncertainty_consults_but_user_preference_does_not(self):
        for technical in (False, True):
            initial = dict(self.initial, status='NEED_CLARIFY', needsTechnicalReview=technical)
            agent, _, checker = self.agent(initial=initial)
            agent.handle('做个旋律')
            self.assertEqual(checker.ask.call_count, int(technical))

    def test_checker_is_read_only_and_records_environment_and_decision(self):
        checker = FeasibilityChecker(self.root / 'framework.md', self.root)
        checker.cli = '/bin/codex'
        response = subprocess.CompletedProcess([], 0, json.dumps(self.developer), '')
        with patch.dict(os.environ, {'AUTO_PROCEDURE_USE_CODEX_CLI': '1'}), \
             patch('apk_product_manager.feasibility.subprocess.run', return_value=response) as run:
            self.assertEqual(checker.ask('旋律', self.intent, environment_context='snapshot', product_assessment=self.initial), self.developer)
        args = run.call_args.args[0]
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('login', args)
        checker.record_decision(self.final, 'decided')
        audit = json.loads((checker.review_dir / 'review.json').read_text())
        self.assertEqual(audit['developerVerdict'], self.developer)
        self.assertEqual(audit['finalVerdict'], self.final)
        self.assertEqual(audit['environment'], 'snapshot')
        self.assertEqual(audit['productAssessment']['status'], 'UNSUPPORTED')

    def test_invalid_boolean_and_contradictory_extension_are_not_approved(self):
        for verdict in ({'feasible': 'false'}, {'feasible': 1}, {'feasible': None},
                        {'feasible': True}, {'feasible': True, 'plan': {}},
                        {'feasible': True, 'requiredHostChanges': ['new API']}):
            self.assertIsNone(_load_json(json.dumps(verdict)))
            self.assertEqual(apply_verdict('WAITING_APPROVAL', 'ok', self.intent, verdict)[0], 'NEED_CLARIFY')

    def test_environment_reads_only_declared_sources_and_handles_missing_device(self):
        manifest = self.root / 'code/phone_agent/app/src/main/AndroidManifest.xml'
        manifest.parent.mkdir(parents=True)
        manifest.write_text('<manifest>test-permission</manifest>')
        (self.root / 'env.sh').write_text('SECRET=should-not-be-read')
        with patch('runtime_environment.subprocess.run') as run:
            snapshot = json.loads(RuntimeEnvironment(self.root).snapshot())
        run.assert_not_called()
        self.assertEqual(snapshot['phone']['status'], 'unknown')
        self.assertIn('test-permission', str(snapshot['sourceEvidence']))
        self.assertNotIn('should-not-be-read', str(snapshot))
        self.assertIn('onDestroy', snapshot['capabilitiesAndBoundaries'])

    def test_device_probe_reports_only_basic_properties(self):
        responses = [subprocess.CompletedProcess([], 0, 'List of devices attached\nprivate-serial\tdevice\n', ''),
                     subprocess.CompletedProcess([], 0, 'TestPhone\n33\narm64-v8a\n', '')]
        with patch('runtime_environment.shutil.which', return_value='/bin/adb'), \
             patch('runtime_environment.subprocess.run', side_effect=responses):
            snapshot = json.loads(RuntimeEnvironment(self.root, probe_device=True).snapshot())
        self.assertEqual(snapshot['phone']['sdk'], '33')
        self.assertNotIn('private-serial', str(snapshot))
        self.assertEqual(snapshot['phone']['runtimePermissions'], 'unknown')

    def test_approved_plan_and_audit_reference_reach_generation_docs(self):
        from apk_product_manager.archive import ProductArchive
        intent = dict(self.intent, devPlan='合成 PCM 并在销毁时释放音频',
                      feasibilityReviewPath='/review/example/review.json')
        archive = ProductArchive(self.root / 'tasks', self.root)
        meta = archive.create(intent, ['做一段旋律'])
        product = Path(meta['productDir'])
        self.assertIn(intent['devPlan'], (product / 'implementation.md').read_text())
        self.assertIn(intent['feasibilityReviewPath'], (product / 'runtime.md').read_text())


if __name__ == '__main__':
    unittest.main()
