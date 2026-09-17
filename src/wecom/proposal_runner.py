from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol


class ProposalRunner(Protocol):
    def run(self, request: str, output_dir: Path) -> Path: ...


class ExistingProposalCliRunner:
    """Run the existing proposal CLI; it deliberately does not duplicate proposal logic."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    @staticmethod
    def _redact(text: str) -> str:
        for key_name in ("DASHSCOPE_API_KEY", "AIBOT_SECRET"):
            value = os.environ.get(key_name)
            if value:
                text = text.replace(value, "[REDACTED]")
        text = re.sub(r"(?im)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)
        text = re.sub(r"(?im)(cookie\s*:\s*)[^\r\n]+", r"\1[REDACTED]", text)
        text = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[REDACTED]", text)
        return text

    def run(self, request: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / "proposal.md"
        run_log = output_dir / "proposal.run.log"
        started_at = datetime.now().astimezone()
        completed = subprocess.run(
            [sys.executable, "src/main.py", "proposal", request, "--output", str(output), "--run-log", str(run_log)],
            cwd=self._project_root, check=False, capture_output=True, text=True,
        )
        finished_at = datetime.now().astimezone()
        (output_dir / "runner.stdout.log").write_text(self._redact(completed.stdout or ""), encoding="utf-8")
        (output_dir / "runner.stderr.log").write_text(self._redact(completed.stderr or ""), encoding="utf-8")
        (output_dir / "runner_result.json").write_text(json.dumps({
            "command_type": "proposal_cli",
            "returncode": completed.returncode,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_seconds": round((finished_at - started_at).total_seconds(), 6),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if completed.returncode:
            raise subprocess.CalledProcessError(completed.returncode, completed.args, output=completed.stdout, stderr=completed.stderr)
        if not output.is_file():
            raise RuntimeError("proposal CLI completed without proposal.md")
        return output
