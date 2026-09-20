from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zipfile import is_zipfile


@dataclass(frozen=True)
class PptResult:
    primary_artifact_path: Path
    preview_artifact_path: Path | None = None
    contact_sheet_path: Path | None = None
    evaluation_status: str = "NOT_RUN"
    warnings: tuple[str, ...] = ()


class PptRunner(Protocol):
    def run(
        self,
        approved_md_path: Path,
        task_root: Path,
        output_dir: Path,
        task_id: str,
        job_id: str,
    ) -> PptResult: ...


class ProductionPptRunner:
    """Launch the parameterized PPT production CLI and retain redacted evidence."""

    def __init__(
        self,
        project_root: Path,
        *,
        existing_scene_dir: Path | None = None,
        target_slides: int | None = 20,
        visual_critic: bool = False,
        max_revisions: int = 0,
        model_enabled: bool = False,
        model_scene_command: str | None = None,
    ) -> None:
        self._project_root = project_root
        self._existing_scene_dir = existing_scene_dir
        self._target_slides = target_slides
        self._visual_critic = visual_critic
        self._max_revisions = max_revisions
        self._model_enabled = model_enabled
        self._model_scene_command = model_scene_command
        if model_enabled and not model_scene_command:
            raise ValueError("model_scene_command is required when model_enabled is true")

    @staticmethod
    def _redact(text: str) -> str:
        for key_name in ("ARK_API_KEY", "DASHSCOPE_API_KEY", "AIBOT_SECRET"):
            value = os.environ.get(key_name)
            if value:
                text = text.replace(value, "[REDACTED]")
        return re.sub(r"(?im)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)

    def run(self, approved_md_path: Path, task_root: Path, output_dir: Path, task_id: str, job_id: str) -> PptResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        output_pptx = output_dir / "proposal.pptx"
        run_log = output_dir / "ppt_runner.run.log"
        command = [
            sys.executable,
            "scripts/run_ppt_production.py",
            "--input-md", str(approved_md_path),
            "--task-root", str(task_root),
            "--output-dir", str(output_dir),
            "--output-pptx", str(output_pptx),
            "--run-log", str(run_log),
            "--job-id", job_id,
            "--task-id", task_id,
            "--max-revisions", str(self._max_revisions),
        ]
        if self._model_enabled:
            command.extend(("--enable-model", "--model-scene-command", self._model_scene_command or ""))
        else:
            # Offline and ordinary local re-renders may only consume an existing
            # Scene Graph.  This makes accidental model invocation impossible.
            command.append("--disable-model")
        if self._target_slides is not None:
            command.extend(("--target-slides", str(self._target_slides)))
        if self._existing_scene_dir:
            command.extend(("--existing-scene-dir", str(self._existing_scene_dir)))
        if self._visual_critic:
            command.append("--enable-visual-critic")
        started = datetime.now().astimezone()
        completed = subprocess.run(command, cwd=self._project_root, check=False, capture_output=True, text=True)
        finished = datetime.now().astimezone()
        (output_dir / "ppt_runner.stdout.log").write_text(self._redact(completed.stdout or ""), encoding="utf-8")
        (output_dir / "ppt_runner.stderr.log").write_text(self._redact(completed.stderr or ""), encoding="utf-8")
        result_path = output_dir / "ppt_runner_result.json"
        result_path.write_text(json.dumps({
            "command_type": "ppt_production", "returncode": completed.returncode,
            "started_at": started.isoformat(), "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 6),
            "model_enabled": self._model_enabled,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if completed.returncode:
            raise subprocess.CalledProcessError(completed.returncode, command)
        if not output_pptx.is_file() or output_pptx.stat().st_size == 0 or not is_zipfile(output_pptx):
            raise RuntimeError("PPT renderer completed without a valid PPTX")
        report_path = output_dir / "artifact_evaluation" / "evaluation_report.json"
        if not report_path.is_file():
            raise RuntimeError("PPT renderer completed without an artifact evaluation report")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        status = str(report.get("overall_status", "NOT_RUN"))
        if status == "FAIL":
            raise RuntimeError("PPT artifact evaluation failed")
        pdf = output_dir / "proposal.pdf"
        contact = output_dir / "contact_sheet.png"
        warnings = tuple(str(x) for x in report.get("limitations", []))
        return PptResult(
            output_pptx,
            pdf if pdf.is_file() else None,
            contact if contact.is_file() else None,
            status,
            warnings,
        )
