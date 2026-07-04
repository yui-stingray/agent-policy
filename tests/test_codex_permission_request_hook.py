"""Where: tests/test_codex_permission_request_hook.py
What: subprocess tests for the Codex PermissionRequest delegation example.
Why: Codex PermissionRequest hooks can delegate by returning no decision; this
     pins the wrapper behavior separately from the block-style PreToolUse hook.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SH = REPO_ROOT / "examples" / "codex_permission_request_hook.sh"
POLICY_TOML = REPO_ROOT / "examples" / "policy.toml"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("jq") is None,
    reason="codex_permission_request_hook.sh requires bash and jq on PATH",
)


def _codex_payload(command: str) -> str:
    """Build a minimal Codex PermissionRequest JSON payload."""

    return json.dumps({
        "turn_id": "turn_test_001",
        "tool_name": "Bash",
        "tool_use_id": "tu_test_001",
        "tool_input": {"command": command},
    })


def _run_hook(
    payload: str,
    *,
    policy: Path = POLICY_TOML,
    repo: str = "acme/app",
    ownership: str | None = "internal",
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["AGENT_POLICY_FILE"] = str(policy)
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


def test_require_approval_returns_no_decision_to_delegate() -> None:
    result = _run_hook(_codex_payload("ls -la"))

    assert result.returncode == 0
    assert result.stdout == ""
    assert "delegating to Codex approval prompt" in result.stderr
    assert "capability=shell" in result.stderr


def test_deny_returns_permission_decision_deny() -> None:
    result = _run_hook(_codex_payload("git push --force origin main"))

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["permissionDecision"] == "deny"
    assert "capability=push.force" in payload["permissionDecisionReason"]


def test_auto_allow_returns_permission_decision_allow(tmp_path: Path) -> None:
    policy = tmp_path / "policy.toml"
    policy.write_text(
        """
default_mode = "require_approval"

[[repo_policy]]
repo = "acme/app"
ownership_class = "internal"

[repo_policy.capabilities]
shell = "auto_allow"
""".lstrip(),
        encoding="utf-8",
    )

    result = _run_hook(_codex_payload("git status --short"), policy=policy)

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["permissionDecision"] == "allow"
    assert "capability=shell" in payload["permissionDecisionReason"]


def test_invalid_payload_returns_permission_decision_deny() -> None:
    result = _run_hook("{not-json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["permissionDecision"] == "deny"
    assert "invalid PermissionRequest JSON payload" in payload["permissionDecisionReason"]
