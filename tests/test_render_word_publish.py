"""DOCX and its validation report must remain one recoverable delivery."""

import importlib
from pathlib import Path

import pytest


word = importlib.import_module("src.render_word")


@pytest.mark.parametrize("previous_delivery", [False, True])
def test_report_publish_failure_restores_word_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, previous_delivery: bool,
) -> None:
    markdown = tmp_path / "proposal.md"
    output = tmp_path / "proposal.docx"
    report = output.with_suffix(".render_report.json")
    markdown.write_text("# Earlier proposal\n\nEarlier content.\n", encoding="utf-8")
    if previous_delivery:
        word.render_word(markdown, output)
    old_docx = output.read_bytes() if previous_delivery else None
    old_report = report.read_bytes() if previous_delivery else None
    markdown.write_text("# New proposal\n\nNew content.\n", encoding="utf-8")

    real_replace = word.os.replace

    def fail_report(source, destination):
        if Path(destination) == report and Path(source).name == report.name:
            raise OSError("injected report failure")
        return real_replace(source, destination)

    monkeypatch.setattr(word.os, "replace", fail_report)
    with pytest.raises(word.RenderWordError, match="publication failed"):
        word.render_word(markdown, output)

    assert output.exists() is previous_delivery
    assert report.exists() is previous_delivery
    if previous_delivery:
        assert output.read_bytes() == old_docx
        assert report.read_bytes() == old_report
