# Artifact Evaluation Framework V1

## Goal

This is a read-only, deterministic quality gate for Markdown, DOCX, PPTX and optional PDF inputs. It produces JSON and Markdown reports with bounded evidence, and never calls a model, network service, renderer, database, or generator.

## Scope and status

Profiles are `internal-source` (allows confirmation markers and provenance) and `client-delivery` (rejects internal labels, local paths and provenance chains). Checks are `PASS`, `PASS_WITH_WARNINGS`, `FAIL`, or `NOT_RUN`; skipped visual checks are never PASS. `ERROR` or FAIL makes the overall result FAIL; warnings make it PASS_WITH_WARNINGS.

The stable JSON schema contains `EvaluationReport`, `ArtifactReport`, and `CheckResult` fields described by the Python dataclasses: status, severity, code, message, bounded evidence/location, metrics, hashes, sizes and limitations. Severity is INFO/WARNING/ERROR.

## Checks and limitations

OOXML preflight runs before high-level parsers. Defaults limit archives to 4,096 members, 64 MiB/member, 256 MiB total, and 100:1 compression ratio; members are streamed in 64 KiB chunks for CRC and actual-byte validation. Encryption, unsupported compression, duplicate normalized names and unsafe paths are rejected as FAIL. These are resource limits, not a complete malicious-file sandbox.

Markdown checks UTF-8, headings, empty sections, image syntax/path containment/files, and delivery leaks. DOCX checks OOXML, readable document content, headings/tables/media, A4 metadata and text residue. PPTX checks OOXML, slides, blank pages, canvas bounds, image PPI, small fonts and layout repetition. Cross checks currently record input coverage and optional source hash.

V1 is not an auto-fixer, semantic reviewer, or aesthetic judge. Without trusted rendered page images, true overflow, clipping, collisions, font replacement, page breaks and image distortion are `NOT_RUN`. A future optional Visual Critic must supplement—not replace—these deterministic gates.
