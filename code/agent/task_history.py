"""Task identity and durable build evidence used for continuation routing."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def task_catalog(tasks_root: Path, limit: int = 30) -> list:
    from program_agent.history_reuse import find_plugin_main
    if not tasks_root.is_dir():
        return []
    jobs = read_object(tasks_root.parent / 'apk_jobs.json')
    result = []
    for folder in tasks_root.iterdir():
        if not folder.is_dir() or folder.is_symlink():
            continue
        meta = read_object(folder / 'meta.json')
        if not meta or str(meta.get('taskId')) != folder.name:
            continue
        aligned = read_object(folder / 'product' / 'aligned.json')
        latest = read_object(folder / 'product' / 'intent.json')
        build = read_object(folder / 'dev' / 'build-state.json')
        job = jobs.get(folder.name) or {}
        # A persisted making state after process restart is recoverable, not success.
        state = build.get('state') or job.get('state') or ('confirmed' if aligned else 'draft')
        if job.get('state') == 'failed':
            state = 'failed'
        source = find_plugin_main(folder)
        result.append({
            'taskId': folder.name, 'title': meta.get('title') or folder.name,
            'summary': meta.get('summary') or '', 'state': state,
            'buildState': build.get('state') or 'unknown', 'deliveryState': job.get('state') or 'unknown',
            'error': str(build.get('error') or job.get('error') or '')[:1200],
            'hasSource': source is not None, 'confirmed': bool(aligned),
            'intent': latest.get('intent') or aligned.get('intent') or {},
            'confirmedIntent': aligned.get('intent') or {},
            'meta': dict(meta, taskDir=str(folder), productDir=str(folder / 'product')),
            'updatedAt': build.get('updatedAt') or aligned.get('confirmedAt') or '',
        })
    return sorted(result, key=lambda item: item['updatedAt'], reverse=True)[:limit]


def save_build_state(meta: dict, state: str, *, error: str = '', operation: str = 'build') -> None:
    if not meta.get('taskDir'):
        return
    path = Path(meta['taskDir']) / 'dev' / 'build-state.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'taskId': meta.get('taskId'), 'state': state, 'operation': operation,
               'error': error, 'updatedAt': datetime.now(timezone.utc).isoformat()}
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)
