import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
import sys as runtime_sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from plugin.program_agent import CompileError, ProgramAgent


class ProgramAgentTest(unittest.TestCase):
    def test_fallback_compiles_versioned_declarative_package(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = ProgramAgent(output, codex_command="not-a-real-codex-cli").build(
                {"title": "喝水打卡", "summary": "记录今日喝水", "capabilities": []}
            )
            self.assertEqual(result["provider"], "deterministic-fallback")
            self.assertTrue(result["path"].is_file())
            self.assertEqual(result["path"].parent.name, result["programId"])
            with zipfile.ZipFile(result["path"]) as package:
                self.assertEqual(package.namelist(), ["manifest.json", "program/main.json", "tests/smoke.json"])
                manifest = json.loads(package.read("manifest.json"))
                program = json.loads(package.read("program/main.json"))
                self.assertEqual(manifest["programId"], program["programId"])
                self.assertTrue(manifest["debugUnsigned"])
                for descriptor in manifest["files"]:
                    self.assertEqual(
                        descriptor["sha256"],
                        hashlib.sha256(package.read(descriptor["path"])).hexdigest(),
                    )

    def test_compiler_rejects_unknown_component(self):
        agent = ProgramAgent(Path(tempfile.gettempdir()), codex_command="not-a-real-codex-cli")
        candidate = {"program": {"title": "坏包", "state": {}, "screens": [{"id": "home", "children": [{"id": "bad", "type": "WebView"}]}]}}
        with self.assertRaisesRegex(CompileError, "SCHEMA_INVALID"):
            agent._compile({"title": "坏包", "capabilities": []}, candidate)

    def test_capability_request_is_rejected_before_builder(self):
        agent = ProgramAgent(Path(tempfile.gettempdir()), codex_command="not-a-real-codex-cli")
        with self.assertRaisesRegex(CompileError, "UNSUPPORTED_CAPABILITY"):
            agent.build({"title": "拍照", "capabilities": ["camera"]})

    def test_invalid_cli_candidate_is_recorded_and_retried_at_most_three_times(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "bad_cli.py"
            script.write_text("print('not json')\n", encoding="utf-8")
            archive = root / "archive"
            archive.mkdir()
            result = ProgramAgent(root / "plugins", codex_command=f"{runtime_sys.executable} {script}").build(
                {"title": "测试", "summary": "x", "capabilities": [], "productArchive": str(archive)}
            )
            report = result["developmentReport"]
            self.assertEqual(report["attempts"], 3)
            self.assertEqual(report["decision"], "design_review")
            progress = (archive / "development-progress.jsonl").read_text(encoding="utf-8")
            self.assertEqual(progress.count('"state": "started"'), 3)


if __name__ == "__main__":
    unittest.main()
