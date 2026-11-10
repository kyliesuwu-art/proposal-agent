"""方案运行日志和请求记录。"""

import json
from datetime import datetime
from pathlib import Path


def _write_proposal_request(output_path: Path, request: str, *, run_log: Path, debug: bool) -> Path:
    """在方案成功写入后，保存用户需求和可复现运行元数据。

    输入是方案输出路径、用户需求和运行日志路径；返回写出的 JSON 文件路径。
    """
    path = output_path.with_name("proposal.request.json")
    path.write_text(json.dumps({
        "request": request,
        "generated_at": datetime.now().astimezone().isoformat(),
        "output_file": output_path.name,
        "sources_file": "proposal.sources.json",
        "run_log": run_log.name,
        "debug": debug,
        "cli_arguments": {"debug": debug, "run_log": run_log.name},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _write_proposal_run_log(path: Path, *, status: str, stage: str, **details: object) -> None:
    """记录方案生成的阶段状态，供失败诊断和后续追踪使用。

    日志只保存状态和结构化元数据，不保存提示词或凭据；由 ``proposal`` 分支在关键阶段调用。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().isoformat()
    try:
        previous = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        previous = {}
    event = {"status": status, "stage": stage, "updated_at": timestamp, **details}
    events = previous.get("events", []) if isinstance(previous, dict) else []
    if not isinstance(events, list):
        events = []
    payload = {**previous, **event, "events": [*events, event]}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
