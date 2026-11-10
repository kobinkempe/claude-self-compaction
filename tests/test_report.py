"""The --report mode: the relay wakes the session to post a status. Synthetic state only."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("selfcompact_report", Path(__file__).parents[1] / "selfcompact.py")
sc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sc)

STAMP = "2026-11-10T15:00:00+00:00"


class ReportMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "selfcompact"
        self.patch = patch.object(sc, "HOME", self.home)
        self.patch.start()
        self.sdir = self.home / "sessions" / "work"
        self.sdir.mkdir(parents=True)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def relay_output(self, **result):
        base = {"session": "work", "requested": STAMP, "host_session": "host-1", "then": "", "report": "",
                "handoff": "", "detail": "compacted", "finished": STAMP}
        sc.write_json(self.sdir / "result.json", {**base, **result})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            sc.cmd_relay(argparse.Namespace(session="work", request=STAMP, wait=0))
        return out.getvalue()

    def session_start_note(self, **result):
        base = {"session": "work", "requested": STAMP, "finished": STAMP, "then": "", "report": "",
                "handoff": "", "backup": "backup.jsonl", "detail": "compacted"}
        sc.write_json(self.sdir / "result.json", {**base, **result})
        out = io.StringIO()
        with patch.object(sc, "config", return_value={"setup_complete": True, "enabled": True}), \
             patch.object(sc, "read_hook_event", return_value={"session_id": "work", "source": "resume"}), \
             patch.dict(sc.os.environ, {"CLAUDE_CODE_HOST_SESSION_ID": "host-1"}), contextlib.redirect_stdout(out):
            sc.cmd_hook_session_start(None)
        return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]

    def test_relay_sends_report_after_success(self):
        out = self.relay_output(ok=True, report="The fixes are done.", tokens_before=500000)
        self.assertIn("SEND\nTO: host-1", out)
        self.assertIn("your compaction finished", out)
        self.assertIn("Report: The fixes are done.", out)
        self.assertIn("Compaction complete.", out)
        self.assertNotIn("Continue:", out)

    def test_relay_sends_report_after_failure(self):
        out = self.relay_output(ok=False, report="Answer the two questions.", detail="the CLI is not signed in")
        self.assertIn("did not happen (the CLI is not signed in)", out)
        self.assertIn("Report: Answer the two questions.", out)
        self.assertIn("Compaction failed", out)

    def test_relay_stays_quiet_if_the_user_already_wrote(self):
        out = self.relay_output(ok=False, report="Done.", detail="the user sent a message first")
        self.assertTrue(out.startswith("NOTHING"))

    def test_session_start_tells_it_to_post_and_stop(self):
        note = self.session_start_note(ok=True, report="Done.", tokens_before=1000)
        self.assertIn("Compaction complete. ", note)
        self.assertIn("end your turn", note)
        self.assertNotIn("Carry on with it", note)

    def test_session_start_for_then_is_unchanged(self):
        note = self.session_start_note(ok=True, then="Do step 3.", tokens_before=1000)
        self.assertIn("Carry on with it without waiting for the user", note)
        self.assertNotIn("Compaction complete", note)

    def test_then_and_report_cannot_be_combined(self):
        with patch.object(sc.sys, "argv", ["selfcompact.py", "request", "--then", "a", "--report", "b"]), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as stop:
            sc.main()
        self.assertEqual(stop.exception.code, 2)

    def test_report_needs_a_relay(self):
        args = argparse.Namespace(session=None, pid="1", then=None, report="Done.", handoff=None, focus=None, keep=None)
        env = {"CLAUDE_CODE_SESSION_ID": "work", "CLAUDE_PID": "1", "CLAUDE_CODE_EXECPATH": "claude.exe",
               "CLAUDE_CODE_HOST_SESSION_ID": "host-1"}
        transcript = Path(self.tmp.name) / "proj" / "work.jsonl"
        transcript.parent.mkdir()
        transcript.write_text("", encoding="utf-8")
        with patch.object(sc, "config", return_value={"setup_complete": True, "enabled": True}), \
             patch.object(sc, "relay_session", return_value=None), \
             patch.object(sc, "is_session_process", return_value=True), \
             patch.object(sc, "process_info", return_value={}), \
             patch.object(sc, "find_transcript", return_value=transcript), \
             patch.dict(sc.os.environ, env), self.assertRaises(SystemExit) as stop:
            sc.cmd_request(args)
        self.assertIn("--report needs a relay session", str(stop.exception.code))
        self.assertFalse((self.sdir / "request.json").exists())


if __name__ == "__main__":
    unittest.main()
