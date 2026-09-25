# custody

[![ci](https://github.com/MaXiMo000/custody/actions/workflows/ci.yml/badge.svg)](https://github.com/MaXiMo000/custody/actions/workflows/ci.yml)

**A receipt for every tool call an AI coding agent makes -- not just a diff
at the end of the session.**

[`receipt`](https://github.com/MaXiMo000/receipt) proves what one shell
command actually touched, once, when a human runs it with a declared scope.
An agentic coding session is a few hundred tool calls with nobody typing
`--declare` before each one. `custody` is a Claude Code hook: it watches
`Edit`, `Write`, and `Bash` calls live, and for each `Edit`/`Write` it
checks, from the file's real bytes, that **what's on disk is exactly what
the tool says it wrote** -- a separate code path from the tool's own
self-report.

```
$ cat .custody/receipts/toolu_01ABC123....json
{
  "providence_version": 1,
  "tool": "custody",
  "payload": {
    "tool_name": "Write",
    "declared_scope": ["/repo/auth.py"],
    "status": "fail",
    "detail": "/repo/auth.py on disk is not what Write reported writing -- a partial write, or something else changed it in between",
    "changed": "modified",
    "tool_reported_success": true
  },
  "sha256": "9fab..."
}
```

A partial write, a silently-swallowed permission error, a formatter or
another process racing the agent -- any of them can leave a tool call
reporting success while the file holds something else. A transcript review
catches this only if someone happens to look. A receipt catches it every
time, for every call, automatically.

## Install

```
pip install custody-evidence   # the command it installs is `custody-hook`
```

(`custody` was already taken on PyPI -- same story as `receipt-evidence`
and `providence-evidence` in this portfolio.)

## Wire it into Claude Code

Merge [`hooks/settings.snippet.json`](hooks/settings.snippet.json) into
`.claude/settings.json` (project) or `~/.claude/settings.json` (every
project):

```json
{
  "hooks": {
    "PreToolUse": [{ "matcher": "Edit|Write|Bash",
      "hooks": [{ "type": "command", "command": "custody-hook" }] }],
    "PostToolUse": [{ "matcher": "Edit|Write|Bash",
      "hooks": [{ "type": "command", "command": "custody-hook" }] }]
  }
}
```

**`custody-hook` has to be on the `PATH` Claude Code runs hooks with.** A
project-local `.venv` never is, and a hook command that doesn't resolve
fails silently -- no receipts, no error. Either install it with a Python
whose scripts directory is already on `PATH` (`pipx install
custody-evidence` is the cleanest), or put the absolute path in `command`,
e.g. `"/path/to/venv/bin/custody-hook"` (on Windows, quote it:
`"\"C:/path/to/venv/Scripts/custody-hook.exe\""`).

One binary, dispatched by the `hook_event_name` field Claude Code already
sends on stdin -- registering it under both events doesn't mean writing two
scripts. Receipts land in `.custody/receipts/` under the session's own
`cwd`; add that directory to `.gitignore` unless you specifically want to
commit them.

## What gets checked, per tool

**`Edit` and `Write`** declare their own scope: `tool_input.file_path` *is*
the one thing the call could possibly touch. So the question isn't
containment -- it's whether the file on disk matches what the tool reports
having written. Claude Code's `PostToolUse` response carries enough to
answer that exactly: `Write` reports the `content` it wrote, and `Edit`
reports the `originalFile` plus the `oldString` -> `newString` replacement.
custody rebuilds the text the tool claims to have left on disk and
compares it to the file's real bytes.

| what's on disk | status |
|---|---|
| exactly the text the tool reported writing | `pass` |
| anything else | **`fail`** -- the tool's claim and the file disagree |
| the file is gone after the call | `unverified` |
| not UTF-8, so it can't be compared as text | falls back to a before/after sha256: changed = `pass`, byte-identical = `fail` |

Two things are deliberately *not* a difference, because the tool preserves
them on disk while reporting normalized text (both measured against real
Claude Code, not assumed): a file's CRLF line endings, and a UTF-8 BOM.

`PostToolUse` only fires after a tool succeeded -- failures are a separate
`PostToolUseFailure` event -- and real `Edit`/`Write` responses have no
`success` field. So `tool_reported_success` is `true` on every `Edit`/`Write`
receipt: the event arriving *is* the success claim being checked.

**`Bash`** gets no declared scope at all. A shell command really could
touch anything, but this hook has no mechanism to collect a declaration
from an autonomous agent's command text the way `receipt run --declare`
collects one a human typed. So a Bash receipt stays at the same honest
floor `receipt` itself uses with no `--declare`: `unverified`, never a
guessed pass or fail -- what changed is still recorded (via `receipt`'s own
`snapshot()`/`diff()`, reused directly, not reimplemented), just not
judged. `tool_reported_success` is `null` on Bash receipts: Bash's response
is `{stdout, stderr, interrupted, isImage}`, and whether a non-zero exit
counts as a tool failure hasn't been measured.

## Why this depends on `receipt`

`receipt.snapshot.snapshot()` and `.diff()` power the Bash path and are
imported directly -- `pip install custody-evidence` pulls in
`receipt-evidence` for exactly this, not duplicated logic under a different
name.

## Providence

Every receipt is written as a [Providence](https://github.com/MaXiMo000/providence)
single-file bundle (`providence_version`, `payload`, `sha256` over the
payload's canonical serialization), to `SPEC.md`'s documented recipe.
`providence check` validates a `.custody/receipts/*.json` file with no
`custody`-specific code of its own.

## What custody does not do

- **Never blocks a tool call.** No `permissionDecision` is ever emitted;
  the hook always exits 0. This is a witness, not a gate.
- **No declared scope for Bash** (see above) -- a stated limit, not a bug.
- **Doesn't catch a file changed by something other than the hooked tool
  call.** A `PostToolUse` hook matching `Edit|Write` doesn't fire when a
  `Bash` command or an external process rewrites the same file --
  `custody`'s Bash hook is what records that case instead, observed but
  not judged.
- **Bash snapshots hash the whole working directory**, before and after
  every command (skipping `.git`, `node_modules`, `.venv`). Fine for a
  normal repo; slow on a huge one.
- **An orphaned `.custody/pending/*.json`** (a crash between `PreToolUse`
  and `PostToolUse`) is harmless and never cleaned up automatically.

## Tests

```
pip install -e .
python tests/test_core.py    # pure decision logic
python tests/test_hook.py    # real files, real temp dirs, real stdin/stdout,
                              # tool_response shapes captured from real
                              # Claude Code PostToolUse events
```

CI runs both on Linux and Windows.

## Status: verified live

Checked end to end in a real Claude Code 2.1 session (`claude -p`, the
project's `.claude/settings.json` pointing at the installed hook), on
Windows: a real `Write`, two real `Edit`s (one on a CRLF file), and a real
`Bash` call each produced a receipt -- `pass`, `pass`, `pass` and
`unverified` -- with no hand-built events involved.

Earlier attempts to see the hook fire got no receipts at all. The cause
was the one [playhead](https://github.com/MaXiMo000/playhead) later pinned
down: the hook command was `custody-hook`, installed only into a
project-local `.venv`, which isn't on the `PATH` Claude Code runs hooks
with -- and a hook command that doesn't resolve fails silently. That's why
"Wire it into Claude Code" above now says so first.

## Dogfooding

`.claude/settings.json` in this repo wires custody onto itself. With
`custody-hook` on `PATH` (see above), editing a file in this checkout with
Claude Code writes a real receipt to `.custody/receipts/` for that edit.

MIT licensed.
