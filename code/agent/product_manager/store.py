"""Persist the user-confirmed intent. Later stages will read this file."""
from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class IntentStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self.latest_path = self.directory / "confirmed_intent.json"

    def save(self, intent: Dict[str, Any], utterances: list) -> Path:
        record = {
            "confirmedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "phone",
            "utterances": utterances,
            "intent": intent,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        versioned = self.directory / ("confirmed_intent_%s.json" % stamp)
        payload = json.dumps(record, ensure_ascii=False, indent=2)
        versioned.write_text(payload + "\n", encoding="utf-8")
        self.latest_path.write_text(payload + "\n", encoding="utf-8")
        return self.latest_path

    def archive_product(self, intent: Dict[str, Any], utterances: list, proposal: str) -> Path:
        """Persist the handoff that the programmer Agent must implement.

        These files are intentionally human-readable: approval is for a
        product/design/implementation package, not for an opaque model reply.
        """
        title = str(intent.get("title") or "未命名功能").strip()
        product_id = _product_id(title, intent)
        directory = self.directory / "products" / product_id
        directory.mkdir(parents=True, exist_ok=True)
        runtime = {
            "channel": "declarative-apkg",
            "host": "code/phone_agent",
            "minSdk": 21,
            "compileSdk": 33,
            "runtime": "fixed Android interpreter; the downloaded ProgramIR is validated before activation",
            "constraints": [
                "the package contains JSON/data only and is never installed with PackageManager",
                "generated DEX, APK, native libraries, scripts, and arbitrary URLs are forbidden",
                "no Android permissions, host tokens, file paths, or network client are exposed",
            ],
        }
        design = "# 产品设计\n\n## 名称\n\n%s\n\n## 用户意图\n\n%s\n\n## 方案\n\n%s\n\n## 验收\n\n%s\n" % (
            title,
            str(intent.get("summary") or title),
            proposal.strip(),
            "\n".join("- " + str(item) for item in intent.get("acceptance", []) or ["在宿主功能列表中显示正确状态并可启动"]),
        )
        implementation = "# 实现方案\n\n1. Codex CLI 只生成声明式 ProgramIR 与 smoke 候选。\n2. 程序 Agent 用确定性编译器执行 Schema、语义、Capability、预算和 smoke 校验。\n3. 产出版本化 `.apkg`，镜像至 `code/phone_agent/data/plugins/<programId>/<versionId>.apkg`。\n4. 控制面只下发带摘要和大小的制品描述符；手机从独立制品通道流式下载。\n5. 手机在 `Context.filesDir/plugins/` 内完成摘要、ZIP、Manifest、ProgramIR 与 smoke 校验后原子激活。\n6. 用户从程序广场启动，由固定 Runtime 解释执行；不加载生成代码。\n\n插件不得包含 DEX/APK/SO/脚本、申请权限、访问宿主凭据或任意网络。\n"
        handoff = {"productId": product_id, "intent": intent, "utterances": utterances, "proposal": proposal, "runtime": runtime, "codeLocations": {"programCandidate": str(directory / "program-candidate.json"), "artifactMirror": "code/phone_agent/data/plugins/<programId>/<versionId>.apkg"}}
        (directory / "product-design.md").write_text(design, encoding="utf-8")
        (directory / "implementation-plan.md").write_text(implementation, encoding="utf-8")
        (directory / "runtime-environment.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (directory / "handoff.json").write_text(json.dumps(handoff, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return directory

    def archive_alignment(
        self,
        session_id: str,
        phase: str,
        event: str,
        user_text: str,
        reply: str,
        intent: Dict[str, Any] | None,
    ) -> Path:
        """Append an immutable turn snapshot for every user/product alignment."""
        directory = self.directory / "alignments" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        sequence = len(list(directory.glob("*.json")))
        record = {
            "sequence": sequence,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "phase": phase,
            "event": event,
            "user": user_text,
            "reply": reply,
            "intent": intent,
        }
        path = directory / ("%04d.json" % sequence)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (directory / "latest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path


def _product_id(title: str, intent: Dict[str, Any]) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-").lower()
    digest = hashlib.sha256(json.dumps(intent, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    return (slug[:32] if slug else "product") + "-" + digest
