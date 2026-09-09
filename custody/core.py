"""Pure decision logic for one tool-call receipt.

No filesystem beyond a single `hash_file()`, no stdin/stdout, no Providence
envelope writing -- `hook.py` does that I/O. This module only decides what a
receipt says once the before/after facts are already in hand, so it can be
tested with plain dicts and strings, the same way `receipt.snapshot.diff()`
itself is pure.
"""
from __future__ import annotations

import hashlib
import pathlib

PASS, FAIL, UNVERIFIED = "pass", "fail", "unverified"


def hash_file(path: str) -> str | None:
    """sha256 of a file's bytes, or None if it doesn't exist -- a `Write`
    call can create a file that had nothing to hash before it ran."""
    p = pathlib.Path(path)
    if not p.exists():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def file_receipt(*, file_path: str, before_hash: str | None, after_hash: str | None,
                  tool_name: str, tool_success: bool | None) -> dict:
    """Edit/Write: the declared scope is trivially the one path the tool
    itself named in its own call -- there is no "unexpected file" it could
    have touched instead, the way a shell command could. So the useful
    question here isn't scope containment, it's independent confirmation:
    does the tool's own `tool_response.success` claim match what the file's
    bytes on disk actually did, checked by a second, separate code path
    (a sha256 taken before and after) rather than trusted from the
    transcript alone.
    """
    if after_hash is None:
        # Edit/Write always leaves a file behind on a real success -- this
        # is a genuine anomaly, not a shape this function is designed around.
        return {
            "declared_scope": [file_path],
            "status": UNVERIFIED,
            "detail": (f"{file_path} does not exist after the {tool_name} call "
                       "completed -- cannot confirm anything was written"),
            "changed": None,
            "tool_reported_success": tool_success,
        }

    changed = before_hash != after_hash
    kind = "created" if before_hash is None else ("modified" if changed else "unchanged")

    if tool_success is False and changed:
        return {
            "declared_scope": [file_path], "status": FAIL,
            "detail": f"{tool_name} reported failure, but {file_path} changed on disk anyway",
            "changed": kind, "tool_reported_success": tool_success,
        }
    if tool_success is True and not changed:
        return {
            "declared_scope": [file_path], "status": FAIL,
            "detail": (f"{tool_name} reported success, but {file_path}'s content is "
                       "byte-identical to before -- nothing was actually written"),
            "changed": kind, "tool_reported_success": tool_success,
        }
    if tool_success is None:
        return {
            "declared_scope": [file_path], "status": UNVERIFIED,
            "detail": (f"no tool_response.success was recorded for this {tool_name} call; "
                       f"the file {kind} on disk, but the tool's own claim is unknown"),
            "changed": kind, "tool_reported_success": tool_success,
        }
    return {
        "declared_scope": [file_path], "status": PASS,
        "detail": f"{tool_name} reported success, and {file_path} {kind} on disk to match",
        "changed": kind, "tool_reported_success": tool_success,
    }


def _drop_own_state(files: dict[str, dict], prefix: str = ".custody/") -> dict[str, dict]:
    """A Bash snapshot of the cwd would otherwise see custody's own pending/
    receipt files from earlier tool calls in the same session and report
    them as noise on every later diff -- excluded the same way `receipt`
    already excludes its own `receipts/` output directory."""
    return {p: v for p, v in files.items() if not p.startswith(prefix)}


def bash_receipt(*, before: dict, after: dict, tool_success: bool | None, diff_fn) -> dict:
    """Bash: unlike Edit/Write, a shell command really could touch anything
    -- but this hook has no mechanism to collect a declared scope from an
    autonomous agent's command text the way `receipt run --declare` collects
    one typed by a human. So this stays at the same honest floor `receipt`
    itself uses with no `--declare`: UNVERIFIED, never a guessed pass or
    fail. What changed is still recorded -- observed, just not judged.
    Wiring a real declaration mechanism (e.g. a sidecar file a skill writes
    before running a command) is real future work, deliberately not done
    here -- see README.md.
    """
    changes = diff_fn(_drop_own_state(before), _drop_own_state(after))
    touched = sum(len(v) for v in changes.values())
    detail = ("no files changed" if touched == 0 else
              f"{touched} path(s) changed; no declared scope was given for "
              "this command, so this is observed, not judged")
    return {
        "declared_scope": None, "status": UNVERIFIED, "detail": detail,
        "changes": changes, "tool_reported_success": tool_success,
    }
