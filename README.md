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

## First: stop the 30-day expiry from deleting your history

Claude Code already deletes old session data on its own. Anything under `~/.claude`
older than [`cleanupPeriodDays`](https://code.claude.com/docs/en/claude-directory#cleaned-up-automatically)
is swept: transcripts, subagent transcripts, checkpoint snapshots, plans. The default is
**30 days**, so a terminal session you want to resume or search next month is gone.

Desktop app sessions (including routine runs) are kept at any age since Claude Code
v2.1.248, unless you set `desktopSessionCleanupPeriodDays`. Earlier versions deleted
them after `cleanupPeriodDays` too, and the sweep removes only the transcript, not the
app's Runs entry. That leaves runs you can no longer open, which this skill reports as
"unverifiable".

An age cutoff can't tell the sessions you care about from routine noise, so it deletes
both. We recommend turning the expiry up so nothing is lost to age, and pruning routine
runs with this skill instead:

```json
// ~/.claude/settings.json
{ "cleanupPeriodDays": 3650 }
```

Leave `desktopSessionCleanupPeriodDays` unset. (`0` is not "keep forever": Claude Code
rejects it. The minimum is 1.) The planner prints a reminder when your setting is
below a year.

## What a plan looks like

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

## Known limitation: 25 runs per approval

The desktop app's delete tool accepts at most 25 sessions per call, and every call
shows its own approval card. That limit is the app's, not this skill's, and there is
no way around it: deleting the files directly is exactly what leaves the ghost entries
described above.

In practice:

| Runs to delete | Approval cards |
| --- | --- |
| 100 | 4 |
| 571 | 23 |
| 1,338 | 54 |

To make it less tedious, Claude sends several batches at once, so the cards arrive
together and you can approve them one after another without waiting between them.
The plan tells you the batch count up front, so you know how many clicks it will be
before you start. Runs only pile up this far once; after the first cleanup, running
it every few weeks keeps it to a card or two.

If you know a way to delete more per call, or Anthropic raises the limit, please
open an issue.

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
