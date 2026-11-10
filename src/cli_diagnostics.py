"""CLI adapters for diagnostics commands."""

import json
import sys
from pathlib import Path

from src.render_pptx import RenderPptxError


def proposal_diagnose(context) -> None:
    diagnose_markdown_proposal = context.diagnose_markdown_proposal
    if len(sys.argv) not in {3, 5} or (len(sys.argv) == 5 and sys.argv[3] != "--output"):
        print('用法: python main.py proposal-diagnose <proposal.md> [--output <proposal.diagnosis.md>]', file=sys.stderr)
        sys.exit(1)
    try:
        output = diagnose_markdown_proposal(Path(sys.argv[2]), Path(sys.argv[4]) if len(sys.argv) == 5 else None)
    except Exception as exc:  # noqa: BLE001
        print(f"方案诊断失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"已写入诊断：{output.resolve()}")

    # 【生成 Word】消费已有方案 Markdown，离线渲染 DOCX，不重新检索或调用模型。


def audit_pptx(context) -> None:
    write_pptx_layout_audit = context.write_pptx_layout_audit
    import argparse
    parser = argparse.ArgumentParser(prog="python src/main.py audit-pptx", description="Write PPTX geometry audit. It reports visual rendering as BLOCKED until a renderer produces page PNGs.")
    parser.add_argument("--input", required=True); parser.add_argument("--plan", required=True); parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(sys.argv[2:])
    try:
        json_path, md_path = write_pptx_layout_audit(args.input, args.plan, args.output_dir)
    except RenderPptxError as exc:
        parser.error(str(exc))
    print(f"Visual audit JSON: {json_path}\nVisual audit Markdown: {md_path}")
