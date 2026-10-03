"""Tests for cleanup_runs.py. Standard library only: python -m unittest discover tests"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "skills", "routine-cleanup"))
import cleanup_runs as cr  # noqa: E402

DAY = 86400 * 1000
NOW = int(time.time() * 1000)
KICKOFF = '<scheduled-task name="nightly" file="/x/SKILL.md">\nDo the thing.\n</scheduled-task>'


def user(text, **extra):
    o = {"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
    o.update(extra)
    return o


def assistant(text="ok"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "output"}]}}


class Sandbox(unittest.TestCase):
    """A fake Claude config dir and desktop app dir."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.claude = os.path.join(self.tmp, "claude")
        self.app = os.path.join(self.tmp, "app")
        self.sessions = os.path.join(self.app, "claude-code-sessions", "ws", "win")
        self.projects = os.path.join(self.claude, "projects", "-proj")
        os.makedirs(self.sessions)
        os.makedirs(self.projects)
        self.env = {k: os.environ.get(k) for k in
                    ("CLAUDE_CONFIG_DIR", "ROUTINE_CLEANUP_APP_DIR",
                     "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_HOST_SESSION_ID")}
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude
        os.environ["ROUTINE_CLEANUP_APP_DIR"] = self.app
        os.environ.pop("CLAUDE_CODE_SESSION_ID", None)
        os.environ.pop("CLAUDE_CODE_HOST_SESSION_ID", None)
        self.n = 0

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_(self, task="nightly", age_days=10, messages=None, transcript=True, title="Nightly", **rec):
        """Create one run: a desktop record plus (optionally) a transcript."""
        self.n += 1
        cli = "cli-%04d" % self.n
        local = "local_%04d" % self.n
        created = NOW - int(age_days * DAY)
        d = {"sessionId": local, "cliSessionId": cli, "title": title, "scheduledTaskId": task,
             "createdAt": created, "lastActivityAt": created + 60000}
        d.update(rec)
        with open(os.path.join(self.sessions, local + ".json"), "w") as fh:
            json.dump(d, fh)
        if transcript:
            lines = [user(KICKOFF), assistant()] + (messages or [])
            with open(os.path.join(self.projects, cli + ".jsonl"), "w") as fh:
                for o in lines:
                    fh.write(json.dumps(o) + "\n")
        return local

    def plan(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cr.main(list(argv) + ["--json"])
        text = out.getvalue()
        return code, (json.loads(text) if text.strip().startswith("{") else text)

    def text(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cr.main(list(argv))
        return code, out.getvalue()


class EngagementTests(Sandbox):
    def bucket_of(self, local, plan):
        for b, v in plan["buckets"].items():
            if any(r["sessionId"] == local for r in v["runs"]):
                return b
        return None

    def test_classification(self):
        auto = self.run_()
        typed = self.run_(messages=[user("thanks, now fix the other one")])
        slash = self.run_(messages=[user("<command-name>/goal</command-name>\n<command-args>x</command-args>")])
        skill = self.run_(messages=[user("Base directory for this skill: /x\n# Skill", isMeta=True)])
        notif = self.run_(messages=[user("<task-notification>\n<summary>done</summary>")])
        remind = self.run_(messages=[user("<system-reminder>\nnote\n</system-reminder>")])
        tool = self.run_(messages=[tool_result()])
        stdout = self.run_(messages=[user("<local-command-stdout>Set model</local-command-stdout>")])
        side = self.run_(messages=[user("subagent prompt", isSidechain=True)])
        gone = self.run_(transcript=False)
        code, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertEqual(code, 0)
        for local in (auto, skill, notif, remind, tool, stdout, side):
            self.assertEqual(self.bucket_of(local, p), "autonomous", local)
        self.assertEqual(self.bucket_of(typed, p), "engaged")
        self.assertEqual(self.bucket_of(slash, p), "engaged")
        self.assertEqual(self.bucket_of(gone, p), "unverifiable")

    def test_string_content_and_bad_lines(self):
        local = self.run_()
        path = os.path.join(self.projects, "cli-%04d.jsonl" % self.n)
        with open(path, "a") as fh:
            fh.write("not json\n\n")
            fh.write(json.dumps({"type": "user", "message": {"role": "user", "content": "plain string"}}) + "\n")
        code, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertEqual(self.bucket_of(local, p), "engaged")


class SelectionTests(Sandbox):
    def ids(self, p):
        return [i for b in p["selected"]["batches"] for i in b]

    def test_keep_newest(self):
        old = [self.run_(age_days=d) for d in (30, 20, 10)]
        newest = self.run_(age_days=5)
        code, p = self.plan("--routine", "nightly", "--keep", "1")
        self.assertEqual(sorted(self.ids(p)), sorted(old))
        self.assertNotIn(newest, self.ids(p))

    def test_engaged_protected_unless_flag(self):
        e = self.run_(messages=[user("hi")])
        _, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertNotIn(e, self.ids(p))
        _, p = self.plan("--routine", "nightly", "--keep", "0", "--include-engaged")
        self.assertIn(e, self.ids(p))

    def test_unverifiable_default_config_and_flags(self):
        g = self.run_(transcript=False)
        _, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertNotIn(g, self.ids(p))
        _, p = self.plan("--routine", "nightly", "--keep", "0", "--include-unverifiable")
        self.assertIn(g, self.ids(p))
        with open(os.path.join(self.claude, "routine-cleanup.json"), "w") as fh:
            json.dump({"include_unverifiable": True}, fh)
        _, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertIn(g, self.ids(p))
        _, p = self.plan("--routine", "nightly", "--keep", "0", "--exclude-unverifiable")
        self.assertNotIn(g, self.ids(p))

    def test_recent_and_current_skipped(self):
        live = self.run_(age_days=0)
        cur = self.run_(age_days=3)
        os.environ["CLAUDE_CODE_HOST_SESSION_ID"] = cur
        _, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertNotIn(live, self.ids(p))
        self.assertNotIn(cur, self.ids(p))
        self.assertEqual(p["skipped"]["recentlyActive"], [live])
        self.assertEqual(p["skipped"]["current"], [cur])

    def test_other_routines_untouched(self):
        mine = self.run_(task="nightly")
        other = self.run_(task="nightly-2")
        _, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertEqual(self.ids(p), [mine])
        self.assertNotIn(other, self.ids(p))

    def test_batches_of_25(self):
        for i in range(60):
            self.run_(age_days=100 - i)
        _, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertEqual([len(b) for b in p["selected"]["batches"]], [25, 25, 10])

    def test_ids_file(self):
        a = self.run_()
        path = os.path.join(self.tmp, "ids.json")
        self.text("--routine", "nightly", "--keep", "0", "--ids-file", path)
        with open(path) as fh:
            data = json.load(fh)
        self.assertEqual(data["batches"], [[a]])
        self.assertEqual(data["scheduledTaskId"], "nightly")


class SafetyTests(Sandbox):
    def test_lists_routines_without_routine(self):
        self.run_(task="nightly")
        code, out = self.text()
        self.assertEqual(code, 0)
        self.assertIn("nightly", out)
        code, _ = self.text("--keep", "3")
        self.assertEqual(code, 2)

    def test_inexact_routine_refused_with_suggestions(self):
        self.run_(task="acme-bug-fixes", title="Acme bug fixes")
        code, out = self.text("--routine", "bug-fixes", "--keep", "0")
        self.assertEqual(code, 2)
        self.assertIn("REFUSE", out)
        self.assertIn("acme-bug-fixes", out)

    def test_ambiguous_session_prefix_refused(self):
        self.run_()
        self.run_()
        code, out = self.text("--routine", "nightly", "--session", "local_000")
        self.assertEqual(code, 2)
        self.assertIn("matches 2 runs", out)

    def test_unique_session(self):
        self.run_()
        b = self.run_()
        code, p = self.plan("--routine", "nightly", "--session", b)
        self.assertEqual(p["selected"]["batches"], [[b]])

    def test_needs_keep_or_session(self):
        self.run_()
        code, out = self.text("--routine", "nightly")
        self.assertEqual(code, 2)

    def test_corrupt_record_ignored(self):
        with open(os.path.join(self.sessions, "broken.json"), "w") as fh:
            fh.write("{not json")
        a = self.run_()
        code, p = self.plan("--routine", "nightly", "--keep", "0")
        self.assertEqual(p["selected"]["batches"], [[a]])

    def test_never_deletes_files(self):
        a = self.run_()
        before = sorted(os.listdir(self.sessions)) + sorted(os.listdir(self.projects))
        self.text("--routine", "nightly", "--keep", "0", "--ids")
        after = sorted(os.listdir(self.sessions)) + sorted(os.listdir(self.projects))
        self.assertEqual(before, after)

    def test_text_output(self):
        self.run_()
        self.run_(messages=[user("hello")])
        code, out = self.text("--routine", "nightly", "--keep", "0", "--ids")
        self.assertIn("WILL DELETE", out)
        self.assertIn("PROTECTED (needs --include-engaged)", out)
        self.assertIn("PLAN ONLY", out)
        self.assertIn("SESSION ID BATCHES", out)
        code, out = self.text("--routine", "nightly", "--keep", "0", "--brief")
        self.assertIn("AUTONOMOUS", out)


if __name__ == "__main__":
    unittest.main()
