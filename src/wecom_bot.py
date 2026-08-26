# wecom_bot.py
"""企业微信智能机器人长连接最小验证：收文本消息 → 原样回复 + 打印原始消息体。

用法：
    uv run python src/wecom_bot.py

功能：
    - 用 .env 里的 Bot ID + Secret 建立 WebSocket 长连接
    - 收到文本消息时，原样回复 "收到：{消息内容}"
    - 收到消息时打印完整的原始消息体（JSON），方便确认 from_userid 等字段格式
"""

import asyncio
import json
import os
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from wecom_aibot_sdk import WSClient, WSClientOptions, MessageType, generate_req_id

# 确保能 import src 下的模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _get_env(key: str) -> str:
    """从环境变量读取，不存在时退出并提示。"""
    val = os.environ.get(key)
    if not val:
        print(f"错误: 环境变量 {key} 未设置，请在 .env 文件中配置")
        sys.exit(1)
    return val


async def main() -> None:
    """建立长连接，注册消息回调，等待接收消息。"""
    bot_id = _get_env("AIBOT_BOT_ID")
    secret = _get_env("AIBOT_SECRET")

    opts = WSClientOptions(
        bot_id=bot_id,
        secret=secret,
        # 以下用默认值即可，需要时可调整
        reconnect_interval=1000,      # 重连间隔（毫秒）
        max_reconnect_attempts=10,    # 最大重连次数
        heartbeat_interval=30000,     # 心跳间隔（毫秒）
    )

    client = WSClient(opts)

    # 注册消息回调：收到任何消息类型都会触发
    async def on_message(frame) -> None:
        """处理收到的消息。"""
        body = frame.body
        msg_type = body.get("msgtype", "unknown")

        # 打印完整原始消息体（JSON），方便调试
        print("\n" + "=" * 60)
        print("收到原始消息体：")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        print("=" * 60)

        # 只处理文本消息，其他类型不回复
        if msg_type != MessageType.TEXT.value:
            print(f"收到非文本消息类型: {msg_type}，跳过回复")
            return

        # 提取关键字段（注意：实际消息体的字段嵌套在 from/text 对象里，
        # 不是扁平的 from_userid/content，见之前打印的原始消息体）
        from_obj = body.get("from", {})
        from_userid = from_obj.get("userid", "(未知)")
        chatid = body.get("chatid", "")
        content = body.get("text", {}).get("content", "")

        print(f"\nfrom.userid: {from_userid}")
        print(f"chatid: {chatid}")
        print(f"消息内容: {content}")

        # 用 reply_stream 回复（SDK 的 reply() 发纯 text 会报 40008 错误）
        reply_text = f"收到：{content}"
        stream_id = generate_req_id("stream")
        await client.reply_stream(frame, stream_id, reply_text, finish=True)
        print(f"已回复: {reply_text}")

    client.on("message", on_message)

    # 连接
    print(f"正在连接企微长连接 (bot_id={bot_id[:10]}...)...")
    await client.connect_async()
    print("已连接，等待消息...")

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
    client.disconnect()
    print("已断开连接")


if __name__ == "__main__":
    asyncio.run(main())
