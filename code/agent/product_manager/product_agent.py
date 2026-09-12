"""Product agent: understand intent, confirm with the user. No codegen in this stage."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any, Dict, List, Mapping, Optional

from llm import LlmClient
from plugin import ProgramAgent
from trace import begin, end, flow, kv

from .store import IntentStore

CONFIRM_WORDS = ("确认", "同意", "就这样", "没问题", "可以了", "好的", "好", "可以", "对的", "ok", "approve")
REJECT_WORDS = ("不满意", "不对", "重来", "换一个", "不是")
CANCEL_WORDS = ("取消", "取消需求", "取消任务", "算了", "不做了", "cancel")
MAX_UNDIRECTED_REJECTS = 3

# Planner/LLM output is not a policy decision.  Keep this small registry on
# the trusted side so a phrase such as “open the camera” cannot become a local
# plugin merely because the model labelled it as supported.
UNSUPPORTED_REQUESTS = {
    "相机": "相机",
    "拍照": "相机",
    "摄像头": "相机",
    "支付": "支付",
    "付款": "支付",
    "网络": "任意网络",
    "联网": "任意网络",
    "网页": "任意网络",
    "浏览器": "任意网络",
    "定位": "定位",
    "gps": "定位",
    "联系人": "联系人",
    "通讯录": "联系人",
    "后台通知": "后台通知",
    "定时通知": "后台通知",
}

SYSTEM_PROMPT = """你是自动程序扩充系统的产品 Agent（Planner）。
用户通过手机描述想要的功能。你只理解意图、澄清、给可实现方案摘要，请用户确认。
不要写代码，不要声称已经生成插件。

当前 MVP 只支持纯本地、声明式界面：文本、按钮、列表、本地计数/打卡状态。
不支持：相机、支付、任意网络、后台定时通知、系统权限、打开任意 URL。
越界时 status 必须是 UNSUPPORTED，reply 说明边界并给可落在 MVP 内的替代建议。

只输出一个 JSON 对象：
{
  "status": "NEED_CLARIFY" | "WAITING_APPROVAL" | "UNSUPPORTED",
  "reply": "给用户看的中文，简短",
  "intent": {
    "title": "",
    "summary": "",
    "features": ["ui.text", "ui.button", "ui.list", "state"],
    "capabilities": [],
    "assumptions": [],
    "acceptance": [],
    "unsupportedReason": null
  }
}
NEED_CLARIFY：信息不够，reply 最多 3 个具体问题。
WAITING_APPROVAL：已经能做，reply 用用户语言复述方案，并明确请用户回复「确认」或提出修改。
UNSUPPORTED：做不到，不伪造功能。
"""


class ProductAgent:
    def __init__(
        self,
        store: IntentStore,
        llm: Optional[LlmClient] = None,
        on_plugin=None,
        on_job=None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.utterances: List[str] = []
        self.last_intent: Optional[Dict[str, Any]] = None
        self.phase = "intake"
        self.history: List[Dict[str, str]] = []
        self.undirected_reject_count = 0
        self.last_archive_path = None
        self.alignment_session_id = uuid.uuid4().hex
        # Program artifacts belong in phone_agent/data/plugins.  IntentStore is
        # only the Agent's audit record and must not become a bypass around the
        # phone-side installer.
        self.program_agent = ProgramAgent()
        self.on_plugin = on_plugin
        self.on_job = on_job

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        begin("LLM flow")
        kv("user", text)
        kv("phase", self.phase)
        self._archive_alignment("received", text, "")

        if not text:
            flow("1. skip model: empty text")
            reply = "请用一句话描述你想要的功能。"
            kv("reply", reply)
            self._archive_alignment("replied", text, reply)
            end()
            return reply

        if _command(text) in CANCEL_WORDS:
            self._reset_request()
            reply = "已取消当前需求。已生成的程序会保留；你可以重新描述想要的功能。"
            self._archive_alignment("canceled", text, reply)
            end()
            return reply

        if _is_confirm(text) and self.phase != "waiting_approval":
            reply = "还没有待确认的新方案，请先描述需求或修改意见。"
            self._archive_alignment("approval_missing", text, reply)
            end()
            return reply

        unsupported = _unsupported_feature(text)
        if unsupported:
            self._reset_request()
            reply = _unsupported_reply(unsupported)
            flow("1. trusted policy rejected unsupported request")
            kv("reply", reply)
            self._archive_alignment("policy_rejected", text, reply)
            end()
            return reply

        if self.phase == "waiting_approval" and _is_confirm(text):
            flow("1. route: user confirmed, skip model")
            if not self.last_intent:
                self.phase = "intake"
                reply = "还没有可确认的方案，请先描述你想要的功能。"
                kv("reply", reply)
                self._archive_alignment("approval_missing", text, reply)
                end()
                return reply
            approval_digest = _ensure_approval_digest(self.last_intent)
            task_id = "task-" + hashlib.sha256(approval_digest.encode("utf-8")).hexdigest()[:16]
            path = self.store.save(self.last_intent, self.utterances + [text])
            self.phase = "confirmed"
            self.undirected_reject_count = 0
            kv("saved", path)
            self._notify_job(
                {
                    "taskId": task_id,
                    "status": "RUNNING",
                    "phase": "COMPILING",
                    "title": str(self.last_intent.get("title") or "未命名功能"),
                }
            )
            stage = "COMPILING"
            result = None
            try:
                result = self.program_agent.build(self.last_intent)
                task_id = str((result.get("manifest") or {}).get("taskId") or task_id)
                stage = "PUBLISHING"
                self._notify_job(
                    {
                        "taskId": task_id,
                        "status": "RUNNING",
                        "phase": stage,
                        "title": str(result.get("title") or self.last_intent.get("title") or "未命名功能"),
                    }
                )
                descriptor = None
                if self.on_plugin:
                    descriptor = self.on_plugin(result)
                success_job = {
                    "taskId": task_id,
                    "status": "SUCCEEDED",
                    "phase": "PUBLISHING",
                    "title": str(result.get("title") or self.last_intent.get("title") or "未命名功能"),
                }
                if isinstance(descriptor, Mapping):
                    success_job.update(descriptor)
                self._notify_job(success_job)
                report = result.get("developmentReport") or {}
                if result.get("warning"):
                    reply = "已确认，插件已用安全本地模板生成并发布，手机正在下载验证：%s。开发记录结论：%s" % (result["title"], report.get("summary") or result["warning"])
                else:
                    reply = "已确认，插件已发布，手机正在下载验证：%s" % result["title"]
                kv("plugin", result["path"])
            except Exception as exc:
                self._notify_job(
                    {
                        "taskId": task_id,
                        "status": "FAILED",
                        "phase": stage,
                        "title": str(self.last_intent.get("title") or "未命名功能"),
                        "errorCode": getattr(
                            exc,
                            "code",
                            "PUBLISH_FAILED" if stage == "PUBLISHING" else "BUILD_FAILED",
                        ),
                        "error": str(exc)[:500],
                    }
                )
                reply = (
                    "方案已确认，插件已生成但发布失败：%s"
                    if stage == "PUBLISHING"
                    else "方案已确认，但插件生成失败：%s"
                ) % exc
                kv("plugin_error", repr(exc))
            kv("reply", reply)
            self._archive_alignment("approved", text, reply)
            end()
            return reply

        if self.phase == "waiting_approval" and _is_reject(text):
            self.undirected_reject_count += 1
            if self.undirected_reject_count >= MAX_UNDIRECTED_REJECTS:
                self._reset_request()
                reply = "已连续 3 次未给修改意见地拒绝方案，本次需求已结束。请重新描述你想要的功能，或直接说明要修改什么。"
                flow("1. revision exhausted")
                kv("reply", reply)
                self._archive_alignment("revision_exhausted", text, reply)
                end()
                return reply
            flow("1. route: user rejected, ask model to revise")
            self.utterances.append(text)
            return self._ask_model(
                text,
                revise_hint="用户不满意且未给新需求，请换一种仍落在 MVP 内的方案。当前无意见拒绝次数：%s/%s。" % (self.undirected_reject_count, MAX_UNDIRECTED_REJECTS),
            )

        if self.phase == "waiting_approval":
            # Any non-command message at this point is actionable feedback.
            # It creates a new proposal rather than consuming the bounded
            # no-feedback reject budget.
            self.undirected_reject_count = 0
            self.utterances.append(text)
            flow("1. route: user feedback, revise proposal")
            return self._ask_model(text, revise_hint="用户正在修改方案；只输出新的可实现方案，并继续等待确认。")

        if self.phase == "confirmed":
            flow("1. route: new request after confirm, reset session")
            self._reset_request()
        else:
            flow("1. route: understand intent, call model")
        self.utterances.append(text)
        return self._ask_model(text, revise_hint=None)

    def _reset_request(self) -> None:
        self.phase = "intake"
        self.last_intent = None
        self.history = []
        self.utterances = []
        self.last_archive_path = None
        self.undirected_reject_count = 0

    def _notify_job(self, job: Dict[str, Any]) -> None:
        if self.on_job is None:
            return
        try:
            self.on_job(job)
        except Exception as exc:
            # Artifact generation must not be rolled back merely because the
            # phone disconnected. MainAgent persists and replays normal sends.
            flow("job notification failed: %s" % exc)

    def _ask_model(self, text: str, revise_hint: Optional[str]) -> str:
        if self.llm is None:
            flow("2. model unavailable, fallback")
            reply = self._fallback(text)
            kv("reply", reply)
            self._archive_alignment("fallback_reply", text, reply)
            end()
            return reply

        user_content = text if not revise_hint else revise_hint + "\n用户说：" + text
        self.history.append({"role": "user", "content": user_content})
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + self.history[-8:]
        kv("history_turns", len(self.history))
        kv("prompt", user_content)
        flow("2. call LLM")
        try:
            self.llm.reload()
            kv("active", self.llm.provider_name)
            result = self.llm.chat_json(messages)
            if not isinstance(result, dict):
                raise ValueError("model response must be an object")
        except Exception as exc:
            flow("3. LLM failed: %s: %s" % (type(exc).__name__, exc))
            self.history.pop()
            reply = self._fallback(text)
            kv("reply", reply)
            self._archive_alignment("llm_failed_fallback", text, reply)
            end()
            return reply

        self.history.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})
        self.history = self.history[-8:]
        status = str(result.get("status") or "")
        reply = str(result.get("reply") or "").strip()
        intent = result.get("intent")
        # A response may approve only its own complete proposal, never a stale one.
        self.last_intent = None
        if status == "WAITING_APPROVAL" and not (
            isinstance(intent, dict)
            and isinstance(intent.get("title"), str) and intent["title"].strip()
            and isinstance(intent.get("summary"), str) and intent["summary"].strip()
        ):
            status = "NEED_CLARIFY"
            reply = "本次方案信息不完整，请补充功能名称和具体操作后再确认。"
        if isinstance(intent, dict):
            intent = dict(intent)
            for key in ("approvalDigest", "productArchive"):
                intent.pop(key, None)
            policy_reason = _intent_policy_reason(intent)
            if policy_reason:
                status = "UNSUPPORTED"
                reply = _unsupported_reply(policy_reason)
                intent["unsupportedReason"] = policy_reason
                intent["capabilities"] = []
            self.last_intent = intent

        flow("3. parse model JSON")
        kv("status", status or "(empty)")
        if isinstance(intent, dict):
            kv("intent.title", intent.get("title") or "")
            kv("intent.summary", intent.get("summary") or "")
            features = intent.get("features") or []
            if features:
                kv("intent.features", features)

        if status == "WAITING_APPROVAL" and self.last_intent:
            reply = "方案：%s\n%s\n回复「确认」开始制作，或直接提出修改；回复「取消」结束本次需求。" % (
                self.last_intent["title"], self.last_intent["summary"],
            )
            self.last_archive_path = self.store.archive_product(
                self.last_intent,
                self.utterances,
                reply or "等待用户确认的产品方案。",
            )
            self.last_intent["productArchive"] = str(self.last_archive_path)
            self.phase = "waiting_approval"
        elif status == "UNSUPPORTED":
            self.phase = "intake"
            self.last_intent = None
        else:
            self.phase = "intake"
        kv("next_phase", self.phase)

        if not reply:
            reply = "请再补充一点你想要的功能细节。"
            flow("4. empty reply, use default")
        else:
            flow("4. model reply ready")
        kv("reply", reply)
        self._archive_alignment("proposal_reply", text, reply)
        end()
        return reply

    def _archive_alignment(self, event: str, user_text: str, reply: str) -> None:
        self.store.archive_alignment(
            self.alignment_session_id,
            self.phase,
            event,
            user_text,
            reply,
            self.last_intent,
        )

    def _fallback(self, text: str) -> str:
        if self.phase == "waiting_approval" and self.last_intent and _is_reject(text):
            return "当前未连上大模型，无法生成新方案，请提供具体修改意见。"
        if self.phase == "waiting_approval" and self.last_intent:
            # A template cannot interpret arbitrary edits. Keep the old proposal
            # as context, but require a fresh reviewed proposal before approval.
            self.phase = "intake"
            previous = str(self.last_intent.get("summary") or self.last_intent.get("title") or "")
            self.history.append({"role": "user", "content": "原方案：%s\n修改意见：%s" % (previous, text)})
            self.history = self.history[-8:]
            return "当前未连上大模型，暂时无法重新评估方案。原方案：%s\n已记录意见：%s\n请恢复模型连接后重发修改意见，重新出方案后再确认。" % (previous, text)
        unsupported = _unsupported_feature(text)
        if unsupported:
            self.last_intent = None
            self.phase = "intake"
            return _unsupported_reply(unsupported)
        intent = {
            "title": text[:40],
            "summary": text,
            "features": ["ui.text", "ui.button", "state"],
            "capabilities": [],
            "assumptions": ["纯本地，无相机/支付/任意网络"],
            "acceptance": ["打开后能看到该功能并完成一次主操作"],
            "unsupportedReason": None,
        }
        self.last_intent = intent
        self.phase = "waiting_approval"
        self.last_archive_path = self.store.archive_product(intent, self.utterances, "纯本地功能方案，等待用户确认。")
        self.last_intent["productArchive"] = str(self.last_archive_path)
        return (
            "当前未连上大模型，先按你的原话理解：\n"
            "「%s」\n"
            "这是一个纯本地小功能。回复「确认」后会编译声明式插件；有修改请直接发新描述。"
            % text
        )


def _is_confirm(text: str) -> bool:
    return _command(text) in CONFIRM_WORDS


def _command(text: str) -> str:
    return text.strip().rstrip("。！.!！ ").strip().lower()


def _ensure_approval_digest(intent: Dict[str, Any]) -> str:
    supplied = intent.get("approvalDigest")
    if isinstance(supplied, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", supplied):
        return supplied
    canonical = json.dumps(
        intent,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    intent["approvalDigest"] = digest
    return digest


def _is_reject(text: str) -> bool:
    return _command(text) in REJECT_WORDS


def _unsupported_feature(text: str) -> Optional[str]:
    lowered = text.lower()
    for token, boundary in UNSUPPORTED_REQUESTS.items():
        if token in lowered:
            return boundary
    return None


def _intent_policy_reason(intent: Dict[str, Any]) -> Optional[str]:
    capabilities = intent.get("capabilities")
    if isinstance(capabilities, list) and capabilities:
        return "外部能力"
    content = " ".join(str(intent.get(key) or "") for key in ("title", "summary", "unsupportedReason"))
    return _unsupported_feature(content)


def _unsupported_reply(boundary: str) -> str:
    return "当前版本不支持%s，因此不会生成插件。可以改成纯本地的文字、清单、打卡、登记表或简单计算功能。" % boundary
