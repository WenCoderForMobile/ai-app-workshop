"""Program agent: turn the product archive into an independently compiled APK."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from llm import LlmClient
from trace import flow, kv
from task_history import save_build_state, read_object

from .apk_builder import ApkBuildError, ApkBuilder, CliNeedUser


class ProgramAgent:
    def __init__(self, agent_root: Path, llm: Optional[LlmClient] = None) -> None:
        self.agent_root = agent_root
        self.phone_data = agent_root.parent / "phone_agent" / "data" / "plugins"
        self.builder = ApkBuilder(agent_root, llm)

    def build(
        self,
        meta: Dict[str, Any],
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str, str]:
        flow("program agent: build plugin apk")
        kv("taskId", (meta or {}).get("taskId") or "")
        kv("title", (meta or {}).get("title") or "")
        previous = read_object(Path(meta['taskDir']) / 'dev/build-state.json') if meta.get('taskDir') else {}
        if meta.get('generationMode') == 'continue' and previous.get('operation') == 'repair' and previous.get('state') in ('failed', 'making'):
            error = read_object(Path(meta['taskDir']) / 'dev/runtime-error.json')
            if error:
                return self.repair(meta, error, on_progress)
        save_build_state(meta, 'making')
        try:
            job = self.builder.build(meta or {}, on_progress=on_progress)
            save_build_state(meta, 'compiled')
            title = job.get("title") or job.get("packageName")
            return (
                job,
                "插件「%s」已编译。主 Agent 已通知手机下载到 data，点击后再动态加载。" % title,
                "ok",
            )
        except CliNeedUser as exc:
            save_build_state(meta, 'failed', error=str(exc))
            flow("program agent: need user (%s)" % exc.decision)
            return None, str(exc), exc.decision
        except ApkBuildError as exc:
            save_build_state(meta, 'failed', error=(str(exc) + '\n' + exc.log)[-4000:])
            flow("program agent: apk compile failed")
            return None, "插件编译失败：%s" % exc, "failed"
        except Exception as exc:
            save_build_state(meta, 'failed', error=str(exc))
            flow("program agent: build failed: %s" % exc)
            return None, "程序 Agent 生成失败：%s" % exc, "failed"

    def repair(
        self,
        meta: Dict[str, Any],
        error: Optional[Dict[str, Any]] = None,
        on_progress: Optional[Callable[[str], None]] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str, str]:
        flow("program agent: repair plugin from runtime error")
        kv("taskId", (meta or {}).get("taskId") or "")
        kv("phase", (error or {}).get("phase") or "")
        save_build_state(meta, 'making', operation='repair')
        try:
            job = self.builder.repair(meta or {}, error or {}, on_progress=on_progress)
            save_build_state(meta, 'compiled', operation='repair')
            title = job.get("title") or job.get("packageName")
            return (
                job,
                "已根据运行错误修复「%s」。请重新打开程序广场中的卡片。" % title,
                "ok",
            )
        except CliNeedUser as exc:
            save_build_state(meta, 'failed', error=str(exc), operation='repair')
            flow("program agent: repair need user (%s)" % exc.decision)
            return None, str(exc), exc.decision
        except ApkBuildError as exc:
            save_build_state(meta, 'failed', error=(str(exc) + '\n' + exc.log)[-4000:], operation='repair')
            flow("program agent: repair compile failed")
            return None, "根据运行错误修复失败：%s" % exc, "failed"
        except Exception as exc:
            save_build_state(meta, 'failed', error=str(exc), operation='repair')
            flow("program agent: repair failed: %s" % exc)
            return None, "程序 Agent 修复失败：%s" % exc, "failed"
