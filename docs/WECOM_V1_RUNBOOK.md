# WeCom V1 Runbook

## Normal production

```powershell
uv run python src/wecom_bot.py
```

This prints `PROPOSAL_ENABLED`. Clear requests can incur model cost because they start the existing proposal CLI. Use only `runtime_data/wecom_agent.sqlite` and `outputs/wecom_tasks/` for normal production.

## Communication-only live testing

```powershell
uv run python src/wecom_bot.py --disable-proposal --db-path runtime_data/wecom_agent_live.sqlite --output-root outputs/wecom_live
```

This prints `PROPOSAL_DISABLED`. Use new, isolated paths for every acceptance run. It exercises WebSocket and state handling but does not call the ProposalRunner or any model. Do not pass a clear request to normal mode merely to test communication.

## Operations

- Stop safely with Ctrl+C; the service closes its executor, SQLite connection, and WebSocket.
- Inspect an individual task only through its isolated task directory and SQLite state; do not publish raw logs or identities.
- A failed Task stays `FAILED`; inspect its safe `error_stage` and Task ID. Do not automatically rerun it.
- After restart, an interrupted `GENERATING_MD` Task becomes `FAILED` with `interrupted_restart`; it is not resumed.
- Use `scripts/run_wecom_v1_acceptance.py --help` before running the local production-runner acceptance. That script invokes a real proposal and can incur model cost.

## Word delivery and controls

An `MD_APPROVED` Task can receive `生成Word`; the Word work is a separate persisted ArtifactJob and never changes the approved content Task to `FAILED`. `重发Word` sends only an existing successful DOCX and does not run a renderer or model. See [WECOM_COMMANDS.md](WECOM_COMMANDS.md) for the complete command matrix.

For an isolated communication-only Word control test, retain `--disable-proposal` and pass isolated `--db-path`, `--output-root`, and `--artifact-output-root` values. Do not use it to regenerate a proposal.

## PPT production and recovery

PPT model generation remains disabled by default. A fresh model-backed V4 run requires the existing explicit enable flag, `PPT_MODEL_LIVE_APPROVED=1`, an approved producer argv, a positive call budget, and an explicit visual-reference root. Do not set a historical Job, Task, user, chat ID, or customer content as a default.

For a zero-model V4 re-render, provide an explicit completed checkpoint root with `--ppt-v4-offline-source-root` and an explicit visual-reference root. The runner verifies the approved Markdown hash and all 20 canonical Scene Graph hashes, keeps the canonical checkpoint unchanged, writes a `client_scene_graphs` derivative for drawing, evaluates canonical Markdown under `internal-source`, and evaluates only the sent PPTX under `client-delivery`. A `FAIL` result blocks the adapter's send event; warnings remain in the evaluation report.

PPT功能链路已有全新设计成功和修复后正式交付的真实证据；修复后的单次全新自动生成到交付验收尚未完成。

The recorded work for the 2026-10-12 evidence run took about 63 minutes; it has not met a 20–30 minute target. Customer receipt of a file is delivery evidence, not evidence that the customer opened or confirmed it.
