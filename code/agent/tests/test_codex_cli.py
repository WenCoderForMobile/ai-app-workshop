import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_cli import cli_enabled, exec_args, find_codex
from program_agent.apk_builder import _extract_java
from program_agent.cli_supervisor import (
    UPGRADE_REQUIRED, _CliProcRun, classify_cli_failure, run_cli_with_retries,
    summarize_cli_event, cli_output_looks_like_error, user_message_for_decision,
)
from apk_product_manager.feasibility import FeasibilityChecker
from plugin.program_agent import ProgramAgent


UPGRADE_MESSAGE = (
    "The 'gpt-6-astra' model requires a newer version of Codex. "
    "Please upgrade to the latest app or CLI and try again."
)


def upgrade_event():
    return {'type': 'turn.failed', 'error': {'message': json.dumps({
        'type': 'error', 'status': 400,
        'error': {'type': 'invalid_request_error', 'message': UPGRADE_MESSAGE},
    })}}


class CodexCliTest(unittest.TestCase):
    def test_default_and_executable_are_codex(self):
        with patch.dict(os.environ, {}, clear=True), patch('codex_cli.shutil.which', return_value='/bin/codex') as which:
            self.assertTrue(cli_enabled())
            self.assertEqual(find_codex(), '/bin/codex')
            which.assert_called_once_with('codex')
        with patch.dict(os.environ, {'AUTO_PROCEDURE_USE_CODEX_CLI': '0'}):
            self.assertFalse(cli_enabled())

    def test_exec_has_explicit_sandbox_and_no_cursor_flags(self):
        args = exec_args('codex', 'generate code', Path('/tmp/work'))
        self.assertEqual(args[:4], ['codex','exec','--sandbox','read-only'])
        self.assertIn('--skip-git-repo-check', args)
        self.assertEqual(args[-1], 'generate code')
        for flag in ('-p','-f','--trust','--print','--mode','--output-format','--dangerously-bypass-approvals-and-sandbox'):
            self.assertNotIn(flag, args)

    def test_codex_events(self):
        self.assertIn('正常', summarize_cli_event({'type':'turn.completed'}))
        self.assertIn('工具', summarize_cli_event({'type':'item.started','item':{'type':'command_execution','status':'in_progress'}}))
        self.assertIn('hello', summarize_cli_event({'type':'item.completed','item':{'type':'agent_message','text':'hello'}}))
        self.assertTrue(cli_output_looks_like_error('{"type":"turn.failed","error":{"message":"quota"}}'))

    def test_nested_api_error_retains_actionable_message(self):
        self.assertEqual(summarize_cli_event(upgrade_event()), 'Codex 报错：' + UPGRADE_MESSAGE)

    def test_upgrade_is_not_a_retry_or_design_failure(self):
        for attempt in (1, 3):
            for docs in ('', 'acceptance: ' + 'x' * 100):
                self.assertEqual(classify_cli_failure('exit=1', json.dumps(upgrade_event()), docs, attempt), UPGRADE_REQUIRED)
        message = user_message_for_decision(UPGRADE_REQUIRED, UPGRADE_MESSAGE, 1)
        self.assertIn('npm install -g @openai/codex@latest', message)
        self.assertIn('CODEX_CLI', message)

    def test_upgrade_rejection_stops_after_one_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            failed = _CliProcRun(output=json.dumps(upgrade_event()), error=UPGRADE_MESSAGE, exit_code=1)
            with patch('program_agent.cli_supervisor._run_logged', return_value=failed) as run:
                result = run_cli_with_retries('codex', 'write code', root, root / 'PluginMain.java',
                                              'acceptance: ' + 'x' * 100, root)
            run.assert_called_once()
            self.assertEqual(result.attempts, 1)
            self.assertEqual(result.decision, UPGRADE_REQUIRED)
            self.assertFalse(result.ok)

    def test_declarative_upgrade_rejection_stops_after_one_attempt(self):
        with tempfile.TemporaryDirectory() as tmp, patch('plugin.program_agent.shutil.which', return_value='/bin/codex'):
            agent = ProgramAgent(Path(tmp), codex_command='codex')
            failed = subprocess.CompletedProcess(['codex'], 1, '', UPGRADE_MESSAGE)
            with patch.object(agent, '_run_codex_with_progress', return_value=failed) as run:
                agent._codex_candidate({'title': 'demo'})
            run.assert_called_once()
            self.assertEqual(agent.last_development_report['decision'], 'operator_action')

    def test_upgrade_does_not_reopen_product_design(self):
        from unittest.mock import Mock
        from orchestrator import Orchestrator
        product = Mock()
        orchestrator = Orchestrator(product, Mock(), Mock())
        reply = orchestrator._after_program({'taskId': 'game', 'title': 'game'},
                                            (None, UPGRADE_MESSAGE, UPGRADE_REQUIRED))
        self.assertEqual(reply.job['state'], 'failed')
        product.reopen_for_dev_question.assert_not_called()

    def make_cli(self, root, exit_code=0):
        script = root / 'fake-codex'
        script.write_text('#!' + sys.executable + '\n' + '''import sys, json
from pathlib import Path
args=sys.argv[1:]
assert args[0]=='exec', args
assert '--sandbox' in args and '--json' in args, args
assert args[args.index('--sandbox')+1]=='workspace-write', args
source='package com.demo; public class PluginMain {}'
Path(args[args.index('--output-last-message')+1]).write_text(source)
print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':source}}))
''' + 'sys.exit(%s)\n' % exit_code)
        script.chmod(0o755)
        return str(script)

    def test_supervisor_reads_final_message_from_real_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=run_cli_with_retries(self.make_cli(root),'write code',root,root/'PluginMain.java',
                                        'acceptance: '+ 'x'*100,root,extract_java=_extract_java,max_attempts=1)
            self.assertTrue(result.ok)
            self.assertIn('class PluginMain', result.java)

    def test_failed_cli_does_not_publish_partial_java(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=run_cli_with_retries(self.make_cli(root,1),'write code',root,root/'PluginMain.java',
                                        'acceptance: '+ 'x'*100,root,extract_java=_extract_java,max_attempts=1)
            self.assertFalse(result.ok)
            self.assertFalse((root/'PluginMain.java').exists())

    def test_feasibility_invokes_codex_exec(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'AUTO_PROCEDURE_USE_CODEX_CLI':'1'}):
            root=Path(tmp)
            checker=FeasibilityChecker(root/'framework.md',root)
            checker.cli='codex'
            with patch('apk_product_manager.feasibility.subprocess.run') as run:
                run.return_value.returncode=0
                run.return_value.stdout='{"feasible":true,"title":"game","plan":"Use Canvas and touch input"}'
                self.assertTrue(checker.ask('game',{})['feasible'])
                args=run.call_args.args[0]
                self.assertEqual(args[:2],['codex','exec'])

    def test_declarative_path_uses_read_only_codex(self):
        with tempfile.TemporaryDirectory() as tmp, patch('plugin.program_agent.shutil.which',return_value='/bin/codex'):
            agent=ProgramAgent(Path(tmp),codex_command='codex')
            with patch.object(agent,'_run_codex_with_progress') as run:
                run.return_value.returncode=0
                run.return_value.stdout='{}'
                agent._codex_candidate({'title':'demo'})
                args=run.call_args.args[0]
                self.assertEqual(args[:4],['codex','exec','--sandbox','read-only'])

    def test_missing_codex_does_not_silently_select_product_model(self):
        from unittest.mock import Mock
        from program_agent.apk_builder import ApkBuilder, ApkBuildError
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {'AUTO_PROCEDURE_USE_CODEX_CLI':'1'}):
            root=Path(tmp)
            llm=Mock()
            builder=ApkBuilder(root,llm)
            builder.codex.cli=None
            with self.assertRaises(ApkBuildError):
                builder._from_codex_or_llm(root,{'packageName':'com.demo'})
            llm.chat_json.assert_not_called()
