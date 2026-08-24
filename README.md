# agent-policy

> Pure-function policy matrix for AI coding agents.
> Maps `(repo, capability, context)` to one of three modes:
> `deny` / `require_approval` / `auto_allow`.

**Status**: `0.1.14` alpha. The core evaluator API is stable for v0.1;
additive wrapper helpers may still grow while the package is alpha.

Note: `v0.1.10` is retained as a tag-only, unpublished release attempt; use
`0.1.14` for installation and provenance verification.

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

## Optional runtime companion

[`agent-guard`](https://github.com/yui-stingray/agent-guard) is the standalone
static entry point for repositories touched by coding agents such as Codex,
Claude Code, Aider, and similar tools. It answers the repository question:

> "Does the repository content still obey the safety rules before hooks, CI,
> release, or publication?"

`agent-policy` is an optional advanced runtime companion for integrations that
need action-level authorization:

> "Given this repo, capability, and context, should the agent be denied,
> require approval, or be allowed?"

It is not required by `agent-guard` or a basic static setup. When an integration
needs runtime admission control, it can call `agent-policy` in a hook or wrapper
before an agent performs a side effect.

| Role | Tool | Responsibility |
| --- | --- | --- |
| Standalone static entry | `agent-guard` | Scan paths, text, API surfaces, and pinned digests for repository safety drift. |
| Optional advanced runtime companion | `agent-policy` | Decide whether a normalized agent action is `deny`, `require_approval`, or `auto_allow`. |

[`agent-safety-toolkit-example`](https://github.com/yui-stingray/agent-safety-toolkit-example)
is a reference integration for projects that intentionally use both tools; it
is not required setup.

## Install

```bash
pip install yui-agent-policy==0.1.14
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
   across multiple entries is supported. Entries that can both match the
   same repo, ownership class, and capability must declare the same mode;
   contradictory overlaps fail validation instead of depending on list order.
3. **`default_mode` fallback** — used when no repo policy declares the
   capability. Defaults to `require_approval` if unset.

`HARD_GUARDRAILS` remains an ordinary inspection `dict` for compatibility.
Evaluation uses separate private immutable state, so mutating or rebinding the
public copy cannot weaken the force-push guardrail.

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

Installed wheels include two JSON Schema resources:

| Resource | Contract |
| --- | --- |
| `agent_policy.schemas/agent-policy.audit_event.v1.schema.json` | Backward-compatible event shape. Optional wrapper strings remain unconstrained. |
| `agent_policy.schemas/agent-policy.audit_event.v1.1.schema.json` | Opt-in stricter event shape with additive constraints for `decision.matched_repo`, `session_id`, `command`, and `path`. |

Downstream wrappers can load either resource with `importlib.resources` to
validate the event shape they write to logs, CI artifacts, or approval records:

```python
from importlib import resources
import json

schema_text = (
    resources.files("agent_policy.schemas")
    .joinpath("agent-policy.audit_event.v1.1.schema.json")
    .read_text(encoding="utf-8")
)
schema = json.loads(schema_text)
```

The schemas describe the deterministic payload only; they do not prove that a
human approval exists.

`session_id`, `command`, and `path` are serialized verbatim when supplied.
The packaged `.v1` schema intentionally accepts any string for those optional
fields for backward compatibility. The packaged `.v1.1` schema makes the
recommended length and character constraints machine-checkable for consumers
that opt in. Operators should still enforce public-safe values and keep them
redacted before calling `build_audit_event()`:

| Field | Recommended operator constraint |
| --- | --- |
| `session_id` | 1-256 characters matching `^[A-Za-z0-9._:@/+~-]+$(?![\s\S])`; the final lookahead requires true end-of-input. |
| `command` | 1-4096 redacted characters with no control characters, using `^[^\x00-\x1f]+$(?![\s\S])`. |
| `path` | 1-1024 characters with no leading POSIX slash or control characters, using `^[^/\x00-\x1f][^\x00-\x1f]*$(?![\s\S])`; producers should also reject parent traversal and alternate local-path syntax before treating it as repository-relative. |

Do not pass private command transcripts, absolute local paths, secrets, or
personal identifiers into these fields if the event may be stored or
published. The bundled `examples/check.py --audit-event` producer enforces
these stricter optional-field constraints before serialization, including
rejection of parent traversal components, absolute POSIX paths, Windows drive
or UNC paths, local home or environment shorthand, file URI syntax, empty
strings, overlong values, and control characters.

Schema validation does not redact values, scan for secrets, reject parent
traversal, reject alternate local-path syntax, or prove repository containment.
Those checks remain producer-owned responsibilities.

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
`ownership_class` is a closed optional gate: it accepts only `internal` or
`external`; omit it (or use `None` in Python) to match either ownership class.
Overlapping entries may split capabilities or repeat the same mode, but a
wildcard or ownership-specific overlap that assigns different modes to the
same capability is rejected.

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
- The example hooks take first-write state only from
  `AGENT_POLICY_FIRST_WRITE=true|false`, never from a hook payload. For an
  external mutating capability, it is required: `true` supplies
  `--first-write`, while `false` does not. Read-only Claude tools are
  unaffected.
- `--audit-event` emits deterministic evidence for the decision that was made;
  it is not itself an approval record.

These checks belong in the wrapper/admission layer rather than the pure
evaluator. The `ai_resilience_policy.toml` example shows the capability
vocabulary; downstream repositories can combine it with their own approval
record schema and CI gates.

## Examples

See [`examples/`](examples/). To run them from this source checkout, install it
with `pip install -e .`; public users who need the released library should use
`pip install yui-agent-policy==0.1.14`:

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
  hook's environment, then point `~/.claude/settings.json` at it. Unknown
  tools and wrapper failures block with fixed sanitized stderr.
- `codex_hook.sh` — a Codex CLI `PreToolUse` hook (**block-style shell
  guardrail pilot**). This wrapper only maps Bash commands: `git push
  --force` to `push.force`, `gh pr merge` to `merge.pr`, and everything else
  to `shell`. Codex hooks themselves can match more than Bash; this example is
  intentionally narrower.
- `codex_permission_request_hook.sh` — a Codex CLI `PermissionRequest` hook
  (**delegation shell pilot**). It returns `allow` for `auto_allow`, returns
  `deny` for `deny`, and returns no decision for `require_approval`, which
  delegates to Codex's normal approval prompt. Any wrapper failure returns a
  fixed protocol-valid deny JSON payload instead of exposing an error.
- `capability_map.py` — stdlib-only helper that turns a raw Bash
  command into `push.force` / `merge.pr` / `shell` / `unknown`. The hook
  wrappers shell out to it instead of doing substring matching, so
  quoted literals like `printf '%s\n' 'git push --force'` no longer
  produce a false `push.force` classification. Active arithmetic, active
  unquoted brace or pathname expansion, shell startup selectors and state,
  selected visible dynamic argv execution, and Git subcommands outside the
  bounded builtin allowlist become `unknown` and are rejected before policy
  evaluation. See the file header for the exact bounded flow: heredoc
  stripping, expansion screening, `shlex` tokenization, statement
  classification, and recursive `bash -c` / `eval` handling.

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
  `require_approval`, with one fixed sanitized stderr message.
- **PermissionRequest delegation path.** `PermissionRequest` runs just before
  Codex asks for approval. A hook can return `allow` or `deny`; if it returns
  no decision, Codex falls back to the normal approval flow. The
  `codex_permission_request_hook.sh` example uses that no-decision case for
  `require_approval`.
- **Heuristic command parsing.** `capability_map.py` is `shlex`-based,
  not a full shell. It handles quoted literals, heredocs, compound
  statements, a bounded set of Git global options such as `--git-dir=/path`,
  a deliberate Git builtin allowlist (`status`, `add`, `commit`, `diff`,
  `fetch`, `push`, and `send-pack`), and the common `bash -c '...'` /
  `eval` wrappers. Active arithmetic is not interpreted, including `$((...))`,
  `((...))`, legacy `$[...]`, `let`, array/integer/nameref declarations,
  arithmetic parameter offsets, indexed-array or indirect parameter
  expansions, and variable-target
  builtins such as `printf -v`; these map to `unknown` because Bash may
  evaluate variable values and array subscripts recursively. Expanding
  heredocs are checked after active backslash-newline folding. Active unquoted
  brace or pathname expansion, selected visible `xargs` and `find -exec`-style argv generation,
  and Git subcommands outside that allowlist (including
  `config` alias mutation) also map to `unknown`. Leading `GIT_CONFIG*`
  assignments and shell startup-file selectors such as `BASH_ENV`, imported
  shell state through `SHELLOPTS`, allexport wrapper options, `wait -p`
  assignment, and trap mutation map to
  `unknown` because they can change execution before visible argv is
  evaluated. Interactive/login shell modes and explicit `--rcfile` /
  `--init-file` inputs are also rejected because they execute startup files
  before the inspected body. A modeled shell wrapper is accepted only when
  `-c` has exactly one inspected command body; trailing arguments or
  redirections are not modeled and therefore fail closed.
  Parameter-expanded builtin options fail closed, and expanding-heredoc
  delimiters are matched after backslash-newline folding so following commands
  cannot be mistaken for heredoc data.
  A standalone `-exec`, `-execdir`, `-ok`, or `-okdir` token in
  `find` argv is conservatively blocked even when it could be another
  primary's value; the example does not model full `find` expression arity.
  Unresolved parameter expansion anywhere in `find` argv is rejected for the
  same reason. A literal Git, `gh`, direct Git helper, or shell interpreter
  token behind an otherwise unmodeled command prefix is also conservatively
  blocked rather than treated as inert argument text.
  Leading redirection, known execution prefixes such as `exec`, `time`, and
  `nohup`, grouping, and shell compound keywords also map to `unknown`.
  Forms such as `git -c alias.p=push p --force`, process substitution, or
  function definitions are not modeled. Clear commands
  outside the narrow patterns map to `shell`, while ambiguous, unbalanced,
  or unterminated parsing maps to `unknown` and is rejected by the hooks.

## Releases

Tag-driven. Pushing a `vX.Y.Z` annotated tag triggers
[`.github/workflows/release.yml`](.github/workflows/release.yml), which verifies
that the tag matches `[project].version`, points at the current `master`
commit, and has a successful completed `master` push CI run. It also checks
that the version is not already present on PyPI, then builds the sdist + wheel
and publishes through PyPI Trusted Publishing (OIDC). No maintainer-side PyPI
token is required. Manual `workflow_dispatch` with `publish=false` is a
build-only dry run; it skips attestation and publication. Manual `publish=true`
must run against a `v*` tag ref; running it from a branch fails before build.

After PyPI publication succeeds, the separate
[`github-release`](.github/workflows/github-release.yml) workflow creates the
GitHub Release notes from the verified tag commit, or verifies that an existing
Release is already public and asset-free. The automatic path proceeds only
after a fully successful tag-push `release.yml` run for the same annotated tag
object and peeled commit.

Manual recovery must be dispatched from the repository's default branch for an
exact `vX.Y.Z` tag. Successful `0.1.1` through `0.1.9` tag-push runs use the
bounded historical path when their build and publish jobs succeeded and PyPI exposes the exact expected,
non-yanked wheel and sdist. Historical releases do not require retained workflow
artifacts or an attestation job that did not yet exist; the historical path also
accepts the repository's `v0.1.6` legacy lightweight tag when its exact commit is
unchanged. Other successful versions must match the full current job topology
and use an annotated tag. If a historical tagged tree predates `CHANGELOG.md`, a newly created Release
uses a fixed provenance-only recovery note rather than invented feature notes.
A failed current tag-push run is recoverable only from an annotated tag and only
when the build, attestation, publisher step, retained artifact, and matching
PyPI wheel/sdist byte evidence are all proven.
When automatic run selection is ambiguous, the optional `release_run_id` input
selects the exact run. Recovery fails closed when evidence required for the
selected path is unavailable, ambiguous, or inconsistent. It never moves tags
or reuploads distributions; immediately before creating or accepting the
GitHub Release, it rechecks that the remote tag is unchanged.

The release build creates GitHub artifact attestations for `dist/*` before the
publish job downloads those files. PyPI Trusted Publishing and the PyPA
publish action also provide PyPI-side distribution attestations. These are
provenance and integrity evidence for a specific artifact and workflow
identity. They do not prove code correctness, dependency safety, maintainer
approval, absence of secrets, or policy compliance.

To verify the GitHub provenance for downloaded `0.1.14` artifacts, check the
tag, repository, and signer workflow explicitly:

```bash
(
set -euo pipefail
verify_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-policy-dist-verify.XXXXXX")"
trap 'rm -rf -- "$verify_dir"' EXIT
python - "$verify_dir" <<'PY'
import json
import shutil
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

version = "0.1.14"
target = Path(sys.argv[1])
request_timeout_seconds = 20
metadata_url = f"https://pypi.org/pypi/yui-agent-policy/{version}/json"
with urllib.request.urlopen(metadata_url, timeout=request_timeout_seconds) as response:
    final_metadata_url = urlparse(response.geturl())
    if final_metadata_url.scheme != "https" or final_metadata_url.hostname != "pypi.org":
        raise SystemExit("PyPI release metadata URL is not an expected HTTPS host")
    release = json.load(response)
if not isinstance(release, dict):
    raise SystemExit("PyPI release metadata is malformed")
expected = {
    f"yui_agent_policy-{version}-py3-none-any.whl": "bdist_wheel",
    f"yui_agent_policy-{version}.tar.gz": "sdist",
}
files = release.get("urls")
if not isinstance(files, list) or len(files) != len(expected):
    raise SystemExit("PyPI release does not contain the exact expected artifact set")
by_name = {}
for file_info in files:
    if not isinstance(file_info, dict):
        raise SystemExit("PyPI release metadata is malformed")
    filename = file_info.get("filename")
    url = file_info.get("url")
    if (
        not isinstance(filename, str)
        or filename not in expected
        or file_info.get("packagetype") != expected[filename]
        or file_info.get("yanked") is not False
        or not isinstance(url, str)
    ):
        raise SystemExit("PyPI release metadata does not match the expected artifact contract")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "files.pythonhosted.org":
        raise SystemExit("PyPI release artifact URL is not an expected HTTPS host")
    if filename in by_name:
        raise SystemExit("PyPI release metadata contains duplicate artifacts")
    by_name[filename] = url
if set(by_name) != set(expected):
    raise SystemExit("PyPI release does not contain the exact expected artifact set")
for filename in sorted(expected):
    with urllib.request.urlopen(
        by_name[filename], timeout=request_timeout_seconds
    ) as response:
        final_artifact_url = urlparse(response.geturl())
        if (
            final_artifact_url.scheme != "https"
            or final_artifact_url.hostname != "files.pythonhosted.org"
        ):
            raise SystemExit("Downloaded artifact URL is not an expected HTTPS host")
        with (target / filename).open("xb") as destination:
            shutil.copyfileobj(response, destination)
PY
gh attestation verify "$verify_dir/yui_agent_policy-0.1.14-py3-none-any.whl" \
  --repo yui-stingray/agent-policy \
  --signer-workflow yui-stingray/agent-policy/.github/workflows/release.yml \
  --source-ref refs/tags/v0.1.14
gh attestation verify "$verify_dir/yui_agent_policy-0.1.14.tar.gz" \
  --repo yui-stingray/agent-policy \
  --signer-workflow yui-stingray/agent-policy/.github/workflows/release.yml \
  --source-ref refs/tags/v0.1.14
)
```

## License

MIT.
