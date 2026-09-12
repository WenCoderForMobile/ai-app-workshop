"""Keep source revisions separately from publishable, compiled APK artifacts."""
from pathlib import Path
import shutil
from uuid import uuid4


def checkpoint(task_dir: Path, source: Path, label: str) -> None:
    if not source.is_file() or not source.stat().st_size:
        return
    target = task_dir / 'dev' / 'source-checkpoints' / (label + '-' + uuid4().hex) / 'PluginMain.java'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
