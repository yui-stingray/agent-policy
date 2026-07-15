"""Where: tests/test_check_example.py
What: subprocess-driven contract tests for examples/check.py.
Why: lock the CLI wrapper's JSON output shape and exit-code mapping.

These tests exist because examples/check.py is the shape every downstream
wrapper will copy. The exit-code map (0/2/3) and the `matched_repo: null`
JSON serialization are load-bearing for PreToolUse hooks, and the
argparse usage-error normalization (argparse's default is 2, we force 1)
is easy to break by accident.

We invoke the script as a subprocess rather than import it, because the
exit-code path is what downstream hooks will actually observe.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_PY = REPO_ROOT / "examples" / "check.py"
POLICY_TOML = REPO_ROOT / "examples" / "policy.toml"
BASE_AUDIT_ARGS: Final[tuple[str, ...]] = (
    "--policy",
    str(POLICY_TOML),
    "--repo",
    "acme/app",
    "--capability",
    "commit",
    "--ownership-class",
    "internal",
    "--audit-event",
)


# Exit codes mirrored from examples/check.py. Keep these in sync with that
# file — the whole point of this test module is that breaking the mapping
# shows up as a test failure, not a silently broken wrapper.
EXIT_AUTO_ALLOW = 0
EXIT_PROGRAM_ERROR = 1
EXIT_REQUIRE_APPROVAL = 2
EXIT_DENY = 3


def _run_check(*args: str) -> subprocess.CompletedProcess[str]:
    """Invoke examples/check.py with the given CLI args.

    sys.executable points at the pytest venv, which has agent-policy
    installed in editable mode — same module resolution the example
    docstring documents for source checkouts.
    """
    return subprocess.run(
        [sys.executable, str(CHECK_PY), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _parse_stdout(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    """Parse the JSON decision payload the script prints on stdout."""
    return json.loads(result.stdout.strip())


# ---------------------------------------------------------------------------
# Exit-code mapping (0 / 2 / 3)
# ---------------------------------------------------------------------------


def test_auto_allow_prints_json_and_exits_zero() -> None:
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "acme/app",
        "--capability", "commit",
        "--ownership-class", "internal",
    )

    assert result.returncode == EXIT_AUTO_ALLOW
    payload = _parse_stdout(result)
    assert payload == {
        "mode": "auto_allow",
        "reason": "repo_policy",
        "matched_repo": "acme/app",
    }


def test_require_approval_exits_two() -> None:
    # Shell on acme/app is pinned by the split repo_policy entry in
    # examples/policy.toml. This doubles as an end-to-end P1 regression
    # check: if the evaluator ever stops scanning past the first match,
    # this test flips to auto_allow.
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "acme/app",
        "--capability", "shell",
        "--ownership-class", "internal",
    )

    assert result.returncode == EXIT_REQUIRE_APPROVAL
    payload = _parse_stdout(result)
    assert payload["mode"] == "require_approval"
    assert payload["reason"] == "repo_policy"


def test_deny_exits_three() -> None:
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "acme/app",
        "--capability", "push.force",
        "--ownership-class", "internal",
    )

    assert result.returncode == EXIT_DENY
    payload = _parse_stdout(result)
    assert payload["mode"] == "deny"
    assert payload["reason"] == "hard_guardrail"


# ---------------------------------------------------------------------------
# JSON shape: matched_repo is always present, null when no repo matched.
# ---------------------------------------------------------------------------


def test_matched_repo_is_null_when_no_repo_policy_matches() -> None:
    # Unknown repo, unknown capability → default_mode fallback.
    # The wrapper contract says matched_repo must still appear as a key
    # (serialized as JSON null), so downstream code can rely on a stable
    # payload shape.
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "ghost/missing",
        "--capability", "commit",
    )

    assert result.returncode == EXIT_REQUIRE_APPROVAL
    payload = _parse_stdout(result)
    assert "matched_repo" in payload
    assert payload["matched_repo"] is None
    assert payload["mode"] == "require_approval"
    assert payload["reason"] == "default_mode"


# ---------------------------------------------------------------------------
# Context flags: external first_write must not block read (P2 regression).
# ---------------------------------------------------------------------------


def test_external_first_write_read_is_auto_allowed() -> None:
    # P2 regression, seen through the CLI: the first-write guardrail
    # must only fire for mutating capabilities. `read` on an external
    # repo with first_write_to_repo=true should pass straight through
    # to the repo_policy match.
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "someone-else/their-repo",
        "--capability", "read",
        "--ownership-class", "external",
        "--first-write",
    )

    assert result.returncode == EXIT_AUTO_ALLOW
    payload = _parse_stdout(result)
    assert payload["mode"] == "auto_allow"


def test_external_first_write_write_requires_approval() -> None:
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "someone-else/their-repo",
        "--capability", "write",
        "--ownership-class", "external",
        "--first-write",
    )

    assert result.returncode == EXIT_REQUIRE_APPROVAL
    payload = _parse_stdout(result)
    assert payload["mode"] == "require_approval"
    assert payload["reason"] == "hard_guardrail"


def test_audit_event_output_is_opt_in() -> None:
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "acme/app",
        "--capability", "commit",
        "--ownership-class", "internal",
        "--audit-event",
        "--session-id", "session-123",
        "--path", "README.md",
        "--command", "git status --short",
    )

    assert result.returncode == EXIT_AUTO_ALLOW
    payload = _parse_stdout(result)
    assert payload == {
        "repo": "acme/app",
        "capability": "commit",
        "context": {"ownership_class": "internal"},
        "decision": {
            "mode": "auto_allow",
            "reason": "repo_policy",
            "matched_repo": "acme/app",
        },
        "session_id": "session-123",
        "path": "README.md",
        "command": "git status --short",
    }


def test_audit_event_preserves_require_approval_exit_code() -> None:
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--repo", "acme/app",
        "--capability", "shell",
        "--ownership-class", "internal",
        "--audit-event",
        "--session-id", "session-456",
        "--path", "scripts/release.sh",
        "--command", "bash scripts/release.sh",
    )

    assert result.returncode == EXIT_REQUIRE_APPROVAL
    payload = _parse_stdout(result)
    assert payload["repo"] == "acme/app"
    assert payload["capability"] == "shell"
    assert payload["context"] == {"ownership_class": "internal"}
    assert payload["decision"] == {
        "mode": "require_approval",
        "reason": "repo_policy",
        "matched_repo": "acme/app",
    }
    assert payload["session_id"] == "session-456"
    assert payload["path"] == "scripts/release.sh"
    assert payload["command"] == "bash scripts/release.sh"


def test_audit_event_accepts_tilde_prefixed_repo_relative_path() -> None:
    result = _run_check(
        *BASE_AUDIT_ARGS,
        "--path",
        "~docs/file.txt",
    )

    assert result.returncode == EXIT_AUTO_ALLOW
    payload = _parse_stdout(result)
    assert payload["path"] == "~docs/file.txt"


@pytest.mark.parametrize(
    "path",
    [
        "docs/file.txt",
        "src/module/file.py",
        "folder.with.dots/file.txt",
    ],
)
def test_audit_event_accepts_repo_relative_paths(path: str) -> None:
    result = _run_check(
        *BASE_AUDIT_ARGS,
        "--path",
        path,
    )

    assert result.returncode == EXIT_AUTO_ALLOW
    payload = _parse_stdout(result)
    assert payload["path"] == path


@pytest.mark.parametrize(
    ("extra_args", "raw_value"),
    [
        (("--session-id", "session with space"), "session with space"),
        (("--session-id", ""), ""),
        (("--command", ""), ""),
        (("--command", "git status\ncat redacted.txt"), "git status\ncat redacted.txt"),
        (("--path", "/REDACTED/project/file.txt"), "/REDACTED/project/file.txt"),
        (("--path", r"C:\REDACTED\project\file.txt"), r"C:\REDACTED\project\file.txt"),
        (("--path", "~/project/file.txt"), "~/project/file.txt"),
        (("--path", "../SENTINEL_OUTSIDE/example.txt"), "../SENTINEL_OUTSIDE/example.txt"),
        (("--path", "docs/../SENTINEL_OUTSIDE/example.txt"), "docs/../SENTINEL_OUTSIDE/example.txt"),
        (("--path", r"..\SENTINEL_OUTSIDE\example.txt"), r"..\SENTINEL_OUTSIDE\example.txt"),
        (("--path", r"docs\..\SENTINEL_OUTSIDE\example.txt"), r"docs\..\SENTINEL_OUTSIDE\example.txt"),
        (("--path", r"\\SENTINEL_HOST\share\file.txt"), r"\\SENTINEL_HOST\share\file.txt"),
        (("--path", "$SENTINEL_HOME/project/file.txt"), "$SENTINEL_HOME/project/file.txt"),
        (("--path", "${SENTINEL_HOME}/project/file.txt"), "${SENTINEL_HOME}/project/file.txt"),
        (("--path", "${SENTINEL_HOME}suffix/file.txt"), "${SENTINEL_HOME}suffix/file.txt"),
        (("--path", "${SENTINEL_HOME:-fallback}/file.txt"), "${SENTINEL_HOME:-fallback}/file.txt"),
        (("--path", "%SENTINEL_HOME%\\project\\file.txt"), "%SENTINEL_HOME%\\project\\file.txt"),
        (
            ("--path", "%ProgramFiles(x86)%\\SENTINEL_OUTSIDE\\file.txt"),
            "%ProgramFiles(x86)%\\SENTINEL_OUTSIDE\\file.txt",
        ),
        (
            ("--path", "%HOMEDRIVE%%HOMEPATH%\\SENTINEL_OUTSIDE\\file.txt"),
            "%HOMEDRIVE%%HOMEPATH%\\SENTINEL_OUTSIDE\\file.txt",
        ),
        (
            ("--path", "%USERPROFILE%suffix\\SENTINEL_OUTSIDE\\file.txt"),
            "%USERPROFILE%suffix\\SENTINEL_OUTSIDE\\file.txt",
        ),
        (("--path", "file:///SENTINEL_HOST/project/file.txt"), "file:///SENTINEL_HOST/project/file.txt"),
        (("--path", "file:docs/file.txt"), "file:docs/file.txt"),
    ],
)
def test_invalid_audit_event_optional_strings_are_rejected_without_echoing_values(
    extra_args: tuple[str, str],
    raw_value: str,
) -> None:
    result = _run_check(*BASE_AUDIT_ARGS, *extra_args)

    assert result.returncode == EXIT_PROGRAM_ERROR
    assert result.stdout == ""
    assert "invalid audit event argument" in result.stderr
    if raw_value:
        assert raw_value not in result.stderr


def test_audit_event_rejects_overlong_optional_strings_without_serializing() -> None:
    result = _run_check(
        *BASE_AUDIT_ARGS,
        "--session-id",
        "a" * 257,
    )

    assert result.returncode == EXIT_PROGRAM_ERROR
    assert result.stdout == ""
    assert "session_id" in result.stderr


# ---------------------------------------------------------------------------
# Program errors: exit 1, never 2.
# ---------------------------------------------------------------------------


def test_missing_policy_file_exits_program_error() -> None:
    result = _run_check(
        "--policy", "examples/does-not-exist.toml",
        "--repo", "acme/app",
        "--capability", "commit",
    )

    assert result.returncode == EXIT_PROGRAM_ERROR
    assert "error" in result.stderr.lower()


def test_argparse_usage_error_is_normalised_to_one() -> None:
    # Critical: argparse's default exit code for a usage error is 2,
    # which collides with our require_approval judgment. check.py must
    # normalise argparse errors to 1 so wrappers never confuse
    # "missing --repo" with "a human needs to approve this".
    result = _run_check(
        "--policy", str(POLICY_TOML),
        "--capability", "commit",
    )

    assert result.returncode == EXIT_PROGRAM_ERROR
    assert result.returncode != EXIT_REQUIRE_APPROVAL  # guard against regression


def test_help_flag_exits_zero_without_json() -> None:
    # argparse prints help and exits 0 for --help. This must not be
    # reported as an "auto_allow" decision — there is simply no JSON
    # on stdout. Downstream wrappers that shell out to check.py for
    # --help discovery depend on this.
    result = _run_check("--help")

    assert result.returncode == 0
    assert "--policy" in result.stdout
    # No JSON payload on --help.
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout.strip())
