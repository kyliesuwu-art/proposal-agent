"""Small, redacted SSE diagnostics for the paused V4 Art Director experiment."""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "outputs/ppt_style_v3/slides.final.md"
OUT = ROOT / "outputs/ppt_style_v4/art_director_probe_report.md"
MODEL = "doubao-seed-2-1-pro-260628"


def env() -> dict[str, str]:
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("\"'")
    return values


def pages(count: int) -> str:
    pieces = [item.strip() for item in SOURCE.read_text(encoding="utf-8").split("\n---\n") if item.strip()]
    return "\n---\n".join(pieces[:count])


def probe(count: int) -> dict:
    values = env(); key = values.get("ARK_API_KEY", "")
    base = values.get("ARK_BASE_URL", "https://ark.cn-beijing.volces.com").removesuffix("/api/v3")
    prompt = ("你是企业 Presentation Art Director。仅为给定的 1–3 页内容输出极简视觉建议，每页不超过三行。"
              "不得改写事实或输出 JSON。")
    body = {"model": MODEL, "thinking": {"type": "enabled"}, "stream": True, "max_tokens": 1200,
            "messages": [{"role": "system", "content": prompt}, {"role": "user", "content": pages(count)}]}
    request = urllib.request.Request(base + "/api/v3/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                     headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    started = time.perf_counter(); first = None; chars = 0; events = 0; done = False; error = None; status = None
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            status = response.status
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                value = line[5:].strip()
                if value == "[DONE]":
                    done = True; break
                try:
                    delta = (json.loads(value).get("choices") or [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if isinstance(content, list): content = "".join(x.get("text", "") for x in content if isinstance(x, dict))
                    if content:
                        if first is None: first = time.perf_counter() - started
                        chars += len(content)
                    events += 1
                except json.JSONDecodeError:
                    error = "SSE_JSON_PARSE_ERROR"; break
    except urllib.error.HTTPError as exc:
        status = exc.code; error = "HTTP_ERROR"
    except urllib.error.URLError as exc:
        error = f"NETWORK_ERROR:{type(exc.reason).__name__}"
    except socket.timeout:
        error = "TIMEOUT"
    except OSError as exc:
        error = f"OS_ERROR:{type(exc).__name__}"
    return {"page_count": count, "http_status": status, "ttft_seconds": round(first, 2) if first else None,
            "elapsed_seconds": round(time.perf_counter() - started, 2), "sse_events": events, "done_event": done,
            "accumulated_characters": chars, "exception": error, "final_parse": "PASS" if done and chars else "FAIL"}


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("# Art Director SSE 最小探针\n\nSTATUS: STARTED\n", encoding="utf-8")
    results = [probe(1)]
    OUT.write_text("# Art Director SSE 最小探针\n\nSTATUS: ONE_PAGE_FINISHED\n\n```json\n" + json.dumps(results, ensure_ascii=False, indent=2) + "\n```\n", encoding="utf-8")
    results.append(probe(3))
    lines = ["# Art Director SSE 最小探针", "", "- 模型：`doubao-seed-2-1-pro-260628`；thinking=`enabled`；stream=`true`", "- Prompt 与页面内容不写入报告；未记录 API Key、Authorization 或 `.env`。", "", "| 页面数 | HTTP | TTFT(s) | elapsed(s) | SSE events | DONE | chars | exception | parse |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for item in results:
        lines.append(f"| {item['page_count']} | {item['http_status']} | {item['ttft_seconds']} | {item['elapsed_seconds']} | {item['sse_events']} | {item['done_event']} | {item['accumulated_characters']} | {item['exception'] or '-'} | {item['final_parse']} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False))


if __name__ == "__main__":
    main()
