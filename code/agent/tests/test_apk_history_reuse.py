from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from program_agent.history_reuse import find_similar_plugin, retarget_package


class HistoryReuseTest(unittest.TestCase):
    def test_prefers_current_task_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp)
            current = tasks / "俄罗斯方块"
            java_dir = current / "plugin_src" / "app" / "src" / "main" / "java" / "pkg"
            java_dir.mkdir(parents=True)
            (java_dir / "PluginMain.java").write_text(
                "package old.pkg;\npublic class PluginMain {}\n",
                encoding="utf-8",
            )
            (current / "meta.json").write_text(
                '{"taskId":"俄罗斯方块","title":"俄罗斯方块","summary":"下落方块"}',
                encoding="utf-8",
            )
            found = find_similar_plugin(tasks, {"title": "俄罗斯方块加爆炸"}, "俄罗斯方块")
            self.assertIsNotNone(found)
            self.assertEqual(found["score"], 100)
            self.assertIn("本任务", found["reason"])

    def test_picks_similar_other_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tasks = Path(tmp)
            other = tasks / "掌机造型俄罗斯方块"
            java_dir = other / "plugin_src" / "app" / "src" / "main" / "java" / "pkg"
            java_dir.mkdir(parents=True)
            (java_dir / "PluginMain.java").write_text(
                "package old.pkg;\npublic class PluginMain { void game() {} }\n",
                encoding="utf-8",
            )
            (other / "meta.json").write_text(
                '{"taskId":"掌机造型俄罗斯方块","title":"掌机造型俄罗斯方块","summary":"下落消除"}',
                encoding="utf-8",
            )
            found = find_similar_plugin(
                tasks,
                {"title": "俄罗斯方块消除爆炸", "summary": "消行爆炸"},
                "新任务",
            )
            self.assertIsNotNone(found)
            self.assertEqual(found["title"], "掌机造型俄罗斯方块")

    def test_retarget_package(self) -> None:
        src = "package com.old.app;\npublic class PluginMain {}"
        out = retarget_package(src, "com.new.app")
        self.assertIn("package com.new.app;", out)
        self.assertNotIn("com.old.app", out)


if __name__ == "__main__":
    unittest.main()
