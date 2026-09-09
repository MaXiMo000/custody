# custody

[![ci](https://github.com/MaXiMo000/custody/actions/workflows/ci.yml/badge.svg)](https://github.com/MaXiMo000/custody/actions/workflows/ci.yml)

**A receipt for every tool call an AI coding agent makes -- not just a diff
at the end of the session.**

[`receipt`](https://github.com/MaXiMo000/receipt) proves what one shell
command actually touched, once, when a human runs it with a declared scope.
An agentic coding session is a few hundred tool calls with nobody typing
`--declare` before each one. `custody` is a Claude Code hook: it watches
`Edit`, `Write`, and `Bash` calls live, and for each one, independently
confirms -- from a sha256 taken before and after, a separate code path from
the tool's own self-report -- whether what the tool *said* happened is what
actually happened on disk.

```
$ cat .custody/receipts/toolu_01ABC123....json
{
  "providence_version": 1,
  "tool": "custody",
  "payload": {
    "tool_name": "Edit",
    "declared_scope": ["/repo/auth.py"],
    "status": "fail",
    "detail": "Edit reported success, but /repo/auth.py's content is byte-identical to before -- nothing was actually written",
    "changed": "unchanged",
    "tool_reported_success": true
  },
  "sha256": "9fab..."
}
```

That specific failure is real and not hypothetical: a partial write, a
silently-swallowed permission error, a race with another process -- any of
them can leave a tool call reporting success while nothing changed. A
transcript review catches this only if someone happens to look. A receipt
catches it every time, for every call, automatically.

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

One binary, dispatched by the `hook_event_name` field Claude Code already
sends on stdin -- registering it under both events doesn't mean writing two
scripts. Receipts land in `.custody/receipts/` under the session's own
`cwd`; add that directory to `.gitignore` unless you specifically want to
commit them.

## What "declared scope" means, per tool

**`Edit` and `Write`** declare their own scope: `tool_input.file_path` *is*
the one thing the call could possibly touch, unlike a shell command that
could touch anything. So the interesting question here isn't containment
(structurally guaranteed by the tool's own interface) -- it's whether the
tool's `tool_response.success` claim matches an independent, separately
computed sha256 diff of that exact file. Four outcomes, all live-tested in
`tests/test_hook.py`:

| tool claims | file actually changed | status |
|---|---|---|
| success | yes | `pass` |
| success | no | **`fail`** -- claimed a write that didn't happen |
| failure | yes | **`fail`** -- claimed nothing happened, but something did |
| failure | no | `pass` -- the claim and reality agree |

**`Bash`** gets no declared scope at all. A shell command really could
touch anything, but this hook has no mechanism to collect a declaration
from an autonomous agent's command text the way `receipt run --declare`
collects one a human typed. So a Bash receipt stays at the same honest
floor `receipt` itself uses with no `--declare`: `unverified`, never a
guessed pass or fail -- what changed is still recorded (via `receipt`'s own
`snapshot()`/`diff()`, reused directly, not reimplemented), just not
judged. A real declaration mechanism -- a sidecar file a skill writes
before running a command it wants scoped -- is real future work,
deliberately not built here.

## Why this depends on `receipt`

`custody`'s Bash path is, almost literally, the audit's own MVP note for
this idea: "a thin wrapper around receipt's existing snapshot-diff core,
called once per tool invocation instead of once per whole session."
`receipt.snapshot.snapshot()` and `.diff()` are imported directly --
`pip install custody-evidence` pulls in `receipt-evidence` for exactly
this, not duplicated logic under a different name.

## Providence

Every receipt is written as a [Providence](https://github.com/MaXiMo000/providence)
single-file bundle (`providence_version`, `payload`, `sha256` over the
payload's canonical serialization) -- by hand, to `SPEC.md`'s documented
recipe, rather than as a dependency on the `providence` package, which
hadn't been published to PyPI yet when this was written. `providence check`
(once installed) validates a `.custody/receipts/*.json` file with no
`custody`-specific code of its own. This is the "third tool" `providence`'s
own `SPEC.md` invites -- the first to write the shape natively instead of
needing a converter.

## What custody does not do

- **Never blocks a tool call.** No `permissionDecision` is ever emitted;
  the hook always exits 0. This is a witness, not a gate -- the same
  posture `receipt` itself has.
- **No declared scope for Bash** (see above) -- this is a stated limit, not
  a bug to report.
- **Doesn't catch a file changed by something other than the hooked tool
  call.** Claude Code's own docs note that a `PostToolUse` hook matching
  `Edit|Write` doesn't fire when a `Bash` command or an external process
  rewrites the same file -- `custody`'s Bash hook is what catches that case
  instead, unverified but observed.
- **An orphaned `.custody/pending/*.json`** (a crash between `PreToolUse`
  and `PostToolUse`) is harmless and ignorable -- it's cleaned up the next
  time that exact `tool_use_id` completes, which never happens for an
  abandoned one. A `custody gc` command to sweep old ones on a schedule is
  a five-line addition, not built here.

## Tests

```
pip install -e .
python tests/test_core.py    # pure decision logic, no filesystem beyond one hash
python tests/test_hook.py    # real files, real temp dirs, real stdin/stdout,
                              # event JSON shaped exactly like Claude Code's
                              # own documented PreToolUse/PostToolUse payloads
```

## Status

Built and tested against Claude Code's published hook contract (exact
field names for `PreToolUse`/`PostToolUse` input, the
`tool_response.success` shape, the `hooks`/`matcher`/`command`
registration format). Verified at three levels, not just unit tests:

1. `tests/test_core.py` / `tests/test_hook.py` -- pure logic and real
   files, run in-process.
2. The real installed console script (`pip install -e .` into a clean
   venv, real `receipt-evidence` dependency resolved from the sibling
   checkout), piped real doc-shaped JSON over an actual OS pipe via
   `subprocess`/shell, in a real scratch directory -- catches packaging and
   entry-point breaks the in-process tests can't, and confirmed all four
   Edit/Write outcomes and the Bash "observed, not judged" path live, not
   just asserted.
3. **Cross-repo**: the receipt that produced ran through `providence check`
   -- a second, independently-written tool built by someone else's spec (in
   this portfolio, someone else being the same author on a different day)
   -- and it validated clean, with no `custody`-specific code in the
   checker at all.

**Not yet done**: registering it in an actual `.claude/settings.json` and
watching a real, live Claude Code session fire it during real Edit/Write/Bash
tool calls end to end -- the one remaining mile between "the contract is
implemented correctly" and "it does this automatically at the end of every
tool call in daily use." That's the next thing to do before trusting this
beyond what the three levels above already pin down.

MIT licensed.
