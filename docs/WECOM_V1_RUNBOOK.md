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

## Known limitation

Production Runner and real WeCom communication have separately passed. A new combined real-WeCom clear-request-to-full-proposal-to-file test has not been run. Current readiness is `YES_WITH_LIMITATIONS`; V2 has not started.
