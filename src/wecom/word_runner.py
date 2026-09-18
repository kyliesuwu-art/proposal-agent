from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol
from zipfile import is_zipfile


@dataclass(frozen=True)
class WordResult:
    primary_artifact_path: Path
    preview_artifact_path: Path | None = None


class WordRunner(Protocol):
    def run(self, approved_md_path: Path, output_dir: Path, task_id: str) -> WordResult: ...


class ProductionWordRunner:
    """Invoke the established Word director CLI; it owns all Word rendering."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root

    @staticmethod
    def _redact(text: str) -> str:
        for key_name in ("ARK_API_KEY", "DASHSCOPE_API_KEY", "AIBOT_SECRET"):
            value = os.environ.get(key_name)
            if value:
                text = text.replace(value, "[REDACTED]")
        return re.sub(r"(?im)(authorization\s*:\s*bearer\s+)[^\s]+", r"\1[REDACTED]", text)

    def run(self, approved_md_path: Path, output_dir: Path, task_id: str) -> WordResult:
        output_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.now().astimezone()
        command = [
            sys.executable, "scripts/run_word_document_director.py",
            "--input-md", str(approved_md_path),
            "--output-dir", str(output_dir),
            "--task-id", task_id,
            "--client-delivery-editorial-v2",
        ]
        completed = subprocess.run(command, cwd=self._project_root, check=False, capture_output=True, text=True)
        finished = datetime.now().astimezone()
        (output_dir / "word_runner.stdout.log").write_text(self._redact(completed.stdout or ""), encoding="utf-8")
        (output_dir / "word_runner.stderr.log").write_text(self._redact(completed.stderr or ""), encoding="utf-8")
        (output_dir / "word_runner_result.json").write_text(json.dumps({
            "command_type": "word_document_director", "returncode": completed.returncode,
            "started_at": started.isoformat(), "finished_at": finished.isoformat(),
            "duration_seconds": round((finished - started).total_seconds(), 6),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        if completed.returncode:
            raise subprocess.CalledProcessError(completed.returncode, command)
        document = output_dir / "client_delivery_editorial_v2" / "proposal_client_delivery_editorial_v2.docx"
        if not document.is_file() or document.stat().st_size == 0 or not is_zipfile(document):
            raise RuntimeError("Word renderer completed without a valid DOCX")
        preview = output_dir / "client_delivery_editorial_v2" / "proposal_client_delivery_editorial_v2.pdf"
        return WordResult(document, preview if preview.is_file() else None)
