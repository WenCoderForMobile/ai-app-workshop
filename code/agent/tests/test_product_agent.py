import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from product_manager import IntentStore, ProductAgent
from plugin import ProgramAgent


class ProductAgentPolicyTest(unittest.TestCase):
    def test_camera_is_rejected_without_a_builder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            agent = ProductAgent(IntentStore(root), llm=None)
            reply = agent.handle("做一个可以拍照识别花朵的功能")
            self.assertIn("不支持相机", reply)
            self.assertEqual(agent.phase, "intake")
            snapshots = list((root / "alignments" / agent.alignment_session_id).glob("*.json"))
            self.assertGreaterEqual(len(snapshots), 3)  # received, response, latest

    def test_three_empty_rejects_end_the_request(self):
        with tempfile.TemporaryDirectory() as directory:
            agent = ProductAgent(IntentStore(Path(directory)), llm=None)
            agent.handle("做一个喝水打卡")
            agent.handle("不满意")
            agent.handle("不满意")
            reply = agent.handle("不满意")
            self.assertIn("连续 3 次", reply)
            self.assertEqual(agent.phase, "intake")

    def test_confirm_compiles_and_sends_a_declarative_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = []
            jobs = []
            agent = ProductAgent(
                IntentStore(root / "intent"),
                llm=None,
                on_plugin=lambda result: artifacts.append(result),
                on_job=jobs.append,
            )
            agent.program_agent = ProgramAgent(root / "plugins", codex_command="not-a-real-codex-cli")
            agent.handle("做一个本地喝水打卡")
            reply = agent.handle("确认")
            self.assertIn("发布", reply)
            self.assertEqual(len(artifacts), 1)
            self.assertIn("bytes", artifacts[0])
            self.assertEqual(
                [job["status"] for job in jobs],
                ["RUNNING", "RUNNING", "SUCCEEDED"],
            )
            self.assertEqual(jobs[0]["phase"], "COMPILING")
            self.assertEqual(jobs[1]["phase"], "PUBLISHING")
            self.assertEqual(jobs[-1]["taskId"], artifacts[0]["manifest"]["taskId"])
            self.assertTrue(list((root / "plugins").rglob("*.apkg")))

    def test_publish_failure_is_not_reported_as_compile_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = []

            def fail_publish(_result):
                raise OSError("artifact port unavailable")

            agent = ProductAgent(
                IntentStore(root / "intent"),
                llm=None,
                on_plugin=fail_publish,
                on_job=jobs.append,
            )
            agent.program_agent = ProgramAgent(
                root / "plugins", codex_command="not-a-real-codex-cli"
            )
            agent.handle("做一个本地喝水打卡")
            reply = agent.handle("确认")
            self.assertIn("已生成但发布失败", reply)
            self.assertEqual(jobs[-1]["status"], "FAILED")
            self.assertEqual(jobs[-1]["phase"], "PUBLISHING")
            self.assertEqual(jobs[-1]["errorCode"], "PUBLISH_FAILED")


if __name__ == "__main__":
    unittest.main()
