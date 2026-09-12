"""Entry: phone bridge + product manager agent."""
from __future__ import annotations

import argparse
import json
import threading
from contextlib import nullcontext
from adb_reverse import AdbReverseWatchdog
from pathlib import Path
from codex_cli import CodexStartupError, ensure_codex_login
from runtime_environment import RuntimeEnvironment

from connect_phone import ConnectPhone
from download_server import DownloadServer
from llm import LlmClient
from llm.config import CONFIG_PATH
from main_agent import MainAgent
from product_manager import IntentStore, ProductAgent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=17890)
    parser.add_argument("--file-port", type=int, default=17891)
    parser.add_argument("--runtime", choices=("apk", "declarative"), default="apk")
    parser.add_argument("--adb-reverse", action="store_true", help="Restore ADB port mappings after USB reconnect")
    parser.add_argument("--adb-serial", default=None, help="Pin ADB mapping to this phone")
    args = parser.parse_args()

    # Direct Python launches and the shell launcher share the same preflight.
    # Never expose the phone/download ports before Codex is ready.
    try:
        ensure_codex_login()
    except CodexStartupError as exc:
        parser.exit(1, "server 启动失败：%s\n" % exc)

    try:
        mappings = (AdbReverseWatchdog(args.port, args.file_port, args.adb_serial)
                    if args.adb_reverse else nullcontext())
    except RuntimeError as exc:
        parser.exit(1, "server 启动失败：%s\n" % exc)
    with mappings:
        serve(args)


def serve(args) -> None:
    root = Path(__file__).resolve().parent
    store = IntentStore(root / "out")
    llm = LlmClient()
    llm.reload()
    llm_ok = llm.available()
    print("llm config file:", CONFIG_PATH, flush=True)
    print(
        "llm:",
        "on" if llm_ok else "off",
        "provider=",
        llm.provider_name,
        "label=",
        llm.label,
        "model=",
        llm.model,
        "base=",
        llm.base_url,
        "api_key=",
        "set" if llm.api_key else "MISSING",
        flush=True,
    )
    if llm.provider_name != "local" and not llm.api_key:
        print(
            "WARNING: active=%s but API key is empty. "
            "Put the key in codex/startServer/env.sh and restart."
            % llm.provider_name,
            flush=True,
        )
    print("confirmed intents ->", store.latest_path, flush=True)

    if args.runtime == "apk":
        from apk_product_manager import ProductAgent as ApkProductAgent, IntentStore as ApkIntentStore, ProductArchive
        from apk_product_manager.feasibility import FeasibilityChecker
        from program_agent import ProgramAgent as ApkProgramAgent
        from orchestrator import Orchestrator
        from apk_delivery import ApkDelivery

        downloads = DownloadServer(root / "out", host=args.host, port=args.file_port)
        downloads.start()
        bridge = ConnectPhone(host=args.host, port=args.port)
        product = ApkProductAgent(
            ApkIntentStore(root / "out" / "apk_intents"), llm,
            ProductArchive(root / "out" / "tasks", root.parent.parent, llm),
            FeasibilityChecker(root / "program_agent" / "FRAMEWORK.md", root / "out" / "codex_work"),
            environment=RuntimeEnvironment(root.parent.parent, probe_device=True,
                                           capability_provider=bridge.request_capabilities),
        )
        session = Orchestrator(product, ApkProgramAgent(root, llm), downloads)
        delivery = ApkDelivery(bridge, downloads, root / "out" / "apk_jobs.json")
        def handle_apk(message_type, text):
            if message_type == "task_check":
                delivery.send_task_statuses(text)
                return
            if message_type == "catalog_check":
                delivery.send_catalog(text)
                return
            if message_type not in ("chat", "runtime_error"):
                return "不支持的消息类型。"
            delivery.dispatch(session.handle_inbound(message_type, text))
        def on_phone_connected():
            # Warm the same per-connection cache used by product reasoning, without blocking the reader.
            def inspect_phone():
                report = bridge.request_capabilities()
                audio = report.get('audioOutput', {})
                tts = report.get('offlineEnglishTts', {})
                print('phone capabilities: audio=%s, offlineEnglishTts=%s, reason=%s' %
                      (audio.get('status', 'unknown'), tts.get('status', 'unknown'),
                       tts.get('reasonCode', report.get('reasonCode', 'unknown'))), flush=True)
                if report.get('status') == 'observed':
                    (root / 'out' / 'phone-capabilities.json').write_text(
                        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            threading.Thread(target=inspect_phone, name='phone-capabilities', daemon=True).start()

        try:
            bridge.receive_from_phone(handle_apk, on_connected=on_phone_connected)
        finally:
            bridge.stop()
            downloads.stop()
        return

    artifact_root = root.parent / "phone_agent" / "data" / "plugins"
    downloads = DownloadServer(artifact_root, host=args.host, port=args.file_port)
    downloads.start()
    bridge = ConnectPhone(host=args.host, port=args.port)
    main_agent = MainAgent(
        bridge,
        downloads,
        state_path=root / "out" / "published_jobs.json",
        report_directory=root / "out" / "artifact_reports",
    )
    agent = ProductAgent(
        store=store,
        llm=llm,
        on_plugin=main_agent.publish_plugin,
        on_job=main_agent.publish_job,
    )

    def handle_inbound(message_type: str, text: str):
        if message_type == "chat":
            return agent.handle(text)
        if message_type == "artifact_report":
            return main_agent.handle_artifact_report(text)
        return "不支持的消息类型。"

    print("artifacts ->", artifact_root, flush=True)
    print("artifact download -> http://%s:%s/artifacts/<artifactId>" % (args.host, args.file_port), flush=True)
    try:
        bridge.receive_from_phone(
            on_inbound=handle_inbound,
            on_connected=main_agent.replay_published,
        )
    finally:
        bridge.stop()
        downloads.stop()


if __name__ == "__main__":
    main()
