# agent-policy

> Pure-function policy matrix for AI coding agents.
> Maps `(repo, capability, context)` to one of three modes:
> `deny` / `require_approval` / `auto_allow`.

**Status**: `0.1.0` alpha. The public API is frozen for v0.1; examples and
hook/wrapper recipes will grow in v0.2.

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

## Install

```bash
pip install yui-agent-policy  # once published to PyPI
```

From a source checkout (until the PyPI release is live), install the
package in editable mode so both the library and `examples/check.py` can
resolve `import agent_policy`:

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

## Examples

See [`examples/`](examples/). Runnable after installing the package
(`pip install yui-agent-policy`, or `pip install -e .` from a source checkout):

- `policy.toml` — a minimal fail-closed policy with two repos.
- `ai_resilience_policy.toml` — a safety-oriented vocabulary example for
  publication, constitution, audit, secret-materialization, and scanner
  policy changes. These remain repo-policy capabilities rather than hard
  guardrails until downstream wrappers prove they are universal invariants.
- `check.py` — a tiny CLI wrapper that maps `PolicyDecision` to JSON on
  stdout and a process exit code, suitable for PreToolUse hooks.
- `claude_code_hook.sh` — a Claude Code `PreToolUse` hook that reads the
  hook payload from stdin, maps the tool to a capability, and shells out
  to `check.py`. Set `AGENT_POLICY_FILE` and `AGENT_POLICY_REPO` in the
  hook's environment, then point `~/.claude/settings.json` at it.
- `codex_hook.sh` — a Codex CLI `PreToolUse` hook (**shell guardrail
  pilot**). Codex hooks currently intercept Bash commands only — read,
  write, and edit operations are not covered. Maps `git push --force` to
  `push.force`, `gh pr merge` to `merge.pr`, and everything else to
  `shell`. Requires `features.codex_hooks = true` in your Codex config
  and a `hooks.json` in `~/.codex/` or `<repo>/.codex/`.
- `capability_map.py` — stdlib-only helper that turns a raw Bash
  command into one of `push.force` / `merge.pr` / `shell`. Both hook
  wrappers shell out to it instead of doing substring matching, so
  quoted literals like `printf '%s\n' 'git push --force'` no longer
  produce a false `push.force` classification. See the file header
  for the exact algorithm (heredoc stripping → `shlex` tokenization →
  scan-anywhere → recursive `bash -c` / `eval`).

### Codex CLI hook — known MVP limitations

The Codex CLI hook feature is marked "Under development" in upstream
docs. Two gaps affect how `agent-policy` presents decisions through
it, and they are worth knowing before you enable it:

- **`require_approval` degrades to block.** Codex hook events accept
  only `allow` or `deny` — there is no `permissionDecision: "ask"`
  yet. `examples/codex_hook.sh` therefore exits `2` for both
  `deny` and `require_approval`, and the only UX signal distinguishing
  the two is the stderr line (`DENY ...` vs
  `require_approval ...`). Users must retry after manual approval
  rather than being prompted inline.
- **Bash-only scope.** Codex hooks intercept shell commands and
  nothing else. Read, write, and edit tool calls are invisible — if
  you need capability gating on those, use the Claude Code hook.
- **Heuristic command parsing.** `capability_map.py` is `shlex`-based,
  not a full shell. It handles quoted literals, heredocs, compound
  statements, and the common `bash -c '...'` / `eval` wrappers, but
  exotic forms such as `git --git-dir=/path push --force`, process
  substitution, or function definitions are not modeled. The
  fail-closed default is `shell`, which policy can still flag as
  `require_approval` or `deny`.

## Releases

Tag-driven. Pushing a `vX.Y.Z` annotated tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml), which first verifies that the tag matches `[project].version` in `pyproject.toml`, checks that the version is not already present on PyPI, then builds the sdist + wheel and publishes to PyPI via Trusted Publishing (OIDC). No maintainer-side credentials are required. Manual `workflow_dispatch` with `publish=false` is a build-only dry run; it skips the publish job.

## License

MIT.
