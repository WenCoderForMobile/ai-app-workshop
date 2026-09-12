from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from program_agent.apk_builder import ApkBuildError, _gradle_errors


class GradleRetryTest(unittest.TestCase):
    def test_extracts_javac_lines(self) -> None:
        log = """
Note: something
error: cannot find symbol
  symbol:   method foo()
* What went wrong:
Execution failed for task ':app:compileDebugJavaWithJavac'.
> Compilation failed; see the compiler error output for details.
BUILD FAILED in 4s
"""
        picked = _gradle_errors(log)
        self.assertIn("cannot find symbol", picked)
        self.assertIn("Compilation failed", picked)

    def test_build_error_keeps_log(self) -> None:
        err = ApkBuildError("gradle exit 1", log="error: boom")
        self.assertEqual(str(err), "gradle exit 1")
        self.assertIn("boom", err.log)


if __name__ == "__main__":
    unittest.main()
