# Changelog

## 1.0.0 (2026-10-03)

First public release.

- Plans bulk (`--keep N`) or single-run (`--session`) cleanup of one scheduled task's runs.
- Deletes through the desktop app's `delete_session` tool in batches of 25, so the Runs list stays in sync.
- Protects runs a human replied in; ignores skill loads, tool output, sidechains and harness notifications when deciding.
- Protects runs whose transcript is gone unless `--include-unverifiable` or `~/.claude/routine-cleanup.json` allows it.
- Refuses inexact routine ids and ambiguous session prefixes, with suggestions.
- Skips the current conversation and runs active in the last 15 minutes.
- `--brief`, `--json`, `--ids`, `--ids-file` outputs; `CLAUDE_CONFIG_DIR` and `ROUTINE_CLEANUP_APP_DIR` overrides.
