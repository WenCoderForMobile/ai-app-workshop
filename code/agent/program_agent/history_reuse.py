"""Find a similar historical PluginMain so new programs are revised, not rewritten."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional


def find_plugin_main(task_dir: Path) -> Optional[Path]:
    found = _plugin_in_task(task_dir)
    if not found:
        return None
    path = Path(str(found.get("path") or ""))
    return path if path.is_file() else None


def find_similar_plugin(
    tasks_root: Path,
    intent: Dict[str, Any],
    current_task_id: str = "",
    min_score: int = 2,
) -> Optional[Dict[str, Any]]:
    """Prefer the current task's existing PluginMain, else the closest historical one."""
    current = _plugin_in_task(tasks_root / current_task_id) if current_task_id else None
    if current:
        current["score"] = 100
        current["reason"] = "本任务已有源码，在此基础上改"
        return current

    query = _query_text(intent)
    q_tokens = _tokens(query)
    if not q_tokens:
        return None
    best: Optional[Dict[str, Any]] = None
    for item in list_plugin_mains(tasks_root):
        if current_task_id and item.get("taskId") == current_task_id:
            continue
        score = _score(q_tokens, query, item)
        if score < min_score:
            continue
        if best is None or score > int(best.get("score") or 0):
            item["score"] = score
            item["reason"] = "历史相似程序"
            best = item
    return best


def list_plugin_mains(tasks_root: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    if not tasks_root.is_dir():
        return items
    for folder in tasks_root.iterdir():
        if not folder.is_dir():
            continue
        found = _plugin_in_task(folder)
        if found:
            items.append(found)
    return items


def _plugin_in_task(task_dir: Path) -> Optional[Dict[str, Any]]:
    if not task_dir.is_dir():
        return None
    import json
    try:
        meta = json.loads((task_dir / 'meta.json').read_text(encoding='utf-8'))
        meta = meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        meta = {}
    java_files = list((task_dir / 'plugin_src').glob('app/src/main/java/**/PluginMain.java'))
    java_files += [p for p in (task_dir / 'dev/codex_work/PluginMain.java',
                              task_dir / 'dev/source-draft/PluginMain.java') if p.is_file()]
    # Recover pre-upgrade shared drafts only when their package identifies this
    # task; never blindly reuse the last global PluginMain for an unrelated task.
    legacy = task_dir.parent.parent / 'codex_work/PluginMain.java'
    if not java_files and task_dir.parent.name == 'tasks' and legacy.is_file() and meta.get('packageName'):
        try:
            package = re.search(r'^package\s+([\w.]+)\s*;', legacy.read_text(encoding='utf-8'), re.M)
            if package and package.group(1) == meta['packageName']:
                java_files.append(legacy)
        except OSError:
            pass
    java_files.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    if not java_files:
        return None
    path, java = None, ''
    for candidate in java_files:
        try:
            text = candidate.read_text(encoding='utf-8')
        except OSError:
            continue
        if 'class PluginMain' in text:
            path, java = candidate, text
            break
    if path is None:
        return None
    title = str(meta.get("title") or task_dir.name)
    summary = str(meta.get("summary") or "")
    return {
        "taskId": str(meta.get("taskId") or task_dir.name),
        "title": title,
        "summary": summary,
        "path": str(path),
        "java": java,
        "text": "%s %s" % (title, summary),
    }


def _query_text(intent: Dict[str, Any]) -> str:
    features = intent.get("features") or []
    feat = " ".join(str(x) for x in features) if isinstance(features, list) else str(features)
    return " ".join(
        [
            str(intent.get("title") or ""),
            str(intent.get("summary") or ""),
            feat,
        ]
    )


_CJK = re.compile(r"[\u4e00-\u9fff]")
_WORD = re.compile(r"[a-z0-9]+", re.I)


def _tokens(text: str) -> set:
    raw = text or ""
    words = set(_WORD.findall(raw.lower()))
    chars = _CJK.findall(raw)
    grams = set(chars)
    for i in range(len(chars) - 1):
        grams.add(chars[i] + chars[i + 1])
    return words | grams


def _score(query_tokens: set, query_text: str, item: Dict[str, Any]) -> int:
    title = str(item.get("title") or "")
    blob_tokens = _tokens(str(item.get("text") or title))
    overlap = len(query_tokens & blob_tokens)
    bonus = 0
    q = (query_text or "").replace(" ", "")
    t = title.replace(" ", "")
    if t and (t in q or q in t):
        bonus += 8
    if "俄罗斯方块" in q and "俄罗斯方块" in t:
        bonus += 6
    if "消除" in q and "消除" in t:
        bonus += 3
    return overlap + bonus


def retarget_package(java: str, package: str) -> str:
    updated, n = re.subn(
        r"^package\s+[\w.]+;",
        "package %s;" % package,
        java or "",
        count=1,
        flags=re.M,
    )
    return updated if n else java
