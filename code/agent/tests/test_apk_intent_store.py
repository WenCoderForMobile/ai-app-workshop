from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apk_product_manager.archive import ProductArchive, _safe_title
from apk_product_manager.store import IntentStore


class IntentStoreTest(unittest.TestCase):
    def test_saves_by_program_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = IntentStore(Path(tmp))
            path = store.save(
                {"title": "俄罗斯方块小游戏", "summary": "下落方块"},
                ["我想要俄罗斯方块", "确认"],
            )
            self.assertEqual(path.name, "俄罗斯方块小游戏.json")
            self.assertTrue(path.is_file())
            self.assertTrue((Path(tmp) / "confirmed_intent.json").is_file())
            text = path.read_text(encoding="utf-8")
            self.assertIn("俄罗斯方块小游戏", text)
            self.assertIn("我想要俄罗斯方块", text)

    def test_finalize_preserves_identity_when_display_title_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = ProductArchive(Path(tmp) / "tasks", Path("/repo"))
            archive.start({"title": "草稿名", "summary": "先对齐"}, ["做个方块游戏"])
            self.assertEqual(archive.current["taskId"], "草稿名")
            meta = archive.finalize(
                {"title": "俄罗斯方块小游戏", "summary": "下落消除"},
                ["做个方块游戏", "确认"],
            )
            self.assertEqual(meta["taskId"], "草稿名")
            self.assertEqual(meta['title'], '俄罗斯方块小游戏')
            self.assertTrue((Path(tmp) / "tasks" / "草稿名" / "product" / "aligned.json").is_file())

    def test_safe_title_strips_path_chars(self) -> None:
        self.assertEqual(_safe_title("a/b:c"), "a-b-c")
        self.assertEqual(_safe_title("   "), "未命名程序")


if __name__ == "__main__":
    unittest.main()
