"""Generate an independent plugin APK from the product archive and host code."""
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Any, Dict, Optional

from llm import LlmClient
from codex_cli import cli_enabled
from trace import flow, kv

from .cli_supervisor import MAX_CLI_ATTEMPTS, run_cli_with_retries, user_message_for_decision
from .codex_builder import CodexBuilder
from .history_reuse import find_plugin_main, retarget_package
from .source_checkpoints import checkpoint


class ApkBuildError(RuntimeError):
    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log or ""


class CliNeedUser(ApkBuildError):
    def __init__(self, decision: str, message: str) -> None:
        super().__init__(message)
        self.decision = decision


class ApkBuilder:
    def __init__(self, agent_root: Path, llm: Optional[LlmClient] = None) -> None:
        self.agent_root = agent_root
        self.repo_root = agent_root.parent.parent
        self.template = agent_root / "program_agent" / "plugin_template"
        self.phone_agent = agent_root.parent / "phone_agent"
        self.llm = llm
        self.codex = CodexBuilder(
            agent_root / "out" / "codex_work",
            agent_root / "program_agent" / "FRAMEWORK.md",
            llm,
        )

    def build(self, meta: Dict[str, Any], on_progress: Optional[Any] = None) -> Dict[str, Any]:
        task_dir = Path(meta["taskDir"])
        src = task_dir / "plugin_src"
        dist = task_dir / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        mode = meta.get('generationMode') or 'modify'
        # The main LLM selected this task. Do not substitute a similar title.
        similar = None
        existing = find_plugin_main(task_dir)
        if existing is not None:
            similar = {"java": existing.read_text(encoding="utf-8"), "title": meta["title"], "reason": "当前任务源码", "score": 1}
            checkpoint(task_dir, existing, 'before-build')
            if on_progress:
                on_progress('开发 Agent：复用「%s」已保存源码。' % meta['title'])
        if not src.exists():
            self._copy_template(src, meta)
        entry_class = "%s.PluginMain" % meta["packageName"]
        java_path = self._entry_path(src, meta["packageName"])
        if mode == 'continue' and similar and existing is not None and src in existing.parents and self._generation_ready(meta):
            java_path.parent.mkdir(parents=True, exist_ok=True)
            java_path.write_text(retarget_package(similar['java'], meta['packageName']), encoding='utf-8')
            if on_progress:
                on_progress('开发 Agent：继续原任务，先编译已保存源码；有错误时在原代码上修复。')
        else:
            self._write_entry(src, meta, entry_class, on_progress=on_progress, similar=similar)
        apk = self._compile_with_fixes(src, java_path, meta, on_progress=on_progress)
        dest = dist / "plugin.apk"
        shutil.copy2(apk, dest)
        kv("plugin.apk", str(dest))
        kv("entryClass", entry_class)
        flow("program agent: apk compile ok")
        return {
            "taskId": meta["taskId"],
            "title": meta["title"],
            "summary": meta["summary"],
            "icon": meta.get("icon") or "📦",
            "packageName": meta["packageName"],
            "entryClass": entry_class,
            "apkPath": str(dest),
            "state": "downloading",
        }

    def repair(
        self,
        meta: Dict[str, Any],
        error: Dict[str, Any],
        on_progress: Optional[Any] = None,
    ) -> Dict[str, Any]:
        task_dir = Path(meta["taskDir"])
        src = task_dir / "plugin_src"
        dist = task_dir / "dist"
        dist.mkdir(parents=True, exist_ok=True)
        found = find_plugin_main(task_dir)
        if found is None:
            flow("program agent: no existing PluginMain, fallback to full build")
            if on_progress:
                on_progress("开发 Agent：没有可改的历史源码，只能按产品文档重建。")
            return self.build(meta, on_progress=on_progress)
        original = retarget_package(found.read_text(encoding="utf-8"), meta["packageName"])
        checkpoint(task_dir, found, 'before-repair')
        gradle_ok = (src / "app" / "build.gradle").is_file() or (src / "settings.gradle").is_file()
        if src.is_dir() and not gradle_ok:
            flow("program agent: restore gradle template, keep PluginMain")
            # Restore missing template files without deleting existing sources.
            import tempfile
            with tempfile.TemporaryDirectory(dir=str(task_dir)) as tmp:
                restored = Path(tmp) / 'template'
                self._copy_template(restored, meta)
                shutil.copytree(restored, src, dirs_exist_ok=True)
        if not src.is_dir():
            self._copy_template(src, meta)
        java_path = self._entry_path(src, meta["packageName"])
        java_path.parent.mkdir(parents=True, exist_ok=True)
        java_path.write_text(original, encoding="utf-8")
        entry_class = "%s.PluginMain" % meta["packageName"]
        dev_dir = task_dir / "dev"
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / "runtime-error.json").write_text(
            json.dumps(error, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if on_progress:
            on_progress(
                "开发 Agent：根据手机「%s」错误修改现有 PluginMain，不从零重写。"
                % (error.get("phase") or "runtime")
            )
        fixed = self._fix_java_after_runtime(meta, java_path, error, on_progress)
        if not fixed:
            raise ApkBuildError("未能根据运行错误改出新的 PluginMain")
        java_path.write_text(fixed, encoding="utf-8")
        work = self._work_dir(meta) / "PluginMain.java"
        work.parent.mkdir(parents=True, exist_ok=True)
        work.write_text(fixed, encoding="utf-8")
        apk = self._compile_with_fixes(src, java_path, meta, on_progress=on_progress)
        dest = dist / "plugin.apk"
        shutil.copy2(apk, dest)
        kv("plugin.apk", str(dest))
        flow("program agent: runtime repair ok")
        return {
            "taskId": meta["taskId"],
            "title": meta["title"],
            "summary": meta["summary"],
            "icon": meta.get("icon") or "📦",
            "packageName": meta["packageName"],
            "entryClass": entry_class,
            "apkPath": str(dest),
            "state": "downloading",
        }

    def _copy_template(self, dest: Path, meta: Dict[str, Any]) -> None:
        if not self.template.is_dir():
            raise ApkBuildError("missing plugin_template")
        shutil.copytree(self.template, dest)
        replacements = {
            "__PACKAGE__": meta["packageName"],
            "__APP_NAME__": meta["title"][:40],
            "__TITLE__": meta["title"],
            "__SUMMARY__": meta.get("summary") or meta["title"],
        }
        for path in dest.rglob("*"):
            if not path.is_file():
                continue
            if path.suffix in (".png", ".jar", ".so"):
                continue
            text = path.read_text(encoding="utf-8")
            for key, value in replacements.items():
                text = text.replace(key, escape(value) if path.suffix == ".xml" else value)
            path.write_text(text, encoding="utf-8")
        sdk = _find_sdk(self.phone_agent)
        if not sdk:
            raise ApkBuildError("Android SDK not found; set ANDROID_HOME")
        (dest / "local.properties").write_text("sdk.dir=%s\n" % _escape_sdk(sdk), encoding="utf-8")
        kv("android.sdk", sdk)

    def _write_entry(
        self,
        src: Path,
        meta: Dict[str, Any],
        entry_class: str,
        on_progress: Optional[Any] = None,
        similar: Optional[Dict[str, Any]] = None,
    ) -> None:
        package = meta["packageName"]
        dest = self._entry_path(src, package)
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = None
        product_dir = Path(meta["productDir"])
        self._mark_generation(meta, 'writing')
        try:
            body = self._from_codex_or_llm(
                product_dir, meta, on_progress=on_progress, similar=similar
            )
        except CliNeedUser:
            raise
        except Exception as exc:
            flow("program agent: entry codegen fallback: %s" % exc)
        if not body:
            raise ApkBuildError("未生成符合本次需求的代码，请恢复开发模型后重试；不会用计数器代替小游戏。")
        dest.write_text(body, encoding="utf-8")
        self._mark_generation(meta, 'complete')
        kv("entry", str(dest))
        kv("entryClass", entry_class)

    def _from_codex_or_llm(
        self,
        product_dir: Path,
        meta: Dict[str, Any],
        on_progress: Optional[Any] = None,
        similar: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        docs = _read_product_docs(product_dir)
        skill = _read_program_skill(self.repo_root)
        host_hint = (
            "遵守程序编写 skill：宿主进程内插件，不是独立 App。\n"
            "实现 com.autoprocedure.pluginapi.PluginEntry，类名必须是 %s.PluginMain。\n"
            "在 onCreate(Activity host, ViewGroup container) 里用代码 addView。\n"
            "可以自定义 View、Canvas、Handler 游戏循环、触摸手势；禁止 R.layout、禁止支付。\n"
            "新小游戏默认有适配玩法的短音效和可见静音开关，遵守用户明确的无声选择。"
            "模板提供 com.autoprocedure.pluginsupport.GameSoundEffects，随插件打包；"
            "不用 R.raw、不要新增音频权限，暂停停音、onDestroy 释放。\n"
            "不要从零重写：有历史相似源码就改它。\n"
            % meta["packageName"]
        )
        history_block = _history_prompt_block(similar, meta["packageName"])
        history_block += ('\n本次任务身份已由主 Agent 确定：taskId=%s，generationMode=%s。'
                          '只修改本任务输出，不扫描或改写其他任务。没有本任务源码时按模板新建。\n'
                          % (meta.get('taskId', ''), meta.get('generationMode', 'modify')))
        use_cli = cli_enabled()
        if use_cli and not self.codex.cli:
            raise ApkBuildError("未找到 Codex CLI，请安装 codex 或设置 CODEX_CLI。")
        if use_cli and self.codex.cli:
            work = self._work_dir(meta)
            work.mkdir(parents=True, exist_ok=True)
            dest = work / "PluginMain.java"
            if similar and similar.get("java"):
                (work / "history_PluginMain.java").write_text(similar["java"], encoding="utf-8")
                checkpoint(Path(meta.get('taskDir') or product_dir.parent), dest, 'before-seed')
                dest.write_text(retarget_package(similar['java'], meta['packageName']), encoding='utf-8')
            prompt = (
                "根据产品归档写 PluginMain.java，实现 PluginEntry。\n"
                "包名：%s\n写到：%s\n不要写 Activity，不要写网络。\n\n"
                "%s\n\n%s\n\n%s\n\n%s\n"
                % (meta["packageName"], dest, skill, host_hint, history_block, docs)
            )
            task_dir = Path(meta.get("taskDir") or product_dir.parent)
            result = run_cli_with_retries(
                self.codex.cli,
                prompt,
                work,
                dest,
                docs,
                task_dir,
                on_progress=on_progress,
                extract_java=_extract_java,
            )
            if result.ok:
                return result.java
            raise CliNeedUser(
                result.decision,
                user_message_for_decision(result.decision, result.analysis, result.attempts),
            )
        if self.llm is None:
            return None
        self.llm.reload()
        result = self.llm.chat_json(
            [
                {
                    "role": "system",
                    "content": "只输出 JSON {\"java\":\"...\"}。java 是完整 PluginMain.java，实现 PluginEntry。",
                },
                {
                    "role": "user",
                    "content": "packageName=%s\n%s\n%s\n%s"
                    % (meta["packageName"], host_hint, history_block, docs),
                },
            ]
        )
        java = result.get("java")
        if isinstance(java, str) and "class PluginMain" in java:
            flow("program agent: LLM PluginMain")
            return java
        return None

    def _entry_path(self, src: Path, package: str) -> Path:
        return src / "app" / "src" / "main" / "java" / Path(*package.split(".")) / "PluginMain.java"

    def _work_dir(self, meta: Dict[str, Any]) -> Path:
        task = Path(meta.get('taskDir') or Path(meta['productDir']).parent)
        work = task / 'dev' / 'codex_work'
        work.mkdir(parents=True, exist_ok=True)
        return work

    def _mark_generation(self, meta: Dict[str, Any], state: str) -> None:
        task = Path(meta.get('taskDir') or Path(meta['productDir']).parent)
        path = task / 'dev/generation-state.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        docs = _read_product_docs(Path(meta['productDir']))
        path.write_text(json.dumps({'state': state, 'designSha256': hashlib.sha256(docs.encode()).hexdigest()}), encoding='utf-8')

    def _generation_ready(self, meta: Dict[str, Any]) -> bool:
        from task_history import read_object
        task = Path(meta.get('taskDir') or Path(meta['productDir']).parent)
        state = read_object(task / 'dev/generation-state.json')
        docs = _read_product_docs(Path(meta['productDir']))
        return state.get('state') == 'complete' and state.get('designSha256') == hashlib.sha256(docs.encode()).hexdigest()

    def _compile_with_fixes(
        self,
        src: Path,
        java_path: Path,
        meta: Dict[str, Any],
        on_progress: Optional[Any] = None,
    ) -> Path:
        last_error: Optional[ApkBuildError] = None
        for attempt in range(1, MAX_CLI_ATTEMPTS + 1):
            try:
                msg = "开发 Agent：Gradle 编译第 %s/%s 次…" % (attempt, MAX_CLI_ATTEMPTS)
                flow(msg)
                if on_progress:
                    on_progress(msg)
                return self._compile(src, Path(meta.get("taskDir") or src.parent), attempt)
            except ApkBuildError as exc:
                last_error = exc
                note = "Gradle 第 %s/%s 次失败：%s" % (attempt, MAX_CLI_ATTEMPTS, exc)
                flow(note)
                if on_progress:
                    on_progress("开发 Agent：" + note)
                if attempt >= MAX_CLI_ATTEMPTS:
                    break
                java = self._fix_java_after_compile(meta, java_path, exc.log or str(exc), on_progress)
                if not java:
                    flow("program agent: CLI did not rewrite PluginMain after gradle error")
                    continue
                java_path.write_text(java, encoding="utf-8")
                work = self._work_dir(meta) / "PluginMain.java"
                work.parent.mkdir(parents=True, exist_ok=True)
                work.write_text(java, encoding="utf-8")
                kv("entry.fixed", str(java_path))
        raise last_error or ApkBuildError("gradle failed")

    def _fix_java_after_compile(
        self,
        meta: Dict[str, Any],
        java_path: Path,
        gradle_log: str,
        on_progress: Optional[Any] = None,
    ) -> Optional[str]:
        use_cli = cli_enabled()
        if not (use_cli and self.codex.cli):
            return None
        current = java_path.read_text(encoding="utf-8") if java_path.is_file() else ""
        errors = _gradle_errors(gradle_log)
        dest = self._work_dir(meta) / "PluginMain.java"
        dest.write_text(current, encoding='utf-8')
        product_docs = _read_product_docs(Path(meta["productDir"])) if meta.get("productDir") else ""
        docs = (product_docs + "\n## 验收\n- Gradle 能编译通过 PluginMain.java\n" + ("." * 80))[:8000]
        prompt = (
            "Gradle 编译 PluginMain.java 失败。请按编译错误修改，覆盖写出完整可编译源码。\n"
            "包名：%s\n写到：%s\nJava 8 / minSdk 21，实现 PluginEntry，不要 R.layout、不要网络、不要新 Activity。\n\n"
            "编译错误：\n%s\n\n当前源码：\n%s\n"
            % (meta["packageName"], dest, errors, current[:12000])
        )
        result = run_cli_with_retries(
            self.codex.cli,
            prompt,
            self._work_dir(meta),
            dest,
            docs,
            Path(meta.get("taskDir") or java_path.parent),
            on_progress=on_progress,
            extract_java=_extract_java,
            max_attempts=1,
        )
        if result.ok:
            return result.java
        return None

    def _fix_java_after_runtime(
        self,
        meta: Dict[str, Any],
        java_path: Path,
        error: Dict[str, Any],
        on_progress: Optional[Any] = None,
    ) -> Optional[str]:
        current = java_path.read_text(encoding="utf-8") if java_path.is_file() else ""
        dest = self._work_dir(meta) / "PluginMain.java"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(current, encoding="utf-8")
        skill = _read_program_skill(self.repo_root)
        product_docs = _read_product_docs(Path(meta["productDir"])) if meta.get("productDir") else ""
        docs = (product_docs + "\n## 验收\n- 按堆栈修现有 PluginMain，不要重写\n" + ("." * 80))[:8000]
        prompt = _runtime_fix_prompt(meta["packageName"], dest, skill, error, current)
        use_cli = cli_enabled()
        if use_cli and not self.codex.cli:
            raise ApkBuildError("未找到 Codex CLI，请安装 codex 或设置 CODEX_CLI。")
        if use_cli and self.codex.cli:
            result = run_cli_with_retries(
                self.codex.cli,
                prompt,
                self._work_dir(meta),
                dest,
                docs,
                Path(meta.get("taskDir") or java_path.parent),
                on_progress=on_progress,
                extract_java=_extract_java,
                max_attempts=1,
            )
            if result.ok:
                return result.java
            raise CliNeedUser(result.decision, user_message_for_decision(result.decision, result.analysis, result.attempts))
        if self.llm is None:
            return None
        self.llm.reload()
        result = self.llm.chat_json(
            [
                {
                    "role": "system",
                    "content": (
                        "只输出 JSON {\"java\":\"...\"}。java 是完整 PluginMain.java。"
                        "必须在现有源码上修运行错误，禁止从零重写。"
                    ),
                },
                {"role": "user", "content": prompt[:18000]},
            ]
        )
        java = result.get("java")
        if isinstance(java, str) and "class PluginMain" in java:
            flow("program agent: LLM runtime fix PluginMain")
            return java
        return None

    def _sync_plugin_support(self, src: Path) -> None:
        """Ship the same audio helper in new and previously generated projects."""
        relative = Path('app/src/main/java/com/autoprocedure/pluginsupport')
        for source in (self.template / relative).glob('*.java'):
            dest = src / relative / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)

    def _sync_app_icon(self, src: Path) -> None:
        """Backfill the shared app icon in older generated projects as well."""
        resource_root = self.template / "app/src/main/res"
        for source in resource_root.glob("mipmap-*/ic_factory_app*.png"):
            dest = src / "app/src/main/res" / source.relative_to(resource_root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
        manifest = src / "app/src/main/AndroidManifest.xml"
        namespace = "http://schemas.android.com/apk/res/android"
        ET.register_namespace("android", namespace)
        tree = ET.parse(manifest)
        application = tree.getroot().find("application")
        if application is None:
            raise ApkBuildError("plugin manifest has no application")
        changed = False
        for key, value in (("icon", "@mipmap/ic_factory_app"), ("roundIcon", "@mipmap/ic_factory_app_round")):
            attr = "{%s}%s" % (namespace, key)
            if not application.get(attr):
                application.set(attr, value)
                changed = True
        if changed:
            tree.write(manifest, encoding="utf-8", xml_declaration=True)

    def _compile(self, src: Path, task_dir: Path, attempt: int = 1) -> Path:
        self._sync_plugin_support(src)
        self._sync_app_icon(src)
        gradlew = self.phone_agent / "gradlew"
        if not gradlew.is_file():
            raise ApkBuildError("missing %s" % gradlew)
        java_home = os.environ.get("JAVA_HOME", "")
        if not java_home:
            detected = subprocess.run(["/usr/libexec/java_home", "-v", "17"], capture_output=True, text=True) if os.sys.platform == "darwin" else None
            java_home = detected.stdout.strip() if detected and detected.returncode == 0 else ""
        env = os.environ.copy()
        if java_home:
            env["JAVA_HOME"] = java_home
        cmd = [str(gradlew), "-p", str(src), "assembleDebug", "--no-daemon"]
        kv("gradle", " ".join(cmd))
        flow("program agent: gradle assembleDebug")
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(src),
                capture_output=True,
                text=True,
                timeout=420,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ApkBuildError("gradle timeout 420s") from exc
        log = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        if log:
            kv("gradle_tail", log[-1200:])
        dev_dir = Path(task_dir) / "dev"
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / ("gradle-%s.log" % attempt)).write_text(log + "\n", encoding="utf-8")
        if proc.returncode != 0:
            raise ApkBuildError("gradle exit %s" % proc.returncode, log=log)
        apk = src / "app" / "build" / "outputs" / "apk" / "debug" / "app-debug.apk"
        if not apk.is_file():
            raise ApkBuildError("apk missing: %s" % apk, log=log)
        return apk


def _read_product_docs(product_dir: Path) -> str:
    parts = []
    for name in ("design.md", "implementation.md", "runtime.md", "code-locations.md", "intent.json"):
        path = product_dir / name
        if path.is_file():
            parts.append("## %s\n%s" % (name, path.read_text(encoding="utf-8")))
    return "\n\n".join(parts)


def _read_program_skill(repo_root: Path) -> str:
    path = repo_root / "code" / "agent" / "skills" / "write-plugin-program" / "SKILL.md"
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            text = parts[2].strip()
    return text[:6000]


def _history_prompt_block(similar: Optional[Dict[str, Any]], package: str) -> str:
    if not similar:
        return "本任务没有可复用源码，按已确认需求与模板新建；不要擅自选择其他历史任务。"
    java = str(similar.get("java") or "")
    return (
        "不要从零重写。最相似历史程序：「%s」（%s，score=%s）。\n"
        "已保存为 history_PluginMain.java。请改包名为 %s，按产品文档改差异功能，"
        "保留已能跑的游戏循环、绘制和触控。\n\n历史源码：\n%s\n"
        % (
            similar.get("title") or "",
            similar.get("reason") or "相似",
            similar.get("score") or 0,
            package,
            java[:18000],
        )
    )


def _gradle_errors(log: str) -> str:
    lines = []
    for line in (log or "").splitlines():
        low = line.lower()
        if any(
            token in low
            for token in (
                "error:",
                "错误:",
                "cannot find symbol",
                "incompatible types",
                "pluginmain.java",
                "what went wrong",
                "compilation failed",
            )
        ):
            lines.append(line.strip())
    picked = "\n".join(lines[-80:]).strip()
    return picked or (log or "")[-2500:]


def _runtime_fix_prompt(
    package: str,
    dest: Path,
    skill: str,
    error: Dict[str, Any],
    current: str,
) -> str:
    return (
        "插件在手机上加载或运行失败（不是 Gradle 编译错误）。\n"
        "必须修改现有 PluginMain.java，不要从零重写，不要换游戏循环实现。\n"
        "包名：%s\n写到：%s\nJava 8 / minSdk 21，实现 PluginEntry，不要 R.layout、不要网络、不要新 Activity。\n\n"
        "%s\n\n"
        "失败阶段：%s\n异常：%s\n信息：%s\n堆栈：\n%s\n\n当前源码：\n%s\n"
        % (
            package,
            dest,
            skill,
            error.get("phase") or "runtime",
            error.get("exceptionClass") or "",
            error.get("message") or "",
            str(error.get("stackTrace") or "")[:8000],
            current[:14000],
        )
    )


def _find_sdk(phone_agent: Path) -> Optional[str]:
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        value = os.environ.get(key, "").strip()
        if value and Path(value).is_dir():
            return value
    home = Path.home()
    for candidate in (home / "Library/Android/sdk", home / "Android/Sdk"):
        if candidate.is_dir():
            return str(candidate)
    local = phone_agent / "local.properties"
    if local.is_file():
        for line in local.read_text(encoding="utf-8").splitlines():
            if line.startswith("sdk.dir="):
                raw = line.split("=", 1)[1].strip().replace("\\:", ":")
                if Path(raw).is_dir():
                    return raw
    return None


def _escape_sdk(sdk: str) -> str:
    return sdk.replace("\\", "\\\\").replace(":", "\\:")


def _extract_java(text: str) -> Optional[str]:
    start = text.find("package ")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    body = text[start : end + 1]
    return body if "class PluginMain" in body else None


def _heuristic_entry(package: str, title: str, summary: str) -> str:
    return """package %s;

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import com.autoprocedure.pluginapi.PluginEntry;

public class PluginMain implements PluginEntry {
    @Override
    public void onCreate(final Activity host, ViewGroup container) {
        final SharedPreferences prefs = host.getSharedPreferences("plugin", Context.MODE_PRIVATE);
        LinearLayout root = new LinearLayout(host);
        root.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * host.getResources().getDisplayMetrics().density);
        root.setPadding(pad, pad, pad, pad);

        TextView titleView = new TextView(host);
        titleView.setText(%s);
        titleView.setTextSize(22);
        root.addView(titleView);

        TextView summaryView = new TextView(host);
        summaryView.setText(%s);
        summaryView.setTextSize(16);
        summaryView.setPadding(0, pad, 0, pad);
        root.addView(summaryView);

        final TextView countView = new TextView(host);
        countView.setTextSize(18);
        root.addView(countView);

        Button button = new Button(host);
        button.setText("完成一次");
        root.addView(button);

        final Runnable refresh = new Runnable() {
            @Override
            public void run() {
                countView.setText("已完成 " + prefs.getInt("count", 0) + " 次");
            }
        };
        refresh.run();
        button.setOnClickListener(new android.view.View.OnClickListener() {
            @Override
            public void onClick(android.view.View v) {
                prefs.edit().putInt("count", prefs.getInt("count", 0) + 1).apply();
                refresh.run();
            }
        });
        container.addView(root);
    }

    @Override
    public void onDestroy() {
    }
}
""" % (package, _java_string(title), _java_string(summary or title))


def _java_string(value: str) -> str:
    escaped = json.dumps(value, ensure_ascii=False)
    return escaped
