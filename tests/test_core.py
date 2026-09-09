"""Run: python tests/test_core.py

core.py is pure -- no filesystem beyond hash_file(), no stdin/stdout -- so
these tests build before/after facts by hand rather than invoking a real
tool call. test_hook.py covers the stdin/stdout glue and real files.
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

from custody import core


class TestHashFile(unittest.TestCase):
    def test_missing_file_hashes_to_none(self):
        self.assertIsNone(core.hash_file("/no/such/path/on/disk"))

    def test_real_file_hashes_deterministically(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("hello")
            path = f.name
        try:
            self.assertEqual(core.hash_file(path), core.hash_file(path))
            self.assertIsNotNone(core.hash_file(path))
        finally:
            pathlib.Path(path).unlink()


class TestFileReceipt(unittest.TestCase):
    """Edit/Write: the declared scope is trivially the one path named, so
    what's being checked is the tool's own success claim against the file's
    real bytes, not scope containment."""

    def test_success_claimed_and_content_changed_is_pass(self):
        r = core.file_receipt(file_path="a.py", before_hash="aaa", after_hash="bbb",
                               tool_name="Edit", tool_success=True)
        self.assertEqual(r["status"], core.PASS)
        self.assertEqual(r["changed"], "modified")

    def test_new_file_created_by_write_is_pass(self):
        r = core.file_receipt(file_path="new.py", before_hash=None, after_hash="bbb",
                               tool_name="Write", tool_success=True)
        self.assertEqual(r["status"], core.PASS)
        self.assertEqual(r["changed"], "created")

    def test_success_claimed_but_nothing_changed_is_fail(self):
        """The exact failure mode this exists to catch: the tool says it
        wrote the file, the bytes on disk say otherwise."""
        r = core.file_receipt(file_path="a.py", before_hash="aaa", after_hash="aaa",
                               tool_name="Edit", tool_success=True)
        self.assertEqual(r["status"], core.FAIL)
        self.assertIn("byte-identical", r["detail"])

    def test_failure_claimed_but_content_changed_anyway_is_fail(self):
        """The mirror case: a partial write before a crash, reported as a
        failure, that still left real bytes on disk."""
        r = core.file_receipt(file_path="a.py", before_hash="aaa", after_hash="bbb",
                               tool_name="Edit", tool_success=False)
        self.assertEqual(r["status"], core.FAIL)
        self.assertIn("anyway", r["detail"])

    def test_no_tool_response_is_unverified_not_guessed(self):
        r = core.file_receipt(file_path="a.py", before_hash="aaa", after_hash="bbb",
                               tool_name="Edit", tool_success=None)
        self.assertEqual(r["status"], core.UNVERIFIED)

    def test_file_missing_after_the_call_is_unverified(self):
        r = core.file_receipt(file_path="a.py", before_hash="aaa", after_hash=None,
                               tool_name="Edit", tool_success=True)
        self.assertEqual(r["status"], core.UNVERIFIED)
        self.assertIsNone(r["changed"])

    def test_failure_claimed_and_nothing_changed_is_pass(self):
        """A tool reporting failure and leaving the file untouched is
        exactly right -- not a fail, the failure claim and reality agree."""
        r = core.file_receipt(file_path="a.py", before_hash="aaa", after_hash="aaa",
                               tool_name="Edit", tool_success=False)
        self.assertEqual(r["status"], core.PASS)


def _entry(h: str, mode: int = 0o644) -> dict:
    return {"hash": h, "mode": mode}


def _fake_diff(before: dict, after: dict) -> dict:
    """Same shape receipt.snapshot.diff() returns, without depending on
    receipt being installed just to run this one pure-logic test file."""
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(p for p in set(before) & set(after) if before[p] != after[p])
    return {"added": added, "modified": modified, "removed": removed,
            "renamed": [], "mode_changed": []}


class TestBashReceipt(unittest.TestCase):
    def test_always_unverified_even_with_no_changes(self):
        """The rule custody is built on for Bash: no declared scope was
        ever collected, so there is nothing to judge pass or fail against,
        the same floor `receipt` itself uses with no --declare."""
        r = core.bash_receipt(before={}, after={}, tool_success=True, diff_fn=_fake_diff)
        self.assertEqual(r["status"], core.UNVERIFIED)
        self.assertEqual(r["detail"], "no files changed")

    def test_unverified_even_when_something_did_change(self):
        before = {"a.py": _entry("aaa")}
        after = {"a.py": _entry("bbb"), "b.py": _entry("ccc")}
        r = core.bash_receipt(before=before, after=after, tool_success=True, diff_fn=_fake_diff)
        self.assertEqual(r["status"], core.UNVERIFIED)
        self.assertEqual(r["changes"]["modified"], ["a.py"])
        self.assertEqual(r["changes"]["added"], ["b.py"])

    def test_own_pending_and_receipt_state_is_excluded_from_the_diff(self):
        """Without this, custody's own receipt files from an earlier tool
        call in the same session would show up as noise on every later
        Bash diff -- self-referential and useless."""
        before = {".custody/receipts/toolu_1.json": _entry("aaa")}
        after = {".custody/receipts/toolu_1.json": _entry("aaa"),
                  ".custody/pending/toolu_2.json": _entry("bbb"),
                  "real.py": _entry("ccc")}
        r = core.bash_receipt(before=before, after=after, tool_success=True, diff_fn=_fake_diff)
        self.assertEqual(r["changes"]["added"], ["real.py"])


if __name__ == "__main__":
    unittest.main()
