from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from program_agent.cli_supervisor import (
    ASK_USER,
    DESIGN_INCOMPLETE,
    EXHAUSTED,
    MAX_CLI_ATTEMPTS,
    RETRY,
    classify_cli_failure,
    cli_output_looks_like_error,
    format_cli_tick,
    should_extend_cli_wait,
    summarize_cli_event,
)


class CliSupervisorTest(unittest.TestCase):
    def test_timeout_retries_until_cap(self) -> None:
        docs = "# 设计\n## 验收\n- 能完成一次主操作\n" + ("x" * 80)
        self.assertEqual(classify_cli_failure("timeout", "", docs, 1), RETRY)
        self.assertEqual(classify_cli_failure("timeout", "", docs, MAX_CLI_ATTEMPTS), EXHAUSTED)

    def test_auth_asks_user(self) -> None:
        docs = "# 设计\n## 验收\n- ok\n" + ("x" * 80)
        self.assertEqual(classify_cli_failure("unauthorized api key", "", docs, 1), ASK_USER)

    def test_short_docs_incomplete(self) -> None:
        self.assertEqual(classify_cli_failure("fail", "", "too short", 1), DESIGN_INCOMPLETE)

    def test_tick_distinguishes_running_failed_timeout(self) -> None:
        running = format_cli_tick(24, 8, 99, True, "等待首个事件", 0)
        self.assertIn("运行中", running)
        self.assertIn("尚未结束", running)
        failed = format_cli_tick(12, 0, 99, False, "unauthorized", 1, exit_code=1)
        self.assertIn("已失败", failed)
        self.assertIn("exit=1", failed)
        timeout = format_cli_tick(
            180, 180, 99, False, "timeout：进程仍在运行", 0, timed_out=True
        )
        self.assertIn("超时", timeout)
        self.assertIn("仍在跑", timeout)
        done = format_cli_tick(40, 0, 99, False, "CLI 正常返回结果", 3, exit_code=0)
        self.assertIn("已结束", done)
        self.assertIn("exit=0", done)

    def test_summarize_tool_and_error_events(self) -> None:
        self.assertIn("工具", summarize_cli_event({"type": "tool_call", "name": "Write"}))
        self.assertIn("报错", summarize_cli_event({"type": "result", "is_error": True, "result": "boom"}))
        self.assertIn("正常", summarize_cli_event({"type": "result", "result": "ok"}))

    def test_extend_wait_only_without_error(self) -> None:
        self.assertTrue(should_extend_cli_wait(False, False))
        self.assertFalse(should_extend_cli_wait(True, False))
        self.assertFalse(should_extend_cli_wait(False, True))

    def test_connection_lost_counts_as_cli_error(self) -> None:
        self.assertTrue(
            cli_output_looks_like_error(
                "stream disconnected before completion; reconnecting connection (attempt 1)..."
            )
        )
        self.assertTrue(cli_output_looks_like_error('{"type":"error","message":"boom"}'))
        self.assertFalse(cli_output_looks_like_error('{"type":"assistant","text":"writing PluginMain"}'))


if __name__ == "__main__":
    unittest.main()
