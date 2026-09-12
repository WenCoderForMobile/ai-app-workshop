"""Write the product archive after the user confirms a plan."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm import LlmClient
from trace import flow, kv


class ProductArchive:
    def __init__(self, tasks_root: Path, repo_root: Path, llm: Optional[LlmClient] = None) -> None:
        self.tasks_root = tasks_root
        self.repo_root = repo_root
        self.llm = llm
        self.current: Optional[Dict[str, Any]] = None

    def create(self, intent: Dict[str, Any], utterances: List[str]) -> Dict[str, Any]:
        task_id = _task_id(intent)
        base_id = task_id
        suffix = 2
        while (self.tasks_root / task_id).exists():
            task_id = '%s-%s' % (base_id, suffix)
            suffix += 1
        task_dir = self.tasks_root / task_id
        product_dir = task_dir / "product"
        product_dir.mkdir(parents=True, exist_ok=True)
        docs = _template_docs(intent, utterances, task_id, self.repo_root)
        (product_dir / "design.md").write_text(docs["design"], encoding="utf-8")
        (product_dir / "implementation.md").write_text(docs["implementation"], encoding="utf-8")
        (product_dir / "runtime.md").write_text(docs["runtime"], encoding="utf-8")
        (product_dir / "code-locations.md").write_text(docs["locations"], encoding="utf-8")
        intent_path = product_dir / "intent.json"
        intent_path.write_text(
            json.dumps(
                {
                    "taskId": task_id,
                    "confirmedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "utterances": utterances,
                    "intent": intent,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        meta = {
            "taskId": task_id,
            "title": str(intent.get("title") or "本地小工具"),
            "summary": str(intent.get("summary") or ""),
            "packageName": _package_name(dict(intent, title=task_id)),
            "icon": _icon(intent),
            "taskDir": str(task_dir),
            "productDir": str(product_dir),
            "generationMode": (intent.get('taskRoute') or {}).get('action', 'new'),
            "baseTaskId": (intent.get('taskRoute') or {}).get('targetTaskId', ''),
        }
        (task_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        kv("product.archive", str(product_dir))
        flow("product agent: archive written")
        self.current = meta
        return meta

    def reset(self) -> None:
        self.current = None

    def reopen(self, meta: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Resume an existing task folder so the user can rebuild or revise it."""
        if not meta:
            return None
        self.current = dict(meta)
        return self.current

    def list_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        root = self.tasks_root
        if not root.is_dir():
            return []
        dirs = [p for p in root.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        items: List[Dict[str, Any]] = []
        for folder in dirs:
            meta_path = folder / "meta.json"
            if not meta_path.is_file():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(meta, dict) and (meta.get("taskId") or meta.get("title")):
                items.append(meta)
            if len(items) >= limit:
                break
        return items

    def start(self, intent: Dict[str, Any], utterances: List[str], reason: str = "proposal") -> Dict[str, Any]:
        """Open a task folder as soon as we have a scheme to align, not only on confirm."""
        if self.current:
            self._write_docs(self.current, intent, utterances)
            self.current["title"] = str(intent.get("title") or self.current.get("title") or "")
            self.current["summary"] = str(intent.get("summary") or self.current.get("summary") or "")
            return self.current
        return self.create(intent, utterances)

    def append_alignment(
        self,
        kind: str,
        user_text: str = "",
        agent_reply: str = "",
        intent: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        meta = self.current
        if meta is None:
            return
        product_dir = Path(meta["productDir"])
        product_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "at": stamp,
            "kind": kind,
            "userText": user_text,
            "agentReply": agent_reply,
            "intent": intent or {},
            "extra": extra or {},
        }
        jsonl = product_dir / "alignments.jsonl"
        with jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        log_path = product_dir / "alignment-log.md"
        block = _alignment_markdown(record)
        if log_path.is_file():
            log_path.write_text(log_path.read_text(encoding="utf-8") + block, encoding="utf-8")
        else:
            log_path.write_text("# 与用户对齐记录\n\n用于后续跟踪方案变更。\n" + block, encoding="utf-8")
        kv("alignment.kind", kind)
        flow("product agent: alignment archived")

    def finalize(self, intent: Dict[str, Any], utterances: List[str]) -> Dict[str, Any]:
        meta = self.start(intent, utterances, reason="confirm-prepare")
        self._align_task_name(meta, intent)
        self._write_docs(meta, intent, utterances)
        aligned = {
            "confirmedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "taskId": meta["taskId"],
            "utterances": utterances,
            "intent": intent,
        }
        Path(meta["productDir"]).joinpath("aligned.json").write_text(
            json.dumps(aligned, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        self.append_alignment(
            kind="confirm",
            user_text=utterances[-1] if utterances else "确认",
            agent_reply="用户已确认，以此版方案为准。",
            intent=intent,
        )
        return meta

    def _write_docs(self, meta: Dict[str, Any], intent: Dict[str, Any], utterances: List[str]) -> None:
        product_dir = Path(meta["productDir"])
        docs = _template_docs(intent, utterances, meta["taskId"], self.repo_root)
        (product_dir / "design.md").write_text(docs["design"], encoding="utf-8")
        (product_dir / "implementation.md").write_text(docs["implementation"], encoding="utf-8")
        (product_dir / "runtime.md").write_text(docs["runtime"], encoding="utf-8")
        (product_dir / "code-locations.md").write_text(docs["locations"], encoding="utf-8")
        (product_dir / "intent.json").write_text(
            json.dumps(
                {
                    "taskId": meta["taskId"],
                    "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "utterances": utterances,
                    "intent": intent,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        meta["title"] = str(intent.get("title") or meta.get("title") or "")
        meta["summary"] = str(intent.get("summary") or meta.get("summary") or "")
        route = intent.get('taskRoute') or {}
        if route:
            meta['generationMode'] = route.get('action', 'new')
            meta['baseTaskId'] = route.get('targetTaskId', '')
        Path(meta["taskDir"]).joinpath("meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _align_task_name(self, meta: Dict[str, Any], intent: Dict[str, Any]) -> None:
        """A display-title change never changes task identity or moves source."""
        meta['title'] = str(intent.get('title') or meta.get('title') or '')


def _alignment_markdown(record: Dict[str, Any]) -> str:
    intent = record.get("intent") or {}
    title = intent.get("title") or ""
    summary = intent.get("summary") or ""
    lines = [
        "",
        "## %s · %s" % (record.get("at"), record.get("kind")),
    ]
    if record.get("userText"):
        lines.append("**用户：** %s" % record["userText"])
    if title:
        lines.append("**方案标题：** %s" % title)
    if summary:
        lines.append("**对齐摘要：** %s" % summary)
    if record.get("agentReply"):
        lines.append("**产品 Agent：** %s" % record["agentReply"])
    lines.append("")
    return "\n".join(lines)



def _task_id(intent: Dict[str, Any]) -> str:
    return _safe_title(str(intent.get("title") or ""))


_UNSAFE_TITLE = re.compile(r'[\\/:*?"<>|#%?&\n\r\t]+')


def _safe_title(title: str) -> str:
    raw = _UNSAFE_TITLE.sub("-", (title or "").strip()).strip(" .-")
    return raw[:80] or "未命名程序"


def _package_name(intent: Dict[str, Any]) -> str:
    import hashlib

    title = str(intent.get("title") or "app")
    slug = re.sub(r"[^a-z0-9]+", "", title.lower())
    if len(slug) < 2 or not slug[0].isalpha():
        slug = "p" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:10]
    return "com.autoprocedure.plugin." + slug[:28]


def _slug(title: str) -> str:
    import hashlib

    raw = re.sub(r"[^\w.-]+", "-", title, flags=re.UNICODE).strip("-").lower()
    if not raw or re.fullmatch(r"[\W_]+", raw or ""):
        raw = "p-" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:10]
    return raw[:40]


_ICON_RULES = (
    ("俄罗斯方块", "🧱"),
    ("方块", "🧱"),
    ("连连看", "🧩"),
    ("消除", "💥"),
    ("表情", "😄"),
    ("涂鸦", "🎨"),
    ("画图", "🎨"),
    ("画画", "🎨"),
    ("画板", "🎨"),
    ("音乐", "🎵"),
    ("计算器", "🔢"),
    ("打卡", "✅"),
    ("待办", "☑️"),
    ("笔记", "📝"),
    ("计时", "⏱️"),
    ("时钟", "⏱️"),
    ("日历", "📅"),
    ("天气", "🌤️"),
)

def pick_icon(intent: Dict[str, Any]) -> str:
    given = str(intent.get("icon") or "").strip()
    if given:
        ch = given[0]
        code = ord(ch)
        if code > 127 and not (0x4E00 <= code <= 0x9FFF):
            return ch
    text = "%s %s" % (intent.get("title") or "", intent.get("summary") or "")
    for ch in text:
        code = ord(ch)
        if code > 127 and not (0x4E00 <= code <= 0x9FFF) and not ch.isspace():
            return ch
    for word, icon in _ICON_RULES:
        if word in text:
            return icon
    return "📦"


def _icon(intent: Dict[str, Any]) -> str:
    return pick_icon(intent)


def _template_docs(
    intent: Dict[str, Any],
    utterances: List[str],
    task_id: str,
    repo_root: Path,
) -> Dict[str, str]:
    title = str(intent.get("title") or "本地小工具")
    summary = str(intent.get("summary") or title)
    features = intent.get("features") or []
    acceptance = intent.get("acceptance") or []
    assumptions = intent.get("assumptions") or []
    spoken = "\n".join("- %s" % u for u in utterances) or "- （无）"
    design = """# 产品设计文档

## 名称
%s

## 一句话
%s

## 用户原话
%s

## 范围内功能
%s

## 验收
%s

## 假设
%s

## 交互
1. 用户在宿主「对话」页描述需求并确认方案。
2. 程序列表出现本卡片，状态从制作到可运行。
3. 可运行后点卡片：宿主动态加载插件并在自己的进程里启动。
""" % (
        title,
        summary,
        spoken,
        "\n".join("- %s" % x for x in features) or "- 本地界面与本地状态",
        "\n".join("- %s" % x for x in acceptance) or "- 打开后能完成一次主操作",
        "\n".join("- %s" % x for x in assumptions) or "- 以当前运行环境与最终可行性评审为准",
    )
    implementation = """# 详细实现方案

## 产物
宿主进程内 APK 插件（不安装）。applicationId / 包名由 meta.json 的 packageName 决定。
入口类 `{package}.PluginMain` 实现 `com.autoprocedure.pluginapi.PluginEntry`。
可单独 `assembleDebug` 得到 APK；手机只保存到宿主 data，点击后 DexClassLoader 加载。

## 实现步骤
1. 复制 `code/agent/program_agent/plugin_template`。
2. 按产品文档写 `PluginMain`：`onCreate(Activity, ViewGroup)` 里用代码构建 UI。
3. 用宿主 Gradle Wrapper 编译 `app-debug.apk`。
4. 主 Agent 通知下载；宿主写入 `filesDir/plugins/<taskId>/plugin.apk`，不调用系统安装器。

## 界面
- 按已确认的效果与开发评审方案实现界面，不限定为计数或列表。
- 禁止 R.layout、禁止额外权限、禁止网络。
"""
    runtime = """# 运行环境

## 宿主
- 应用：手机程序作坊（`com.autoprocedure.plat`）
- 系统：Android 5.0+（minSdk 21），当前联调 arm64
- 传输：USB `adb reverse`，控制口 17890，下载口 17891

## 插件
- APK 仅作 DEX 容器，SDK / AGP / Java 配置以工程文件和评审环境快照为准
- 编译：`phone_agent/gradlew -p <plugin_src> assembleDebug`
- 存放：宿主 `filesDir/plugins/<taskId>/plugin.apk`
- 加载：点击后 `DexClassLoader` + `PluginEntry`，跑在宿主进程
- 不安装到系统、不出现在桌面

## 本机依赖
- Android SDK（`ANDROID_HOME` 或 `~/Library/Android/sdk`）
- 已能编译宿主 `code/phone_agent` 的同一套 JDK/SDK
"""
    if isinstance(intent.get('runtimeCapabilities'), dict):
        runtime += ("\n## 本次设备能力实测（历史证据，运行时仍需重新检查）\n\n```json\n"
                    + json.dumps(intent['runtimeCapabilities'], ensure_ascii=False, indent=2)
                    + "\n```\n")
    locations = """# 运行代码位置

仓库根：`%s`

## 用户已经在跑的宿主
- 入口两页：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/MainActivity.kt`
- 对话页：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/ChatFragment.kt`
- 程序列表：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/CatalogFragment.kt`
- 任务状态：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/job/JobStore.kt`
- 插件加载：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/plugin/PluginLoader.kt`
- 插件容器：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/PluginContainerActivity.kt`
- 契约：`code/phone_agent/app/src/main/java/com/autoprocedure/pluginapi/PluginEntry.java`
- 既有声明式 Runtime（样例仍可用）：`code/phone_agent/app/src/main/java/com/autoprocedure/plat/plugin/PluginRuntime.kt`

## 本任务归档
- 任务目录：`code/agent/out/tasks/%s/`
- 产品文档：`code/agent/out/tasks/%s/product/`
- 插件工程：`code/agent/out/tasks/%s/plugin_src/`
- 编译产物：`code/agent/out/tasks/%s/dist/plugin.apk`

## 插件模板
- `code/agent/program_agent/plugin_template/`
""" % (
        repo_root,
        task_id,
        task_id,
        task_id,
        task_id,
    )
    if intent.get("devPlan"):
        implementation += "\n## 编程 Agent 对齐后，主 LLM 确定的实现路径\n\n" + str(intent["devPlan"]) + "\n"
    if intent.get("feasibilityReviewPath"):
        runtime += ("\n## 本次可行性评审证据\n\n运行环境快照、主 Agent 初判、编程意见和最终 LLM 决策：\n"
                    + str(intent["feasibilityReviewPath"]) + "\n")
    return {
        "design": design,
        "implementation": implementation,
        "runtime": runtime,
        "locations": locations,
    }
