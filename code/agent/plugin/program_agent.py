"""Compile Codex's untrusted declarative-program candidate into an .apkg.

An .apkg is not an APK.  The phone verifies this JSON-only package and its
fixed Runtime interprets it; no generated DEX, source, shell, or native code
is ever loaded.
"""
from __future__ import annotations

from codex_cli import exec_args, requires_cli_upgrade

import copy
import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Tuple


RUNTIME_ID = "declarative-v1"
FORMAT_VERSION = 1
UI_REGISTRY_VERSION = "1"
ALLOWED_COMPONENTS = {"Text", "Image", "Button", "TextInput", "Checkbox", "Row", "Column", "List", "Spacer", "Dialog"}
ALLOWED_ACTIONS = {"setState", "showMessage", "finish", "navigate"}
CONTAINERS = {"Row", "Column", "List", "Dialog"}
MAX_NODES = 200
MAX_DEPTH = 16
MAX_TEXT = 500
MAX_CODEX_BYTES = 512 * 1024
MAX_CODEX_ATTEMPTS = 3
CLI_PROGRESS_INTERVAL_SECONDS = 5


class CompileError(ValueError):
    def __init__(self, code: str, message: str, pointer: str = "") -> None:
        self.code = code
        self.pointer = pointer
        super().__init__("%s%s: %s" % (code, " at " + pointer if pointer else "", message))


class ProgramAgent:
    """Codex candidate builder + deterministic compiler + development mirror."""

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        *,
        codex_command: Optional[str] = None,
        codex_timeout_seconds: int = 120,
    ) -> None:
        # The source-tree mirror is convenient for the ADB/TCP development
        # bridge. Android never directly executes this directory.
        self.output_dir = Path(output_dir or (Path(__file__).resolve().parents[2] / "phone_agent" / "data" / "plugins"))
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.codex_command = codex_command
        self.codex_timeout_seconds = codex_timeout_seconds
        self.last_development_report: Dict[str, Any] = {}

    def build(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(intent, dict):
            raise CompileError("SCHEMA_INVALID", "intent must be an object")
        if intent.get("capabilities"):
            raise CompileError("UNSUPPORTED_CAPABILITY", "Runtime v1 permits no external capabilities")
        candidate, provider, warning = self._codex_candidate(intent)
        program, smoke = self._compile(intent, candidate)
        self._run_smoke(program, smoke)
        package, manifest = self._package(program, smoke)
        target = self.output_dir / program["programId"] / (manifest["versionId"] + ".apkg")
        target.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_write(target, package)
        return {
            "path": target,
            "programId": program["programId"],
            "title": program["title"],
            "provider": provider,
            "warning": warning,
            "bytes": package,
            "program": program,
            "smoke": smoke,
            "manifest": manifest,
            "artifactId": manifest["artifactId"],
            "developmentReport": self.last_development_report,
        }

    def _codex_candidate(self, intent: Mapping[str, Any]) -> Tuple[Dict[str, Any], str, Optional[str]]:
        monitor = _CliProgressMonitor(_progress_path(intent))
        command = self.codex_command or os.environ.get("CODEX_CLI", "codex")
        try:
            parts = shlex.split(command) if self.codex_command else [command]
        except ValueError:
            self.last_development_report = monitor.finish("blocked", "cli_command_invalid", "需要修正 Codex CLI 命令，不重试。")
            return _fallback_candidate(intent), "deterministic-fallback", "Codex 命令格式无效，已使用本地模板。"
        if not parts or shutil.which(parts[0]) is None:
            self.last_development_report = monitor.finish("blocked", "cli_unavailable", "未找到 Codex CLI；需恢复 CLI 后再发起开发。")
            return _fallback_candidate(intent), "deterministic-fallback", "未找到 Codex CLI，已使用本地模板。"

        # Candidate JSON is stdout-only; generated actions are validated below.
        workspace = self.output_dir / ".codex-candidate-workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        args = exec_args(parts, _codex_prompt(intent), workspace)
        for attempt in range(1, MAX_CODEX_ATTEMPTS + 1):
            monitor.event("started", attempt=attempt, message="已向 Codex CLI 发起候选生成请求")
            try:
                result = self._run_codex_with_progress(args, workspace, monitor, attempt)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
                if len(result.stdout.encode("utf-8")) > MAX_CODEX_BYTES:
                    raise ValueError("candidate is too large")
                candidate = _parse_json(result.stdout)
                self.last_development_report = monitor.finish("succeeded", "ready", "CLI 候选已通过格式检查。", attempt)
                return candidate, "codex-cli", None
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                decision, reason = _classify_cli_failure(exc)
                monitor.event("failed", attempt=attempt, decision=decision, reason=reason)
                if decision == "retry" and attempt < MAX_CODEX_ATTEMPTS:
                    monitor.event("retrying", attempt=attempt, message="暂态或格式错误，使用同一批准设计重试。")
                    continue
                if decision == "retry":
                    decision = "design_review"
                    reason = "retry_exhausted_requires_design_review"
                final = "需要补全产品设计后再请求 CLI。" if decision == "design_review" else "需要修复 CLI 配置或由人工检查后再继续。"
                self.last_development_report = monitor.finish("blocked", decision, final, attempt)
                # Do not expose stderr to the phone. The deterministic package
                # remains available only as the safe fallback for the v1 lane.
                return _fallback_candidate(intent), "deterministic-fallback", "Codex 开发未完成（%s）；已停止重试并使用本地模板。" % decision
        raise AssertionError("unreachable")

    def _run_codex_with_progress(self, args: list[str], workspace: Path, monitor: "_CliProgressMonitor", attempt: int) -> subprocess.CompletedProcess:
        process = subprocess.Popen(args, cwd=workspace, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        started = time.monotonic()
        while True:
            try:
                stdout, stderr = process.communicate(timeout=CLI_PROGRESS_INTERVAL_SECONDS)
                return subprocess.CompletedProcess(args, process.returncode, stdout or "", stderr or "")
            except subprocess.TimeoutExpired:
                elapsed = int(time.monotonic() - started)
                monitor.event("heartbeat", attempt=attempt, elapsedSeconds=elapsed, message="Codex CLI 仍在执行；继续收集进展。")
                if elapsed >= self.codex_timeout_seconds:
                    process.kill()
                    process.communicate()
                    raise subprocess.TimeoutExpired(args, self.codex_timeout_seconds)

    def _compile(self, intent: Mapping[str, Any], candidate: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        if not isinstance(candidate, Mapping):
            raise CompileError("SCHEMA_INVALID", "candidate must be an object")
        raw = candidate.get("program", candidate)
        if not isinstance(raw, Mapping):
            raise CompileError("SCHEMA_INVALID", "program must be an object", "/program")
        if raw.get("capabilities") not in (None, []):
            raise CompileError("UNSUPPORTED_CAPABILITY", "program capabilities must be empty", "/program/capabilities")
        title = _text(raw.get("title") or intent.get("title") or "未命名功能", "/title", required=True, limit=80)
        state = self._state(raw.get("state", {}))
        screens_raw = raw.get("screens")
        if not isinstance(screens_raw, list) or not screens_raw or len(screens_raw) > 8:
            raise CompileError("SCHEMA_INVALID", "screens must contain 1..8 entries", "/screens")
        node_ids: set[str] = set()
        screens: list[Dict[str, Any]] = []
        screen_ids: set[str] = set()
        count = [0]
        for i, source in enumerate(screens_raw):
            pointer = "/screens/%d" % i
            if not isinstance(source, Mapping) or set(source) - {"id", "children", "onLoad"}:
                raise CompileError("SCHEMA_INVALID", "invalid screen fields", pointer)
            screen_id = _identifier(source.get("id") or ("home" if not i else "screen%d" % i), pointer + "/id")
            if screen_id in screen_ids:
                raise CompileError("SEMANTIC_INVALID", "duplicate screen id", pointer + "/id")
            screen_ids.add(screen_id)
            children = source.get("children")
            if not isinstance(children, list):
                raise CompileError("SCHEMA_INVALID", "children must be an array", pointer + "/children")
            screen = {"id": screen_id, "children": [self._node(node, "%s/children/%d" % (pointer, j), 1, count, node_ids, state) for j, node in enumerate(children)]}
            if "onLoad" in source:
                screen["onLoad"] = self._action(source["onLoad"], pointer + "/onLoad", state)
            screens.append(screen)
        for action in _actions(screens):
            if action["type"] == "navigate" and action["screenId"] not in screen_ids:
                raise CompileError("SEMANTIC_INVALID", "navigation target does not exist", "/screens")
        program = {
            "schemaVersion": 1,
            "programId": _program_id(raw.get("programId"), title),
            "title": title,
            "runtime": RUNTIME_ID,
            "approvalDigest": _approval_digest(intent),
            "capabilities": [],
            "state": state,
            "screens": screens,
        }
        smoke = self._smoke(candidate.get("smoke", candidate.get("tests")), program)
        return program, smoke

    def _state(self, raw: Any) -> Dict[str, Dict[str, Any]]:
        if raw is None:
            raw = {}
        if not isinstance(raw, Mapping) or len(raw) > 32:
            raise CompileError("SCHEMA_INVALID", "state must be an object with at most 32 entries", "/state")
        result: Dict[str, Dict[str, Any]] = {}
        for key, value in raw.items():
            name = _identifier(key, "/state")
            descriptor = value if isinstance(value, Mapping) else {"initial": value}
            if set(descriptor) - {"type", "initial", "persistence"} or "initial" not in descriptor:
                raise CompileError("SCHEMA_INVALID", "invalid state descriptor", "/state/" + name)
            initial = descriptor["initial"]
            value_type = str(descriptor.get("type") or _value_type(initial))
            if value_type not in {"bool", "int64", "string", "list"} or not _matches(initial, value_type):
                raise CompileError("SCHEMA_INVALID", "invalid state initial value", "/state/" + name)
            persistence = str(descriptor.get("persistence") or "session")
            if persistence not in {"session", "local"}:
                raise CompileError("SCHEMA_INVALID", "invalid persistence", "/state/" + name)
            result[name] = {"type": value_type, "initial": initial, "persistence": persistence}
        return result

    def _node(self, raw: Any, pointer: str, depth: int, count: list[int], node_ids: set[str], state: MutableMapping[str, Dict[str, Any]]) -> Dict[str, Any]:
        allowed = {"id", "type", "text", "label", "children", "onClick", "onChange", "action", "stateKey", "hint", "src"}
        if not isinstance(raw, Mapping) or set(raw) - allowed:
            raise CompileError("SCHEMA_INVALID", "invalid component fields", pointer)
        if depth > MAX_DEPTH:
            raise CompileError("BUDGET_EXCEEDED", "component nesting is too deep", pointer)
        kind = raw.get("type")
        if kind not in ALLOWED_COMPONENTS:
            raise CompileError("SCHEMA_INVALID", "unknown component", pointer + "/type")
        count[0] += 1
        if count[0] > MAX_NODES:
            raise CompileError("BUDGET_EXCEEDED", "too many components", pointer)
        node_id = _identifier(raw.get("id") or "node%03d" % count[0], pointer + "/id")
        if node_id in node_ids:
            raise CompileError("SEMANTIC_INVALID", "duplicate component id", pointer + "/id")
        node_ids.add(node_id)
        node: Dict[str, Any] = {"id": node_id, "type": kind}
        if "text" in raw or "label" in raw:
            node["text"] = _text(raw.get("text", raw.get("label")), pointer + "/text")
        if kind in {"Text", "Button", "Checkbox"} and not node.get("text"):
            raise CompileError("SCHEMA_INVALID", "component requires text", pointer + "/text")
        if kind == "Image":
            source = _text(raw.get("src"), pointer + "/src", required=True)
            if not source.startswith("apkg:///resources/") or ".." in source:
                raise CompileError("SEMANTIC_INVALID", "image source must be an apkg resource", pointer + "/src")
            # No resource binary is accepted by this v1 bridge yet, so do not
            # emit an installable-looking package with a dangling image.
            raise CompileError("INCOMPATIBLE", "image resources are not supported by the current builder", pointer + "/src")
        if kind in {"TextInput", "Checkbox"}:
            state_key = _identifier(raw.get("stateKey") or (node_id + ("Value" if kind == "TextInput" else "Checked")), pointer + "/stateKey")
            expected, initial = ("string", "") if kind == "TextInput" else ("bool", False)
            existing = state.get(state_key)
            if existing is None:
                state[state_key] = {"type": expected, "initial": initial, "persistence": "session"}
            elif existing["type"] != expected:
                raise CompileError("SEMANTIC_INVALID", "input state type mismatch", pointer + "/stateKey")
            node["stateKey"] = state_key
            if kind == "TextInput" and "hint" in raw:
                node["hint"] = _text(raw["hint"], pointer + "/hint")
        children = raw.get("children", [])
        if not isinstance(children, list) or (children and kind not in CONTAINERS):
            raise CompileError("SEMANTIC_INVALID", "children are only valid in layout components", pointer + "/children")
        if kind in CONTAINERS or children:
            node["children"] = [self._node(child, "%s/children/%d" % (pointer, i), depth + 1, count, node_ids, state) for i, child in enumerate(children)]
        action = raw.get("onClick", raw.get("action"))
        if action is not None:
            if kind not in {"Button", "Checkbox", "Text"}:
                raise CompileError("SEMANTIC_INVALID", "component cannot handle clicks", pointer + "/onClick")
            node["onClick"] = self._action(action, pointer + "/onClick", state)
        if "onChange" in raw:
            if kind not in {"Checkbox", "TextInput"}:
                raise CompileError("SEMANTIC_INVALID", "component cannot handle changes", pointer + "/onChange")
            node["onChange"] = self._action(raw["onChange"], pointer + "/onChange", state)
        return node

    def _action(self, raw: Any, pointer: str, state: Mapping[str, Dict[str, Any]]) -> Dict[str, Any]:
        if isinstance(raw, str):
            raw = {"type": raw, **({"message": "已完成"} if raw == "showMessage" else {})}
        if not isinstance(raw, Mapping) or raw.get("type") not in ALLOWED_ACTIONS:
            raise CompileError("SEMANTIC_INVALID", "unknown action", pointer)
        kind = raw["type"]
        allowed = {"setState": {"type", "key", "value"}, "showMessage": {"type", "message"}, "finish": {"type"}, "navigate": {"type", "screenId"}}[kind]
        if set(raw) - allowed:
            raise CompileError("SCHEMA_INVALID", "unknown action fields", pointer)
        if kind == "setState":
            key = _identifier(raw.get("key"), pointer + "/key")
            if key not in state or "value" not in raw or not _matches(raw["value"], state[key]["type"]):
                raise CompileError("SEMANTIC_INVALID", "invalid state assignment", pointer)
            return {"type": kind, "key": key, "value": raw["value"]}
        if kind == "showMessage":
            return {"type": kind, "message": _text(raw.get("message"), pointer + "/message", required=True)}
        if kind == "navigate":
            return {"type": kind, "screenId": _identifier(raw.get("screenId"), pointer + "/screenId")}
        return {"type": "finish"}

    def _smoke(self, raw: Any, program: Mapping[str, Any]) -> Dict[str, Any]:
        if raw is None:
            raw = _derived_smoke(program)
        if not isinstance(raw, Mapping) or set(raw) - {"schemaVersion", "events", "assertions"}:
            raise CompileError("SCHEMA_INVALID", "invalid smoke test", "/tests/smoke")
        events, assertions = raw.get("events"), raw.get("assertions")
        if not isinstance(events, list) or not events or len(events) > 64 or not isinstance(assertions, list) or not assertions or len(assertions) > 64:
            raise CompileError("SCHEMA_INVALID", "smoke test needs bounded events and assertions", "/tests/smoke")
        nodes = {node["id"]: node for node in _nodes(program["screens"])}
        checked_events = []
        for i, event in enumerate(events):
            pointer = "/tests/smoke/events/%d" % i
            if not isinstance(event, Mapping):
                raise CompileError("SCHEMA_INVALID", "event must be an object", pointer)
            if event.get("type") == "open" and set(event) == {"type"}:
                checked_events.append({"type": "open"})
            elif event.get("type") == "click" and set(event) == {"type", "nodeId"} and event.get("nodeId") in nodes and "onClick" in nodes[event["nodeId"]]:
                checked_events.append({"type": "click", "nodeId": event["nodeId"]})
            elif event.get("type") == "change" and set(event) == {"type", "nodeId", "value"} and event.get("nodeId") in nodes and nodes[event["nodeId"]].get("stateKey"):
                key = nodes[event["nodeId"]]["stateKey"]
                if not _matches(event["value"], program["state"][key]["type"]):
                    raise CompileError("SEMANTIC_INVALID", "change value type mismatch", pointer)
                checked_events.append(dict(event))
            else:
                raise CompileError("SCHEMA_INVALID", "invalid smoke event", pointer)
        checked_assertions = []
        for i, assertion in enumerate(assertions):
            pointer = "/tests/smoke/assertions/%d" % i
            if not isinstance(assertion, Mapping) or set(assertion) != {"path", "equals"} or not isinstance(assertion["path"], str):
                raise CompileError("SCHEMA_INVALID", "invalid smoke assertion", pointer)
            if assertion["path"] not in {"/programId", "/title"} and not assertion["path"].startswith("/state/"):
                raise CompileError("SEMANTIC_INVALID", "assertion observes unsupported path", pointer)
            checked_assertions.append(dict(assertion))
        return {"schemaVersion": 1, "events": checked_events, "assertions": checked_assertions}

    def _run_smoke(self, program: Mapping[str, Any], smoke: Mapping[str, Any]) -> None:
        state = {key: copy.deepcopy(value["initial"]) for key, value in program["state"].items()}
        nodes = {node["id"]: node for node in _nodes(program["screens"])}
        first_screen = program["screens"][0]
        for event in smoke["events"]:
            if event["type"] == "open":
                if "onLoad" in first_screen:
                    _apply(first_screen["onLoad"], state)
            elif event["type"] == "click":
                _apply(nodes[event["nodeId"]]["onClick"], state)
            elif event["type"] == "change":
                node = nodes[event["nodeId"]]
                state[node["stateKey"]] = copy.deepcopy(event["value"])
                if "onChange" in node:
                    _apply(node["onChange"], state)
        observed = {"programId": program["programId"], "title": program["title"], "state": state}
        for assertion in smoke["assertions"]:
            if _at(observed, assertion["path"]) != assertion["equals"]:
                raise CompileError("TEST_FAILED", "smoke assertion failed", "/tests/smoke")

    def _package(self, program: Mapping[str, Any], smoke: Mapping[str, Any]) -> Tuple[bytes, Dict[str, Any]]:
        files = {"program/main.json": _canonical(program), "tests/smoke.json": _canonical(smoke)}
        digest = _sha256(_canonical({"program": program, "smoke": smoke}))
        manifest = {
            "formatVersion": FORMAT_VERSION,
            "artifactId": "artifact-" + digest[:24],
            "taskId": "task-" + _sha256(program["approvalDigest"].encode())[:16],
            "programId": program["programId"],
            "versionId": digest[:16],
            "revision": digest[:16],
            "securityEpoch": 1,
            "approvedProposalDigest": program["approvalDigest"],
            "runtime": RUNTIME_ID,
            "uiRegistryVersion": UI_REGISTRY_VERSION,
            # Development only. A release phone must require a COSE ES256
            # signature at signatures/manifest.cose.
            "keyId": "debug-unsigned",
            "algorithm": "none",
            "debugUnsigned": True,
            "files": [{"path": path, "mime": "application/json", "size": len(data), "sha256": _sha256(data)} for path, data in sorted(files.items())],
        }
        files["manifest.json"] = _canonical(manifest)
        return _zip(files), manifest

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        if path.exists() and path.read_bytes() == data:
            return
        with tempfile.NamedTemporaryFile(prefix="." + path.name + ".", suffix=".part", dir=path.parent, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        try:
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _codex_prompt(intent: Mapping[str, Any]) -> str:
    return (
        "Return exactly one JSON object {program:{...},smoke:{...}} for a fixed Android declarative Runtime. "
        "This is not an APK generator. Do not output source code, Markdown, files, URLs, permissions, scripts, network calls, or Android APIs. "
        "Allowed components=" + json.dumps(sorted(ALLOWED_COMPONENTS)) + "; allowed actions=" + json.dumps(sorted(ALLOWED_ACTIONS)) + ". "
        "Use state descriptors {type,initial,persistence}; nodes have id,type,text,children,onClick/onChange; "
        "actions are {type:'setState',key,value}, {type:'showMessage',message}, {type:'finish'}, or {type:'navigate',screenId}. "
        "smoke contains events (open/click/change) and assertions ({path:'/programId' or '/state/key',equals:value}). Confirmed intent: "
        + json.dumps(intent, ensure_ascii=False, separators=(",", ":"))
    )


def _fallback_candidate(intent: Mapping[str, Any]) -> Dict[str, Any]:
    title = str(intent.get("title") or "我的功能")[:80]
    summary = str(intent.get("summary") or "")[:200]
    children = [{"id": "title", "type": "Text", "text": title}]
    if summary:
        children.append({"id": "summary", "type": "Text", "text": summary})
    children.extend([
        {"id": "done", "type": "Button", "text": "完成", "onClick": {"type": "setState", "key": "completed", "value": True}},
        {"id": "hint", "type": "Text", "text": "此功能仅在本地运行。"},
    ])
    return {
        "program": {"title": title, "state": {"completed": {"type": "bool", "initial": False, "persistence": "local"}}, "screens": [{"id": "home", "children": [{"id": "layout", "type": "Column", "children": children}]}]},
        "smoke": {"events": [{"type": "open"}, {"type": "click", "nodeId": "done"}], "assertions": [{"path": "/state/completed", "equals": True}]},
    }


def _derived_smoke(program: Mapping[str, Any]) -> Dict[str, Any]:
    for node in _nodes(program["screens"]):
        action = node.get("onClick")
        if action and action.get("type") == "setState":
            return {"events": [{"type": "open"}, {"type": "click", "nodeId": node["id"]}], "assertions": [{"path": "/state/" + action["key"], "equals": action["value"]}]}
    return {"events": [{"type": "open"}], "assertions": [{"path": "/programId", "equals": program["programId"]}]}


def _parse_json(text: str) -> Dict[str, Any]:
    clean = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE).strip()
    if clean.startswith("```"):
        lines = clean.splitlines()
        clean = "\n".join(lines[1:-1] if lines and lines[-1].strip().startswith("```") else lines[1:]).strip()
    value = json.loads(clean)
    if not isinstance(value, dict):
        raise ValueError("Codex CLI did not return an object")
    return value


def _approval_digest(intent: Mapping[str, Any]) -> str:
    supplied = intent.get("approvalDigest")
    if isinstance(supplied, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", supplied):
        return supplied
    return "sha256:" + _sha256(_canonical(intent))


def _program_id(raw: Any, title: str) -> str:
    if isinstance(raw, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,47}", raw):
        return raw
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    return slug[:48] if slug else "program-" + _sha256(title.encode())[:16]


def _identifier(value: Any, pointer: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise CompileError("SCHEMA_INVALID", "invalid identifier", pointer)
    return value


def _text(value: Any, pointer: str, *, required: bool = False, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str):
        raise CompileError("SCHEMA_INVALID", "must be a string", pointer)
    value = value.strip()
    if (required and not value) or len(value) > limit:
        raise CompileError("SCHEMA_INVALID" if not value else "BUDGET_EXCEEDED", "invalid text", pointer)
    return value


def _value_type(value: Any) -> str:
    if isinstance(value, bool): return "bool"
    if isinstance(value, int) and not isinstance(value, bool): return "int64"
    if isinstance(value, str): return "string"
    if isinstance(value, list): return "list"
    return "invalid"


def _matches(value: Any, value_type: str) -> bool:
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)): return False
    if value_type == "bool": return isinstance(value, bool)
    if value_type == "int64": return isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 2**63
    if value_type == "string": return isinstance(value, str) and len(value) <= MAX_TEXT
    return value_type == "list" and isinstance(value, list) and len(value) <= 100 and all(item is None or isinstance(item, (str, bool, int)) for item in value)


def _nodes(screens: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for screen in screens:
        yield from _walk(screen.get("children", []))


def _walk(nodes: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for node in nodes:
        yield node
        yield from _walk(node.get("children", []))


def _actions(screens: Iterable[Mapping[str, Any]]) -> Iterable[Mapping[str, Any]]:
    for screen in screens:
        if "onLoad" in screen: yield screen["onLoad"]
    for node in _nodes(screens):
        for event in ("onClick", "onChange"):
            if event in node: yield node[event]


def _apply(action: Mapping[str, Any], state: MutableMapping[str, Any]) -> None:
    if action["type"] == "setState": state[action["key"]] = copy.deepcopy(action["value"])


def _at(value: Any, pointer: str) -> Any:
    current = value
    for part in pointer.strip("/").split("/"):
        if not part: continue
        if not isinstance(current, Mapping) or part not in current: return object()
        current = current[part]
    return current


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _zip(files: Mapping[str, bytes]) -> bytes:
    data = BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as package:
        for path, value in sorted(files.items()):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, value, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return data.getvalue()


def _progress_path(intent: Mapping[str, Any]) -> Optional[Path]:
    archive = intent.get("productArchive")
    if isinstance(archive, str) and archive:
        return Path(archive) / "development-progress.jsonl"
    return None


class _CliProgressMonitor:
    """Append-only development audit; safe to read while the CLI is running."""

    def __init__(self, path: Optional[Path]) -> None:
        self.path = path
        self.events: list[Dict[str, Any]] = []

    def event(self, state: str, **details: Any) -> None:
        record = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "state": state, **details}
        self.events.append(record)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def finish(self, status: str, decision: str, summary: str, attempts: int = 0) -> Dict[str, Any]:
        result = {"status": status, "decision": decision, "summary": summary, "attempts": attempts, "progressPath": str(self.path) if self.path else None}
        self.event("finished", **result)
        return result


def _classify_cli_failure(error: BaseException) -> Tuple[str, str]:
    """Select the next action without blindly retrying a broken design."""
    if isinstance(error, subprocess.TimeoutExpired):
        return "retry", "timeout"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "retry", "invalid_candidate"
    if isinstance(error, subprocess.CalledProcessError):
        output = ((error.stderr or "") + "\n" + (error.stdout or "")).lower()
        if requires_cli_upgrade(output):
            return "operator_action", "cli_upgrade_required"
        if any(token in output for token in ("api key", "authentication", "unauthorized", "login", "quota")):
            return "operator_action", "cli_authentication_or_quota"
        if any(token in output for token in ("schema", "requirements", "ambiguous", "unsupported")):
            return "design_review", "design_or_contract_incomplete"
        return "retry", "cli_nonzero_exit"
    if isinstance(error, OSError):
        return "operator_action", "cli_process_error"
    return "design_review", "unknown_development_failure"
