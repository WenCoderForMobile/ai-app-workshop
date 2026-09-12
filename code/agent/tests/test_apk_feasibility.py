from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apk_product_manager.feasibility import apply_verdict


class FeasibilityTest(unittest.TestCase):
    def test_pending_technical_review_does_not_forward_device_question(self):
        for kind in (None, 'technical_verification'):
            verdict = {'feasible': None, 'assessment': 'needs_clarification',
                       'effect': '请先确认手机能否发音', 'questions': ['是否能离线朗读？']}
            if kind:
                verdict['clarificationKind'] = kind
            status, reply, intent = apply_verdict('NEED_CLARIFY', '', {}, verdict)
            self.assertEqual(status, 'NEED_CLARIFY')
            self.assertNotIn('请先确认', reply)
            self.assertIn('自动核对', reply)
            self.assertEqual(intent['feasibility']['questions'], verdict['questions'])

    def test_product_preference_can_still_be_asked(self):
        status, reply, _ = apply_verdict('NEED_CLARIFY', '', {}, {
            'feasible': None, 'assessment': 'needs_clarification',
            'clarificationKind': 'user_preference', 'questions': ['希望自动朗读还是点击朗读？']})
        self.assertEqual(status, 'NEED_CLARIFY')
        self.assertEqual(reply, '希望自动朗读还是点击朗读？')

    def test_cli_overrides_unsupported_game(self) -> None:
        status, reply, intent = apply_verdict(
            "UNSUPPORTED",
            "打开后能玩俄罗斯方块，方块下落、消行得分。",
            {"title": "俄罗斯方块", "summary": "下落方块消除"},
            {
                "feasible": True,
                "title": "俄罗斯方块",
                "plan": "在宿主插件里用 Canvas 和 Handler 做下落、旋转、消除与计分。",
                "effect": "🧱 俄罗斯方块\n打开后：方块往下落，左右移动旋转，消行得分。",
            },
        )
        self.assertEqual(status, "WAITING_APPROVAL")
        self.assertNotIn("Canvas", reply)
        self.assertNotIn("Handler", reply)
        self.assertIn("方块", reply)
        self.assertIn("Canvas", intent.get("devPlan") or "")

    def test_payment_records_are_not_blocked_by_keyword(self) -> None:
        status, reply, _ = apply_verdict(
            "UNSUPPORTED", "涉及付款做不了",
            {"title": "付款记录", "summary": "本地记录付款金额"},
            {"feasible": True, "plan": "本地表单保存金额，不执行交易"},
        )
        self.assertEqual(status, "WAITING_APPROVAL")
        self.assertNotIn("做不了", reply)

    def test_no_cli_keeps_game_feasibility_pending(self) -> None:
        status, reply, _ = apply_verdict(
            "UNSUPPORTED",
            "动画做不了",
            {"title": "俄罗斯方块", "summary": "方块游戏"},
            None,
        )
        self.assertEqual(status, "NEED_CLARIFY")
        self.assertIn("未完成", reply)


if __name__ == "__main__":
    unittest.main()
