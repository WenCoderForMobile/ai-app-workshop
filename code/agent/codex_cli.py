"""Shared non-interactive Codex CLI invocation and compatibility checks."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional, Sequence, Union


def cli_enabled() -> bool:
    return os.environ.get("AUTO_PROCEDURE_USE_CODEX_CLI", "1").strip().lower() in ("1", "true", "yes")


def find_codex() -> Optional[str]:
    return shutil.which(os.environ.get("CODEX_CLI", "codex"))


class CodexStartupError(RuntimeError):
    """Codex is not ready; do not start accepting phone requests."""


def ensure_codex_login() -> None:
    """Authenticate at server startup only, before creating any listeners."""
    if not cli_enabled():
        return
    cli = find_codex()
    if not cli:
        raise CodexStartupError("未找到 Codex CLI，请安装或设置 CODEX_CLI 后重启 server。")
    # Use the same executable for authentication and subsequent generation.
    os.environ["CODEX_CLI"] = cli
    print("Codex CLI executable:", cli, flush=True)
    try:
        version = subprocess.run([cli, "--version"], capture_output=True, text=True, timeout=12)
        if version.returncode != 0:
            raise CodexStartupError("Codex CLI 无法运行，请修复安装后重启 server。")
        print((version.stdout or "Codex CLI ready").strip(), flush=True)
        if os.environ.get("CODEX_API_KEY"):
            print("Codex CLI：已配置 API Key，后续调用复用该凭据。", flush=True)
            return
        status = subprocess.run([cli, "login", "status"], capture_output=True, text=True, timeout=12)
        if status.returncode != 0:
            if not sys.stdin.isatty():
                raise CodexStartupError("Codex 尚未登录。请在电脑终端运行 codex login，然后重启 server。")
            print("Codex 尚未登录，请完成浏览器登录；完成后才会启动 server。", flush=True)
            login = subprocess.run([cli, "login"], timeout=300)
            if login.returncode != 0:
                raise CodexStartupError("Codex 登录未完成，server 未启动。")
            status = subprocess.run([cli, "login", "status"], capture_output=True, text=True, timeout=12)
            if status.returncode != 0:
                raise CodexStartupError("Codex 登录状态仍无效，server 未启动。")
        print("Codex CLI：已登录，后续生成直接复用登录状态。", flush=True)
    except subprocess.TimeoutExpired as exc:
        raise CodexStartupError("Codex 登录或启动检查超时，server 未启动，请重试。") from exc
    except OSError as exc:
        raise CodexStartupError("无法启动 Codex CLI，server 未启动，请检查 CODEX_CLI。") from exc


def requires_cli_upgrade(output: str) -> bool:
    text = (output or "").lower()
    return "codex" in text and any(phrase in text for phrase in (
        "requires a newer version", "requires a newer", "upgrade to the latest app or cli",
    ))


def exec_args(cli: Union[str, Sequence[str]], prompt: str, work_dir: Path, *,
              writable: bool = False, events: bool = False, last_message: Optional[Path] = None) -> list[str]:
    command = [cli] if isinstance(cli, str) else list(cli)
    command += ["exec", "--sandbox", "workspace-write" if writable else "read-only",
                "--skip-git-repo-check", "--ephemeral", "--color", "never", "-C", str(work_dir.resolve())]
    if events:
        command.append("--json")
    if last_message is not None:
        command += ["--output-last-message", str(last_message.resolve())]
    return command + [prompt]
