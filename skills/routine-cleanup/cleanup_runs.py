#!/usr/bin/env python3
"""
routine-cleanup planner.

Plans the deletion of past RUN sessions of one Claude Code scheduled task
("routine") in the Claude desktop app. It never deletes anything itself.

The desktop app keeps its Runs list in memory and writes a run's record file
back when it saves or the run is clicked, so files removed behind its back come
back as "Session not found on disk". Deletion therefore goes through the app's
own delete_session tool (mcp__ccd_session_mgmt__delete_session), which takes at
most 25 session ids per call. This script prints those ids in batches.

The app's delete_session tool is unavailable inside scheduled-task runs, so the
cleanup is driven from an ordinary conversation that names the routine with
--routine <exact scheduledTaskId>.

Safety model
------------
* --routine must match a scheduledTaskId exactly. Near misses are listed, never
  picked.
* The current session is never selected.
* Runs active in the last 15 minutes (configurable) are skipped as possibly live.
* "Engaged" runs, where a human typed a message or slash command after the
  scheduled kickoff, are protected unless --include-engaged is passed.
* "Unverifiable" runs, whose transcript is gone so engagement cannot be
  checked, are protected unless --include-unverifiable is passed or the config
  file sets include_unverifiable.
* --session must match exactly one run.

Exit codes: 0 plan printed, 1 nothing matched, 2 usage error or refusal.
"""
import argparse
import datetime
import difflib
import glob
import json
import os
import sys

__version__ = "1.1.0"

BATCH_SIZE = 25  # delete_session's per-call limit
DEFAULT_LIVE_WINDOW_MIN = 15
KICKOFF_MARKER = "<scheduled-task name="
INJECTED_PREFIXES = (
    "<system-reminder",
    "<task-notification",
    "<local-command-stdout",
    "<local-command-stderr",
    "<local-command-caveat",
)


# ---------------------------------------------------------------- locations

def claude_dir():
    """Claude Code's config dir (honours CLAUDE_CONFIG_DIR)."""
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def app_dir():
    """The Claude desktop app's data dir."""
    override = os.environ.get("ROUTINE_CLEANUP_APP_DIR")
    if override:
        return override
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "Claude")
    if os.name == "nt":
        return os.path.join(os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming"), "Claude")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "Claude")


RETENTION_ADVICE_DAYS = 365


def retention_note():
    """A reminder when Claude Code's age-based sweep will delete history soon.

    cleanupPeriodDays (default 30) deletes transcripts, checkpoints and more for
    every session older than it. Desktop sessions are exempt since v2.1.248 unless
    desktopSessionCleanupPeriodDays is set.
    """
    try:
        with open(os.path.join(claude_dir(), "settings.json"), encoding="utf-8") as fh:
            settings = json.load(fh)
    except (OSError, ValueError):
        settings = {}
    if not isinstance(settings, dict):
        settings = {}
    days = settings.get("cleanupPeriodDays", 30)
    desk = settings.get("desktopSessionCleanupPeriodDays")
    notes = []
    if isinstance(days, (int, float)) and days < RETENTION_ADVICE_DAYS:
        notes.append("cleanupPeriodDays is %s%s: Claude Code deletes session transcripts older than that."
                     % (days, " (the default)" if "cleanupPeriodDays" not in settings else ""))
    if isinstance(desk, (int, float)) and desk < RETENTION_ADVICE_DAYS:
        notes.append("desktopSessionCleanupPeriodDays is %s: desktop and routine transcripts expire too." % desk)
    if notes:
        notes.append('To keep your history, set "cleanupPeriodDays": 3650 in ~/.claude/settings.json '
                     "and prune routine runs with this skill instead.")
    return notes


def load_config():
    path = os.path.join(claude_dir(), "routine-cleanup.json")
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------- records

def load_records():
    """Desktop session records: <app>/claude-code-sessions/<ws>/<win>/local_<id>.json"""
    recs = []
    for f in glob.glob(os.path.join(app_dir(), "claude-code-sessions", "*", "*", "*.json")):
        try:
            with open(f, encoding="utf-8", errors="replace") as fh:
                raw = fh.read()
            if '"scheduledTaskId"' not in raw:
                continue  # an ordinary session; only routine runs matter here
            d = json.loads(raw)
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("sessionId"):
            d["_file"] = f
            recs.append(d)
    return recs


_TRANSCRIPTS = {}


def transcript_path(cli):
    """<claude>/projects/<slug>/<cli>.jsonl, from a one-time index of all projects."""
    if not cli:
        return None
    root = os.path.join(claude_dir(), "projects")
    index = _TRANSCRIPTS.get(root)
    if index is None:
        index = {}
        try:
            slugs = list(os.scandir(root))
        except OSError:
            slugs = []
        for slug in slugs:
            if not slug.is_dir():
                continue
            try:
                for f in os.scandir(slug.path):
                    if f.name.endswith(".jsonl"):
                        index.setdefault(f.name[:-len(".jsonl")], f.path)
            except OSError:
                continue
        _TRANSCRIPTS[root] = index
    return index.get(cli)


def message_text(content):
    """(text, is_tool_result) for a user message's content."""
    if isinstance(content, str):
        return content, False
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return "", True
        return "".join(b.get("text", "") for b in content
                       if isinstance(b, dict) and b.get("type") == "text"), False
    return "", False


def is_human_message(o):
    """True when a transcript entry is something a person typed."""
    if o.get("type") != "user" or o.get("isMeta") or o.get("isSidechain"):
        return False
    m = o.get("message")
    if not isinstance(m, dict) or m.get("role") != "user":
        return False
    text, is_tool = message_text(m.get("content"))
    if is_tool:
        return False
    s = text.lstrip()
    if not s:
        return False
    if KICKOFF_MARKER in text:
        return False  # the scheduled kickoff prompt
    if s.startswith(INJECTED_PREFIXES):
        return False  # injected by the harness, not typed
    return True  # typed text, or a typed slash command (<command-name>...)


def human_turns(cli):
    """Messages a human typed after the kickoff. None when the transcript is gone."""
    p = transcript_path(cli)
    if not p:
        return None
    n = 0
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"user"' not in line:
                    continue  # cheap prefilter: most lines are assistant or tool output
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                if isinstance(o, dict) and is_human_message(o):
                    n += 1
    except OSError:
        return None
    return n


def artifacts(rec):
    """Every on-disk path belonging to a run (used for size reporting only)."""
    cli = rec.get("cliSessionId") or ""
    local = rec.get("sessionId") or ""
    cd = claude_dir()
    paths = [rec["_file"]]
    tp = transcript_path(cli)
    if tp:
        paths.append(tp)
        side = tp[:-len(".jsonl")]
        if os.path.isdir(side):
            paths.append(side)  # tool-results/, subagents/
    if cli:
        for sub in ("session-env", "tasks", "file-history"):
            d = os.path.join(cd, sub, cli)
            if os.path.isdir(d):
                paths.append(d)
    if local:
        paths.extend(glob.glob(os.path.join(app_dir(), "local-agent-mode-sessions", "*", "*", glob.escape(local))))
    return paths


def size_of(paths):
    total = 0
    for p in paths:
        if os.path.isdir(p):
            for root, _, files in os.walk(p):
                for x in files:
                    try:
                        total += os.path.getsize(os.path.join(root, x))
                    except OSError:
                        pass
        else:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
    return total


# ---------------------------------------------------------------- formatting

def loc(ms):
    try:
        return datetime.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


def iso(ms):
    try:
        return datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def mb(n):
    return "%.1f MB" % (n / 1e6)


def routines_summary(recs):
    out = {}
    for d in recs:
        k = d.get("scheduledTaskId")
        if not k:
            continue
        r = out.setdefault(k, {"scheduledTaskId": k, "title": "", "runs": 0, "newest": 0})
        r["runs"] += 1
        if (d.get("createdAt") or 0) >= r["newest"]:
            r["newest"] = d.get("createdAt") or 0
            r["title"] = d.get("title") or r["title"]
    return sorted(out.values(), key=lambda r: -r["runs"])


def suggest(task_id, routines):
    ids = [r["scheduledTaskId"] for r in routines]
    low = task_id.lower()
    near = [i for i in ids if low in i.lower()]
    near += [r["scheduledTaskId"] for r in routines if low in (r["title"] or "").lower()]
    near += difflib.get_close_matches(task_id, ids, n=5, cutoff=0.5)
    seen, res = set(), []
    for i in near:
        if i not in seen:
            seen.add(i)
            res.append(i)
    return res[:8]


# ---------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(
        description="Plan the deletion of a Claude Code routine's past runs (never deletes).")
    ap.add_argument("--version", action="version", version="%(prog)s " + __version__)
    ap.add_argument("--routine", help="exact scheduledTaskId of the routine (omit to list routines)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--keep", type=int, help="keep the newest N runs; consider older ones")
    g.add_argument("--session", help="one run, by local id, CLI session id, or unique prefix")
    ap.add_argument("--include-engaged", action="store_true",
                    help="also select runs a human replied in (needs explicit user approval)")
    unv = ap.add_mutually_exclusive_group()
    unv.add_argument("--include-unverifiable", dest="unverifiable", action="store_true", default=None,
                     help="also select runs whose transcript is gone (overrides the config file)")
    unv.add_argument("--exclude-unverifiable", dest="unverifiable", action="store_false",
                     help="protect runs whose transcript is gone (overrides the config file)")
    ap.add_argument("--live-window-minutes", type=int, default=None,
                    help="skip runs active this recently (default %d)" % DEFAULT_LIVE_WINDOW_MIN)
    ap.add_argument("--brief", action="store_true",
                    help="per-bucket counts and date ranges instead of one line per run")
    ap.add_argument("--ids", action="store_true",
                    help="print selected session ids as JSON batches for delete_session")
    ap.add_argument("--ids-file", help="also write the batches to this JSON file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = load_config()
    recs = load_records()
    routines = routines_summary(recs)

    cli_cur = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
    host_cur = os.environ.get("CLAUDE_CODE_HOST_SESSION_ID", "").strip()
    cur_ids = {i for i in (cli_cur, host_cur) if i}
    cur = next((d for d in recs if d.get("sessionId") in cur_ids or d.get("cliSessionId") in cur_ids), None)
    in_routine = bool(cur and cur.get("scheduledTaskId"))

    task_id = args.routine or (cur.get("scheduledTaskId") if in_routine else None)
    if not task_id:
        if args.json:
            print(json.dumps({"routines": [dict(r, newest=iso(r["newest"])) for r in routines]}, indent=2))
        elif not routines:
            print("No scheduled-task runs found under %s" % os.path.join(app_dir(), "claude-code-sessions"))
        else:
            print("Pass --routine <scheduledTaskId>. Routines on this machine:")
            for r in routines:
                print("  %6d runs  %-40s %s" % (r["runs"], r["scheduledTaskId"], r["title"]))
        return 2 if (args.keep is not None or args.session) else 0

    runs = sorted((d for d in recs if d.get("scheduledTaskId") == task_id),
                  key=lambda d: (d.get("createdAt") or 0, d.get("sessionId")))
    if not runs:
        print("REFUSE: no runs for scheduledTaskId %r (it must match exactly)." % task_id)
        near = suggest(task_id, routines)
        if near:
            print("Did you mean: %s" % ", ".join(near))
        return 2
    title = runs[-1].get("title") or "(untitled)"

    if args.keep is None and not args.session:
        print("Pass --keep N (bulk) or --session <id> (single).")
        return 2

    # ---- candidates
    if args.session:
        s = args.session
        cand = [d for d in runs if s in (d.get("sessionId"), d.get("cliSessionId"))]
        if not cand:
            cand = [d for d in runs
                    if (d.get("sessionId") or "").startswith(s) or (d.get("cliSessionId") or "").startswith(s)
                    or (d.get("sessionId") or "").startswith("local_" + s)]
        if not cand:
            print("No run of %s matches --session %r." % (task_id, s))
            return 1
        if len(cand) > 1:
            print("REFUSE: --session %r matches %d runs; use a longer prefix:" % (s, len(cand)))
            for d in cand:
                print("    %s  %s" % (loc(d.get("createdAt")), d.get("sessionId")))
            return 2
        mode = "single run"
    else:
        keep = max(0, args.keep)
        cand = runs[:-keep] if keep else list(runs)
        mode = "keeping newest %d" % keep

    window_min = args.live_window_minutes
    if window_min is None:
        window_min = int(cfg.get("live_window_minutes", DEFAULT_LIVE_WINDOW_MIN))
    now_ms = datetime.datetime.now().timestamp() * 1000
    skipped_live, skipped_current, selected_pool = [], [], []
    for d in cand:
        if d.get("sessionId") in cur_ids or d.get("cliSessionId") in cur_ids:
            skipped_current.append(d)
        elif now_ms - (d.get("lastActivityAt") or d.get("createdAt") or 0) <= window_min * 60000:
            skipped_live.append(d)
        else:
            selected_pool.append(d)

    # ---- classify
    buckets = {"autonomous": [], "engaged": [], "unverifiable": []}
    for d in selected_pool:
        ht = human_turns(d.get("cliSessionId"))
        d["_human"] = ht
        buckets["unverifiable" if ht is None else ("engaged" if ht > 0 else "autonomous")].append(d)

    inc_unv = args.unverifiable
    if inc_unv is None:
        inc_unv = bool(cfg.get("include_unverifiable", False))
    will = {"autonomous": True, "engaged": args.include_engaged, "unverifiable": inc_unv}
    selected = [d for b in ("autonomous", "engaged", "unverifiable") if will[b] for d in buckets[b]]
    selected.sort(key=lambda d: d.get("createdAt") or 0)
    ids = [d["sessionId"] for d in selected]
    batches = [ids[i:i + BATCH_SIZE] for i in range(0, len(ids), BATCH_SIZE)]
    sizes = {b: sum(size_of(artifacts(d)) for d in rows) for b, rows in buckets.items()}
    freed = sum(sizes[b] for b in buckets if will[b])

    if args.ids_file:
        with open(args.ids_file, "w", encoding="utf-8") as fh:
            json.dump({"scheduledTaskId": task_id, "title": title, "mode": mode,
                       "created": iso(now_ms), "count": len(ids), "batches": batches}, fh, indent=2)

    if args.json:
        def row(d):
            return {"sessionId": d.get("sessionId"), "cliSessionId": d.get("cliSessionId"),
                    "createdAt": iso(d.get("createdAt")), "humanReplies": d.get("_human"),
                    "archived": bool(d.get("isArchived"))}
        print(json.dumps({
            "version": __version__, "scheduledTaskId": task_id, "title": title, "mode": mode,
            "totalRuns": len(runs), "currentSessionInRoutine": in_routine,
            "buckets": {b: {"willDelete": will[b], "bytes": sizes[b], "runs": [row(d) for d in buckets[b]]}
                        for b in buckets},
            "skipped": {"current": [d.get("sessionId") for d in skipped_current],
                        "recentlyActive": [d.get("sessionId") for d in skipped_live]},
            "selected": {"count": len(ids), "bytes": freed, "batches": batches},
            "retentionNotes": retention_note(),
        }, indent=2))
        return 0

    print("Routine        : %s" % title)
    print("scheduledTaskId: %s" % task_id)
    print("Total runs     : %d" % len(runs))
    if in_routine:
        print("NOTE: this conversation is a routine run. The app's delete_session tool does not")
        print("      work here; run the deletion from an ordinary conversation.")
    print("\nPlan (%s):\n" % mode)
    labels = {"autonomous": "AUTONOMOUS (no human reply)",
              "engaged": "ENGAGED (a human replied)",
              "unverifiable": "UNVERIFIABLE (transcript gone)"}
    flags = {"engaged": "--include-engaged", "unverifiable": "--include-unverifiable"}
    for b in ("autonomous", "engaged", "unverifiable"):
        rows = buckets[b]
        if not rows:
            continue
        state = "WILL DELETE" if will[b] else "PROTECTED (needs %s)" % flags[b]
        rows.sort(key=lambda d: d.get("createdAt") or 0)
        if args.brief:
            print("%-32s %4d runs  %9s  %s  %s .. %s" % (labels[b], len(rows), mb(sizes[b]), state,
                                                       loc(rows[0].get("createdAt")), loc(rows[-1].get("createdAt"))))
            continue
        print("%s  (%d runs, %s)  %s" % (labels[b], len(rows), mb(sizes[b]), state))
        for d in rows:
            extra = "" if not d["_human"] else "  (%d human repl%s)" % (d["_human"], "y" if d["_human"] == 1 else "ies")
            print("    %s  %s  %s%s" % (loc(d.get("createdAt")), d.get("sessionId"), title, extra))
        print()
    if args.brief:
        print()
    if skipped_current:
        print("Skipped (this conversation): %d" % len(skipped_current))
    if skipped_live:
        print("Skipped (active in the last %d min): %d" % (window_min, len(skipped_live)))
    print("Selected for deletion: %d runs, %s, in %d batch%s of <=%d" % (
        len(ids), mb(freed), len(batches), "" if len(batches) == 1 else "es", BATCH_SIZE))
    for b in ("engaged", "unverifiable"):
        if buckets[b] and not will[b]:
            print("  (%d %s run(s) protected; %s selects them)" % (len(buckets[b]), b, flags[b]))
    print("\nPLAN ONLY: nothing was deleted. Deletion goes through the app's delete_session tool.")
    notes = retention_note()
    if notes:
        print("\nRETENTION: " + "\n           ".join(notes))
    if args.ids and batches:
        print("\nSESSION ID BATCHES (%d ids, %d batches):" % (len(ids), len(batches)))
        for bt in batches:
            print(json.dumps(bt))
    if args.ids_file:
        print("\nBatches written to %s" % args.ids_file)
    return 0 if ids else 1


if __name__ == "__main__":
    sys.exit(main())
