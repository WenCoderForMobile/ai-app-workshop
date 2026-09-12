"""Deterministic ProgramIR compiler: validate whitelist, write plugin files. No DEX."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .schema import (
    ALLOWED_ACTIONS,
    ALLOWED_COMPONENTS,
    ALLOWED_OPS,
    ALLOWED_PERSISTENCE,
    ALLOWED_STATE_TYPES,
    MAX_NODES,
    MAX_SCREENS,
    MAX_STATE_KEYS,
)


class CompileError(ValueError):
    pass


def compile_program(program: Dict[str, Any], dest_dir: Path) -> Path:
    errors = validate(program)
    if errors:
        raise CompileError("; ".join(errors))
    dest = dest_dir / str(program["programId"])
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "program.json"
    path.write_text(
        json.dumps(program, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def validate(program: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if not isinstance(program, dict):
        return ["program must be an object"]
    for key in ("formatVersion", "programId", "title", "screens"):
        if not program.get(key):
            errors.append("missing " + key)
    if program.get("formatVersion") not in (None, "1.0"):
        errors.append("formatVersion must be 1.0")
    pid = str(program.get("programId") or "")
    if pid and not re.match(r"^[a-zA-Z0-9._-]{1,64}$", pid):
        errors.append("programId must be [a-zA-Z0-9._-]{1,64}")
    caps = program.get("capabilities")
    if caps is None:
        program["capabilities"] = []
    elif caps != []:
        errors.append("capabilities must be [] in MVP")
    state = program.get("state") or {}
    if not isinstance(state, dict):
        errors.append("state must be an object")
        state = {}
    if len(state) > MAX_STATE_KEYS:
        errors.append("too many state keys")
    for name, spec in state.items():
        if not isinstance(spec, dict):
            errors.append("state.%s must be object" % name)
            continue
        if spec.get("type") not in ALLOWED_STATE_TYPES:
            errors.append("state.%s type not allowed" % name)
        persist = spec.get("persistence") or "session"
        if persist not in ALLOWED_PERSISTENCE:
            errors.append("state.%s persistence not allowed" % name)
    screens = program.get("screens") or []
    if not isinstance(screens, list) or not screens:
        errors.append("screens must be a non-empty array")
        return errors
    if len(screens) > MAX_SCREENS:
        errors.append("too many screens")
    ids = []
    nodes = [0]
    for i, screen in enumerate(screens):
        if not isinstance(screen, dict) or not screen.get("id") or "root" not in screen:
            errors.append("/screens/%s invalid" % i)
            continue
        ids.append(screen["id"])
        errors.extend(_walk_node(screen["root"], "/screens/%s/root" % i, nodes))
    if len(ids) != len(set(ids)):
        errors.append("duplicate screen id")
    if nodes[0] > MAX_NODES:
        errors.append("too many UI nodes")
    return errors


def _walk_node(node: Any, pointer: str, nodes: List[int]) -> List[str]:
    errors: List[str] = []
    nodes[0] += 1
    if not isinstance(node, dict):
        return [pointer + " must be object"]
    ntype = node.get("type")
    if ntype not in ALLOWED_COMPONENTS:
        errors.append("%s unknown component %s" % (pointer, ntype))
        return errors
    for action in node.get("onClick") or []:
        errors.extend(_check_action(action, pointer + "/onClick"))
    for action in node.get("onChange") or []:
        errors.extend(_check_action(action, pointer + "/onChange"))
    children = node.get("children") or []
    if children and ntype not in ("Column", "Row"):
        errors.append("%s cannot have children" % pointer)
    for j, child in enumerate(children):
        errors.extend(_walk_node(child, pointer + "/children/%s" % j, nodes))
    if ntype == "List" and "item" in node:
        errors.extend(_walk_node(node["item"], pointer + "/item", nodes))
    return errors


def _check_action(action: Any, pointer: str) -> List[str]:
    if not isinstance(action, dict):
        return [pointer + " action must be object"]
    name = action.get("action")
    if name not in ALLOWED_ACTIONS:
        return ["%s unknown action %s" % (pointer, name)]
    if name == "setState" and action.get("op", "set") not in ALLOWED_OPS:
        return [pointer + " bad setState.op"]
    return []


def mirror_to_phone_data(compiled_path: Path, phone_data: Path) -> Path:
    dest = phone_data / compiled_path.parent.name
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / "program.json"
    shutil.copyfile(compiled_path, target)
    return target
