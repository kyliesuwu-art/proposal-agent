"""Recorded pre-refactor CLI help, errors, exit codes and guarded dispatch."""
import json
from pathlib import Path
import sys

import pytest
from src import main

BASELINE = json.loads((Path(__file__).with_name("cli_baseline_2a.json")).read_text(encoding="utf-8"))

@pytest.mark.parametrize("case", BASELINE, ids=lambda case: " ".join(case["args"]) or "no-args")
def test_legacy_cli_contract(case, monkeypatch, capsys):
    class Guard(Exception):
        pass
    def guard(*args, **kwargs):
        raise Guard("dispatch blocked")
    monkeypatch.setattr(main.pipeline, "query_rag", guard)
    monkeypatch.setattr(main, "diagnose_markdown_proposal", guard)
    monkeypatch.setattr(sys, "argv", ["src/main.py", *case["args"]])
    code = 0
    try:
        main.main()
    except SystemExit as exc:
        code = exc.code
    except Guard:
        code = "dispatch blocked"
    output = capsys.readouterr()
    assert (code, output.out, output.err) == (case["code"], case["stdout"], case["stderr"])
