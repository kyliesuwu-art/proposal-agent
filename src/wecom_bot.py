# wecom_bot.py
"""企业微信智能机器人长连接 V1：需求澄清、异步 Markdown 生成与确认。

用法：
    uv run python src/wecom_bot.py

功能：
    - 用 .env 里的 Bot ID + Secret 建立 WebSocket 长连接
    - 文本消息交给独立的 WeCom V1 task service
    - 使用现有 proposal CLI 生成独立 task 目录内的 Markdown
"""

import argparse
import asyncio
import json
import os
import re
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv

from wecom_aibot_sdk import WSClient, WSClientOptions, MessageType

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.wecom.adapter import IncomingMessage, SDKTransport, WeComAgentAdapter
from src.wecom.artifact_service import ArtifactService
from src.wecom.control_service import TaskControlService
from src.wecom.proposal_runner import ExistingProposalCliRunner
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_service import TaskService
from src.wecom.word_runner import ProductionWordRunner
from src.wecom.ppt_runner import ProductionPptRunner


def load_runtime_environment(env_file: Path | None) -> None:
    """Load an optional env file without overriding an injected process env."""
    candidate = env_file or (PROJECT_ROOT / ".env")
    if candidate.is_file():
        load_dotenv(dotenv_path=candidate, override=False)


def build_ppt_runner_from_args(args: argparse.Namespace, *, environ: dict[str, str] | None = None) -> ProductionPptRunner:
    """The sole bot entry point for PPT runner configuration."""
    if not args.enable_ppt_model:
        return ProductionPptRunner(PROJECT_ROOT)
    env = os.environ if environ is None else environ
    if env.get("PPT_MODEL_LIVE_APPROVED") != "1":
        raise ValueError("--enable-ppt-model requires PPT_MODEL_LIVE_APPROVED=1")
    if not args.ppt_model_scene_command_json:
        raise ValueError("--enable-ppt-model requires --ppt-model-scene-command-json")
    try:
        producer = json.loads(args.ppt_model_scene_command_json)
    except json.JSONDecodeError as exc:
        raise ValueError("--ppt-model-scene-command-json must be a JSON argv list") from exc
    if not isinstance(producer, list) or not producer or not all(isinstance(item, str) and item for item in producer):
        raise ValueError("--ppt-model-scene-command-json must be a non-empty argv list")
    allowed = {"${INPUT_MD}", "${TASK_ROOT}", "${OUTPUT_DIR}", "${SCENE_DIR}", "${MODEL_MAX_CALLS}"}
    unknown = {token for item in producer for token in re.findall(r"\$\{[^}]+\}", item)} - allowed
    if unknown:
        raise ValueError("producer argv contains an unknown placeholder")
    if not any("${MODEL_MAX_CALLS}" in item for item in producer):
        raise ValueError("producer argv must include ${MODEL_MAX_CALLS}")
    if not isinstance(args.ppt_model_max_calls, int) or args.ppt_model_max_calls <= 0:
        raise ValueError("--enable-ppt-model requires a positive --ppt-model-max-calls")
    return ProductionPptRunner(PROJECT_ROOT, model_enabled=True, model_scene_command=producer, model_max_calls=args.ppt_model_max_calls)


def _get_env(key: str) -> str:
    """从环境变量读取，不存在时退出并提示。"""
    val = os.environ.get(key)
    if not val:
        print(f"错误: 环境变量 {key} 未设置，请在 .env 文件中配置")
        sys.exit(1)
    return val


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WeCom V1 WebSocket bot")
    parser.add_argument(
        "--db-path",
        type=Path,
        default=PROJECT_ROOT / "runtime_data" / "wecom_agent.sqlite",
        help="SQLite task database path",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "wecom_tasks",
        help="per-task output directory",
    )
    parser.add_argument(
        "--artifact-output-root",
        type=Path,
        default=PROJECT_ROOT / "outputs" / "wecom_artifacts",
        help="per-artifact output directory",
    )
    parser.add_argument(
        "--disable-proposal",
        action="store_true",
        help="accept and clarify messages without starting the proposal runner",
    )
    parser.add_argument("--env-file", type=Path, help="optional credential file; process environment values retain precedence")
    parser.add_argument("--enable-ppt-model", action="store_true", help="explicitly enable the guarded live PPT model path")
    parser.add_argument("--ppt-model-scene-command-json", help="JSON argv list for the approved Scene Graph producer")
    parser.add_argument("--ppt-model-max-calls", type=int, help="positive Ark-call budget for the producer")
    parser.add_argument("--proactive-markdown-chatid", help="confirmed live-test chat ID")
    parser.add_argument("--proactive-markdown", help="one-time live-test Markdown content")
    parser.add_argument("--proactive-file-chatid", help="confirmed live-test chat ID")
    parser.add_argument("--proactive-file-path", type=Path, help="one-time live-test file path")
    args = parser.parse_args(argv)
    if bool(args.proactive_markdown_chatid) != bool(args.proactive_markdown):
        parser.error("--proactive-markdown-chatid and --proactive-markdown must be used together")
    if bool(args.proactive_file_chatid) != bool(args.proactive_file_path):
        parser.error("--proactive-file-chatid and --proactive-file-path must be used together")
    return args


def run_mode(disable_proposal: bool) -> str:
    return "PROPOSAL_DISABLED" if disable_proposal else "PROPOSAL_ENABLED"


async def main(
    *,
    db_path: Path,
    output_root: Path,
    artifact_output_root: Path | None = None,
    disable_proposal: bool = False,
    proactive_markdown_chatid: str | None = None,
    proactive_markdown: str | None = None,
    proactive_file_chatid: str | None = None,
    proactive_file_path: Path | None = None,
    ppt_runner: ProductionPptRunner | None = None,
) -> None:
    """建立长连接，注册消息回调，等待接收消息。"""
    bot_id = _get_env("AIBOT_BOT_ID")
    secret = _get_env("AIBOT_SECRET")
    print(f"运行模式：{run_mode(disable_proposal)}")

    opts = WSClientOptions(
        bot_id=bot_id,
        secret=secret,
        # 以下用默认值即可，需要时可调整
        reconnect_interval=1000,      # 重连间隔（毫秒）
        max_reconnect_attempts=10,    # 最大重连次数
        heartbeat_interval=30000,     # 心跳间隔（毫秒）
    )

    client = WSClient(opts)
    store = SQLiteTaskStore(db_path)
    service = TaskService(
        store,
        ExistingProposalCliRunner(PROJECT_ROOT),
        output_root=output_root,
        generation_enabled=not disable_proposal,
    )
    artifacts = ArtifactService(
        store, ProductionWordRunner(PROJECT_ROOT), ppt_runner or ProductionPptRunner(PROJECT_ROOT), task_output_root=output_root,
        output_root=artifact_output_root or PROJECT_ROOT / "outputs" / "wecom_artifacts",
    )
    adapter = WeComAgentAdapter(service, SDKTransport(client), artifacts, TaskControlService(store, artifacts))
    authenticated = asyncio.Event()

    async def on_authenticated(_frame) -> None:
        authenticated.set()

    # 注册消息回调：收到任何消息类型都会触发
    async def on_message(frame) -> None:
        """处理收到的消息。"""
        body = frame.body
        msg_type = body.get("msgtype", "unknown")

        # SDK message shape: from.userid, chatid, msgid, text.content, file.url/aeskey/name.
        from_obj = body.get("from", {})
        from_userid = from_obj.get("userid", "(未知)")
        chatid = body.get("chatid") or from_userid
        message_id = body.get("msgid")
        message = IncomingMessage(from_userid, chatid, body.get("text", {}).get("content", ""), message_id)
        if msg_type == MessageType.TEXT.value:
            await adapter.handle(message, frame)
            return
        if msg_type == MessageType.FILE.value:
            file_info = body.get("file", {})
            url, aeskey = file_info.get("url"), file_info.get("aeskey")
            if not url:
                await adapter.handle_markdown_upload(message, "", b"", frame)
                return
            content, filename = await client.download_file(url, aeskey)
            await adapter.handle_markdown_upload(message, filename or "upload.md", content, frame)
            return
        await adapter.reply(frame, "请发送文字需求，或在 Markdown 生成后上传 .md 文件进行确认。")

    client.on("message", on_message)
    client.on("authenticated", on_authenticated)

    # 连接
    print("正在连接企微长连接...")
    await client.connect_async()
    print("已连接，等待消息...")
    if proactive_markdown_chatid and proactive_markdown:
        try:
            await asyncio.wait_for(authenticated.wait(), timeout=30)
            await SDKTransport(client).send_text(proactive_markdown_chatid, proactive_markdown)
            print("主动 Markdown 联调消息已发送")
        except TimeoutError:
            print("主动 Markdown 联调未发送：认证超时")
    if proactive_file_chatid and proactive_file_path:
        try:
            await asyncio.wait_for(authenticated.wait(), timeout=30)
            await SDKTransport(client).send_file(proactive_file_chatid, proactive_file_path)
            print("主动文件联调消息已发送")
        except TimeoutError:
            print("主动文件联调未发送：认证超时")

    # 等待 Ctrl+C 退出
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\n收到退出信号，断开连接...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows 下 add_signal_handler 不支持 SIGTERM
            pass

    await stop_event.wait()
    service.shutdown()
    artifacts.shutdown()
    store.close()
    await client.disconnect()
    print("已断开连接")


if __name__ == "__main__":
    args = parse_args()
    load_runtime_environment(args.env_file)
    try:
        configured_ppt_runner = build_ppt_runner_from_args(args)
    except ValueError as exc:
        raise SystemExit(f"PPT live configuration error: {exc}") from exc
    print(f"PPT_MODEL_ENABLED: {'TRUE' if configured_ppt_runner.model_enabled else 'FALSE'}")
    print(f"PPT_MODEL_SCENE_COMMAND_CONFIGURED: {'TRUE' if bool(configured_ppt_runner.model_scene_command) else 'FALSE'}")
    print(f"PPT_MODEL_MAX_CALLS: {configured_ppt_runner.model_max_calls or 0}")
    print(f"PPT_MODEL_LIVE_GATE: {'PASS' if configured_ppt_runner.model_enabled else 'NOT_REQUESTED'}")
    asyncio.run(main(
        db_path=args.db_path,
        output_root=args.output_root,
        artifact_output_root=args.artifact_output_root,
        disable_proposal=args.disable_proposal,
        proactive_markdown_chatid=args.proactive_markdown_chatid,
        proactive_markdown=args.proactive_markdown,
        proactive_file_chatid=args.proactive_file_chatid,
        proactive_file_path=args.proactive_file_path,
        ppt_runner=configured_ppt_runner,
    ))
