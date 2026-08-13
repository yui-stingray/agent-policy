"""Subprocess contract tests for the fail-closed Codex PreToolUse hook."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK_SH = REPO_ROOT / "examples" / "codex_hook.sh"
POLICY_TOML = REPO_ROOT / "examples" / "policy.toml"
BASH = shutil.which("bash")
JQ = shutil.which("jq")

HOOK_ALLOW = 0
HOOK_BLOCK = 2
BLOCK_MESSAGE = "agent-policy hook: blocked\n"
FORCE_PUSH_EXECUTION_FORMS = (
    "bash -lc \"git push --force origin main\"",
    "env bash -c \"git push --force origin main\"",
    "command bash -c \"git push --force origin main\"",
    "cat <(bash -c \"git push --force origin main\")",
    "git -C /tmp push --force origin main",
    "git -c alias.p=push p --force origin main",
    "git --config-env=alias.p=GIT_ALIAS p --force origin main",
    "git push origin +HEAD:main",
    "git push --mirror origin",
    "bash <<'EOF'\ngit push --force origin main\nEOF",
    "bash <<< 'git push --force origin main'",
    'cmd="git push --force origin main"; bash -c "$cmd"',
    "runner=bash; $runner -c 'git push --force origin main'",
    "builtin eval 'git push --force origin main'",
    "git push --force-w origin main",
    "git push --mir origin",
    "git send-pack --force origin HEAD:main",
    "bash -c 'echo safe'\ngit push --force origin main",
    "F=--force; git push $F origin main",
    "REF=+HEAD:main; git push origin $REF",
)
VALID_EVALUATOR_DECISION = (
    '{"matched_repo":null,"mode":"auto_allow","reason":"test"}'
)

pytestmark = pytest.mark.skipif(BASH is None, reason="codex_hook.sh requires bash")


def _codex_payload(command: str, **extra: object) -> str:
    """Build a minimal but realistic Codex PreToolUse JSON payload."""
    payload: dict[str, object] = {
        "turn_id": "turn_test_001",
        "tool_name": "Bash",
        "tool_use_id": "tu_test_001",
        "tool_input": {"command": command},
    }
    payload.update(extra)
    return json.dumps(payload)


def _run_hook(
    payload: str,
    *,
    policy: Path | None = POLICY_TOML,
    repo: str | None = "acme/app",
    ownership: str | None = "internal",
    first_write: str | None = None,
    path: str | None = None,
    xtrace: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # BASH_ENV executes before a hook script, so tests isolate that trusted
    # launcher boundary and explicitly opt in to inherited xtrace below.
    env.pop("BASH_ENV", None)
    if path is None:
        if JQ is None:
            pytest.skip("normal hook paths require jq on PATH")
        env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    else:
        env["PATH"] = path

    if policy is None:
        env.pop("AGENT_POLICY_FILE", None)
    else:
        env["AGENT_POLICY_FILE"] = str(policy)
    if repo is None:
        env.pop("AGENT_POLICY_REPO", None)
    else:
        env["AGENT_POLICY_REPO"] = repo
    if ownership is None:
        env.pop("AGENT_POLICY_OWNERSHIP", None)
    else:
        env["AGENT_POLICY_OWNERSHIP"] = ownership
    if first_write is None:
        env.pop("AGENT_POLICY_FIRST_WRITE", None)
    else:
        env["AGENT_POLICY_FIRST_WRITE"] = first_write
    if xtrace:
        env["SHELLOPTS"] = "xtrace"
        env.pop("BASH_XTRACEFD", None)

    assert BASH is not None
    return subprocess.run(
        [BASH, str(HOOK_SH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _external_auto_allow_policy(tmp_path: Path) -> Path:
    policy = tmp_path / "external-auto-allow.toml"
    policy.write_text(
        """
default_mode = "require_approval"

[[repo_policy]]
repo = "someone-else/their-repo"
ownership_class = "external"

[repo_policy.capabilities]
shell = "auto_allow"
""".lstrip(),
        encoding="utf-8",
    )
    return policy


def _default_auto_allow_policy(tmp_path: Path) -> Path:
    policy = tmp_path / "default-auto-allow.toml"
    policy.write_text('default_mode = "auto_allow"\n', encoding="utf-8")
    return policy


def _unexpected_classifier_path(tmp_path: Path) -> str:
    if JQ is None:
        pytest.skip("unexpected-classifier test requires jq")
    python3 = tmp_path / "python3"
    python3.write_text(
        f"""#!/usr/bin/env bash
if [[ "$1" == *"/capability_map.py" ]]; then
    printf '%s\\n' 'unexpected.capability'
    exit 0
fi
exec "{sys.executable}" "$@"
""",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    return (
        f"{tmp_path}{os.pathsep}{Path(sys.executable).parent}"
        f"{os.pathsep}{os.environ.get('PATH', '')}"
    )


def _malformed_evaluator_path(tmp_path: Path) -> str:
    if JQ is None:
        pytest.skip("malformed-evaluator test requires jq")
    python3 = tmp_path / "python3"
    python3.write_text(
        f"""#!/usr/bin/env bash
if [[ "$1" == *"/check.py" ]]; then
    printf '%s\\n' '{{"mode":"auto_allow"}}'
    exit 0
fi
exec "{sys.executable}" "$@"
""",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    return (
        f"{tmp_path}{os.pathsep}{Path(sys.executable).parent}"
        f"{os.pathsep}{os.environ.get('PATH', '')}"
    )


def _unexpected_evaluator_path(tmp_path: Path) -> str:
    if JQ is None:
        pytest.skip("unexpected-evaluator test requires jq")
    python3 = tmp_path / "python3"
    python3.write_text(
        f"""#!/usr/bin/env bash
if [[ "$1" == *"/check.py" ]]; then
    printf '%s\\n' 'not-json'
    exit 0
fi
exec "{sys.executable}" "$@"
""",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    return (
        f"{tmp_path}{os.pathsep}{Path(sys.executable).parent}"
        f"{os.pathsep}{os.environ.get('PATH', '')}"
    )


def _evaluator_output_path(tmp_path: Path, output: str) -> str:
    if JQ is None:
        pytest.skip("evaluator-output test requires jq")
    python3 = tmp_path / "python3"
    python3.write_text(
        f"""#!{sys.executable}
import os
import sys

if len(sys.argv) > 1 and sys.argv[1].endswith("/check.py"):
    sys.stdout.write({output!r})
    raise SystemExit(0)
os.execv({sys.executable!r}, [{sys.executable!r}, *sys.argv[1:]])
""",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    return (
        f"{tmp_path}{os.pathsep}{Path(sys.executable).parent}"
        f"{os.pathsep}{os.environ.get('PATH', '')}"
    )


def test_policy_block_has_fixed_sanitized_stderr() -> None:
    result = _run_hook(_codex_payload("git push --force origin main"))

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


@pytest.mark.parametrize(
    "command",
    (
        "git status --short",
        'bash -c \'printf "%s\\n" "$HOME"\'',
        "builtin printf '%s\\n' ok",
        "git push -o +ci.skip origin main",
        "git push -o+ci.skip origin main",
        "git push --push-option +ci.skip origin main",
        "git push --push-option=+ci.skip origin main",
    ),
)
def test_auto_allow_is_silent(command: str, tmp_path: Path) -> None:
    policy = tmp_path / "auto-allow.toml"
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

    result = _run_hook(_codex_payload(command), policy=policy)

    assert result.returncode == HOOK_ALLOW
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize("command", FORCE_PUSH_EXECUTION_FORMS)
def test_force_push_execution_forms_never_auto_allow(command: str, tmp_path: Path) -> None:
    result = _run_hook(
        _codex_payload(command),
        policy=_default_auto_allow_policy(tmp_path),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


@pytest.mark.parametrize(
    "payload",
    [
        "",
        " \t\n",
        "[]",
        _codex_payload("git status --short")
        + "\n"
        + _codex_payload("git status --short"),
    ],
)
def test_pretooluse_requires_exactly_one_json_object(
    payload: str,
    tmp_path: Path,
) -> None:
    result = _run_hook(payload, policy=_default_auto_allow_policy(tmp_path))

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


def test_inherited_xtrace_does_not_leak_protected_values(tmp_path: Path) -> None:
    marker = "synthetic-xtrace-marker"
    policy = tmp_path / f"{marker}-policy.toml"
    policy.write_text('default_mode = "auto_allow"\n', encoding="utf-8")

    result = _run_hook(
        _codex_payload(f"printf %s {marker}-command"),
        policy=policy,
        repo=f"{marker}-repo",
        xtrace=True,
    )

    assert result.returncode == HOOK_ALLOW
    assert result.stdout == ""
    assert "set +x" in result.stderr
    assert marker not in result.stderr


@pytest.mark.parametrize(
    ("payload", "policy", "repo"),
    [
        ("{not-json", POLICY_TOML, "acme/app"),
        (_codex_payload("ls -la"), None, "acme/app"),
        (_codex_payload("ls -la"), POLICY_TOML, None),
        (_codex_payload("ls -la"), Path("/untrusted/config/policy.toml"), "acme/app"),
    ],
)
def test_payload_and_config_errors_block_without_leaking_details(
    payload: str,
    policy: Path | None,
    repo: str | None,
) -> None:
    result = _run_hook(payload, policy=policy, repo=repo)

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE
    assert "untrusted" not in result.stderr
    assert "not-json" not in result.stderr


def test_missing_jq_blocks_with_the_same_sanitized_message(tmp_path: Path) -> None:
    result = _run_hook(_codex_payload("ls -la"), path=str(tmp_path))

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


@pytest.mark.parametrize(
    "command",
    [
        "echo 'unterminated",
        "cat <<EOF\ngit push --force origin main",
    ],
)
def test_unknown_classifier_result_blocks(command: str) -> None:
    result = _run_hook(_codex_payload(command))

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


def test_unexpected_classifier_result_blocks(tmp_path: Path) -> None:
    result = _run_hook(
        _codex_payload("git status --short"),
        path=_unexpected_classifier_path(tmp_path),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


def test_malformed_evaluator_response_blocks(tmp_path: Path) -> None:
    result = _run_hook(
        _codex_payload("git status --short"),
        path=_malformed_evaluator_path(tmp_path),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


def test_unexpected_evaluator_result_blocks(tmp_path: Path) -> None:
    result = _run_hook(
        _codex_payload("git status --short"),
        path=_unexpected_evaluator_path(tmp_path),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


@pytest.mark.parametrize(
    "output",
    [
        f"{VALID_EVALUATOR_DECISION}\n{VALID_EVALUATOR_DECISION}\n",
        '{"ignored":"leading"}\n' + VALID_EVALUATOR_DECISION + "\n",
    ],
)
def test_evaluator_requires_exactly_one_json_object(
    output: str,
    tmp_path: Path,
) -> None:
    result = _run_hook(
        _codex_payload("git status --short"),
        path=_evaluator_output_path(tmp_path, output),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


@pytest.mark.parametrize(
    ("first_write", "expected_code"),
    [
        ("true", HOOK_BLOCK),
        ("false", HOOK_ALLOW),
        (None, HOOK_BLOCK),
        ("malformed", HOOK_BLOCK),
    ],
)
def test_external_auto_allow_requires_wrapper_owned_first_write_state(
    tmp_path: Path,
    first_write: str | None,
    expected_code: int,
) -> None:
    result = _run_hook(
        _codex_payload("git status --short"),
        policy=_external_auto_allow_policy(tmp_path),
        repo="someone-else/their-repo",
        ownership="external",
        first_write=first_write,
    )

    assert result.returncode == expected_code
    assert result.stdout == ""
    assert result.stderr == ("" if expected_code == HOOK_ALLOW else BLOCK_MESSAGE)


def test_external_first_write_comes_from_environment_not_payload(tmp_path: Path) -> None:
    result = _run_hook(
        _codex_payload(
            "git status --short",
            ownership_class="internal",
            first_write_to_repo=False,
        ),
        policy=_external_auto_allow_policy(tmp_path),
        repo="someone-else/their-repo",
        ownership="external",
        first_write="true",
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE


def test_hook_comments_describe_current_codex_contract() -> None:
    hook = HOOK_SH.read_text(encoding="utf-8")

    assert "features.codex_hooks" not in hook
    assert "features.hooks" in hook
    assert "This wrapper only maps Bash commands" in hook
