from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apk_product_manager.archive import ProductArchive
from apk_product_manager.product_agent import ProductAgent, _approval_reply, _is_confirm, _refers_to_history
from apk_product_manager.store import IntentStore


class ProductConfirmTest(unittest.TestCase):
    def test_confirm_punctuation(self) -> None:
        self.assertTrue(_is_confirm("确认"))
        self.assertTrue(_is_confirm("确认。"))
        self.assertTrue(_is_confirm("好的"))
        self.assertTrue(_is_confirm("好"))
        self.assertTrue(_is_confirm("ok"))
        self.assertTrue(_is_confirm("OK"))
        self.assertTrue(_is_confirm("同意"))
        self.assertTrue(_is_confirm("对的"))
        self.assertTrue(_is_confirm("可以"))
        self.assertTrue(_is_confirm("没问题"))
        self.assertFalse(_is_confirm("好的，把按键再做大一点"))
        self.assertFalse(_is_confirm("不对"))

    def test_refers_to_history(self) -> None:
        self.assertTrue(_refers_to_history("刚才功能"))
        self.assertTrue(_refers_to_history("把上次那个再做一遍"))
        self.assertTrue(_refers_to_history("改一下刚才的爆炸效果"))
        self.assertFalse(_refers_to_history("做一个新的计算器"))

    def test_strips_fake_generate(self) -> None:
        raw = (
            "收到，方案已确认：emoji 连连看，点两个相同表情消除，"
            "带计时和计分，纯本地运行。正在为你生成插件，请稍候。"
        )
        out = _approval_reply(raw, {"title": "连连看", "summary": "点两个相同表情消除"})
        self.assertNotIn("正在生成", out)
        self.assertNotIn("请稍候", out)
        self.assertIn("开始做", out)
        self.assertIn("🧩", out)

    def test_hides_implementation(self) -> None:
        raw = (
            "做成宿主进程内插件。用 Canvas 和 Handler 做下落。\n"
            "打开后能玩俄罗斯方块，消行得分。"
        )
        out = _approval_reply(raw, {"title": "俄罗斯方块"})
        self.assertNotIn("Canvas", out)
        self.assertNotIn("Handler", out)
        self.assertNotIn("宿主", out)
        self.assertIn("俄罗斯方块", out)
        self.assertIn("🧱", out)

    def test_keeps_history_and_resumes_last_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = IntentStore(root)
            archive = ProductArchive(root / "tasks", Path("/repo"))
            agent = ProductAgent(store=store, llm=None, archive=archive)
            agent.handle("俄罗斯方块加爆炸")
            self.assertEqual(agent.phase, "waiting_approval")
            agent.handle("确认")
            self.assertEqual(agent.phase, "confirmed")
            self.assertTrue(agent.history)
            reply = agent.handle("刚才功能")
            self.assertIn("俄罗斯方块", reply)
            self.assertEqual(agent.last_intent.get("title"), "俄罗斯方块加爆炸")
            catalog = (root / "task_index.json").read_text(encoding="utf-8")
            self.assertIn("俄罗斯方块加爆炸", catalog)


if __name__ == "__main__":
    unittest.main()
