---
name: routine-cleanup
description: Bulk-delete old run sessions of a Claude Code scheduled task (routine) in the Claude desktop app, keeping the newest N and every run a human replied in. Use when the user asks to clean up, prune, or delete a routine's old runs or sessions, shrink a routine's Runs list, or reclaim the disk and memory old runs use. Run it from an ordinary conversation, not from inside a routine run.
argument-hint: "[routine name] [keep N]"
license: MIT
---

# routine-cleanup

Deletes past **run sessions of one scheduled task (routine)** in the Claude desktop
app, with safety rails. Two modes: bulk ("keep the newest N") and single run.

`cleanup_runs.py` (next to this file, at `${CLAUDE_SKILL_DIR}/cleanup_runs.py`; if that
variable is not expanded, use the skill's base directory shown when it loaded) only
PLANS. It never deletes. Deletion goes through the app's own tool,
`mcp__ccd_session_mgmt__delete_session` (load it with ToolSearch
`select:mcp__ccd_session_mgmt__delete_session` if it is deferred). Why:

- The app keeps its Runs list in memory and writes a run's record back when it saves
  or the run is clicked. Runs whose files are deleted directly come back as
  "Session not found on disk". Never delete run files yourself.
- `delete_session` accepts at most 25 ids per call and shows the user an approval card
  listing every session, so the planner prints ids in batches of 25.
- `delete_session` is unavailable inside scheduled-task runs. If this conversation is a
  routine run, plan only and tell the user to run the cleanup from a normal
  conversation.

## Safety rules

1. **Identify the routine exactly.** Run the planner with no arguments to list every
   routine (`scheduledTaskId`, title, run count). Match the user's words to one id; if
   more than one could fit, ask. Then always pass `--routine <exact id>`. The planner
   refuses inexact ids and prints suggestions; never guess between them.
2. **Never delete a run a human replied in** ("engaged") without a separate, explicit
   yes for that. Only then add `--include-engaged`.
3. **Runs whose transcript is gone** ("unverifiable") are protected by default. Add
   `--include-unverifiable` only when the user says they can go. A user can make that
   the default in `~/.claude/routine-cleanup.json`: `{"include_unverifiable": true}`.
4. **Show the plan and get a clear yes before deleting.** Deletion is permanent.
5. Pass `delete_session` only the exact ids the planner printed for the approved plan.
   Never set `force_worktree_cleanup`.
6. The current conversation and runs active in the last 15 minutes are never selected.

## What counts as a reply

The planner reads each run's transcript and counts messages a person typed after the
scheduled kickoff: plain messages and typed slash commands. It ignores the kickoff,
tool results, sidechain (subagent) messages, and harness-injected entries (`isMeta`
entries such as skill loads and loop prompts, `<system-reminder>`,
`<task-notification>`, `<local-command-*>`). Buckets:

- **autonomous**: no human reply. Selected.
- **engaged**: at least one human reply. Protected (rule 2).
- **unverifiable**: transcript gone. Protected unless rule 3 allows it.

## Flow

`P` below means `python3 ${CLAUDE_SKILL_DIR}/cleanup_runs.py`.

1. **Pick the routine.** If the user named one, still run `P` to list routines and map
   their words to an exact id (rule 1).
2. **Ask how many to keep** if they did not say (bulk), or which run (single).
3. **Plan.** `P --routine <id> --keep N` (or `--session <id-or-unique-prefix>`). For
   long plans add `--brief` for per-bucket counts and date ranges. Show the user the
   routine, the counts and sizes per bucket, what is protected, and the batch count.
4. **Approval.** Get a clear yes for exactly that plan. Mention it is permanent and
   that each batch of 25 will show an approval card in the app.
5. **Delete.** Re-run the same command with `--ids`. For each printed JSON array, call
   `mcp__ccd_session_mgmt__delete_session` with `session_ids` set to that array and a
   short `reason` such as "routine-cleanup: <id>, keep newest N". Several calls can be
   sent at once so the cards arrive together. Report what each call deleted and
   skipped (the app skips runs that are working, pinned, or open on screen).
6. **Verify.** Re-run the plan. Deleted runs no longer appear, and the app's Runs list
   has already updated; no restart needed.

## Examples

- "/routine-cleanup nightly-report keep 10": list routines, confirm `nightly-report`,
  `P --routine nightly-report --keep 10`, approve, `--ids`, delete per batch.
- "Delete just the 3am run from yesterday": `P --routine <id> --session <prefix>`,
  show it, approve, one `delete_session` call.
- "Also drop the ones I chatted in": get an explicit yes, add `--include-engaged`.

## Options

| Flag | Meaning |
| --- | --- |
| `--routine ID` | exact scheduledTaskId (omit to list routines) |
| `--keep N` / `--session ID` | bulk or single-run mode |
| `--include-engaged` | also select runs a human replied in |
| `--include-unverifiable` / `--exclude-unverifiable` | override the config default |
| `--live-window-minutes M` | skip runs active this recently (default 15) |
| `--brief` | counts and date ranges instead of one line per run |
| `--ids` | print id batches for `delete_session` |
| `--ids-file PATH` | also write the batches as JSON |
| `--json` | machine-readable plan |
