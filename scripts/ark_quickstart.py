# ark_quickstart.py
"""火山方舟 Managed Agents 连通性验证脚本（仅标准库，不依赖 jq / curl）。

两个用法：
    python scripts/ark_quickstart.py --check     # 只做自检：Key、服务开通、模型可用性
    python scripts/ark_quickstart.py             # 完整 4 步：Agent -> Environment -> Session -> SSE

Key 读取顺序：进程环境变量 ARK_API_KEY > 项目根目录 .env 里的 ARK_API_KEY。
本脚本不参与方案生成链路，不读写任何向量库，只做外部服务连通性验证。
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

# Windows 控制台默认 GBK，中文输出会炸，这里强制 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com"
DEFAULT_MODEL = "doubao-seed-2-1-pro-260628"
DEFAULT_ENV_NAME = "demo-env"
DEFAULT_PROMPT = "用 Python 编写一个脚本，生成前 20 个斐波那契数，并将其保存到 fibonacci.txt。"

# 单次 socket 读超时；SSE 靠它做空闲检测，整体另有 deadline
STREAM_READ_TIMEOUT = 60
HTTP_TIMEOUT = 60


class ArkError(RuntimeError):
    """带可读诊断信息的方舟调用失败。"""


def load_api_key() -> tuple[str, str]:
    """返回 (api_key, 来源说明)；读不到时抛 ArkError。"""
    key = os.environ.get("ARK_API_KEY", "").strip()
    if key:
        return key, "环境变量 ARK_API_KEY"

    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if name.strip() == "ARK_API_KEY":
                value = value.strip().strip("\"'")
                if value and not value.startswith("your_"):
                    return value, f"{env_file.name} 文件"

    raise ArkError(
        "找不到可用的 ARK_API_KEY。\n"
        f"  方式一：在项目根目录 {env_file_name()} 里加一行 ARK_API_KEY=你的Key\n"
        "  方式二：先执行 $env:ARK_API_KEY=\"你的Key\"（PowerShell）再运行本脚本\n"
        "  获取地址：https://console.volcengine.com/ark  ->  API Key 管理"
    )


def env_file_name() -> str:
    return str(PROJECT_ROOT / ".env")


def mask(key: str) -> str:
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}...{key[-4:]}(len={len(key)})"


def request_json(
    *,
    base_url: str,
    api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: int = HTTP_TIMEOUT,
) -> dict:
    """发一个 JSON 请求，返回解析后的 dict；失败时抛带诊断信息的 ArkError。"""
    url = f"{base_url.rstrip('/')}{path}"
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ArkError(_diagnose_http_error(exc.code, detail, path)) from exc
    except urllib.error.URLError as exc:
        raise ArkError(
            f"网络请求失败：{exc.reason}\n"
            f"  目标：{url}\n"
            "  检查：代理 / 防火墙是否放行 ark.cn-beijing.volces.com，或本机 DNS 是否正常。"
        ) from exc
    except socket.timeout:
        raise ArkError(f"请求超时（>{timeout}s）：{url}") from None
    except json.JSONDecodeError as exc:
        raise ArkError(f"响应不是合法 JSON：{exc}") from exc


def _diagnose_http_error(status: int, detail: str, path: str) -> str:
    """把常见状态码翻译成人能直接照做的下一步。"""
    head = f"HTTP {status} @ {path}\n  服务端返回：{detail[:600]}"
    low = detail.lower()

    # 方舟把"模型/服务未开通"也报成 400，必须优先识别，否则会被误导成参数问题
    if (
        "service not open" in low
        or "未开通" in detail
        or "modelnotopen" in low
        or "has not activated" in low
    ):
        if "endpoint" in low or "modelnotopen" in low or "has not activated" in low:
            return (
                f"{head}\n"
                "  处理建议：当前 --model 指定的模型尚未开通。\n"
                "  开通路径：方舟控制台 -> 开通管理 -> 模型服务，搜到该模型点“开通”，状态变为“运行中”后重试。\n"
                "  也可先换已开通模型：python scripts/ark_quickstart.py --model <模型ID>\n"
                "  查看模型列表：python scripts/ark_quickstart.py --list-models"
            )
        return (
            f"{head}\n"
            "  处理建议：相关服务尚未开通。\n"
            "  开通路径：方舟控制台 -> 开通管理（Managed Agents 页签 + 模型服务）。"
        )

    if "already exists" in low or "duplicate" in low or "重名" in detail:
        return f"{head}\n  处理建议：资源重名，换一个 --env-name 再试。"

    tips = {
        400: "参数不合法或资源冲突。若是环境重名，换一个 --env-name 再试。",
        401: "API Key 无效或已失效。重新在方舟控制台生成 Key 并更新。",
        403: "Key 有效但没有权限：多半是 Managed Agents 服务或模型服务未开通。\n"
             "  开通 Managed Agents：控制台 -> 开通管理 -> Managed Agents 页签\n"
             "  开通模型服务：控制台 -> 开通管理 -> 选择对应模型",
        404: "接口路径不存在，或该资源已被删除。确认 base-url 与资源 ID。",
        429: "触发限流。稍后重试，或到控制台查看额度。",
    }
    if status in tips:
        return f"{head}\n  处理建议：{tips[status]}"
    if status >= 500:
        return f"{head}\n  处理建议：方舟服务端异常，稍后重试；持续失败请提交工单。"
    return head


def stream_events(
    *,
    base_url: str,
    api_key: str,
    session_id: str,
    overall_timeout: int,
    verbose: bool,
) -> Iterator[dict]:
    """打开 SSE 流，逐个产出事件 dict。"""
    url = f"{base_url.rstrip('/')}/api/v3/sessions/{session_id}/events/stream"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        },
        method="GET",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=STREAM_READ_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ArkError(_diagnose_http_error(exc.code, detail, "/events/stream")) from exc
    except urllib.error.URLError as exc:
        raise ArkError(f"SSE 连接失败：{exc.reason}") from exc

    deadline = time.time() + overall_timeout
    with resp:
        while True:
            if time.time() > deadline:
                print(f"\n[超时] 超过 {overall_timeout}s 仍未收到 session.status_idle，停止等待。")
                return
            try:
                raw = resp.readline()
            except socket.timeout:
                # 单次读超时不算失败，只要整体没超 deadline 就继续等
                continue
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.startswith("data:"):
                if verbose and line:
                    print(f"  [sse:raw] {line[:200]}")
                continue
            payload = line[len("data:"):].strip()
            if not payload or payload == "[DONE]":
                if payload == "[DONE]":
                    return
                continue
            try:
                yield json.loads(payload)
            except json.JSONDecodeError:
                if verbose:
                    print(f"  [sse:unparsable] {payload[:200]}")


def probe_model(base_url: str, api_key: str, model: str) -> None:
    """用极小 token 数试调一次 chat/completions，确认模型已开通且可调用。"""
    request_json(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path="/api/v3/chat/completions",
        payload={
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        },
    )


def run_check(base_url: str, api_key: str, model: str) -> int:
    """只验证 Key 与服务开通状态，不创建任何资源。"""
    print(f"Base URL : {base_url}")
    print(f"API Key  : {mask(api_key)}")
    print(f"Model    : {model}")
    ok = True

    print("\n[1/3] 检查 Managed Agents 服务（GET /api/v3/agents）")
    try:
        data = request_json(base_url=base_url, api_key=api_key, method="GET", path="/api/v3/agents")
        items = data.get("data") or data.get("items") or []
        print(f"  通过。已有 Agent 数量：{len(items) if isinstance(items, list) else '未知'}")
    except ArkError as exc:
        ok = False
        print(f"  失败：{exc}")

    print("\n[2/3] 检查模型列表可读性（GET /api/v3/models）")
    try:
        data = request_json(base_url=base_url, api_key=api_key, method="GET", path="/api/v3/models")
        items = data.get("data") or data.get("items") or []
        names = [m.get("id") for m in items if isinstance(m, dict)] if isinstance(items, list) else []
        print(f"  通过。可访问模型数：{len(names)}")
        if names:
            preview = ", ".join(str(n) for n in names[:8])
            print(f"  示例：{preview}")
    except ArkError as exc:
        ok = False
        print(f"  失败：{exc}")

    print(f"\n[3/3] 检查模型可直接调用（POST /api/v3/chat/completions, {model}）")
    try:
        probe_model(base_url, api_key, model)
        print("  通过。模型已开通且可调用。")
    except ArkError as exc:
        ok = False
        print(f"  失败：{exc}")

    print("\n结论：" + ("自检通过，可以跑完整 quickstart。" if ok else "自检未通过，按上面建议处理后再跑。"))
    return 0 if ok else 1


def list_models(base_url: str, api_key: str, keyword: str = "") -> int:
    """列出账号可访问的模型 ID；注意列表不区分“是否已开通”。"""
    data = request_json(base_url=base_url, api_key=api_key, method="GET", path="/api/v3/models")
    items = data.get("data") or data.get("items") or []
    ids = [str(m.get("id")) for m in items if isinstance(m, dict) and m.get("id")]
    if keyword:
        ids = [i for i in ids if keyword.lower() in i.lower()]
    print(f"模型数量：{len(ids)}" + (f"（过滤关键词：{keyword}）" if keyword else ""))
    print("注意：该列表只代表模型存在，不代表已开通；未开通的模型调用时会报 service not open。")
    for model_id in ids:
        print(f"  {model_id}")
    return 0


def run_quickstart(args: argparse.Namespace, base_url: str, api_key: str) -> int:
    print(f"Base URL : {base_url}")
    print(f"API Key  : {mask(api_key)}")
    print(f"Model    : {args.model}\n")

    # Step 1: 创建 Agent
    print("[1/4] 创建 Agent ...")
    agent = request_json(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path="/api/v3/agents",
        payload={
            "name": "Quick Start Agent",
            "model": {"id": args.model},
            "system": "你是一个高效的编程助手，擅长代码编写和问题排查。",
            "tools": [{"type": "agent_toolset_20260701"}],
        },
    )
    agent_id = agent.get("id")
    if not agent_id:
        raise ArkError(f"创建 Agent 未返回 id：{json.dumps(agent, ensure_ascii=False)[:400]}")
    print(f"  Agent ID: {agent_id}")

    # Step 2: 创建环境（重名会 400，自动换个名字重试一次）
    print("[2/4] 创建 Environment ...")
    env_name = args.env_name
    try:
        environment = request_json(
            base_url=base_url,
            api_key=api_key,
            method="POST",
            path="/api/v3/environments",
            payload={
                "name": env_name,
                "config": {"type": "cloud", "networking": {"type": "unrestricted"}},
            },
        )
    except ArkError as exc:
        if "HTTP 400" not in str(exc):
            raise
        env_name = f"{args.env_name}-{int(time.time())}"
        print(f"  环境名重复，改用 {env_name} 重试 ...")
        environment = request_json(
            base_url=base_url,
            api_key=api_key,
            method="POST",
            path="/api/v3/environments",
            payload={
                "name": env_name,
                "config": {"type": "cloud", "networking": {"type": "unrestricted"}},
            },
        )
    environment_id = environment.get("id")
    if not environment_id:
        raise ArkError(f"创建 Environment 未返回 id：{json.dumps(environment, ensure_ascii=False)[:400]}")
    print(f"  Environment ID: {environment_id}")

    # Step 3: 创建会话
    print("[3/4] 创建 Session ...")
    session = request_json(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path="/api/v3/sessions",
        payload={
            "agent": agent_id,
            "environment_id": environment_id,
            "title": "Quickstart session",
        },
    )
    session_id = session.get("id")
    if not session_id:
        raise ArkError(f"创建 Session 未返回 id：{json.dumps(session, ensure_ascii=False)[:400]}")
    print(f"  Session ID: {session_id}")

    # Step 4: 发消息 + 收 SSE
    print("[4/4] 发送消息并接收流式响应 ...\n" + "-" * 60)
    request_json(
        base_url=base_url,
        api_key=api_key,
        method="POST",
        path=f"/api/v3/sessions/{session_id}/events",
        payload={
            "events": [
                {
                    "type": "user.message",
                    "content": [{"type": "text", "text": args.prompt}],
                }
            ]
        },
    )

    finished = False
    for event in stream_events(
        base_url=base_url,
        api_key=api_key,
        session_id=session_id,
        overall_timeout=args.timeout,
        verbose=args.verbose,
    ):
        etype = event.get("type", "")
        if etype == "agent.message":
            for block in event.get("content", []) or []:
                if block.get("type") == "text":
                    print(block.get("text", ""), end="", flush=True)
        elif etype == "agent.tool_use":
            print(f"\n[Using tool: {event.get('name', '?')}]", flush=True)
        elif etype == "session.status_idle":
            print("\n\nAgent finished.", flush=True)
            finished = True
            break
        elif args.verbose:
            print(f"\n[sse:{etype}] {json.dumps(event, ensure_ascii=False)[:300]}", flush=True)

    print("-" * 60)
    print(f"\n完成。资源 ID：agent={agent_id} environment={environment_id} session={session_id}")
    print("这些资源会保留在方舟控制台，可在控制台手动清理。")
    return 0 if finished else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="火山方舟 Managed Agents 连通性验证")
    parser.add_argument("--check", action="store_true", help="只验证 Key 与服务开通，不创建资源")
    parser.add_argument("--list-models", metavar="关键词", nargs="?", const="",
                        help="列出可访问模型 ID，可按关键词过滤（不加值则列全部）")
    parser.add_argument("--base-url", default=os.environ.get("ARK_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--model", default=os.environ.get("ARK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--env-name", default=DEFAULT_ENV_NAME, help="环境名，project 内需唯一")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=int, default=600, help="等待 Agent 完成的总秒数")
    parser.add_argument("--verbose", action="store_true", help="打印未处理的 SSE 事件原文")
    args = parser.parse_args()

    try:
        api_key, source = load_api_key()
    except ArkError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 1
    print(f"Key 来源：{source}")

    try:
        if args.list_models is not None:
            return list_models(args.base_url, api_key, args.list_models)
        if args.check:
            return run_check(args.base_url, api_key, args.model)
        return run_quickstart(args, args.base_url, api_key)
    except ArkError as exc:
        print(f"\n失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
