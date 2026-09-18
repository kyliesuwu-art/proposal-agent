# Artifact Evaluation Runbook

Run with no network or model access:

```powershell
uv run python scripts/evaluate_artifacts.py --profile client-delivery --docx proposal.docx --pptx proposal.pptx --output-dir reports
```

Use `internal-source` for `proposal.md` or `approved.md`; it permits `【待确认】` and provenance. Use `client-delivery` for customer files. `--strict` changes warnings from exit code 0 to 1. PASS is clean, PASS_WITH_WARNINGS needs human review, FAIL blocks delivery, and NOT_RUN denotes unavailable checks.

Reports contain input hashes, artifact metrics, each machine-readable check, bounded evidence and limitations. Add a check in the dedicated evaluator with a stable code, test it using a synthetic temporary file, and keep heuristics as WARNING unless there is deterministic corruption or a policy violation. Avoid false positives by using profile-specific rules.

V1 cannot determine rendered overflow, clipping, font fallback, genuine visual overlap, or aesthetics. Future rendered-page/Visual Critic integrations must be optional and must not replace deterministic checks. This evaluator is a quality gate, not an auto-repairer or an aesthetic judge.
