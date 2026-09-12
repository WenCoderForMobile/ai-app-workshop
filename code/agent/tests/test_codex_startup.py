import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_cli import CodexStartupError, ensure_codex_login
import main as server


def result(code=0, text=''):
    return subprocess.CompletedProcess([], code, text, '')


class CodexStartupTest(unittest.TestCase):
    def setUp(self):
        for context in (
            patch.dict(os.environ, {}, clear=True),
            patch('codex_cli.find_codex', return_value='/bin/codex'),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_existing_login_is_reused(self):
        with patch('codex_cli.subprocess.run', side_effect=[result(text='codex-cli 0.154.0'), result()]) as run:
            ensure_codex_login()
        self.assertEqual([call.args[0] for call in run.call_args_list],
                         [['/bin/codex', '--version'], ['/bin/codex', 'login', 'status']])
        self.assertEqual(os.environ['CODEX_CLI'], '/bin/codex')

    def test_interactive_login_is_completed_and_verified_at_startup(self):
        with patch('codex_cli.sys.stdin.isatty', return_value=True), \
             patch('codex_cli.subprocess.run', side_effect=[result(), result(1), result(), result()]) as run:
            ensure_codex_login()
        self.assertEqual([call.args[0][1:] for call in run.call_args_list],
                         [['--version'], ['login', 'status'], ['login'], ['login', 'status']])
        self.assertNotIn('capture_output', run.call_args_list[2].kwargs)

    def test_unattended_start_without_login_fails_without_opening_browser(self):
        with patch('codex_cli.sys.stdin.isatty', return_value=False), \
             patch('codex_cli.subprocess.run', side_effect=[result(), result(1)]) as run:
            with self.assertRaisesRegex(CodexStartupError, 'codex login'):
                ensure_codex_login()
        self.assertEqual(run.call_count, 2)

    def test_cancelled_login_or_failed_verification_blocks_start(self):
        for responses in ([result(), result(1), result(1)],
                          [result(), result(1), result(), result(1)]):
            with self.subTest(responses=responses), patch('codex_cli.sys.stdin.isatty', return_value=True), \
                 patch('codex_cli.subprocess.run', side_effect=responses):
                with self.assertRaises(CodexStartupError):
                    ensure_codex_login()

    def test_missing_cli_and_timeout_block_start(self):
        with patch('codex_cli.find_codex', return_value=None):
            with self.assertRaises(CodexStartupError):
                ensure_codex_login()
        with patch('codex_cli.subprocess.run', side_effect=subprocess.TimeoutExpired('codex', 12)):
            with self.assertRaises(CodexStartupError):
                ensure_codex_login()

    def test_api_key_never_opens_login_or_prints_secret(self):
        with patch.dict(os.environ, {'CODEX_API_KEY': 'test-secret'}), \
             patch('codex_cli.subprocess.run', return_value=result()) as run, patch('builtins.print') as output:
            ensure_codex_login()
        self.assertEqual(run.call_count, 1)
        self.assertNotIn('test-secret', str(output.call_args_list))

    def test_explicit_cli_disable_skips_preflight(self):
        with patch.dict(os.environ, {'AUTO_PROCEDURE_USE_CODEX_CLI': '0'}), \
             patch('codex_cli.subprocess.run') as run:
            ensure_codex_login()
        run.assert_not_called()

    def test_main_auth_failure_prevents_all_server_initialization(self):
        for runtime in ('apk', 'declarative'):
            with self.subTest(runtime=runtime), patch.object(sys, 'argv', ['main.py', '--runtime', runtime, '--adb-reverse']), \
                 patch.object(server, 'ensure_codex_login', side_effect=CodexStartupError('not logged in')), \
                 patch.object(server, 'LlmClient') as llm, patch.object(server, 'DownloadServer') as downloads, \
                 patch.object(server, 'ConnectPhone') as phone, \
                 patch.object(server, 'AdbReverseWatchdog') as mappings:
                with self.assertRaises(SystemExit) as error:
                    server.main()
                self.assertEqual(error.exception.code, 1)
                llm.assert_not_called()
                downloads.assert_not_called()
                phone.assert_not_called()
                mappings.assert_not_called()


class AdbStartupIntegrationTest(unittest.TestCase):
    def test_watchdog_wraps_server_lifetime_after_authentication(self):
        with patch.object(sys, 'argv', ['main.py', '--adb-reverse', '--adb-serial', 'phone']), \
             patch.object(server, 'ensure_codex_login') as login, \
             patch.object(server, 'AdbReverseWatchdog') as mappings, \
             patch.object(server, 'serve') as serve:
            mappings.side_effect = lambda *args: self._after_login(login)
            server.main()
            mappings.assert_called_once_with(17890, 17891, 'phone')
            serve.assert_called_once()
            self.context.__enter__.assert_called_once()
            self.context.__exit__.assert_called_once()

    def _after_login(self, login):
        from unittest.mock import MagicMock
        login.assert_called_once()
        self.context = MagicMock()
        return self.context

    def test_direct_launch_does_not_manage_adb_unless_requested(self):
        with patch.object(sys, 'argv', ['main.py']), \
             patch.object(server, 'ensure_codex_login'), \
             patch.object(server, 'AdbReverseWatchdog') as mappings, \
             patch.object(server, 'serve'):
            server.main()
            mappings.assert_not_called()
