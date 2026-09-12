"""Call Codex CLI (`codex exec`) to write ProgramIR. Falls back to the product LLM."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from llm import LlmClient
from codex_cli import exec_args, find_codex, cli_enabled
from trace import flow, kv

from .compiler import validate


class CodexBuilder:
    def __init__(self, work_dir: Path, framework_path: Path, llm: Optional[LlmClient]) -> None:
        self.work_dir = work_dir
        self.framework_path = framework_path
        self.llm = llm
        self.cli = find_codex()

    def build(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        dest = self.work_dir / "program.json"
        if dest.exists():
            dest.unlink()
        env_flag = os.environ.get("AUTO_PROCEDURE_USE_CODEX_CLI", "1").strip()
        use_cli = env_flag.lower() in ("1", "true", "yes")
        kv("AUTO_PROCEDURE_USE_CODEX_CLI", env_flag or "(unset)")
        kv("codex_cli_binary", self.cli or "(not on PATH)")
        if not use_cli:
            flow("program agent: Codex CLI skipped (env not 1/true/yes)")
        elif not self.cli:
            flow("program agent: Codex CLI skipped (codex binary not on PATH)")
        else:
            try:
                flow("program agent: Codex CLI start")
                kv("cli", self.cli)
                program = self._via_cli(intent, dest)
                if program:
                    flow("program agent: Codex CLI ok")
                    kv("programId", program.get("programId") or "")
                    return program
            except subprocess.TimeoutExpired:
                flow("program agent: Codex CLI timeout (300s), fallback LLM")
            except Exception as exc:
                flow("program agent: Codex CLI failed: %s" % exc)
        flow("program agent: LLM generate ProgramIR")
        return self._via_llm(intent)

    def _via_cli(self, intent: Dict[str, Any], dest: Path) -> Optional[Dict[str, Any]]:
        prompt = self._prompt(intent, dest)
        cmd = exec_args(self.cli, prompt, self.work_dir, writable=True)
        kv("cli_cmd", " ".join(cmd[:2]) + " <prompt> " + " ".join(cmd[3:]))
        kv("cli_cwd", str(self.work_dir))
        kv("cli_timeout", "300s")
        flow("   waiting for Codex CLI...")
        env = os.environ.copy()
        proc = subprocess.run(
            cmd,
            cwd=str(self.work_dir),
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        kv("cli_exit", proc.returncode)
        stdout = (proc.stdout or "").strip()
        stderr = (proc.stderr or "").strip()
        if stdout:
            kv("cli_stdout_tail", stdout[-800:])
        if stderr:
            kv("cli_stderr_tail", stderr[-800:])
        if proc.returncode != 0:
            raise RuntimeError(stderr or "Codex CLI failed exit=%s" % proc.returncode)
        if dest.is_file():
            program = json.loads(dest.read_text(encoding="utf-8"))
            errors = validate(program)
            if errors:
                raise RuntimeError("CLI IR invalid: " + "; ".join(errors))
            kv("cli_wrote", str(dest))
            return program
        program = _extract_json(stdout)
        if program:
            errors = validate(program)
            if not errors:
                dest.write_text(json.dumps(program, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                return program
            raise RuntimeError("CLI stdout JSON invalid: " + "; ".join(errors))
        raise RuntimeError(stderr or stdout or "CLI produced no program.json")

    def _via_llm(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        if self.llm is None:
            flow("program agent: no LLM, heuristic ProgramIR")
            return _heuristic_program(intent)
        self.llm.reload()
        messages = [
            {"role": "system", "content": _LLM_SYSTEM},
            {
                "role": "user",
                "content": "框架说明：\n"
                + self.framework_path.read_text(encoding="utf-8")
                + "\n\n已确认意图 JSON：\n"
                + json.dumps(intent, ensure_ascii=False),
            },
        ]
        try:
            program = self.llm.chat_json(messages)
        except Exception as exc:
            flow("program agent: LLM IR failed: %s" % exc)
            flow("program agent: heuristic ProgramIR")
            return _heuristic_program(intent)
        errors = validate(program)
        if errors:
            flow("program agent: LLM IR invalid, heuristic: %s" % errors)
            return _heuristic_program(intent)
        flow("program agent: LLM IR ok")
        kv("programId", program.get("programId") or "")
        return program

    def _prompt(self, intent: Dict[str, Any], dest: Path) -> str:
        framework = self.framework_path.read_text(encoding="utf-8")
        return (
            "你是手机程序作坊的程序 Agent。只生成声明式 ProgramIR，不写 Android/Kotlin/DEX。\n"
            "把结果写入这个文件（JSON，UTF-8）：\n%s\n\n"
            "必须遵守下面的加载框架与白名单。capabilities 必须是 []。\n\n"
            "%s\n\n已确认意图：\n%s\n"
            % (dest, framework, json.dumps(intent, ensure_ascii=False, indent=2))
        )


_LLM_SYSTEM = """你是程序 Agent。只输出一个 ProgramIR JSON 对象，不要 Markdown。
遵守用户消息里的框架说明。capabilities 必须是 []。
组件仅 Column/Row/Text/Button/TextInput/Checkbox/List/Spacer。
动作仅 setState/showMessage/navigate/finish/listAppend/listRemove。
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _heuristic_program(intent: Dict[str, Any]) -> Dict[str, Any]:
    title = str(intent.get("title") or "本地小工具")[:40]
    summary = str(intent.get("summary") or title)
    program_id = _slug(title)
    return {
        "formatVersion": "1.0",
        "programId": program_id,
        "title": title,
        "summary": summary,
        "revision": 1,
        "runtime": {"name": "runtime-v1", "uiRegistryVersion": "1.0"},
        "capabilities": [],
        "state": {
            "count": {"type": "int64", "value": 0, "persistence": "local"},
            "note": {"type": "string", "value": "", "persistence": "session"},
        },
        "screens": [
            {
                "id": "main",
                "root": {
                    "type": "Column",
                    "children": [
                        {"type": "Text", "text": title},
                        {"type": "Text", "text": summary},
                        {"type": "Text", "text": "计数：{count}"},
                        {
                            "type": "Button",
                            "text": "完成一次",
                            "onClick": [{"action": "setState", "key": "count", "op": "inc"}],
                        },
                        {
                            "type": "TextInput",
                            "bind": "note",
                            "hint": "备注",
                            "onChange": [{"action": "setState", "key": "note", "op": "set"}],
                        },
                    ],
                },
            }
        ],
    }


def _slug(title: str) -> str:
    import hashlib
    import re

    raw = re.sub(r"[^\w.-]+", "-", title, flags=re.UNICODE).strip("-").lower()
    if not raw:
        raw = "p-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:10]
    return raw[:40]
