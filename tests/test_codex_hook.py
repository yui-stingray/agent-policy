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
    "git-push --force origin main",
    "bash -c 'echo safe'\ngit push --force origin main",
    "F=--force; git push $F origin main",
    "REF=+HEAD:main; git push origin $REF",
)
FAIL_CLOSED_COMMAND_FORMS = (
    "git push --fo* origin main",
    "bash -O extglob -c 'git push --@(force|force) origin main'",
    r"printf '%s\n' --force | xargs -I{} git push {} origin main",
    "find . -exec git push --force origin main \\;",
    "git config alias.fp 'push --force'",
    "git-config alias.fp 'push --force'",
    "git fp origin main",
    pytest.param(
        "printf '\\tpush = +HEAD:refs/heads/main\\n' >> .git/config; "
        "git push origin",
        id="same-command-config-writer",
    ),
    pytest.param(
        "printf '\\tpush = +HEAD:refs/heads/main\\n' | "
        "tee -a .git/config; git push origin",
        id="same-command-tee-writer",
    ),
    # Plain push text cannot prove that pre-existing refspec, mirror, hook,
    # helper, or selected-repository state is inert.
    pytest.param("git push origin", id="preconfigured-force-refspec"),
    pytest.param("git push origin main", id="preconfigured-mirror"),
    pytest.param("git push origin HEAD:main", id="pre-push-hook"),
    "git -C reviewed-repo push origin",
    "git --git-dir=reviewed-repo/.git "
    "--work-tree=reviewed-repo push origin",
    "env -C reviewed-repo git push origin",
    "git-push origin main",
    "git send-pack origin HEAD:main",
    "git-send-pack origin HEAD:main",
    "printf reviewed > reviewed-output",
    "printf reviewed 2>> reviewed-errors",
    "printf reviewed | tee reviewed-output",
    "git push -o +ci.skip origin main",
    "git push -o+ci.skip origin main",
    "git push --push-option +ci.skip origin main",
    "git push --push-option=+ci.skip origin main",
    r"true $'a\''; git push --force origin main # \'",
    r"true $'a\'' > reviewed-output \'",
    "true $\\\n'a\\''; git push --force origin main # \\'",
    "> /dev/null git push --force origin main",
    "exec git push --force origin main",
    "time git push --force origin main",
    "nohup git push --force origin main",
    "( git push --force origin main )",
    "echo hi |& git push --force origin main",
    "&>/dev/null git push --force origin main",
    "{fd}>/dev/null git push --force origin main",
    "sudo FOO=bar git push --force origin main",
    'P=-exec; find . "$P" git push --force origin main \\;',
    "stdbuf -oL git push --force origin main",
    'runner=git; stdbuf -oL "$runner" push --force origin main',
    "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push "
    "GIT_CONFIG_VALUE_0=+HEAD:refs/heads/main git push origin",
    "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push "
    "GIT_CONFIG_VALUE_0=+HEAD:refs/heads/main git push origin",
    "A='x[$(git push --force origin main)]'; : $((A))",
    "A='x[$(git push --force origin main)]'; ((A))",
    "A='x[$(git push --force origin main)]'; : $[A]",
    "A='x[$(git push --force origin main)]'; let A",
    "A='x[$(git push --force origin main)]'; declare -i A",
    "A='x[$(git push --force origin main)0]'; "
    "declare -a x='([A]=value)'",
    "A='x[$(git push --force origin main)0]'; printf -v 'x[A]' '%s' ok",
    "A='x[$(git push --force origin main)0]'; opt=v; "
    "printf -$opt 'x[A]' '%s' ok",
    "A='x[$(git push --force origin main)0]'; opt=v; "
    "builtin printf -$opt 'x[A]' '%s' ok",
    "A='x[$(git push --force origin main)0]'; value=abc; : ${value:A}",
    "A='x[$(git push --force origin main)0]'; echo \"${missing:-$((A))}\"",
    "A='x[$(git push --force origin main)0]'; "
    "echo \"${missing:-'$((A))'}\"",
    "A='x[$(git push --force origin main)0]'; cat <<EOF\n"
    "$\\\n((A))\nEOF",
    "A='x[$(git push --force origin main)0]'; true & wait -n -p 'x[A]'",
    "bash -ac 'sleep .05 & p=$!; printf SAFE >\"$p\"; "
    "wait -n -p BASH_ENV; bash -c :'",
    "printf 'git push --force origin main\\n' | "
    'bash -c "trap \'export BASH_ENV=/dev/stdin\' DEBUG; bash -c \'echo SAFE\'"',
    "BASH_ENV=/dev/stdin bash -c 'echo SAFE' "
    "<<< 'git push --force origin main'",
    "BASH_ENV=reviewed-startup.sh bash -c 'echo SAFE'",
    "env BASH_ENV=/dev/stdin bash -c 'echo SAFE' "
    "<<< 'git push --force origin main'",
    "set -a; printf -v BASH_ENV /dev/stdin; "
    "printf 'echo STARTUP\n' | bash -c 'echo SAFE'",
    "read BASH_ENV <<< /dev/stdin; bash -c 'echo SAFE'",
    "HOME=/tmp/agent-policy-startup bash -lc 'echo SAFE'",
    "bash --rcfile /dev/stdin -ic 'echo SAFE'",
    "cat <<EOF\nEO\\\nF\ngit push --force origin main\nEOF",
    "printf 'x\\n' | mapfile -C 'git push --force origin main' -c 1 arr",
    "printf 'x\\n' | readarray -C 'git push --force origin main' -c 1 arr",
    "compgen -C 'git push --force origin main' x",
    "PS4='$(git push --force origin main)'; set -x; true",
    "PS4='$(git push --force origin main)' bash -xc true",
    "bash -o xtrace -c true",
    "VALUE=reviewed printf '%s' ok",
    "env VALUE=reviewed printf '%s' ok",
    "export VALUE=reviewed; printf '%s' ok",
    "python -c 'print(1)'",
    "GIT_SSH_COMMAND='sh -c \"git push --force origin main\" dummy' "
    "git push origin main",
    "env GIT_SSH=reviewed-wrapper git push origin main",
    "export GIT_PROXY_COMMAND='sh -c \"git push --force origin main\"'; "
    "git fetch origin",
    "git push --receive-pack='sh -c \"git push --force origin main\"' "
    "origin main",
    "git push --exe='sh -c \"git push --force origin main\"' origin main",
    "git fetch --upload-pack='sh -c \"git push --force origin main\"' origin",
    "git send-pack --rece='sh -c \"git push --force origin main\"' "
    "origin main",
    "git push --force --receive-pack='sh -c \"git push origin main\"' "
    "origin main",
    "git push --rece='sh -c \"git push origin main\"' --force origin main",
    "git-push --mirror --receive-pack='sh -c \"git push origin main\"' "
    "origin main",
    "git push --unknown-option --force origin main",
    "true & wait -n \"${999:--p}\" "
    "'x[$(printf BYPASS >&2)0]'",
    "git fetch \"${999:---upload-pack=sh -c "
    "'git push --force origin main'}\" .",
    'git -C .${IFS}-c${IFS}"diff.external=printf INJECTED" '
    "diff pyproject.toml",
    "/tmp/printf harmless",
    "./cat /dev/null",
    "/attacker/true",
    "sudo /usr/bin/git push --force origin main",
    r"true\\ #x; git push --{force,force} origin main",
)
BASH_LINE_CONTINUATION = "\\" + "\n"
BACKSLASH_CRLF = "\\" + "\r\n"
BRACE_EXPANSION_BYPASSES = (
    "git push --{force,force} origin main",
    "git push --{f..f}orce origin main",
    "git push --{force,{force,force}} origin main",
    f"git push --{{f.{BASH_LINE_CONTINUATION}.f}}force origin main",
    f"true{BASH_LINE_CONTINUATION}#x; "
    "git push --{force,force} origin main",
    f"true # comment {BACKSLASH_CRLF}"
    "git push --{force,force} origin main",
    r"true\ #x; git push --{force,force} origin main",
    r"true\;#x; git push --{force,force} origin main",
    r"true\|#x; git push --{force,force} origin main",
    r"true\&#x; git push --{force,force} origin main",
    r"true\(#x; git push --{force,force} origin main",
    r"true\)#x; git push --{force,force} origin main",
    "true\r#x; git push --{force,force} origin main",
    "git push --{force,\rnoop} origin main",
)
BRACE_EXPANSION_CONTROLS = (
    f"true # comment {BASH_LINE_CONTINUATION}"
    "git push --{force,force} origin main",
    f"true {BASH_LINE_CONTINUATION}# $(echo harmless)",
    "true #x; git push --{force,force} origin main",
    r"printf '%s\n' 'true\ #x; "
    "git push --{force,force} origin main'",
    r"echo {\..a}",
    "printf '%s\\n' 'true\r#x; "
    "git push --{force,force} origin main'",
    "printf '%s\\n' 'value\rnoop'",
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


def _shell_auto_allow_policy(tmp_path: Path) -> Path:
    policy = tmp_path / "shell-auto-allow.toml"
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


@pytest.mark.parametrize("command", BRACE_EXPANSION_BYPASSES)
def test_brace_expansion_blocks_shell_auto_allow_without_command_leakage(
    command: str,
    tmp_path: Path,
) -> None:
    result = _run_hook(
        _codex_payload(command),
        policy=_shell_auto_allow_policy(tmp_path),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE
    assert command not in result.stdout
    assert command not in result.stderr


@pytest.mark.parametrize("command", FAIL_CLOSED_COMMAND_FORMS)
@pytest.mark.parametrize("use_default_policy", (False, True))
def test_new_fail_closed_forms_block_shell_auto_allow_without_command_leakage(
    command: str,
    use_default_policy: bool,
    tmp_path: Path,
) -> None:
    result = _run_hook(
        _codex_payload(command),
        policy=(
            _default_auto_allow_policy(tmp_path)
            if use_default_policy
            else _shell_auto_allow_policy(tmp_path)
        ),
    )

    assert result.returncode == HOOK_BLOCK
    assert result.stdout == ""
    assert result.stderr == BLOCK_MESSAGE
    assert command not in result.stdout
    assert command not in result.stderr


@pytest.mark.parametrize("command", BRACE_EXPANSION_CONTROLS)
def test_nonexpanding_brace_controls_remain_shell_auto_allowed(
    command: str,
    tmp_path: Path,
) -> None:
    result = _run_hook(
        _codex_payload(command),
        policy=_shell_auto_allow_policy(tmp_path),
    )

    assert result.returncode == HOOK_ALLOW
    assert result.stdout == ""
    assert result.stderr == ""


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
