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

    def test_unparseable_stdin_never_raises_out_of_main(self):
        """Below the event-dict level: stdin that isn't even valid JSON at
        all. Found by deliberately feeding the hook garbage while auditing
        this repo a second time -- it used to let json.JSONDecodeError
        escape main() as an uncaught traceback."""
        real_stdin = sys.stdin
        sys.stdin = io.StringIO("not json at all {{{")
        try:
            self.assertEqual(hook.main(), 0)
        finally:
            sys.stdin = real_stdin


class TestRealPayloadShapes(unittest.TestCase):
    """tool_response shapes copied from real Claude Code 2.1 PostToolUse
    events, captured live -- not reconstructed from docs. Neither Edit nor
    Write sends a `success` field; the tests above that pass one exercise
    the fallback path only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = self.tmp.name
        self.path = pathlib.Path(self.cwd) / "a.txt"

    def tearDown(self):
        self.tmp.cleanup()

    def _round_trip(self, tool_name: str, tool_response: dict, write_bytes: bytes | None) -> dict:
        tool_use_id = f"toolu_{tool_name}_{len(tool_response)}"
        pre = {"session_id": "s1", "cwd": self.cwd, "hook_event_name": "PreToolUse",
               "tool_name": tool_name, "tool_use_id": tool_use_id,
               "tool_input": {"file_path": str(self.path)}}
        _run_hook(pre)
        if write_bytes is not None:
            self.path.write_bytes(write_bytes)
        _run_hook({**pre, "hook_event_name": "PostToolUse", "tool_response": tool_response})
        receipt = pathlib.Path(self.cwd, ".custody", "receipts", f"{tool_use_id}.json")
        return json.loads(receipt.read_text())["payload"]

    def _edit_response(self, original: str, old: str, new: str) -> dict:
        return {"filePath": str(self.path), "oldString": old, "newString": new,
                "originalFile": original, "structuredPatch": [], "userModified": False,
                "replaceAll": False}

    def test_write_whose_content_is_on_disk_is_pass(self):
        payload = self._round_trip("Write", {
            "type": "create", "filePath": str(self.path), "content": "x",
            "structuredPatch": [], "originalFile": None, "userModified": False,
        }, b"x")
        self.assertEqual(payload["status"], "pass")
        self.assertIn("matches exactly", payload["detail"])
        self.assertTrue(payload["tool_reported_success"])

    def test_write_whose_content_is_not_what_landed_on_disk_is_fail(self):
        # A partial write, or another process racing the tool.
        payload = self._round_trip("Write", {
            "type": "create", "filePath": str(self.path), "content": "full content\n",
            "structuredPatch": [], "originalFile": None, "userModified": False,
        }, b"full con")
        self.assertEqual(payload["status"], "fail")
        self.assertIn("not what Write reported writing", payload["detail"])

    def test_edit_that_landed_is_pass(self):
        self.path.write_bytes(b"alpha\n")
        payload = self._round_trip("Edit", self._edit_response("alpha\n", "alpha", "beta"), b"beta\n")
        self.assertEqual(payload["status"], "pass")

    def test_edit_that_never_landed_is_fail(self):
        self.path.write_bytes(b"alpha\n")
        payload = self._round_trip("Edit", self._edit_response("alpha\n", "alpha", "beta"), None)
        self.assertEqual(payload["status"], "fail")

    def test_edit_of_a_crlf_file_is_pass(self):
        # Measured live: Edit reports LF text but keeps the file's CRLF.
        self.path.write_bytes(b"one\r\ntwo\r\n")
        payload = self._round_trip("Edit", self._edit_response("one\ntwo\n", "two", "three"),
                                   b"one\r\nthree\r\n")
        self.assertEqual(payload["status"], "pass")

    def test_edit_of_a_bom_file_is_pass(self):
        # Measured live: Edit reports a UTF-8 BOM as the mojibake U+00EF U+00BB U+00BF.
        self.path.write_bytes(b"\xef\xbb\xbfbom line\n")
        payload = self._round_trip(
            "Edit", self._edit_response("ï»¿bom line\n", "bom line", "bom edited"),
            b"\xef\xbb\xbfbom edited\n")
        self.assertEqual(payload["status"], "pass")

    def test_a_non_utf8_file_falls_back_rather_than_guessing(self):
        self.path.write_bytes(b"caf\xe9\n")
        payload = self._round_trip("Edit", self._edit_response("caf�\n", "caf", "bar"),
                                   b"bar\xe9\n")
        # Not compared as text (can't be, honestly), so the hash-based
        # fallback decides: the tool succeeded and the file changed.
        self.assertEqual(payload["status"], "pass")
        self.assertNotIn("matches exactly", payload["detail"])


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
        # Bash's real Output object -- {stdout, stderr, interrupted, isImage},
        # no `success` field at all, confirmed from Claude Code's own docs.
        post = {**pre, "hook_event_name": "PostToolUse",
                 "tool_response": {"stdout": "", "stderr": "", "interrupted": False, "isImage": False}}
        _run_hook(post)

        path = pathlib.Path(self.cwd, ".custody", "receipts", f"{tool_use_id}.json")
        payload = json.loads(path.read_text())["payload"]
        self.assertEqual(payload["status"], "unverified")
        self.assertEqual(payload["changes"]["modified"], ["existing.py"])
        # Locking in the real Output shape's absence of `success`: this must
        # read null on a real Bash receipt, not a guessed True/False.
        self.assertIsNone(payload["tool_reported_success"])


if __name__ == "__main__":
    unittest.main()
