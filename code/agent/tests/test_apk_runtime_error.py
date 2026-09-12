from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from connect_phone import ConnectPhone
from orchestrator import Orchestrator
from program_agent.apk_builder import ApkBuilder, _runtime_fix_prompt
from program_agent.history_reuse import find_plugin_main


class RuntimeErrorProtocolTest(unittest.TestCase):
    def test_parse_runtime_error_type(self) -> None:
        line = json.dumps(
            {"type": "runtime_error", "text": '{"taskId":"俄罗斯方块","phase":"onCreate"}'},
            ensure_ascii=False,
        )
        msg_type, text = ConnectPhone._parse_line(line)
        self.assertEqual(msg_type, "runtime_error")
        self.assertIn("俄罗斯方块", text)

    def test_parse_plain_chat(self) -> None:
        msg_type, text = ConnectPhone._parse_line('{"type":"chat","text":"做个俄罗斯方块"}')
        self.assertEqual(msg_type, "chat")
        self.assertEqual(text, "做个俄罗斯方块")


class RuntimeErrorOrchestratorTest(unittest.TestCase):
    def test_missing_task_does_not_build(self) -> None:
        product = MagicMock()
        product.last_archive = None
        product.completed_archive = None
        product.store = MagicMock()
        product.store.list_recent.return_value = []
        product.archive_writer = None
        program = MagicMock()
        program.agent_root = Path(tempfile.gettempdir())
        orch = Orchestrator(product=product, program=program, downloads=MagicMock())
        reply = orch.handle_runtime_error(json.dumps({"taskId": "no-such", "phase": "load"}))
        self.assertIsNone(reply.follow_up)
        self.assertIn("找不到对应程序归档", reply.text)
        program.repair.assert_not_called()

    def test_known_task_repairs_on_follow_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent_root = Path(tmp)
            task_dir = agent_root / "out" / "tasks" / "俄罗斯方块"
            task_dir.mkdir(parents=True)
            meta = {
                "taskId": "俄罗斯方块",
                "title": "俄罗斯方块",
                "summary": "下落方块",
                "packageName": "com.autoprocedure.plugin.tetris",
                "icon": "📦",
                "taskDir": str(task_dir),
                "productDir": str(task_dir / "product"),
            }
            (task_dir / "meta.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
            product = MagicMock()
            product.last_archive = None
            product.completed_archive = None
            product.store = MagicMock()
            product.store.list_recent.return_value = []
            product.archive_writer = MagicMock()
            program = MagicMock()
            program.agent_root = agent_root
            program.repair.return_value = (
                {"taskId": "俄罗斯方块", "title": "俄罗斯方块"},
                "已修复",
                "ok",
            )
            downloads = MagicMock()
            downloads.url_for.return_value = "http://127.0.0.1:17891/tasks/俄罗斯方块/dist/plugin.apk"
            orch = Orchestrator(product=product, program=program, downloads=downloads)
            payload = {
                "taskId": "俄罗斯方块",
                "phase": "runtime",
                "message": "NPE",
                "stackTrace": "java.lang.NullPointerException",
            }
            reply = orch.handle_runtime_error(json.dumps(payload, ensure_ascii=False))
            self.assertEqual(reply.job["state"], "making")
            self.assertIsNotNone(reply.follow_up)
            done = reply.follow_up(lambda _msg: None)
            program.repair.assert_called_once()
            args = program.repair.call_args
            self.assertEqual(args[0][0]["taskId"], "俄罗斯方块")
            self.assertEqual(args[0][1]["phase"], "runtime")
            self.assertEqual(done.job["state"], "downloading")
            self.assertNotIn("downloadUrl", done.job)  # Registered later by ApkDelivery.
            product.archive_writer.append_alignment.assert_called()


class RuntimeRepairKeepsSourceTest(unittest.TestCase):
    def test_find_plugin_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task = Path(tmp)
            java = task / "plugin_src" / "app" / "src" / "main" / "java" / "pkg" / "PluginMain.java"
            java.parent.mkdir(parents=True)
            java.write_text("package pkg;\npublic class PluginMain {}\n", encoding="utf-8")
            found = find_plugin_main(task)
            self.assertEqual(found, java)

    def test_repair_does_not_wipe_plugin_src(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            agent_root = repo / "code" / "agent"
            agent_root.mkdir(parents=True)
            task_dir = agent_root / "out" / "tasks" / "demo"
            src = task_dir / "plugin_src"
            java = src / "app" / "src" / "main" / "java" / "com" / "demo" / "PluginMain.java"
            java.parent.mkdir(parents=True)
            java.write_text(
                "package com.demo;\npublic class PluginMain { /* sentinel-loop */ }\n",
                encoding="utf-8",
            )
            (src / "app" / "build.gradle").write_text("// keep gradle\n", encoding="utf-8")
            sentinel = src / "SENTINEL"
            sentinel.write_text("keep-me\n", encoding="utf-8")
            fake_apk = task_dir / "fake.apk"
            fake_apk.write_bytes(b"apk")
            builder = ApkBuilder(agent_root)
            builder._fix_java_after_runtime = lambda *args, **kwargs: (
                "package com.demo;\npublic class PluginMain implements "
                "com.autoprocedure.pluginapi.PluginEntry { /* fixed */ }\n"
            )
            builder._compile_with_fixes = lambda *args, **kwargs: fake_apk
            meta = {
                "taskId": "demo",
                "title": "demo",
                "summary": "demo",
                "packageName": "com.demo",
                "taskDir": str(task_dir),
                "productDir": str(task_dir / "product"),
            }
            job = builder.repair(meta, {"phase": "onCreate", "message": "boom", "stackTrace": "NPE"})
            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep-me\n")
            written = java.read_text(encoding="utf-8")
            self.assertIn("fixed", written)
            self.assertTrue(Path(job["apkPath"]).is_file())

    def test_runtime_prompt_keeps_current_source(self) -> None:
        prompt = _runtime_fix_prompt(
            "com.demo",
            Path("PluginMain.java"),
            "skill-text",
            {
                "phase": "runtime",
                "exceptionClass": "java.lang.NullPointerException",
                "message": "board",
                "stackTrace": "at PluginMain.tick",
            },
            "package com.demo;\npublic class PluginMain { void tick() {} }\n",
        )
        self.assertIn("不要从零重写", prompt)
        self.assertIn("NullPointerException", prompt)
        self.assertIn("void tick()", prompt)


if __name__ == "__main__":
    unittest.main()
