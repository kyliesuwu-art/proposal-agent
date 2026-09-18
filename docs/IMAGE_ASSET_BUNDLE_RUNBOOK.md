# Image Asset Bundle V2 Runbook

Build a V1 manifest first, then create a portable customer bundle:

```powershell
uv run python scripts/build_image_asset_bundle.py --markdown <task>\approved.md --task-root <task> --manifest <v1-report>\assets_manifest.json --output-dir <report-dir> --mode strict --zip
```

The command does not modify task content. It writes `bundle/`, an optional `approved_assets_bundle.zip`, and compact JSON/Markdown reports to the specified output directory. Use `strict` for delivery. `permissive` is diagnostics-only: unresolved image nodes remain in copied Markdown and the result is `PASS_WITH_WARNINGS`.

Do not pass a manifest from another task root. A V1 schema mismatch, missing/unsafe asset, symlink, SHA change, or decoded-format change stops strict mode before a final bundle directory is created.
