import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apk_product_manager import ProductAgent, ProductArchive, IntentStore
from orchestrator import Orchestrator
from program_agent.apk_builder import ApkBuilder, _extract_java
from program_agent.cli_supervisor import _CliProcRun, run_cli_with_retries
from program_agent.history_reuse import find_plugin_main
from program_agent.program_agent import ProgramAgent
from task_history import task_catalog, save_build_state


class ContinuationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.agent_root = self.root / 'code/agent'
        self.tasks = self.agent_root / 'out/tasks'
        self.archive = ProductArchive(self.tasks, self.root)
        self.env = Mock()
        self.env.snapshot.return_value = 'environment'

    def task(self, title, *, failed=False, source=True):
        self.archive.reset()
        intent = {'title': title, 'summary': title + '原功能', 'features': ['计分', '重开'], 'acceptance': ['原验收']}
        meta = dict(self.archive.finalize(intent, [title, '确认']))
        if source:
            java = Path(meta['taskDir']) / 'plugin_src/app/src/main/java/pkg/PluginMain.java'
            java.parent.mkdir(parents=True)
            java.write_text('package %s; public class PluginMain { /* %s-original */ }' % (meta['packageName'], title))
        save_build_state(meta, 'failed' if failed else 'compiled', error='javac boom' if failed else '')
        self.archive.reset()
        return meta

    def agent(self, result):
        llm = Mock(provider_name='test')
        llm.chat_json.return_value = result
        return ProductAgent(IntentStore(self.agent_root / 'out/intents'), llm,
                            archive=self.archive, environment=self.env), llm

    def test_continue_failed_task_after_restart_without_reconfirming_or_rewriting_design(self):
        old = self.task('贪吃蛇', failed=True)
        self.task('喝水')
        before = (Path(old['productDir']) / 'aligned.json').read_bytes()
        product, llm = self.agent({'taskRoute': {'action': 'continue', 'targetTaskId': old['taskId']}})
        program = Mock()
        program.build.return_value = ({'taskId': old['taskId']}, 'compiled', 'ok')
        orch = Orchestrator(product, program, Mock())
        reply = orch.handle('继续之前失败的贪吃蛇')
        self.assertIn('贪吃蛇', reply.text)
        self.assertIsNotNone(reply.follow_up)
        reply.follow_up(lambda text: None)
        passed = program.build.call_args.args[0]
        self.assertEqual(passed['taskId'], old['taskId'])
        self.assertEqual(passed['generationMode'], 'continue')
        self.assertEqual((Path(old['productDir']) / 'aligned.json').read_bytes(), before)
        self.assertIn('javac boom', llm.chat_json.call_args.args[0][0]['content'])

    def test_modify_older_program_preserves_identity_and_requires_confirmation(self):
        old = self.task('贪吃蛇')
        other = self.task('喝水')
        original = find_plugin_main(Path(old['taskDir'])).read_bytes()
        product, _ = self.agent({'status': 'WAITING_APPROVAL', 'reply': '给贪吃蛇增加音效',
                                'taskRoute': {'action': 'modify', 'targetTaskId': old['taskId']},
                                'intent': {'title': '音乐蛇', 'summary': '原游戏增加音效'}})
        product.handle('给以前贪吃蛇加音效，改名音乐蛇')
        self.assertFalse(product.just_confirmed)
        self.assertEqual(product.last_intent['features'], ['计分', '重开'])
        product.handle('确认')
        self.assertEqual(product.last_archive['taskId'], old['taskId'])
        self.assertEqual(product.last_archive['packageName'], old['packageName'])
        self.assertEqual(product.last_archive['title'], '音乐蛇')
        self.assertEqual(product.last_archive['generationMode'], 'modify')
        self.assertEqual(find_plugin_main(Path(old['taskDir'])).read_bytes(), original)
        self.assertTrue(Path(other['taskDir']).exists())

    def test_new_same_title_gets_separate_identity_and_package(self):
        old = self.task('贪吃蛇')
        original = (Path(old['taskDir']) / 'meta.json').read_bytes()
        product, _ = self.agent({'status': 'WAITING_APPROVAL', 'reply': '一个独立新游戏',
                                'taskRoute': {'action': 'new'},
                                'intent': {'title': '贪吃蛇', 'summary': '独立新规则'}})
        product.handle('重新做一个独立的贪吃蛇，旧的保留')
        product.handle('确认')
        self.assertNotEqual(product.last_archive['taskId'], old['taskId'])
        self.assertNotEqual(product.last_archive['packageName'], old['packageName'])
        self.assertEqual((Path(old['taskDir']) / 'meta.json').read_bytes(), original)
        self.assertEqual(product.store.latest()['taskId'], product.last_archive['taskId'])

    def test_ambiguous_or_fabricated_target_cannot_start_or_overwrite(self):
        old = self.task('贪吃蛇')
        for route in ({'action': 'clarify'}, {'action': 'modify', 'targetTaskId': '../outside'}, None):
            product, _ = self.agent({'status': 'WAITING_APPROVAL', 'taskRoute': route,
                                    'intent': {'title': '错误目标', 'summary': '不要创建'}})
            self.assertIn('哪个程序', product.handle('改一下那个'))
            product.handle('确认')
            self.assertFalse(product.just_confirmed)
        self.assertEqual(len(list(self.tasks.iterdir())), 1)
        self.assertTrue(Path(old['taskDir']).exists())

    def test_continue_with_changes_is_a_modification_proposal(self):
        old = self.task('贪吃蛇', failed=True)
        product, _ = self.agent({'status': 'WAITING_APPROVAL', 'reply': '继续并加音效',
                                'taskRoute': {'action': 'modify', 'targetTaskId': old['taskId']},
                                'intent': {'summary': '继续原功能并增加音效'}})
        product.handle('继续并加音效')
        self.assertFalse(product.just_confirmed)
        self.assertEqual(product.phase, 'waiting_approval')

    def test_failed_routing_does_not_create_program_named_continue(self):
        self.task('贪吃蛇', failed=True)
        product, llm = self.agent({})
        llm.chat_json.side_effect = RuntimeError('offline')
        self.assertIn('保留历史代码', product.handle('继续'))
        self.assertFalse(product.just_confirmed)
        self.assertEqual(len(list(self.tasks.iterdir())), 1)

    def test_continue_compiles_saved_code_before_calling_codegen(self):
        meta = self.task('贪吃蛇', failed=True)
        meta['generationMode'] = 'continue'
        builder = ApkBuilder(self.agent_root)
        builder._mark_generation(meta, 'complete')
        apk = self.root / 'compiled.apk'
        apk.write_bytes(b'apk')
        with patch.object(builder, '_write_entry') as generate, \
             patch.object(builder, '_compile_with_fixes', return_value=apk) as compile_source:
            job = builder.build(meta)
        generate.assert_not_called()
        self.assertIn('贪吃蛇-original', compile_source.call_args.args[1].read_text())
        self.assertTrue(Path(job['apkPath']).is_file())
        self.assertTrue(list((Path(meta['taskDir']) / 'dev/source-checkpoints').glob('*/PluginMain.java')))

    def test_interrupted_generation_must_continue_coding_even_if_old_source_compiles(self):
        meta = self.task('贪吃蛇', failed=True)
        meta['generationMode'] = 'continue'
        builder = ApkBuilder(self.agent_root)
        builder._mark_generation(meta, 'writing')
        apk = self.root / 'compiled.apk'
        apk.write_bytes(b'apk')
        with patch.object(builder, '_write_entry') as generate, \
             patch.object(builder, '_compile_with_fixes', return_value=apk):
            builder.build(meta)
        self.assertIn('贪吃蛇-original', generate.call_args.kwargs['similar']['java'])

    def test_changed_requirements_invalidate_previously_completed_generation(self):
        meta = self.task('贪吃蛇')
        builder = ApkBuilder(self.agent_root)
        builder._mark_generation(meta, 'complete')
        self.assertTrue(builder._generation_ready(meta))
        (Path(meta['productDir']) / 'design.md').write_text('新增音效及静音开关')
        self.assertFalse(builder._generation_ready(meta))

    def test_new_task_does_not_borrow_similar_other_program(self):
        self.task('贪吃蛇')
        meta = self.task('新贪吃蛇', source=False)
        meta['generationMode'] = 'new'
        builder = ApkBuilder(self.agent_root)
        apk = self.root / 'compiled.apk'
        apk.write_bytes(b'apk')
        with patch.object(builder, '_copy_template', side_effect=lambda p, m: p.mkdir()), \
             patch.object(builder, '_write_entry') as generate, \
             patch.object(builder, '_compile_with_fixes', return_value=apk):
            builder.build(meta)
        self.assertIsNone(generate.call_args.kwargs['similar'])

    def test_workspaces_are_separate(self):
        one, two = self.task('一'), self.task('二')
        builder = ApkBuilder(self.agent_root)
        self.assertNotEqual(builder._work_dir(one), builder._work_dir(two))
        self.assertTrue(str(builder._work_dir(one)).startswith(one['taskDir']))

    def test_failed_cli_draft_is_preserved_and_used_by_next_attempt(self):
        task = self.root / 'task'
        work = task / 'dev/codex_work'
        work.mkdir(parents=True)
        dest = work / 'PluginMain.java'
        original = 'package p; public class PluginMain { /* original */ }'
        partial = 'package p; public class PluginMain { /* partial */ }'
        dest.write_text(original)
        calls = []
        def execute(*args):
            calls.append(dest.read_text())
            if len(calls) == 1:
                dest.write_text(partial)
                return _CliProcRun(exit_code=1, error='exit=1', output='failed')
            return _CliProcRun(exit_code=0, output='done')
        with patch('program_agent.cli_supervisor._run_logged', side_effect=execute):
            result = run_cli_with_retries('codex', 'continue', work, dest, 'acceptance: ' + 'x'*100,
                                          task, extract_java=_extract_java, max_attempts=2)
        self.assertTrue(result.ok)
        self.assertEqual(calls, [original, partial])
        self.assertIn(original, [p.read_text() for p in (task / 'dev/source-checkpoints').glob('*/PluginMain.java')])

    def test_failed_turn_is_not_success_even_when_source_exists(self):
        meta = self.task('贪吃蛇', source=False)
        task = Path(meta['taskDir'])
        dest = task / 'dev/codex_work/PluginMain.java'
        dest.parent.mkdir(parents=True)
        dest.write_text('package p; public class PluginMain { /* unverified */ }')
        with patch('program_agent.cli_supervisor._run_logged', return_value=_CliProcRun(exit_code=1, error='exit=1')):
            result = run_cli_with_retries('codex', 'write', dest.parent, dest, 'acceptance: '+'x'*100, task, max_attempts=1)
        self.assertFalse(result.ok)
        self.assertEqual(result.java, '')
        self.assertIn('unverified', find_plugin_main(task).read_text())
        self.assertTrue(task_catalog(self.tasks)[0]['hasSource'])

    def test_legacy_global_draft_requires_matching_package(self):
        one, two = self.task('一', source=False), self.task('二', source=False)
        legacy = self.tasks.parent / 'codex_work/PluginMain.java'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('package %s; public class PluginMain {}' % one['packageName'])
        self.assertEqual(find_plugin_main(Path(one['taskDir'])), legacy)
        self.assertIsNone(find_plugin_main(Path(two['taskDir'])))

    def test_build_failure_state_survives_restart(self):
        meta = self.task('贪吃蛇', source=False)
        program = ProgramAgent(self.agent_root)
        with patch.object(program.builder, 'build', side_effect=RuntimeError('interrupted-generation')):
            program.build(meta)
        item = task_catalog(self.tasks)[0]
        self.assertEqual(item['state'], 'failed')
        self.assertIn('interrupted-generation', item['error'])


if __name__ == '__main__':
    unittest.main()
