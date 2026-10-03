# Changelog

## 1.1.0 (2026-10-03)

- README: recommends raising `cleanupPeriodDays` (default 30 days) so Claude Code's age-based sweep stops deleting history, and pruning routine runs with this skill instead. Explains why older desktop runs show up as "unverifiable".
- Planner prints a RETENTION reminder (and `retentionNotes` in `--json`) when `cleanupPeriodDays` or `desktopSessionCleanupPeriodDays` is under a year.
- README: documents the 25-runs-per-approval limit of the app's delete tool and how to live with it.
- Size figures now include each run's checkpoint snapshots (`file-history/`).

## 1.0.0 (2026-10-03)

First public release.

- Plans bulk (`--keep N`) or single-run (`--session`) cleanup of one scheduled task's runs.
- Deletes through the desktop app's `delete_session` tool in batches of 25, so the Runs list stays in sync.
- Protects runs a human replied in; ignores skill loads, tool output, sidechains and harness notifications when deciding.
- Protects runs whose transcript is gone unless `--include-unverifiable` or `~/.claude/routine-cleanup.json` allows it.
- Refuses inexact routine ids and ambiguous session prefixes, with suggestions.
- Skips the current conversation and runs active in the last 15 minutes.
- `--brief`, `--json`, `--ids`, `--ids-file` outputs; `CLAUDE_CONFIG_DIR` and `ROUTINE_CLEANUP_APP_DIR` overrides.
