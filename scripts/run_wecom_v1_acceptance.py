"""Controlled local acceptance for the WeCom V1 production proposal runner.

It never connects to WeCom.  Running it invokes the real proposal CLI once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.wecom.adapter import IncomingMessage, WeComAgentAdapter
from src.wecom.proposal_runner import ExistingProposalCliRunner
from src.wecom.store import SQLiteTaskStore
from src.wecom.task_service import TaskService


class RecordingTransport:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.sent: list[tuple[str, str]] = []
        self.files: list[tuple[str, Path]] = []

    async def reply_text(self, _frame: object, text: str) -> None:
        self.replies.append(text)

    async def send_text(self, chatid: str, text: str) -> None:
        self.sent.append((chatid, text))

    async def send_file(self, chatid: str, path: Path) -> None:
        self.files.append((chatid, path))


def _portable(path: Path | str | None) -> str | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        return candidate.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return candidate.name


def _report_markdown(result: dict) -> str:
    lines = ["# WeCom V1 Production ProposalRunner 验收", "", f"- 结论：`{result['status']}`", f"- task_id：`{result.get('task_id', 'N/A')}`", "", "## 检查结果", ""]
    for key, value in result["checks"].items():
        lines.append(f"- `{key}`：`{value}`")
    lines.extend(["", "## 说明", "", result["note"], ""])
    return "\n".join(lines)


async def run(args: argparse.Namespace) -> int:
    db_path = Path(args.db_path)
    output_root = Path(args.output_root)
    report_path = Path(args.report_path)
    store = SQLiteTaskStore(db_path)
    service = TaskService(store, ExistingProposalCliRunner(ROOT), output_root=output_root)
    transport = RecordingTransport()
    adapter = WeComAgentAdapter(service, transport)
    task_id = None
    try:
        await adapter.handle(IncomingMessage("acceptance-user", "acceptance-chat", args.request, "acceptance-message-1"), object())
        task = store.tasks_for("acceptance-user", "acceptance-chat")[0]
        task_id = task.task_id
        immediate_status = task.status.value
        await asyncio.to_thread(service.wait_for_idle)
        await asyncio.sleep(0)
        task = store.get(task.task_id)
        proposal = Path(task.proposal_md_path) if task.proposal_md_path else None
        proposal_text = proposal.read_text(encoding="utf-8") if proposal and proposal.is_file() else ""
        store.close()
        reopened = SQLiteTaskStore(db_path)
        persisted = reopened.get(task.task_id)
        reopened.close()
        checks = {
            "task_created": "PASS",
            "immediate_status_generating_md": "PASS" if immediate_status == "GENERATING_MD" else "FAIL",
            "ack_before_wait": "PASS" if transport.replies and task.task_id in transport.replies[0] else "FAIL",
            "proposal_md_exists": "PASS" if proposal and proposal.is_file() else "FAIL",
            "proposal_md_nonempty": "PASS" if proposal_text.strip() else "FAIL",
            "proposal_md_looks_like_markdown": "PASS" if proposal_text.lstrip().startswith("#") else "FAIL",
            "task_waiting_md_approval": "PASS" if task.status.value == "WAITING_MD_APPROVAL" else "FAIL",
            "completion_event_text": "PASS" if transport.sent else "FAIL",
            "completion_event_file": "PASS" if transport.files and transport.files[0][1] == proposal else "FAIL",
            "sqlite_reopen_consistent": "PASS" if persisted.status == task.status and persisted.proposal_md_path == task.proposal_md_path else "FAIL",
            "safe_user_error": "PASS" if not task.error_message else "NOT_APPLICABLE",
        }
        result = {
            "status": "PASS" if all(value in {"PASS", "NOT_APPLICABLE"} for value in checks.values()) else "FAILED",
            "task_id": task.task_id,
            "proposal_md_path": _portable(proposal),
            "checks": checks,
            "note": "真实 proposal CLI 仅调用一次；WeCom 使用内存 RecordingTransport，未发送网络消息。",
        }
    finally:
        service.shutdown()
        try:
            store.close()
        except Exception:
            pass
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_report_markdown(result), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("local-runner",), required=True)
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--report-path", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
