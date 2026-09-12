from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from apk_product_manager.archive import ProductArchive, pick_icon


class ArchiveTest(unittest.TestCase):
    def test_writes_product_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = ProductArchive(root / "tasks", Path("/repo"))
            meta = archive.create(
                {"title": "喝水打卡", "summary": "点一下加一杯"},
                ["我想喝水打卡"],
            )
            product = Path(meta["productDir"])
            self.assertTrue((product / "design.md").is_file())
            self.assertTrue((product / "implementation.md").is_file())
            self.assertTrue((product / "runtime.md").is_file())
            self.assertTrue((product / "code-locations.md").is_file())
            self.assertTrue((product / "intent.json").is_file())
            self.assertEqual(meta["taskId"], "喝水打卡")
            self.assertTrue((root / "tasks" / "喝水打卡").is_dir())
            self.assertRegex(meta["packageName"], r"^com\.autoprocedure\.plugin\.[a-z][a-z0-9]{1,27}$")
            locations = (product / "code-locations.md").read_text(encoding="utf-8")
            self.assertIn("PluginLoader.kt", locations)
            self.assertIn("PluginEntry.java", locations)

    def test_alignment_log_on_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = ProductArchive(Path(tmp) / "tasks", Path("/repo"))
            intent = {"title": "喝水打卡", "summary": "点一下加一杯", "acceptance": ["能加一杯"]}
            archive.start(intent, ["我想喝水打卡"])
            archive.append_alignment(
                kind="proposal",
                user_text="我想喝水打卡",
                agent_reply="方案：喝水打卡。请回复确认。",
                intent=intent,
            )
            archive.finalize(intent, ["我想喝水打卡", "确认"])
            product = Path(archive.current["productDir"])
            log = (product / "alignment-log.md").read_text(encoding="utf-8")
            self.assertIn("proposal", log)
            self.assertIn("confirm", log)
            self.assertTrue((product / "alignments.jsonl").is_file())
            self.assertTrue((product / "aligned.json").is_file())

    def test_pick_icon_from_title(self) -> None:
        self.assertEqual(pick_icon({"title": "俄罗斯方块"}), "🧱")
        self.assertEqual(pick_icon({"title": "卡通画图程序"}), "🎨")
        self.assertEqual(pick_icon({"title": "提示语表情包生成器"}), "😄")
        self.assertEqual(pick_icon({"title": "喝水打卡"}), "✅")
        self.assertEqual(pick_icon({"title": "连连看", "icon": "🧩"}), "🧩")


if __name__ == "__main__":
    unittest.main()
