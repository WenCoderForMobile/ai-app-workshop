"""Product agent: understand intent, confirm with the user. No codegen in this stage."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from llm import LlmClient
from trace import begin, end, flow, kv

from .archive import ProductArchive, pick_icon
from .feasibility import FeasibilityChecker, apply_verdict, normalize_verdict, product_review_prompt
from runtime_environment import RuntimeEnvironment, AUDIO_DECISION_RULES
from task_history import task_catalog
from .store import IntentStore

CONFIRM_WORDS = (
    "确认",
    "同意",
    "就这样",
    "就按这个",
    "按这个做",
    "没问题",
    "可以了",
    "可以",
    "可以啊",
    "好的",
    "好",
    "好啊",
    "好吧",
    "行",
    "行的",
    "对",
    "对的",
    "对了",
    "嗯",
    "嗯嗯",
    "ok",
    "okay",
    "yes",
    "yep",
    "yeah",
    "approve",
    "开始",
    "开始吧",
    "生成",
)
CONFIRM_HINT = "好的 / ok 就开始做，想改直接说。"
REJECT_WORDS = ("不满意", "不对", "重来", "换一个", "不是")

SYSTEM_PROMPT = """你是 AI 程序工坊的产品 Agent。
用户通过手机描述想要的功能。你只理解意图、澄清，并用「打开后的效果」请用户确认。
不要写代码，不要声称已经生成程序。

给用户看的 reply 只讲体验，不讲实现。
禁止出现：宿主、插件、APK、Java、Canvas、Handler、View、Activity、PluginEntry、DexClassLoader、Gradle、R.layout、SharedPreferences、编译、源码、方案步骤、实现要点。
可以用一个 emoji 图标代表这个程序。

结合后附运行环境、加载接口和 phone-agent 能力理解需求，能力清单不是穷举。
不要按关键词判断可行性，也不要混淆宿主已经有的功能与插件可调用接口。
UNSUPPORTED 只是初判：必须交编程 Agent 对齐后再由你复核，不能直接向用户定论。
技术把握不足时设 needsTechnicalReview=true；普通缺少用户偏好的澄清无需技术咨询。
把初判的技术理由放 technicalReason，不要在面向用户的 reply 中展开实现。
必须判断 taskRoute：new（独立新诉求）、continue（只继续先前已确认任务，不加新要求）、
modify（修改历史程序或当前方案）、clarify（目标不明确）。
continue/modify 的 targetTaskId 必须是历史目录中的确切 ID，不能用标题猜造 ID。
“继续刚才失败的”选择对应失败任务；“继续并加音效”是 modify，须保留原有功能再补差异。
修改时 intent 给出完整更新后的方案，除用户明确删除外保留原功能、验收与标题。
new 不得仅因标题相似而覆盖旧程序；多个候选不能确定时用 clarify 问清目标。
taskRoute.reason 简述选新建/续作/修改的依据，reply 用“继续/修改哪个程序”说明目标。
新小游戏默认设计匹配玩法的短音效（例如得分/成功/失败）和可见静音开关，写入 features 与 acceptance。
用户明确不要音效时尊重该要求。修改历史程序时保留已有选择，不擅自加循环背景音乐。
简单合成音效已有插件模板工具支持，不要误判为需要素材、录音权限或修改手机宿主。

只输出一个 JSON 对象：
{
  "status": "NEED_CLARIFY" | "WAITING_APPROVAL" | "UNSUPPORTED",
  "needsTechnicalReview": false,
  "clarificationKind": "none|user_preference|technical_verification",
  "technicalReason": "初判依据及不确定的能力",
  "taskRoute": {"action":"new|continue|modify|clarify","targetTaskId":"","reason":""},
  "reply": "给用户看的中文：图标 + 打开后能看到/玩到什么，短句，不要实现细节",
  "intent": {
    "title": "",
    "icon": "一个 emoji",
    "summary": "一句话效果，不要实现词",
    "features": [],
    "capabilities": [],
    "assumptions": [],
    "acceptance": [],
    "unsupportedReason": null
  }
}
NEED_CLARIFY：reply 最多 3 个具体问题，问效果不问技术。
WAITING_APPROVAL：用图标和效果复述，请用户好的/ok 就开始，或提出修改。
绝对不要写「已确认」「正在生成」「请稍候」。
UNSUPPORTED：只说做不到的效果（如收付款），不要解释技术原因。
若用户说「刚才功能 / 上次那个 / 再做一遍 / 改一下刚才的」，必须使用下面历史任务里最近匹配的一项。改已有程序时 intent.title 尽量保持原标题。
""" + AUDIO_DECISION_RULES


class ProductAgent:
    def __init__(
        self,
        store: IntentStore,
        llm: Optional[LlmClient] = None,
        archive: Optional[ProductArchive] = None,
        feasibility: Optional[FeasibilityChecker] = None,
        environment: Optional[RuntimeEnvironment] = None,
    ) -> None:
        self.store = store
        self.llm = llm
        self.archive_writer = archive
        self.feasibility = feasibility
        self.environment = environment or RuntimeEnvironment(Path(__file__).resolve().parents[3])
        self.runtime_context = ""
        self.needs_technical_review = False
        self.technical_reason = ""
        self.task_route: Dict[str, Any] = {}
        self.history_tasks: List[Dict[str, Any]] = []
        self.utterances: List[str] = []
        self.last_intent: Optional[Dict[str, Any]] = None
        self.last_archive: Optional[Dict[str, Any]] = None
        self.phase = "intake"
        self.history: List[Dict[str, str]] = []
        self.just_confirmed = False
        self.just_needs_user = False
        self.last_proposal_reply = ""
        self.completed_intent: Optional[Dict[str, Any]] = None
        self.completed_archive: Optional[Dict[str, Any]] = None
        self._restore_completed()

    def handle(self, text: str) -> str:
        text = (text or "").strip()
        self.just_confirmed = False
        self.just_needs_user = False
        begin("LLM flow")
        kv("user", text)
        kv("phase", self.phase)

        if not text:
            flow("1. skip model: empty text")
            reply = "请用一句话描述你想要的功能。"
            kv("reply", reply)
            end()
            return reply

        if text.lower().rstrip("。！.! ") in ("取消", "取消需求", "取消任务", "不做了", "算了", "cancel"):
            self.phase = "intake"
            self.last_intent = None
            self.last_archive = None
            self.history = []
            self.utterances = []
            if self.archive_writer is not None:
                self.archive_writer.reset()
            end()
            return "已取消待确认的需求，已有程序保留。"
        if _is_confirm(text) and self.phase != "waiting_approval":
            end()
            return "还没有待确认的新方案，请先描述需求或修改意见。"

        if self.phase == "waiting_approval" and _is_confirm(text):
            flow("1. route: user confirmed, skip model")
            if not self.last_intent:
                self.phase = "intake"
                reply = "还没有可确认的内容，请先说说你想打开后看到什么。"
                kv("reply", reply)
                end()
                return reply
            spoken = self.utterances + [text]
            if self.archive_writer is not None:
                self.last_archive = self.archive_writer.finalize(self.last_intent, spoken)
                kv("archive", self.last_archive.get("productDir") or "")
            path = self.store.save(self.last_intent, spoken, task_id=(self.last_archive or {}).get('taskId', ''))
            self.phase = "confirmed"
            self.just_confirmed = True
            self.completed_intent = dict(self.last_intent)
            self.completed_archive = dict(self.last_archive) if self.last_archive else None
            self.store.index_task(self.last_archive, self.last_intent, spoken)
            self.history.append({"role": "user", "content": text})
            self.history.append(
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "status": "CONFIRMED",
                            "title": self.last_intent.get("title") or "",
                            "summary": self.last_intent.get("summary") or "",
                        },
                        ensure_ascii=False,
                    ),
                }
            )
            kv("saved", path)
            icon = pick_icon(self.last_intent)
            title = str(self.last_intent.get("title") or "这个程序")
            reply = "好的，开始做了。程序广场会出现 %s「%s」，做好后点开就能用。" % (icon, title)
            kv("reply", reply)
            end()
            return reply

        if self.phase == "waiting_approval" and _is_reject(text):
            flow("1. route: user rejected, ask model to revise")
            self.utterances.append(text)
            self._archive_alignment("reject", text, "")
            return self._ask_model(
                text,
                revise_hint="用户不满意且未给新需求。请换一种打开后的效果描述（仍不要讲实现），用图标请用户确认。",
            )

        self.utterances.append(text)
        if self.llm is not None:
            # Let the LLM choose from durable task identities, including older
            # failed tasks; do not reset/reuse archives based on wording alone.
            return self._ask_model(text, revise_hint=None)
        if self.phase == "confirmed":
            follow = _refers_to_history(text)
            if follow:
                flow("1. route: resume previous task from history")
                self._resume_completed()
            else:
                flow("1. route: new request after confirm, keep history")
                self.last_intent = None
                self.last_archive = None
                if self.archive_writer is not None:
                    self.archive_writer.reset()
            self.phase = "intake"
        elif _refers_to_history(text) and self.completed_intent:
            flow("1. route: user pointed at a historical task")
            self._resume_completed()
        else:
            flow("1. route: understand intent, call model")
        return self._ask_model(text, revise_hint=None)

    def _ask_model(self, text: str, revise_hint: Optional[str]) -> str:
        self.runtime_context = self.environment.snapshot()
        self.needs_technical_review = False
        self.technical_reason = ""
        if self.llm is None:
            flow("2. model unavailable, fallback")
            if self.last_intent and self.phase == "waiting_approval":
                self.phase = "intake"
                self.history.append({"role": "user", "content": "原方案：%s；意见：%s" % (self.last_intent.get("summary"), text)})
                return self._after_intent(text, "NEED_CLARIFY", "当前开发模型不可用，已保留原方案和意见，请恢复连接后重发修改意见。")
            reply = self._fallback(text, keep_intent=_refers_to_history(text))
            return self._after_intent(text, "WAITING_APPROVAL", reply)

        user_content = text if not revise_hint else revise_hint + "\n用户说：" + text
        self.history.append({"role": "user", "content": user_content})
        self.history = self.history[-24:]
        catalog = self._history_catalog_text()
        system = SYSTEM_PROMPT + "\n\n实际运行环境与 phone-agent 能力（用于判断，不直接复制给用户）：\n" + self.runtime_context
        if catalog:
            system += "\n\n" + catalog
        messages = [{"role": "system", "content": system}] + self.history[-12:]
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
            if self.history_tasks:
                self.last_intent = None
                return self._after_intent(text, 'NEED_CLARIFY', '暂时无法判断要继续哪个程序，已保留历史代码，请恢复模型后重试。')
            if self.last_intent and self.phase == "waiting_approval":
                self.phase = "intake"
                self.history.append({"role": "user", "content": "原方案：%s；意见：%s" % (self.last_intent.get("summary"), text)})
                return self._after_intent(text, "NEED_CLARIFY", "当前开发模型不可用，已保留原方案和意见，请恢复连接后重发修改意见。")
            reply = self._fallback(text, keep_intent=_refers_to_history(text))
            return self._after_intent(text, "WAITING_APPROVAL", reply)

        resumed = self._bind_task_route(result, text)
        if resumed:
            end()
            return resumed
        self.history.append({"role": "assistant", "content": json.dumps(result, ensure_ascii=False)})
        status = str(result.get("status") or "")
        self.needs_technical_review = (result.get("needsTechnicalReview") is True or
                                       result.get("clarificationKind") == "technical_verification")
        self.technical_reason = str(result.get("technicalReason") or "")
        reply = str(result.get("reply") or "").strip()
        intent = result.get("intent")
        self.last_intent = None
        if status == "WAITING_APPROVAL" and not (isinstance(intent, dict) and intent.get("title") and intent.get("summary")):
            status = "NEED_CLARIFY"
            reply = "方案信息不完整，请补充具体操作后再确认。"
        if isinstance(intent, dict):
            try:
                capabilities = json.loads(self.runtime_context).get('phoneCapabilities')
                if isinstance(capabilities, dict):
                    intent['runtimeCapabilities'] = capabilities
            except (TypeError, ValueError):
                pass
            intent['taskRoute'] = dict(self.task_route)
            if not str(intent.get("icon") or "").strip():
                intent["icon"] = pick_icon(intent)
            self.last_intent = intent

        flow("3. parse model JSON")
        kv("status", status or "(empty)")
        if isinstance(intent, dict):
            kv("intent.title", intent.get("title") or "")
            kv("intent.summary", intent.get("summary") or "")
            features = intent.get("features") or []
            if features:
                kv("intent.features", features)
        return self._after_intent(text, status, reply)

    def _bind_task_route(self, result: Dict[str, Any], text: str) -> Optional[str]:
        route = result.get('taskRoute')
        if not isinstance(route, dict):
            route = {'action': 'new'} if not self.history_tasks else {'action': 'clarify'}
        action = route.get('action')
        target = next((x for x in self.history_tasks if x['taskId'] == route.get('targetTaskId')), None)
        if action not in ('new', 'continue', 'modify') or (action != 'new' and target is None):
            self.task_route = {'action': 'clarify'}
            self.last_intent = None
            result.update(status='NEED_CLARIFY', intent=None, needsTechnicalReview=False,
                          reply=(str(result.get('reply') or '') if action == 'clarify' else '')
                          or '请说明要继续或修改哪个程序，还是新建一个程序。')
            return None
        self.task_route = {'action': action, 'targetTaskId': target['taskId'] if target and action != 'new' else '',
                           'reason': str(route.get('reason') or '')}
        if action == 'new':
            if self.archive_writer:
                self.archive_writer.reset()
            self.last_archive = None
            self.last_intent = None
            self.utterances = [text]
            self.history = [{'role': 'user', 'content': text}]
            return None
        self.last_archive = dict(target['meta'])
        if self.archive_writer:
            self.archive_writer.reopen(self.last_archive)
        original = target['confirmedIntent'] if action == 'continue' and target['confirmed'] else target['intent']
        if action == 'continue' and target['confirmed']:
            self.last_intent = dict(original, taskRoute=self.task_route)
            self.last_archive['generationMode'] = 'continue'
            self.last_archive['baseTaskId'] = target['taskId']
            self.phase = 'confirmed'
            self.just_confirmed = True
            self.completed_archive = dict(self.last_archive)
            self.completed_intent = dict(self.last_intent)
            self._archive_alignment('continue', text, '继续已确认任务，复用现有源码。')
            reply = '继续制作「%s」，会利用之前保存的代码。' % target['title']
            self.history.append({'role': 'assistant', 'content': reply})
            return reply
        updated = result.get('intent') if isinstance(result.get('intent'), dict) else {}
        result['intent'] = dict(original, **updated)
        # A draft has never been approved; continuing it still requires approval.
        if action == 'continue':
            result.update(status='WAITING_APPROVAL', reply='继续完善「%s」的方案。' % target['title'])
        return None

    def _after_intent(self, text: str, status: str, reply: str) -> str:
        if status != "NEED_CLARIFY" or self.needs_technical_review:
            flow("3b. consult CLI feasibility")
            verdict = None
            if self.feasibility is not None:
                initial = {"status": status, "reply": reply, "technicalReason": self.technical_reason}
                developer = self.feasibility.ask(
                    text, self.last_intent, environment_context=self.runtime_context,
                    product_assessment=initial,
                )
                if developer is not None and self.llm is not None:
                    flow("3c. main LLM reviews programming Agent assessment")
                    try:
                        verdict = normalize_verdict(self.llm.chat_json(product_review_prompt(
                            text, self.last_intent or {}, initial, developer, self.runtime_context)))
                    except Exception as exc:
                        flow("feasibility: final LLM review failed (%s)" % type(exc).__name__)
                self.feasibility.record_decision(verdict, "decided" if verdict is not None else "pending")
                if verdict is None:
                    status = "NEED_CLARIFY"
                    reply = "技术评估暂未完成，暂时不能确认能否实现。请稍后重试，我会继续核对可行方案。"
            elif self.needs_technical_review:
                status = "NEED_CLARIFY"
                reply = "程序能力检测尚未完成，暂时不能确认能否实现；需要继续自动核对。"
            status, reply, merged = apply_verdict(status, reply, self.last_intent, verdict)
            self.last_intent = merged
            if self.feasibility is not None and self.feasibility.review_dir and self.last_intent:
                self.last_intent['feasibilityReviewPath'] = str(self.feasibility.review_dir / 'review.json')
            if self.last_intent and not str(self.last_intent.get("icon") or "").strip():
                self.last_intent["icon"] = pick_icon(self.last_intent)
            kv("status_after_cli", status)

        # Future turns must see the reviewed decision, not a stale initial refusal.
        if self.history and self.history[-1].get("role") == "assistant":
            self.history[-1]["content"] = json.dumps(
                {"status": status, "reply": reply, "intent": self.last_intent}, ensure_ascii=False)

        if status == "WAITING_APPROVAL" and self.last_intent:
            self.phase = "waiting_approval"
            reply = _approval_reply(reply, self.last_intent)
            self.last_proposal_reply = reply
            self._archive_alignment("proposal", text, reply)
        elif status == "UNSUPPORTED":
            self.phase = "intake"
            self._archive_alignment("unsupported", text, reply)
        else:
            self.phase = "intake"
            self._archive_alignment("clarify", text, reply)
        kv("next_phase", self.phase)

        if not reply:
            reply = "请再补充一点你想要的功能细节。"
            flow("4. empty reply, use default")
        else:
            flow("4. model reply ready")
        kv("reply", reply)
        end()
        return reply

    def _fallback(self, text: str, keep_intent: bool = False) -> str:
        if keep_intent and self.last_intent:
            title = str(self.last_intent.get("title") or "刚才那个程序")
            icon = pick_icon(self.last_intent)
            return "%s 继续「%s」。要原样再做，或说说打开后想改成什么样。\n%s" % (
                icon,
                title,
                CONFIRM_HINT,
            )
        intent = {
            "title": text[:40],
            "icon": "",
            "summary": text,
            "features": ["ui", "state"],
            "capabilities": [],
            "assumptions": [],
            "acceptance": ["打开后能看到该功能并完成一次主操作"],
            "unsupportedReason": None,
        }
        intent["icon"] = pick_icon(intent)
        self.last_intent = intent
        self.phase = "waiting_approval"
        return _approval_reply(
            "%s %s\n打开后：%s" % (intent["icon"], intent["title"], text),
            intent,
        )

    def _restore_completed(self) -> None:
        if self.archive_writer is not None:
            confirmed = [item for item in task_catalog(self.archive_writer.tasks_root) if item['confirmed']]
            if confirmed:
                self.completed_archive = dict(confirmed[0]['meta'])
                self.completed_intent = dict(confirmed[0]['confirmedIntent'])
                return
        latest = self.store.latest() if self.store is not None else None
        if latest and isinstance(latest.get("intent"), dict):
            self.completed_intent = dict(latest["intent"])
        metas = []
        if self.archive_writer is not None:
            metas = self.archive_writer.list_recent(1)
        if metas:
            self.completed_archive = dict(metas[0])
            if not self.completed_intent:
                self.completed_intent = {
                    "title": metas[0].get("title") or "",
                    "summary": metas[0].get("summary") or "",
                }

    def _resume_completed(self) -> None:
        if self.completed_intent:
            self.last_intent = dict(self.completed_intent)
        if self.completed_archive:
            self.last_archive = dict(self.completed_archive)
            if self.archive_writer is not None:
                self.archive_writer.reopen(self.last_archive)

    def _history_catalog_text(self) -> str:
        if self.archive_writer is not None:
            self.history_tasks = task_catalog(self.archive_writer.tasks_root)
            if self.history_tasks:
                rows = []
                for item in self.history_tasks:
                    row = {key: item[key] for key in ('taskId', 'title', 'summary', 'updatedAt', 'state', 'buildState', 'deliveryState', 'error', 'hasSource', 'confirmed')}
                    row['intent'] = {key: item['intent'].get(key) for key in ('title', 'summary', 'features', 'acceptance', 'assumptions')}
                    rows.append(row)
                return ('历史任务（使用 taskId 选择，不能只凭标题；当前任务 ID=%s）：\n%s'
                        % ((self.last_archive or self.completed_archive or {}).get('taskId', ''),
                           json.dumps(rows, ensure_ascii=False)))
        items: List[Dict[str, Any]] = []
        if self.store is not None:
            items.extend(self.store.list_recent(8))
        if self.archive_writer is not None:
            for meta in self.archive_writer.list_recent(8):
                items.append(meta)
        lines = []
        seen = set()
        for item in items:
            title = str(item.get("title") or "")
            if not title or title in seen:
                continue
            seen.add(title)
            summary = str(item.get("summary") or (item.get("intent") or {}).get("summary") or "")
            lines.append("- %s：%s" % (title, summary[:80]))
            if len(lines) >= 8:
                break
        if not lines:
            return ""
        return "历史任务（用户说「刚才功能」时用最近一项，可重做或在此基础上改）：\n" + "\n".join(lines)

    def reopen_for_dev_question(self, question: str) -> None:
        """Program agent needs the user; keep intent and wait for the next reply."""
        self.phase = "waiting_approval"
        self.just_confirmed = False
        self.just_needs_user = True
        self.last_proposal_reply = question
        self._archive_alignment("dev_ask_user", "", question)

    def _archive_alignment(self, kind: str, user_text: str, agent_reply: str) -> None:
        if self.archive_writer is None or not self.last_intent:
            return
        if self.archive_writer.current is None:
            self.last_archive = self.archive_writer.start(
                self.last_intent,
                self.utterances,
                reason=kind,
            )
        self.archive_writer.append_alignment(
            kind=kind,
            user_text=user_text,
            agent_reply=agent_reply,
            intent=self.last_intent,
        )


_PUNCT = re.compile(r"[\s。.!！？?，,、~～]+")
_FAKE_GENERATE = re.compile(
    r"(正在为你生成插件[^。.!！？\n]*[。.!！？]?)|(正在生成插件[^。.!！？\n]*[。.!！？]?)|(请稍候[。.!！？]?)"
)


def _compact(text: str) -> str:
    return _PUNCT.sub("", text or "")


def _approval_reply(reply: str, intent: Optional[Dict[str, Any]] = None) -> str:
    """WAITING_APPROVAL：只展示效果和图标，不讲实现，也不能假装已经开始生成。"""
    text = _FAKE_GENERATE.sub("", reply or "").strip()
    text = re.sub(r"方案已确认[:：]?", "", text)
    text = re.sub(r"已确认[:：]?", "", text).strip()
    text = _strip_impl_talk(text)
    if not text:
        text = _effect_from_intent(intent)
    else:
        text = _with_icon(text, intent)
    if "确认" not in text and "同意" not in text and "开始做" not in text:
        text = (text + "\n" if text else "") + CONFIRM_HINT
    elif "正在生成" in text or "请稍候" in text:
        text = _FAKE_GENERATE.sub("", text).strip()
        text = (text + "\n" if text else "") + CONFIRM_HINT
    return text


_IMPL_TERMS = re.compile(
    r"(宿主(进程|插件|Activity)?|独立 ?APK|APK 插件|PluginEntry|DexClassLoader|"
    r"R\.layout|SharedPreferences|ViewGroup|onCreate|assembleDebug|"
    r"自定义 View|addView|Handler|Choreographer|SurfaceView|"
    r"Gradle|Java 代码|实现要点|编译独立|产品归档|"
    r"Canvas|Activity)",
    re.I,
)


def _strip_impl_talk(text: str) -> str:
    kept = []
    for raw in re.split(r"(?<=[。！？\n])", text or ""):
        if _IMPL_TERMS.search(raw):
            continue
        kept.append(raw)
    return "".join(kept).strip()


def _effect_from_intent(intent: Optional[Dict[str, Any]]) -> str:
    intent = intent or {}
    icon = pick_icon(intent)
    title = str(intent.get("title") or "这个程序")
    summary = _strip_impl_talk(str(intent.get("summary") or title)) or title
    return "%s %s\n打开后：%s" % (icon, title, summary)


def _with_icon(text: str, intent: Optional[Dict[str, Any]]) -> str:
    intent = intent or {}
    icon = pick_icon(intent)
    title = str(intent.get("title") or "").strip()
    if icon and icon not in text:
        if title and title in text:
            text = text.replace(title, "%s %s" % (icon, title), 1)
        else:
            head = "%s %s" % (icon, title) if title else icon
            text = "%s\n%s" % (head, text)
    return text.strip()


def _is_confirm(text: str) -> bool:
    return (text or "").strip().rstrip("。！.! ").strip().lower() in CONFIRM_WORDS


def _is_reject(text: str) -> bool:
    return text.strip().rstrip("。！.! ") in REJECT_WORDS


_HISTORY_REF = re.compile(
    r"(刚才|上次|上一个|刚刚|原先|原来那|再做一|重新做|同样的|还是那|历史程序|那个程序|那个功能|刚才的)"
)


def _refers_to_history(text: str) -> bool:
    return bool(_HISTORY_REF.search(text or ""))
