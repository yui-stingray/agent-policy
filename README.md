# agent-policy

> Pure-function policy matrix for AI coding agents.
> Maps `(repo, capability, context)` to one of three modes:
> `deny` / `require_approval` / `auto_allow`.

**Status**: `0.1.6` alpha. The core evaluator API is stable for v0.1;
additive wrapper helpers may still grow while the package is alpha.

## Why

AI coding agents (Claude Code, Codex, Aider, and friends) need a single
place to answer one question, the same way, every time:

> "The agent wants to do X in repo Y — should I let it?"

`agent-policy` is that single place. It is deliberately tiny:

- **One pure function** — `evaluate(policy, repo, capability, context)`.
- **No I/O, no logging, no global state.** The evaluator does not touch
  disk, network, or clocks. It is safe to call from a hook, a test, or a
  long-running daemon.
- **Fail-closed defaults.** A missing `default_mode` is `require_approval`,
  unknown fields in policy files are rejected, and hard guardrails cannot
  be overridden by repo policy.

It does **not** parse shell commands, manage state, or send messages.
Those belong in the wrapper layer that calls `evaluate`.

## Agent safety toolkit

`agent-policy` is one half of a small agent safety toolkit for repositories
touched by coding agents such as Codex, Claude Code, Aider, and similar tools.
It answers the runtime authorization question:

> "Given this repo, capability, and context, should the agent be denied,
> require approval, or be allowed?"

Pair it with [`agent-guard`](https://github.com/yui-stingray/agent-guard),
which answers the static repository question:

> "Does the repository content still obey the safety rules before hooks, CI,
> release, or publication?"

The intended split is:

| Layer | Tool | Responsibility |
| --- | --- | --- |
| Runtime admission | `agent-policy` | Decide whether a normalized agent action is `deny`, `require_approval`, or `auto_allow`. |
| Static repository gate | `agent-guard` | Scan paths, text, API surfaces, and pinned digests for repository safety drift. |

A practical setup uses `agent-policy` in a shell hook or wrapper before an
agent performs a side effect, then runs `agent-guard` in CI or pre-release
checks before the repository is published or merged.

See
[`agent-safety-toolkit-example`](https://github.com/yui-stingray/agent-safety-toolkit-example)
for a small public demo that wires the two tools together.

## Install

```bash
pip install yui-agent-policy
```

From a source checkout, install the package in editable mode so both the
library and `examples/check.py` can resolve `import agent_policy`:

```bash
pip install -e .
```

Requires Python 3.11+ (uses stdlib `tomllib`). The only runtime dependency
is `pydantic >= 2`.

## Quick start

```python
from agent_policy import evaluate, PolicyMatrix, RepoPolicy

policy = PolicyMatrix(
    default_mode="require_approval",
    repo_policy=[
        RepoPolicy(
            repo="acme/app",
            ownership_class="internal",
            capabilities={
                "read": "auto_allow",
                "commit": "auto_allow",
                "push": "auto_allow",
                "shell": "require_approval",
            },
        ),
    ],
)

decision = evaluate(
    policy,
    repo="acme/app",
    capability="commit",
    context={"ownership_class": "internal"},
)

print(decision.mode)         # "auto_allow"
print(decision.reason)       # "repo_policy"
print(decision.matched_repo) # "acme/app"
```

Load the same policy from a TOML file:

```python
from agent_policy import evaluate, load_policy_file

policy = load_policy_file("policy.toml")
decision = evaluate(policy, repo="acme/app", capability="commit")
```

`evaluate` also accepts a plain `dict` in the same shape as `PolicyMatrix`,
which is convenient for tests and one-off scripts.

## Decision model

Every call returns a frozen `PolicyDecision` with three fields:

| Field          | Type                                       | Meaning                                      |
|----------------|--------------------------------------------|----------------------------------------------|
| `mode`         | `"deny" \| "require_approval" \| "auto_allow"` | What the caller should do.              |
| `reason`       | `"hard_guardrail" \| "repo_policy" \| "default_mode" \| ...` | Which rule produced the decision. |
| `matched_repo` | `str \| None`                              | The repo string that matched, or `None`.     |

Decisions are evaluated in this order:

1. **Hard guardrails** — cannot be overridden by repo policy.
   - `push.force` → always `deny`.
   - `merge.pr` → always `require_approval`.
   - External `first_write_to_repo` on a **mutating** capability →
     `require_approval`. Read is not blocked.
2. **Repo policy match** — every `[[repo_policy]]` entry for the requested
   repo is scanned (optionally gated by `ownership_class`). The first
   entry that declares the capability wins. Splitting a repo's policy
   across multiple entries is supported.
3. **`default_mode` fallback** — used when no repo policy declares the
   capability. Defaults to `require_approval` if unset.

`HARD_GUARDRAILS` is exported as a constant so tooling can assert against
it without importing private symbols.

## Audit event schema

Wrappers that need logs or approval records can build a deterministic audit
event from the same inputs they pass to `evaluate`:

```python
from agent_policy import audit_event_to_json, build_audit_event, evaluate

context = {"ownership_class": "internal"}
decision = evaluate(policy, repo="acme/app", capability="shell", context=context)
event = build_audit_event(
    repo="acme/app",
    capability="shell",
    context=context,
    decision=decision,
    session_id="session-123",
    path="scripts/release.sh",
)

print(audit_event_to_json(event))
```

The event is an immutable value object with these fields:

| Field | Meaning |
| --- | --- |
| `repo` | Repository identifier evaluated by the wrapper. |
| `capability` | Normalized capability evaluated by the wrapper. |
| `context` | Recursively copied, key-sorted JSON-compatible context used for the decision. |
| `decision` | Nested `PolicyDecision` payload. |
| `session_id`, `command`, `path` | Optional wrapper-supplied identifiers. |

Installed wheels include a JSON Schema resource at
`agent_policy.schemas/agent-policy.audit_event.v1.schema.json`. Downstream
wrappers can load it with `importlib.resources` to validate the event shape
they write to logs, CI artifacts, or approval records. The schema describes
the deterministic payload only; it does not prove that a human approval exists.

`session_id`, `command`, and `path` are serialized verbatim when supplied.
The packaged `.v1` schema intentionally accepts any string for those optional
fields for backward compatibility. Operators should still enforce public-safe
values and keep them redacted before calling `build_audit_event()`:

| Field | Recommended operator constraint |
| --- | --- |
| `session_id` | 1-256 characters, matching `^[A-Za-z0-9._:@/+~-]+$`. |
| `command` | 1-4096 characters, redacted, with no control characters: `^[^\x00-\x1f]+$`. |
| `path` | 1-1024 characters with no leading POSIX slash or control characters: `^[^/\x00-\x1f][^\x00-\x1f]*$`; producers should also reject parent traversal and alternate local-path syntax before treating it as repository-relative. |

Do not pass private command transcripts, absolute local paths, secrets, or
personal identifiers into these fields if the event may be stored or
published. The bundled `examples/check.py --audit-event` producer enforces
these stricter optional-field constraints before serialization, including
rejection of parent traversal components, absolute POSIX paths, Windows drive
or UNC paths, local home or environment shorthand, file URI syntax, empty
strings, overlong values, and control characters. A future
`agent-policy.audit_event.v1.1` schema may make stricter constraints
machine-checkable for downstream consumers, but regex validation alone cannot
prove repository containment.

`agent-policy` does not persist events, generate timestamps, create IDs,
hash approvals, redact optional wrapper strings, normalize local paths, add a
`schema_version` field, or verify approval records. Those remain wrapper-owned
side effects so the evaluator stays pure and deterministic.

## Policy file format

```toml
# policy.toml
default_mode = "require_approval"

[[repo_policy]]
repo = "acme/app"
ownership_class = "internal"

[repo_policy.capabilities]
read = "auto_allow"
commit = "auto_allow"
push = "auto_allow"

[[repo_policy]]
repo = "acme/app"                # same repo, extra constraint
[repo_policy.capabilities]
shell = "require_approval"
```

Unknown top-level fields or typos inside `[[repo_policy]]` fail loudly
with a `pydantic.ValidationError` — there is no silent degradation.

## Wrapper pattern

`agent-policy` deliberately does not know how to parse `git push --force`
or a shell command line. The intended shape is:

```
           ┌────────────────────────┐
agent ───▶ │ wrapper (hook / CLI)   │ ──▶ agent-policy.evaluate()
           │  - normalize capability│         │
           │  - build context       │         ▼
           │  - act on decision     │   PolicyDecision
           └────────────────────────┘
```

The wrapper owns: parsing the agent's intent, mapping it to one of the
MVP capabilities (`read`, `write`, `commit`, `push`, `push.force`,
`merge.pr`, `shell`), and executing whatever side effect the decision
implies (block, prompt for approval, log and allow).

A runnable minimal wrapper lives in [`examples/check.py`](examples/check.py).

### Approval wrapper checklist

For `require_approval` decisions, keep the approval layer outside
`agent-policy` but make the wrapper contract explicit. Production wrappers
should:

- Bind approval records to the exact capability, session, path, and command
  being executed. A command change after approval should fail closed.
- Record the serialized audit event or a downstream hash of it in the approval
  record, then verify that the referenced event still exists before running
  the approved command.
- Treat approvals as single-use for side-effecting operations such as
  `artifact.publish`; reserve a local use marker before executing the command
  so retry races cannot reuse the same approval.
- Keep bypass corpora, private logs, `.env*` files, and red-team transcripts
  outside tracked paths, and add an independent scanner such as
  `yui-agent-guard` to CI.

### Wrapper contract summary

The stable wrapper contract is:

- `evaluate()` performs no I/O and has no approval, logging, or storage side
  effects.
- Wrappers own command parsing, capability normalization, approval storage,
  logging, redaction, audit-event schema validation, and any human prompt.
- `examples/check.py` maps decisions to process exits: `0` for `auto_allow`,
  `1` for wrapper/program errors, `2` for `require_approval`, and `3` for
  `deny`.
- Agent-specific hooks may translate both `require_approval` and `deny` to the
  hook platform's blocking exit code when the platform has no inline approval
  state.
- `--audit-event` emits deterministic evidence for the decision that was made;
  it is not itself an approval record.

These checks belong in the wrapper/admission layer rather than the pure
evaluator. The `ai_resilience_policy.toml` example shows the capability
vocabulary; downstream repositories can combine it with their own approval
record schema and CI gates.

## Examples

See [`examples/`](examples/). Runnable after installing the package
(`pip install yui-agent-policy`, or `pip install -e .` from a source checkout):

- `policy.toml` — a minimal fail-closed policy with two repos.
- `ai_resilience_policy.toml` — a safety-oriented vocabulary example for
  publication, constitution, audit, secret-materialization, and scanner
  policy changes. These remain repo-policy capabilities rather than hard
  guardrails until downstream wrappers prove they are universal invariants.
- `check.py` — a tiny CLI wrapper that maps `PolicyDecision` to JSON on
  stdout and a process exit code, suitable for PreToolUse hooks. Pass
  `--audit-event` to emit the wrapper-owned audit event schema instead of
  the bare decision payload.
- `claude_code_hook.sh` — a Claude Code `PreToolUse` hook that reads the
  hook payload from stdin, maps the tool to a capability, and shells out
  to `check.py`. Set `AGENT_POLICY_FILE` and `AGENT_POLICY_REPO` in the
  hook's environment, then point `~/.claude/settings.json` at it.
- `codex_hook.sh` — a Codex CLI `PreToolUse` hook (**block-style shell
  guardrail pilot**). This wrapper only maps Bash commands: `git push
  --force` to `push.force`, `gh pr merge` to `merge.pr`, and everything else
  to `shell`. Codex hooks themselves can match more than Bash; this example is
  intentionally narrower.
- `codex_permission_request_hook.sh` — a Codex CLI `PermissionRequest` hook
  (**delegation shell pilot**). It returns `allow` for `auto_allow`, returns
  `deny` for `deny`, and returns no decision for `require_approval`, which
  delegates to Codex's normal approval prompt.
- `capability_map.py` — stdlib-only helper that turns a raw Bash
  command into one of `push.force` / `merge.pr` / `shell`. The hook
  wrappers shell out to it instead of doing substring matching, so
  quoted literals like `printf '%s\n' 'git push --force'` no longer
  produce a false `push.force` classification. See the file header
  for the exact algorithm (heredoc stripping → `shlex` tokenization →
  scan-anywhere → recursive `bash -c` / `eval`).

### Codex CLI hooks — current contract notes

Codex hooks are default enabled. If you need an explicit feature setting, use
`features.hooks`; older Codex-specific aliases should be avoided. Put
`hooks.json` in `~/.codex/` or `<repo>/.codex/`.

Current `PreToolUse` matchers can target Bash, `apply_patch`, and MCP tool calls;
`apply_patch` may also match through `Edit` / `Write` aliases. The examples here
stay Bash-focused because they reuse `capability_map.py`, which only normalizes
shell commands.

- **Block-style PreToolUse path.** `permissionDecision: "ask"` is parsed but not supported
  by current Codex release behavior. A hook that returns it is reported as a hook
  run failure and the tool call continues. To stop execution from a `PreToolUse`
  hook, return `permissionDecision: "deny"`, legacy `decision: "block"`, or exit
  `2`. `examples/codex_hook.sh` uses exit `2` for both `deny` and
  `require_approval`, with stderr distinguishing the policy decision.
- **PermissionRequest delegation path.** `PermissionRequest` runs just before
  Codex asks for approval. A hook can return `allow` or `deny`; if it returns
  no decision, Codex falls back to the normal approval flow. The
  `codex_permission_request_hook.sh` example uses that no-decision case for
  `require_approval`.
- **Heuristic command parsing.** `capability_map.py` is `shlex`-based,
  not a full shell. It handles quoted literals, heredocs, compound
  statements, and the common `bash -c '...'` / `eval` wrappers, but
  exotic forms such as `git --git-dir=/path push --force`, process
  substitution, or function definitions are not modeled. The
  fail-closed default is `shell`, which policy can still flag as
  `require_approval` or `deny`.

## Releases

Tag-driven. Pushing a `vX.Y.Z` annotated tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml), which first verifies that the tag matches `[project].version` in `pyproject.toml`, checks that the version is not already present on PyPI, then builds the sdist + wheel and publishes to PyPI via Trusted Publishing (OIDC). No maintainer-side credentials are required. Manual `workflow_dispatch` with `publish=false` is a build-only dry run; it skips the publish job. Manual `publish=true` must be run against a `v*` tag ref; running it from a branch fails before build.

## License

MIT.
