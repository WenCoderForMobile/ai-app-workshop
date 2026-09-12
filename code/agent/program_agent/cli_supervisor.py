"""Run Codex CLI with periodic progress; retry at most 3 times after classifying failures."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from trace import flow, kv
from codex_cli import exec_args, requires_cli_upgrade
from .source_checkpoints import checkpoint

MAX_CLI_ATTEMPTS = 3
PROGRESS_EVERY_SECONDS = 8
CLI_TIMEOUT_SECONDS = 300
CLI_EXTRA_WAIT_SECONDS = 300
CLI_HARD_MAX_SECONDS = CLI_TIMEOUT_SECONDS + CLI_EXTRA_WAIT_SECONDS

ProgressFn = Callable[[str], None]

RETRY = "retry"
ASK_USER = "ask_user"
DESIGN_INCOMPLETE = "design_incomplete"
OK = "ok"
EXHAUSTED = "exhausted"
UPGRADE_REQUIRED = "upgrade_required"


class CliRunResult:
    def __init__(
        self,
        ok: bool,
        java: str = "",
        output: str = "",
        error: str = "",
        attempts: int = 0,
        decision: str = OK,
        analysis: str = "",
        progress_log: str = "",
    ) -> None:
        self.ok = ok
        self.java = java
        self.output = output
        self.error = error
        self.attempts = attempts
        self.decision = decision
        self.analysis = analysis
        self.progress_log = progress_log


def classify_cli_failure(
    error: str,
    output: str,
    docs: str,
    attempt: int,
    cap: int = MAX_CLI_ATTEMPTS,
) -> str:
    """Decide retry vs ask user vs incomplete design. Never loops past cap."""
    blob = ("%s\n%s" % (error, output)).lower()
    # A client/server compatibility rejection cannot be fixed by retrying or
    # changing the product design, even on the final allowed attempt.
    if requires_cli_upgrade(blob):
        return UPGRADE_REQUIRED
    if attempt >= cap:
        return EXHAUSTED
    if _design_incomplete(docs):
        return DESIGN_INCOMPLETE
    if any(token in blob for token in ("auth", "api key", "unauthorized", "login", "not logged")):
        return ASK_USER
    if any(token in blob for token in ("camera", "支付", "permission denied", "需要你确认", "ask the user")):
        return ASK_USER
    if any(
        token in blob
        for token in (
            "timeout",
            "timed out",
            "econnreset",
            "connection",
            "429",
            "unavailable",
            "empty",
            "no pluginmain",
            "cannot find symbol",
            "error:",
            "compilation",
            "exit",
        )
    ):
        return RETRY
    if "ambiguous" in blob or "不清楚" in blob or "incomplete" in blob:
        return DESIGN_INCOMPLETE
    return RETRY


def _design_incomplete(docs: str) -> bool:
    text = docs or ""
    if len(text.strip()) < 80:
        return True
    if "验收" not in text and "acceptance" not in text.lower():
        return True
    return False


def user_message_for_decision(decision: str, analysis: str, attempts: int) -> str:
    if decision == UPGRADE_REQUIRED:
        return (
            "当前 Codex CLI 版本过旧，无法使用所选模型，已停止重试。\n"
            "请更新电脑上的 Codex CLI；npm 安装可运行 npm install -g @openai/codex@latest。\n"
            "若设置了 CODEX_CLI，请确认它指向更新后的程序，然后重新发起制作。\n%s" % analysis
        )
    if decision == ASK_USER:
        return "程序 Agent 在写插件时卡住了，需要你补充意见。\n%s\n说明后同意即可再编译。" % analysis
    if decision == DESIGN_INCOMPLETE:
        return "当前产品设计还不够完整，程序 Agent 无法继续。\n%s\n请补充主操作或验收，然后同意即可。" % analysis
    if decision == EXHAUSTED:
        return "Codex CLI 已重试 %s 次仍失败，已停止以免死循环。\n%s" % (attempts, analysis)
    return analysis


def run_cli_with_retries(
    cli: str,
    prompt: str,
    work_dir: Path,
    dest: Path,
    docs: str,
    task_dir: Path,
    on_progress: Optional[ProgressFn] = None,
    extract_java=None,
    max_attempts: int = MAX_CLI_ATTEMPTS,
) -> CliRunResult:
    """Poll CLI output periodically; retry transient failures up to max_attempts."""
    import subprocess

    cap = max(1, int(max_attempts or MAX_CLI_ATTEMPTS))
    dev_dir = task_dir / "dev"
    dev_dir.mkdir(parents=True, exist_ok=True)
    progress_path = dev_dir / "cli-progress.log"
    attempts_path = dev_dir / "attempts.jsonl"
    last_output = ""
    last_error = ""
    last_decision = RETRY
    last_analysis = ""

    for attempt in range(1, cap + 1):
        checkpoint(task_dir, dest, 'before-cli')
        prior_source = dest.read_text(encoding='utf-8') if dest.is_file() else ''
        extra = ""
        if dest.is_file():
            extra = '\n已有源码保留在输出路径中，先读取并在原文件上继续；可能是未编译草稿，请修复而非从零重写。\n'
        if attempt > 1:
            extra += (
                "\n\n这是第 %s 次请求（最多 %s 次）。上次失败：%s\n请只输出完整 PluginMain.java。\n"
                % (attempt, cap, (last_error or last_output)[:800])
            )
        msg = "开发 Agent：Codex CLI 第 %s/%s 次…" % (attempt, cap)
        flow(msg)
        if on_progress:
            on_progress(msg)
        _append_progress(progress_path, msg)

        work_dir.mkdir(parents=True, exist_ok=True)
        final_message = work_dir / "codex-last-message.txt"
        if final_message.exists():
            final_message.unlink()
        cmd = exec_args(cli, prompt + extra, work_dir, writable=True, events=True, last_message=final_message)
        run = _run_logged(
            cmd,
            work_dir,
            progress_path,
            on_progress,
            attempt,
        )
        last_output = run.output
        last_error = run.error
        # Never accept files from an interrupted or failed Codex turn.
        java = ""
        if run.exit_code == 0 and not run.timed_out:
            final_text = final_message.read_text(encoding="utf-8") if final_message.is_file() else ""
            if dest.is_file() and dest.read_text(encoding='utf-8') == prior_source:
                emitted = _java_from_stream(run.output or '')
                if extract_java:
                    emitted = extract_java(final_text or '') or emitted
                if emitted:
                    dest.write_text(emitted, encoding='utf-8')
            java = _load_java(dest, final_text or run.output, extract_java)
        else:
            # Retain failed-turn output for the next attempt, never publish it.
            checkpoint(task_dir, dest, 'failed-cli')
            final_text = final_message.read_text(encoding='utf-8') if final_message.is_file() else ''
            draft = _java_from_stream(run.output or '')
            if not draft and extract_java:
                draft = extract_java(final_text or run.output or '')
            if not draft and dest.is_file():
                draft = dest.read_text(encoding='utf-8')
            if draft:
                draft_path = task_dir / 'dev/source-draft/PluginMain.java'
                checkpoint(task_dir, draft_path, 'previous-draft')
                draft_path.parent.mkdir(parents=True, exist_ok=True)
                draft_path.write_text(draft, encoding='utf-8')
                # Make the retained draft available to retries in this same run.
                if attempt < cap:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(draft, encoding='utf-8')
        record = {
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "attempt": attempt,
            "timedOut": run.timed_out,
            "exitCode": run.exit_code,
            "aliveWhenKilled": run.alive_when_killed,
            "lastStatus": run.last_status,
            "eventCount": run.event_count,
            "error": last_error[:500],
            "outputTail": (run.output or "")[-500:],
            "gotJava": bool(java),
        }
        with attempts_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        kv("cli_attempt", attempt)
        kv("cli_got_java", bool(java))

        if java:
            analysis = "CLI 第 %s 次写出 PluginMain" % attempt
            _append_progress(progress_path, analysis)
            return CliRunResult(
                ok=True,
                java=java,
                output=run.output,
                attempts=attempt,
                decision=OK,
                analysis=analysis,
                progress_log=str(progress_path),
            )

        last_analysis = last_error or run.last_status or "CLI 没有写出 PluginMain.java"
        last_decision = classify_cli_failure(last_analysis, run.output, docs, attempt, cap=cap)
        note = "CLI 第 %s 次未完成：%s → %s" % (attempt, last_analysis[:180], last_decision)
        flow(note)
        _append_progress(progress_path, note)
        if on_progress:
            on_progress("开发 Agent：" + note)
        if last_decision != RETRY:
            break

    if last_decision == RETRY:
        last_decision = EXHAUSTED
        last_analysis = "已达 %s 次上限：%s" % (cap, last_analysis)
    return CliRunResult(
        ok=False,
        output=last_output,
        error=last_error,
        attempts=min(cap, max(1, int(record.get("attempt") or 1))),
        decision=last_decision,
        analysis=last_analysis,
        progress_log=str(progress_path),
    )


class _CliProcRun:
    def __init__(
        self,
        output: str = "",
        error: str = "",
        timed_out: bool = False,
        exit_code: Optional[int] = None,
        last_status: str = "",
        event_count: int = 0,
        alive_when_killed: bool = False,
        pid: int = 0,
    ) -> None:
        self.output = output
        self.error = error
        self.timed_out = timed_out
        self.exit_code = exit_code
        self.last_status = last_status
        self.event_count = event_count
        self.alive_when_killed = alive_when_killed
        self.pid = pid


def _run_logged(
    cmd: List[str],
    cwd: Path,
    progress_path: Path,
    on_progress: Optional[ProgressFn],
    attempt: int,
) -> _CliProcRun:
    import subprocess

    env = os.environ.copy()
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
        bufsize=1,
    )
    chunks: List[str] = []
    lock = threading.Lock()
    last_status = "已启动，等待首个事件"
    event_count = 0
    last_event_at = time.monotonic()
    last_report = time.monotonic()
    started = time.monotonic()
    timed_out = False
    alive_when_killed = False
    cli_has_error = False
    extra_granted = False
    deadline = CLI_TIMEOUT_SECONDS

    def _reader() -> None:
        nonlocal last_status, event_count, last_event_at, cli_has_error
        assert proc.stdout is not None
        for line in proc.stdout:
            with lock:
                chunks.append(line)
                summary = _ingest_cli_line(line)
                if summary:
                    last_status = summary
                    event_count += 1
                    last_event_at = time.monotonic()
                if cli_output_looks_like_error(line):
                    cli_has_error = True
            _append_progress(progress_path, "cli[%s] %s" % (attempt, line.rstrip()[:400]))
        proc.stdout.close()

    reader = threading.Thread(target=_reader, name="cli-progress", daemon=True)
    reader.start()
    while proc.poll() is None:
        now = time.monotonic()
        elapsed = now - started
        idle = now - last_event_at
        with lock:
            has_error = cli_has_error
            status_now = last_status
        if extra_granted and has_error:
            alive_when_killed = proc.poll() is None
            if alive_when_killed:
                proc.kill()
            timed_out = True
            last_status = "timeout：续等期间 CLI 报错，已停止。%s" % status_now
            break
        if elapsed >= deadline:
            if should_extend_cli_wait(has_error, extra_granted):
                extra_granted = True
                deadline = CLI_HARD_MAX_SECONDS
                notice = (
                    "CLI 已运行 %s 分钟且未报错，再等待 %s 分钟（最长 %s 分钟）"
                    % (
                        CLI_TIMEOUT_SECONDS // 60,
                        CLI_EXTRA_WAIT_SECONDS // 60,
                        CLI_HARD_MAX_SECONDS // 60,
                    )
                )
                flow(notice)
                _append_progress(progress_path, notice)
                if on_progress:
                    on_progress("开发 Agent：" + notice)
                last_report = now
                continue
            alive_when_killed = proc.poll() is None
            if alive_when_killed:
                proc.kill()
            timed_out = True
            if extra_granted:
                last_status = "timeout：无报错续等后已满 %ss，进程尚未自己结束" % CLI_HARD_MAX_SECONDS
            elif has_error:
                last_status = "timeout：CLI 已报错，%ss 到时停止。%s" % (
                    CLI_TIMEOUT_SECONDS,
                    status_now,
                )
            else:
                last_status = "timeout：已满 %ss，进程尚未自己结束" % CLI_TIMEOUT_SECONDS
            break
        if now - last_report >= PROGRESS_EVERY_SECONDS:
            last_report = now
            with lock:
                tick = format_cli_tick(
                    elapsed_s=int(elapsed),
                    idle_s=int(idle),
                    pid=proc.pid or 0,
                    running=True,
                    last_status=last_status,
                    event_count=event_count,
                )
            flow(tick)
            _append_progress(progress_path, tick)
            if on_progress:
                on_progress("开发 Agent：" + tick)
        time.sleep(0.4)
    reader.join(timeout=5)
    try:
        proc.wait(timeout=2)
    except Exception:
        proc.kill()
    with lock:
        output = "".join(chunks)
        status = last_status
        events = event_count
    exit_code = proc.returncode
    if timed_out:
        error = status if status.startswith("timeout") else (
            "timeout：已满 %ss，进程尚未自己结束" % CLI_TIMEOUT_SECONDS
        )
        final = format_cli_tick(
            elapsed_s=int(time.monotonic() - started),
            idle_s=int(time.monotonic() - last_event_at),
            pid=proc.pid or 0,
            running=False,
            last_status=error,
            event_count=events,
            exit_code=exit_code,
            timed_out=True,
        )
    elif exit_code not in (0, None):
        error = "CLI 失败 exit=%s：%s" % (exit_code, status)
        final = format_cli_tick(
            elapsed_s=int(time.monotonic() - started),
            idle_s=0,
            pid=proc.pid or 0,
            running=False,
            last_status=status,
            event_count=events,
            exit_code=exit_code,
        )
    else:
        error = ""
        final = format_cli_tick(
            elapsed_s=int(time.monotonic() - started),
            idle_s=0,
            pid=proc.pid or 0,
            running=False,
            last_status=status or "已正常退出",
            event_count=events,
            exit_code=exit_code if exit_code is not None else 0,
        )
    flow(final)
    _append_progress(progress_path, final)
    if on_progress:
        on_progress("开发 Agent：" + final)
    return _CliProcRun(
        output=output,
        error=error,
        timed_out=timed_out,
        exit_code=exit_code,
        last_status=status,
        event_count=events,
        alive_when_killed=alive_when_killed,
        pid=proc.pid or 0,
    )


def should_extend_cli_wait(has_error: bool, already_extended: bool) -> bool:
    """After the first 5 minutes, keep waiting only if CLI has not reported an error."""
    return (not has_error) and (not already_extended)


def cli_output_looks_like_error(line: str) -> bool:
    raw = (line or "").strip()
    if not raw:
        return False
    low = raw.lower()
    if "connection lost" in low:
        return True
    if "reconnect" in low and ("stream" in low or "connection" in low):
        return True
    if any(token in low for token in ("unauthorized", "not logged", "permission denied", "api key")):
        return True
    if not raw.startswith("{"):
        return False
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return False
    if not isinstance(obj, dict):
        return False
    if obj.get("is_error") is True:
        return True
    typ = str(obj.get("type") or "")
    subtype = str(obj.get("subtype") or "")
    return typ in ("error", "turn.failed") or subtype in ("error", "failure")


def format_cli_tick(
    elapsed_s: int,
    idle_s: int,
    pid: int,
    running: bool,
    last_status: str,
    event_count: int,
    exit_code: Optional[int] = None,
    timed_out: bool = False,
) -> str:
    """Human-readable CLI process state for phone + terminal."""
    detail = (last_status or "").replace("\n", " ").strip()
    if running:
        return "CLI 运行中 %ss（pid=%s，尚未结束，空闲 %ss）：%s · 已收 %s 条事件" % (
            elapsed_s,
            pid or "?",
            idle_s,
            detail or "等待首个事件",
            event_count,
        )
    if timed_out:
        return "CLI 超时结束 %ss（pid=%s，当时仍在跑）：%s · 共 %s 条事件" % (
            elapsed_s,
            pid or "?",
            detail or "无",
            event_count,
        )
    if exit_code not in (0, None):
        return "CLI 已失败 %ss exit=%s：%s · 共 %s 条事件" % (
            elapsed_s,
            exit_code,
            detail or "无说明",
            event_count,
        )
    return "CLI 已结束 %ss exit=%s：%s · 共 %s 条事件" % (
        elapsed_s,
        0 if exit_code is None else exit_code,
        detail or "正常退出",
        event_count,
    )


def _ingest_cli_line(line: str) -> str:
    raw = (line or "").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:160]
        if isinstance(obj, dict):
            return summarize_cli_event(obj)
    return raw[:160]


def summarize_cli_event(obj: Dict[str, Any]) -> str:
    typ = str(obj.get("type") or obj.get("event") or obj.get("subtype") or "")
    if typ in ("thread.started", "turn.started"):
        return "Codex 已启动" if typ == "thread.started" else "Codex 开始生成"
    if typ == "turn.completed":
        return "Codex 正常完成"
    if typ == "turn.failed":
        return "Codex 报错：" + _event_text(obj, limit=512)
    if typ.startswith("item.") and isinstance(obj.get("item"), dict):
        item = obj["item"]
        kind = item.get("type")
        if kind == "error":
            return "Codex 报错：" + _event_text(item, limit=512)
        if kind == "agent_message":
            return "模型输出：" + _event_text(item)
        if kind == "reasoning":
            return "模型思考中"
        if kind in ("command_execution", "file_change", "mcp_tool_call"):
            return "工具：%s · %s" % (kind, item.get("status") or typ)
    if typ in ("system", "init") or obj.get("subtype") == "init":
        model = obj.get("model") or ""
        return "已启动" + ((" 模型=" + str(model)) if model else "")
    if typ in ("thinking", "reasoning"):
        return "模型思考中"
    if typ in ("assistant", "text", "delta", "partial"):
        text = _event_text(obj)
        return ("模型输出：" + text) if text else "模型输出中"
    if typ in ("tool_call", "tool_use", "tool"):
        name = (
            obj.get("name")
            or obj.get("tool")
            or obj.get("toolName")
            or (obj.get("tool_call") or {}).get("name")
            or ""
        )
        return ("调用工具 " + str(name)) if name else "调用工具"
    if typ in ("tool_result", "tool_response"):
        return "工具已返回"
    if typ == "result":
        if obj.get("is_error") or obj.get("subtype") in ("error", "failure"):
            return "CLI 报错：" + (_event_text(obj, limit=512) or str(obj.get("subtype") or "error"))
        return "CLI 正常返回结果"
    if typ == "error":
        return "CLI 错误：" + (_event_text(obj, limit=512) or str(obj.get("message") or "error"))
    if typ:
        extra = _event_text(obj)
        return (typ + " " + extra).strip()[:160]
    return _event_text(obj)[:160]


def _event_text(obj: Dict[str, Any], limit: int = 120) -> str:
    for key in ("text", "message", "result", "error", "content"):
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            # Codex may embed the API error as JSON inside error.message.
            # Decode it before truncation so the actionable message survives.
            try:
                decoded = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                decoded = None
            if isinstance(decoded, dict):
                inner = _event_text(decoded, limit=limit)
                if inner:
                    return inner
            return value.replace("\n", " ").strip()[:limit]
        if isinstance(value, dict):
            inner = _event_text(value, limit=limit)
            if inner:
                return inner
        if isinstance(value, list):
            parts = []
            for item in value:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(_event_text(item, limit=limit))
            joined = " ".join(x for x in parts if x).replace("\n", " ").strip()
            if joined:
                return joined[:limit]
    return ""


def _load_java(dest: Path, output: str, extract_java) -> str:
    if dest.is_file():
        text = dest.read_text(encoding="utf-8")
        if "class PluginMain" in text:
            return text
    streamed = _java_from_stream(output or "")
    if streamed:
        dest.write_text(streamed, encoding="utf-8")
        return streamed
    if extract_java:
        found = extract_java(output or "")
        if found:
            dest.write_text(found, encoding="utf-8")
            return found
    return ""


def _java_from_stream(output: str) -> str:
    for line in (output or "").splitlines():
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        for key in ("item", "result", "text", "content", "message"):
            value = obj.get(key)
            if isinstance(value, str) and "class PluginMain" in value:
                return value
            if isinstance(value, dict):
                inner = value.get("text") or value.get("result") or ""
                if isinstance(inner, str) and "class PluginMain" in inner:
                    return inner
    return ""


def _append_progress(path: Path, line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8") as fh:
        fh.write("[%s] %s\n" % (stamp, line))
