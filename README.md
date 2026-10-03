# Routine Cleanup for Claude Code

Scheduled tasks (routines) in the Claude desktop app leave a session behind every
time they run. An hourly routine makes 700+ sessions a month. They pile up in the
routine's Runs list, take disk space, and slow the app down when it loads them.
Until now, the only way to remove them was one click at a time.

**Routine Cleanup** is a Claude Code skill that prunes a routine's old runs in bulk:

- **Keeps the newest N runs** you choose.
- **Never touches a run you replied in.** It reads each transcript and only
  counts what a person actually typed, not skill loads, tool output, or background
  notifications.
- **Shows the full plan first** and deletes nothing until you approve it.
- **Deletes through the desktop app itself**, so the Runs list updates
  immediately, instead of leaving "Session not found on disk" ghosts behind.

```
Routine        : Nightly report
scheduledTaskId: nightly-report
Total runs     : 618

Plan (keeping newest 4):

AUTONOMOUS (no human reply)       332 runs   403.3 MB  WILL DELETE  2026-07-16 .. 2026-10-02
ENGAGED (a human replied)          43 runs   120.0 MB  PROTECTED (needs --include-engaged)
UNVERIFIABLE (transcript gone)    239 runs    47.2 MB  PROTECTED (needs --include-unverifiable)

Selected for deletion: 332 runs, 403.3 MB, in 14 batches of <=25

PLAN ONLY: nothing was deleted. Deletion goes through the app's delete_session tool.
```

## Install

**As a plugin** (Claude Code CLI or the desktop app's Code tab):

```
/plugin marketplace add naw103/claude-routine-cleanup
/plugin install routine-cleanup@routine-cleanup
```

**As a plain skill:** copy `skills/routine-cleanup/` to `~/.claude/skills/routine-cleanup/`.

```bash
git clone https://github.com/naw103/claude-routine-cleanup
cp -r claude-routine-cleanup/skills/routine-cleanup ~/.claude/skills/
```

Requires Python 3.9+ (standard library only) and the Claude desktop app.

## Use

In an ordinary conversation in the Code tab (not inside a routine run):

```
/routine-cleanup nightly-report keep 10
```

or just ask: *"clean up my bug-fix routine's old runs, keep the last 10"*.

Claude lists your routines, confirms which one you mean, shows the plan, and waits
for your yes. The app then shows its own approval card for each batch of 25 runs.

## How it decides what to delete

Each run lands in one bucket:

| Bucket | Meaning | Default |
| --- | --- | --- |
| autonomous | nobody typed anything after the scheduled kickoff | deleted |
| engaged | you typed a message or a slash command in it | protected |
| unverifiable | the transcript is gone, so replies can't be checked | protected |

Also never selected: the newest N runs, the conversation you are in, and any run
active in the last 15 minutes.

If your old transcripts were pruned by Claude Code's `cleanupPeriodDays` and you are
happy to delete those runs too, either say so when asked or make it the default:

```json
// ~/.claude/routine-cleanup.json
{ "include_unverifiable": true }
```

## Why it deletes through the app

Two things learned the hard way, so you don't have to:

1. **Deleting the files directly does not work.** The desktop app keeps the Runs list
   in memory and writes a run's record back when it saves or you click the run. You
   get "Session not found on disk" entries that won't go away. So the bundled script
   only *plans*; Claude deletes through the app's own `delete_session` tool.
2. **That tool is off inside routine runs**, and accepts 25 sessions per call. So the
   skill runs from a normal conversation and works in batches of 25.

## The planner on its own

`skills/routine-cleanup/cleanup_runs.py` never deletes anything, so it is safe to run
by hand to see where your disk went:

```bash
python3 skills/routine-cleanup/cleanup_runs.py                       # list routines
python3 skills/routine-cleanup/cleanup_runs.py --routine ID --keep 10 --brief
python3 skills/routine-cleanup/cleanup_runs.py --routine ID --keep 10 --json
```

| Flag | Meaning |
| --- | --- |
| `--routine ID` | exact scheduledTaskId (omit to list routines) |
| `--keep N` / `--session ID` | bulk, or one run by id or unique prefix |
| `--include-engaged` | also select runs you replied in |
| `--include-unverifiable` / `--exclude-unverifiable` | override the config default |
| `--live-window-minutes M` | skip runs active this recently (default 15) |
| `--brief` | counts and date ranges instead of one line per run |
| `--ids` / `--ids-file PATH` | session ids in batches of 25 |
| `--json` | machine-readable plan |

Environment overrides: `CLAUDE_CONFIG_DIR` (default `~/.claude`) and
`ROUTINE_CLEANUP_APP_DIR` (default: the app's data dir for your OS).

## Platform notes

Built and tested on macOS with the Claude desktop app. The app data paths for
Windows (`%APPDATA%\Claude`) and Linux (`~/.config/Claude`) follow the app's usual
layout but are untested; `ROUTINE_CLEANUP_APP_DIR` overrides them. Issues and PRs welcome.

## Development

```bash
python3 -m unittest discover -s tests -v
claude plugin validate . --strict
```

## License

MIT. Not affiliated with Anthropic.
