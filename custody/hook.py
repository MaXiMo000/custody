"""stdin/stdout glue for Claude Code's PreToolUse/PostToolUse hooks.

Reads the JSON Claude Code sends on stdin (see
https://docs.claude.com/en/docs/claude-code/hooks), snapshots/diffs the
tool's declared file (Edit, Write) or working directory (Bash), and writes
one Providence-conformant receipt per tool call to `.custody/receipts/`.

Never blocks a tool call: no `permissionDecision` is ever emitted, and this
always exits 0. This is a witness, not a gate -- the same non-blocking
posture `receipt` itself has (it judges a command after the fact, it
doesn't stop one from running).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys
import time

from receipt.snapshot import diff as diff_dirs
from receipt.snapshot import snapshot as snapshot_dir

from . import core

# Bash gets no declared scope at all (see core.bash_receipt) but is still
# hooked, so "what changed" is on record even when "was it in scope" isn't
# an answerable question -- see README.md's "What custody does not do".
WATCHED_TOOLS = {"Edit", "Write", "Bash"}


def _state_dir(cwd: str) -> pathlib.Path:
    return pathlib.Path(cwd) / ".custody" / "pending"


def _receipts_dir(cwd: str) -> pathlib.Path:
    return pathlib.Path(cwd) / ".custody" / "receipts"


def _providence_bundle(tool: str, payload: dict) -> dict:
    """A single-file Providence bundle (see README.md's "Providence" section),
    written by hand to the exact recipe in providence's SPEC.md rather than depending
    on the `providence` package -- it wasn't yet published to PyPI when
    this was written. `providence check` (once installed) validates this
    output without custody needing providence as a dependency; the recipe
    is small enough to duplicate correctly, and worth re-checking against
    SPEC.md if either repo's envelope ever changes.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return {
        "providence_version": 1,
        "generated_at": time.time(),
        "tool": tool,
        "payload": payload,
        "sha256": hashlib.sha256(blob).hexdigest(),
    }


def pre_tool_use(event: dict) -> None:
    tool_name = event.get("tool_name")
    if tool_name not in WATCHED_TOOLS:
        return
    tool_use_id = event["tool_use_id"]
    cwd = event.get("cwd", ".")

    if tool_name in ("Edit", "Write"):
        file_path = event["tool_input"]["file_path"]
        state = {"tool_name": tool_name, "file_path": file_path,
                  "before_hash": core.hash_file(file_path)}
    else:  # Bash
        state = {"tool_name": tool_name, "cwd": cwd, "before": snapshot_dir(cwd)}

    state_dir = _state_dir(cwd)
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{tool_use_id}.json").write_text(json.dumps(state))


def post_tool_use(event: dict) -> None:
    tool_name = event.get("tool_name")
    if tool_name not in WATCHED_TOOLS:
        return
    tool_use_id = event["tool_use_id"]
    cwd = event.get("cwd", ".")
    state_path = _state_dir(cwd) / f"{tool_use_id}.json"

    if not state_path.exists():
        # A crash between the two hooks, or the hook being added mid-session,
        # leaves nothing to diff against -- unverified, never a guessed pass.
        payload = {
            "event": "PostToolUse", "tool_name": tool_name, "tool_use_id": tool_use_id,
            "session_id": event.get("session_id"),
            "declared_scope": None, "status": core.UNVERIFIED,
            "detail": ("no matching PreToolUse snapshot found for this tool_use_id -- "
                       "the hook may have been registered mid-session, or the "
                       "PreToolUse call for it failed silently"),
            "tool_reported_success": None,
        }
    else:
        state = json.loads(state_path.read_text())
        tool_response = event.get("tool_response")
        tool_success = tool_response.get("success") if isinstance(tool_response, dict) else None

        if tool_name in ("Edit", "Write"):
            after_hash = core.hash_file(state["file_path"])
            result = core.file_receipt(
                file_path=state["file_path"], before_hash=state["before_hash"],
                after_hash=after_hash, tool_name=tool_name, tool_success=tool_success)
        else:  # Bash
            after = snapshot_dir(state["cwd"])
            result = core.bash_receipt(before=state["before"], after=after,
                                        tool_success=tool_success, diff_fn=diff_dirs)

        payload = {
            "event": "PostToolUse", "tool_name": tool_name, "tool_use_id": tool_use_id,
            "session_id": event.get("session_id"), "duration_ms": event.get("duration_ms"),
            **result,
        }
        state_path.unlink(missing_ok=True)

    receipts_dir = _receipts_dir(cwd)
    receipts_dir.mkdir(parents=True, exist_ok=True)
    bundle = _providence_bundle("custody", payload)
    (receipts_dir / f"{tool_use_id}.json").write_text(json.dumps(bundle, indent=2))


_HANDLERS = {"PreToolUse": pre_tool_use, "PostToolUse": post_tool_use}


def main() -> int:
    event = json.load(sys.stdin)
    handler = _HANDLERS.get(event.get("hook_event_name"))
    if handler:
        try:
            handler(event)
        except Exception as exc:  # noqa: BLE001 -- a hook that crashes must never break the agent's turn
            sys.stderr.write(f"custody: {type(exc).__name__}: {exc}\n")
    return 0  # never blocks: this is a witness, not a gate


if __name__ == "__main__":
    sys.exit(main())
