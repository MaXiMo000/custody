"""Run: python tests/test_hook.py

Exercises hook.py's real stdin/stdout glue against real files in a real
temp directory -- not mocks -- with event JSON shaped exactly like Claude
Code's own documented PreToolUse/PostToolUse payloads
(https://docs.claude.com/en/docs/claude-code/hooks), so a change to that
contract this repo hasn't kept up with would show here, not just in
production.
"""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from custody import hook


def _run_hook(event: dict) -> int:
    # contextlib has no redirect_stdin (only stdout/stderr) -- swap it by
    # hand, same as the manual save/restore that context manager does.
    real_stdin = sys.stdin
    sys.stdin = io.StringIO(json.dumps(event))
    try:
        return hook.main()
    finally:
        sys.stdin = real_stdin


class TestEditWriteRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        self.file_path = str(pathlib.Path(self.cwd) / "auth.py")
        pathlib.Path(self.file_path).write_text("original\n")

    def tearDown(self):
        self.tmp.cleanup()

    def _receipt(self, tool_use_id: str) -> dict:
        path = pathlib.Path(self.cwd, ".custody", "receipts", f"{tool_use_id}.json")
        self.assertTrue(path.exists(), f"no receipt written to {path}")
        return json.loads(path.read_text())

    def test_edit_that_actually_changed_the_file_is_pass(self):
        tool_use_id = "toolu_01ABC"
        pre = {
            "session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
            "tool_name": "Edit", "tool_use_id": tool_use_id,
            "tool_input": {"file_path": self.file_path, "old_string": "original",
                            "new_string": "changed"},
        }
        self.assertEqual(_run_hook(pre), 0)

        pathlib.Path(self.file_path).write_text("changed\n")  # what Edit itself would do

        post = {**pre, "hook_event_name": "PostToolUse",
                 "tool_response": {"filePath": self.file_path, "success": True},
                 "duration_ms": 12}
        self.assertEqual(_run_hook(post), 0)

        bundle = self._receipt(tool_use_id)
        self.assertEqual(bundle["providence_version"], 1)
        self.assertEqual(bundle["tool"], "custody")
        payload = bundle["payload"]
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["declared_scope"], [self.file_path])
        self.assertEqual(payload["changed"], "modified")

    def test_edit_that_claims_success_but_never_touched_the_file_is_fail(self):
        """The exact scenario custody exists to catch: independent
        verification disagreeing with the tool's own self-report."""
        tool_use_id = "toolu_02DEF"
        pre = {
            "session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
            "tool_name": "Edit", "tool_use_id": tool_use_id,
            "tool_input": {"file_path": self.file_path},
        }
        _run_hook(pre)
        # File is deliberately left untouched here.
        post = {**pre, "hook_event_name": "PostToolUse",
                 "tool_response": {"filePath": self.file_path, "success": True}}
        _run_hook(post)

        payload = self._receipt(tool_use_id)["payload"]
        self.assertEqual(payload["status"], "fail")
        self.assertIn("byte-identical", payload["detail"])

    def test_write_creating_a_new_file_is_pass(self):
        tool_use_id = "toolu_03GHI"
        new_file = str(pathlib.Path(self.cwd) / "new_module.py")
        pre = {
            "session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
            "tool_name": "Write", "tool_use_id": tool_use_id,
            "tool_input": {"file_path": new_file, "content": "x = 1\n"},
        }
        _run_hook(pre)
        pathlib.Path(new_file).write_text("x = 1\n")
        post = {**pre, "hook_event_name": "PostToolUse",
                 "tool_response": {"filePath": new_file, "success": True}}
        _run_hook(post)

        payload = self._receipt(tool_use_id)["payload"]
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(payload["changed"], "created")

    def test_post_without_a_matching_pre_is_unverified_not_a_crash(self):
        tool_use_id = "toolu_orphan"
        post = {
            "session_id": "s1", "cwd": self.cwd, "hook_event_name": "PostToolUse",
            "tool_name": "Edit", "tool_use_id": tool_use_id,
            "tool_input": {"file_path": self.file_path},
            "tool_response": {"success": True},
        }
        self.assertEqual(_run_hook(post), 0)
        payload = self._receipt(tool_use_id)["payload"]
        self.assertEqual(payload["status"], "unverified")
        self.assertIn("no matching PreToolUse", payload["detail"])

    def test_untouched_tools_are_silently_ignored(self):
        """Read, Grep, etc. never get a pending-state file or a receipt --
        this hook only has anything to say about Edit, Write, and Bash."""
        event = {
            "session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
            "tool_name": "Read", "tool_use_id": "toolu_read",
            "tool_input": {"file_path": self.file_path},
        }
        self.assertEqual(_run_hook(event), 0)
        self.assertFalse((pathlib.Path(self.cwd) / ".custody").exists())

    def test_a_crashing_handler_never_raises_out_of_main(self):
        """A malformed event (missing tool_use_id) must not take the
        agent's turn down with it -- this is a witness, never a gate."""
        event = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                  "tool_input": {"file_path": self.file_path}}  # no tool_use_id
        self.assertEqual(_run_hook(event), 0)


class TestBashRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        pathlib.Path(self.cwd, "existing.py").write_text("x = 1\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_bash_reports_what_changed_but_stays_unverified(self):
        tool_use_id = "toolu_bash_1"
        pre = {
            "session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
            "tool_name": "Bash", "tool_use_id": tool_use_id,
            "tool_input": {"command": "echo hi >> existing.py"},
        }
        _run_hook(pre)
        with open(pathlib.Path(self.cwd, "existing.py"), "a") as f:
            f.write("y = 2\n")
        post = {**pre, "hook_event_name": "PostToolUse",
                 "tool_response": {"stdout": "", "stderr": "", "exitCode": 0}}
        _run_hook(post)

        path = pathlib.Path(self.cwd, ".custody", "receipts", f"{tool_use_id}.json")
        payload = json.loads(path.read_text())["payload"]
        self.assertEqual(payload["status"], "unverified")
        self.assertEqual(payload["changes"]["modified"], ["existing.py"])


if __name__ == "__main__":
    unittest.main()
