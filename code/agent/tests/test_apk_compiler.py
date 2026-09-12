from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from program_agent.compiler import compile_program, validate
from program_agent.codex_builder import _heuristic_program


class CompilerTest(unittest.TestCase):
    def test_sample_ok(self) -> None:
        sample = json.loads(
            (ROOT / "tests/fixtures/drink-water.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(validate(sample), [])

    def test_rejects_unknown_component(self) -> None:
        program = _heuristic_program({"title": "demo", "summary": "x"})
        program["screens"][0]["root"]["children"].append({"type": "WebView"})
        errors = validate(program)
        self.assertTrue(any("unknown component" in e for e in errors))

    def test_compile_writes(self) -> None:
        dest = ROOT / "out" / "test_plugins"
        program = _heuristic_program({"title": "unit-test", "summary": "s"})
        path = compile_program(program, dest)
        self.assertTrue(path.is_file())
        self.assertIn("programId", json.loads(path.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
