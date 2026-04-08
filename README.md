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
- `check.py` — a tiny CLI wrapper that maps `PolicyDecision` to JSON on
  stdout and a process exit code, suitable for PreToolUse hooks.

## Releases

Tag-driven. Pushing a `vX.Y.Z` annotated tag triggers [`.github/workflows/release.yml`](.github/workflows/release.yml), which builds the sdist + wheel and publishes to PyPI via Trusted Publishing (OIDC). No maintainer-side credentials are required.

## License

MIT.
