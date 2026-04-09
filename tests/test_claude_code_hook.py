"""Where: tests/test_claude_code_hook.py
What: subprocess-driven contract tests for examples/claude_code_hook.sh.
Why: the hook is the user-facing glue between Claude Code and check.py.
     If the capability mapping or the exit-code translation drifts, the
     fail-closed promise of agent-policy silently breaks. Pin both here.

These tests are intentionally narrow: one payload per capability path,
plus the error-path checks. We do not re-test check.py's own contract —
that lives in tests/test_check_example.py.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SH = REPO_ROOT / "examples" / "claude_code_hook.sh"
POLICY_TOML = REPO_ROOT / "examples" / "policy.toml"


# Hook contract exit codes — keep in sync with examples/claude_code_hook.sh.
HOOK_ALLOW = 0
HOOK_BLOCK = 2
HOOK_ERROR = 1


pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="claude_code_hook.sh requires bash and jq on PATH",
)


def _run_hook(payload: str, *, repo: str = "acme/app",
              ownership: str | None = "internal") -> subprocess.CompletedProcess[str]:
    """Invoke the hook with PATH-prepended venv python.

    The hook calls `python3` directly, so we expose sys.executable's
    directory at the front of PATH to guarantee the agent_policy import
    resolves to the in-repo editable install.
    """
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["AGENT_POLICY_FILE"] = str(POLICY_TOML)
    env["AGENT_POLICY_REPO"] = repo
    if ownership is not None:
        env["AGENT_POLICY_OWNERSHIP"] = ownership
    else:
        env.pop("AGENT_POLICY_OWNERSHIP", None)

    return subprocess.run(
        ["bash", str(HOOK_SH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


# ---------------------------------------------------------------------------
# Capability mapping: tool name → check.py capability
# ---------------------------------------------------------------------------


def test_read_tool_is_allowed_silently() -> None:
    result = _run_hook('{"tool_name":"Read","tool_input":{"file_path":"/tmp/x"}}')
    assert result.returncode == HOOK_ALLOW
    # Hook contract: allow path must produce no stderr noise.
    assert result.stderr == ""


def test_write_tool_is_allowed_on_internal_repo() -> None:
    result = _run_hook('{"tool_name":"Write","tool_input":{"file_path":"/tmp/x"}}')
    assert result.returncode == HOOK_ALLOW


def test_bash_plain_command_blocks_via_shell_capability() -> None:
    result = _run_hook('{"tool_name":"Bash","tool_input":{"command":"ls -la"}}')
    assert result.returncode == HOOK_BLOCK
    assert "capability=shell" in result.stderr


def test_bash_force_push_blocks_via_push_force_capability() -> None:
    result = _run_hook(
        '{"tool_name":"Bash","tool_input":{"command":"git push --force origin master"}}'
    )
    assert result.returncode == HOOK_BLOCK
    assert "capability=push.force" in result.stderr
    # Hard guardrail proves the mapping reached the right code path.
    assert "DENY" in result.stderr


def test_bash_force_with_lease_also_routes_to_push_force() -> None:
    result = _run_hook(
        '{"tool_name":"Bash","tool_input":'
        '{"command":"git push --force-with-lease origin master"}}'
    )
    assert result.returncode == HOOK_BLOCK
    assert "capability=push.force" in result.stderr


def test_bash_gh_pr_merge_routes_to_merge_pr() -> None:
    result = _run_hook(
        '{"tool_name":"Bash","tool_input":{"command":"gh pr merge 42 --merge"}}'
    )
    assert result.returncode == HOOK_BLOCK
    assert "capability=merge.pr" in result.stderr


# ---------------------------------------------------------------------------
# Regression: quoted-literal false positives (v0.1.2 fix)
# ---------------------------------------------------------------------------
#
# See tests/test_codex_hook.py for the motivation. The same false
# positives applied to the Claude Code hook Bash branch because both
# hooks shared the identical substring case statement. Both branches
# now delegate to examples/capability_map.py.


def test_bash_printf_with_quoted_force_push_is_not_push_force() -> None:
    import json
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "printf '%s\\n' 'git push --force origin master'"},
    })
    result = _run_hook(payload)
    assert result.returncode == HOOK_BLOCK
    assert "capability=shell" in result.stderr
    assert "capability=push.force" not in result.stderr


def test_bash_echo_with_quoted_force_push_is_not_push_force() -> None:
    import json
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "echo 'git push --force'"},
    })
    result = _run_hook(payload)
    assert result.returncode == HOOK_BLOCK
    assert "capability=shell" in result.stderr
    assert "capability=push.force" not in result.stderr


def test_bash_heredoc_force_push_is_not_push_force() -> None:
    import json
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "cat <<EOF\ngit push --force\nEOF"},
    })
    result = _run_hook(payload)
    assert result.returncode == HOOK_BLOCK
    assert "capability=shell" in result.stderr
    assert "capability=push.force" not in result.stderr


def test_bash_wrapped_force_push_is_still_detected() -> None:
    import json
    payload = json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "bash -c 'git push --force origin main'"},
    })
    result = _run_hook(payload)
    assert result.returncode == HOOK_BLOCK
    assert "capability=push.force" in result.stderr


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_missing_tool_name_is_hook_error() -> None:
    result = _run_hook("{}")
    assert result.returncode == HOOK_ERROR
    assert "missing tool_name" in result.stderr
