"""Where: tests/test_capability_map.py
What: Unit tests for examples/capability_map.py — the shlex-based
      helper that maps a Bash command string to an agent-policy
      capability (push.force / merge.pr / shell).
Why: capability_map.py replaces the previous substring matcher in
     the hook wrappers. The original matcher produced a false positive
     on quoted literals like ``printf '%s\\n' 'git push --force'``,
     classifying them as push.force even though the command is never
     executed. These tests pin:

       1. The false positive is fixed (printf / echo / cat <<EOF).
       2. The true positives the old matcher caught are still caught
          (sudo/bash -c/eval/env-assignment/absolute path).
       3. The compound command logic picks the strictest capability.
       4. Malformed or ambiguous syntax returns ``unknown`` rather than a
          policy-controlled capability.

The helper is stdlib-only by design, so these tests do not need the
agent_policy package installed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "examples" / "capability_map.py"


def _load_helper():
    """Load ``examples/capability_map.py`` as a module without install."""
    spec = importlib.util.spec_from_file_location(
        "capability_map", HELPER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["capability_map"] = module
    spec.loader.exec_module(module)
    return module


capability_map = _load_helper()
map_command = capability_map.map_command

BASH_LINE_CONTINUATION = "\\" + "\n"
BACKSLASH_CRLF = "\\" + "\r\n"
ESCAPED_BACKSLASH_NEWLINE = "\\\\" + "\n"
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


# ---------------------------------------------------------------------------
# Regression: the old substring matcher's false positives
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        # The original bug report — printf with a quoted literal argument
        # that happens to contain the substring "git push --force".
        "printf '%s\\n' 'git push --force origin master'",
        # Same shape with echo.
        "echo 'git push --force'",
        # Double-quoted literal.
        'echo "git push --force origin main"',
        # Comment containing the forbidden substring.
        "ls  # git push --force",
        # Heredoc body — the body must be elided before tokenization.
        "cat <<EOF\ngit push --force\nEOF",
        # Indented heredoc with <<-.
        "cat <<-END\n\tgit push --force\n\tEND",
        # Quoted heredoc delimiter (no parameter expansion, but the
        # body still must be elided).
        "cat <<'EOF'\ngit push --force\nEOF",
    ],
)
def test_quoted_literal_is_not_push_force(command: str) -> None:
    assert map_command(command) == "shell"


def test_quoted_heredoc_operator_is_literal_and_does_not_hide_next_command() -> None:
    # ``<<EOF`` is inside single quotes, so it is data rather than a heredoc
    # operator. The following line is therefore executable shell text and
    # must still be recognized as a force push.
    command = "echo '<<EOF'\ngit push --force origin main"

    assert map_command(command) == "push.force"


@pytest.mark.parametrize(
    "command",
    [
        "echo `git push --force origin main`",
        'echo "`git push --force origin main`"',
        "echo $(git push --force origin main)",
        'echo "$(git push --force origin main)"',
        # Single-quote characters are literal inside double quotes and do
        # not suppress command substitution.
        'echo "\'$(git push --force origin main)\'"',
        # Bash removes line continuations before recognizing ``$()``.
        'echo "$\\\n(git push --force origin main)"',
        # An unquoted heredoc delimiter enables command substitution in its
        # body even though ordinary command text there is data.
        "cat <<EOF\n$(git push --force origin main)\nEOF",
        "cat <<EOF\n`git push --force origin main`\nEOF",
        "cat <<EOF\n$\\\n(git push --force origin main)\nEOF",
        # A substitution after a heredoc operator still runs on the command
        # line before the body is consumed.
        'cat <<EOF "$(git push --force origin main)"\nbody\nEOF',
    ],
)
def test_active_command_substitution_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "echo '`git push --force origin main`'",
        "echo '$(git push --force origin main)'",
        # A line continuation remains literal inside single quotes.
        "echo '$\\\n(git push --force origin main)'",
        # Backslash quoting inside double quotes makes these literal data.
        r'echo "\`git push --force origin main\`"',
        r'echo "\$(git push --force origin main)"',
        r"echo \`git push --force origin main\`",
        # Quoting the heredoc delimiter disables expansion in its body.
        "cat <<'EOF'\n$(git push --force origin main)\nEOF",
        "cat <<'EOF'\n`git push --force origin main`\nEOF",
        "cat <<'EOF'\n$\\\n(git push --force origin main)\nEOF",
        # Backslash quoting also disables individual substitutions in an
        # otherwise expanding heredoc body.
        "cat <<EOF\n\\$(git push --force origin main)\nEOF",
        "cat <<EOF\n\\`git push --force origin main\\`\nEOF",
        'bash -c \'printf "%s\\n" "$HOME"\'',
        "builtin printf '%s\\n' ok",
    ],
)
def test_literal_or_non_command_substitution_remains_shell(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (f"true {BASH_LINE_CONTINUATION}# $(echo harmless)", "shell"),
        (
            f"true {BASH_LINE_CONTINUATION}{BASH_LINE_CONTINUATION}"
            "# $(echo harmless)",
            "shell",
        ),
        (f"true {ESCAPED_BACKSLASH_NEWLINE}# $(echo harmless)", "shell"),
        ("true " + "\\\\\\" + "\n# $(echo harmless)", "unknown"),
        (r"true\ " + BASH_LINE_CONTINUATION + "# $(echo harmless)", "unknown"),
        (f"true {BACKSLASH_CRLF}# $(echo harmless)", "shell"),
        ("true " + "\\" + "\r# $(echo harmless)", "unknown"),
    ],
)
def test_logical_line_comment_boundaries(command: str, expected: str) -> None:
    assert map_command(command) == expected


@pytest.mark.parametrize(
    "command",
    [
        "A='x[$(git push --force origin main)]'; : $((A))",
        "((A))",
        "$[A]",
        "A='x[$(git push --force origin main)]'; let A",
        "A='x[$(git push --force origin main)]'; declare -i A",
        "A='x[$(git push --force origin main)]'; typeset -ir A",
        "A='x[$(git push --force origin main)]'; local +i A",
        "A='x[$(git push --force origin main)0]'; declare -n ref='x[A]'",
        "A='x[$(git push --force origin main)0]'; "
        "declare -a x='([A]=value)'",
        "A='x[$(git push --force origin main)0]'; printf -v 'x[A]' '%s' ok",
        "A='x[$(git push --force origin main)0]'; printf -v'x[A]' '%s' ok",
        "A='x[$(git push --force origin main)0]'; opt=v; "
        "printf -$opt 'x[A]' '%s' ok",
        "A='x[$(git push --force origin main)0]'; opt=v; "
        "builtin printf -$opt 'x[A]' '%s' ok",
        "A='x[$(git push --force origin main)0]'; dash=-; opt=v; "
        "printf ${dash}${opt} 'x[A]' '%s' ok",
        "A='x[$(git push --force origin main)0]'; opt=a; "
        "declare -$opt x='([A]=value)'",
        "A='x[$(git push --force origin main)0]'; "
        "builtin printf -v 'x[A]' '%s' ok",
        "A='x[$(git push --force origin main)0]'; unset 'x[A]'",
        "A='x[$(git push --force origin main)0]'; read 'x[A]' <<< ok",
        "A='x[$(git push --force origin main)0]'; test -v 'x[A]'",
        "A='x[$(git push --force origin main)0]'; opt=v; test -$opt 'x[A]'",
        "A='x[$(git push --force origin main)0]'; value=abc; : ${value:A}",
        "A='x[$(git push --force origin main)0]'; : ${x[A]}",
        "A='x[$(git push --force origin main)0]'; name='x[A]'; : ${!name}",
        "A='x[$(git push --force origin main)0]'; "
        "value=abc; : ${value:-${value:A}}",
        "A='x[$(git push --force origin main)0]'; echo \"${missing:-$((A))}\"",
        "A='x[$(git push --force origin main)0]'; "
        "echo \"${missing:-'$((A))'}\"",
        "A='x[$(git push --force origin main)0]'; echo \"${present:+$((A))}\"",
        "A='x[$(git push --force origin main)0]'; echo \"${missing:=$((A))}\"",
        "A='x[$(git push --force origin main)0]'; echo \"${missing:?$((A))}\"",
        "A='x[$(git push --force origin main)0]'; echo \"${value/x/$((A))}\"",
        "A='x[$(git push --force origin main)0]'; echo \"${missing:-$[A]}\"",
        'echo "$((A))"',
        "cat <<EOF\n$((A))\nEOF",
        "A='x[$(git push --force origin main)0]'; cat <<EOF\n"
        "$\\\n((A))\nEOF",
        "A='x[$(git push --force origin main)0]'; cat <<EOF\n"
        "$\\\n[A]\nEOF",
    ],
)
def test_active_arithmetic_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "echo '$((A)) $[A] ((A))'",
        r"echo \$((A))",
        r"echo \$\[A\]",
        r"echo \(\(A\)\)",
        r'echo "\$((A))"',
        "cat <<'EOF'\n$((A))\nEOF",
        "cat <<EOF\n\\$\\\n((A))\nEOF",
        "printf '%s' \"${missing:-\\$((A))}\"",
        "printf '%s' \"${missing:-((safe))}\"",
    ],
)
def test_literal_arithmetic_forms_remain_shell(command: str) -> None:
    assert map_command(command) == "shell"


def test_arithmetic_command_with_nested_substitution_is_unknown() -> None:
    assert map_command("((x = 1 << 2))") == "unknown"
    assert map_command("((x = $(printf 1) << 2))") == "unknown"


def test_deep_parameter_expansion_word_is_processed_once_per_level() -> None:
    word = "safe"
    for _ in range(20):
        word = "${v:-" + word + "}"

    assert map_command(f'printf %s "{word}"') == "shell"


def test_expanding_heredoc_with_later_unmodeled_command_fails_closed() -> None:
    command = (
        "cat <<EOF\n"
        "EO\\\n"
        "F\n"
        "git push --force origin main\n"
        "EOF"
    )
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "cat <<EOF\nEO\\\\\nF\nEOF",
        "cat <<'EOF'\nEO\\\nF\nEOF",
    ],
)
def test_literal_heredoc_delimiter_continuations_remain_body(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize("command", BRACE_EXPANSION_BYPASSES)
def test_active_unquoted_brace_expansion_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "echo {1..2}",
        "echo {-2..+2}",
        "echo {1..3..+1}",
        "echo {a..z..2}",
    ],
)
def test_valid_brace_sequence_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "echo ${value:-{force,force}}",
        "cat <<'EOF'\ngit push --{force,force} origin main\nEOF",
        f"printf '%s\\n' 'git push --{{f.{BASH_LINE_CONTINUATION}.f}}force'",
        f'printf \'%s\\n\' "git push --{{f.{BASH_LINE_CONTINUATION}.f}}force"',
        f"printf '%s\\n' 'true{BASH_LINE_CONTINUATION}#x; "
        "git push --{force,force} origin main'",
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
        "echo {..}",
        "echo {a..bc}",
        "echo {1..2..x}",
    ],
)
def test_non_expanding_brace_forms_remain_shell(command: str) -> None:
    assert map_command(command) == "shell"


# ---------------------------------------------------------------------------
# Pathname expansion: unquoted argv generation must fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push --fo* origin main",
        "git push --fo? origin main",
        "git push --fo[rc]e origin main",
        "git push --fo[[:alpha:]] origin main",
        "git push --@(force|force) origin main",
        "git push --+(force) origin main",
        "git push --!(safe) origin main",
    ],
)
def test_active_unquoted_pathname_expansion_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "printf '%s' 'git push --fo* origin main'",
        "true # git push --fo* origin main",
        """cat <<EOF
git push --fo* origin main
EOF""",
    ],
)
def test_literal_pathname_expansion_controls_remain_shell(command: str) -> None:
    assert map_command(command) == "shell"


# ---------------------------------------------------------------------------
# True positives: must still classify as push.force
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "git push --force origin master",
        "git push --force-with-lease origin master",
        "git push --force-with-lease=origin/main",
        "git push --mirror origin",
        "git push origin +HEAD:main",
        "git push --force-w origin main",
        "git push --mir origin",
        "git send-pack --force origin HEAD:main",
        "git-send-pack --force origin HEAD:main",
        "git-push --force origin main",
        "git push -f origin main",
        # Short-option cluster: -fu == -f -u.
        "git push -fu origin main",
        # Scan-anywhere: sudo wrapper.
        "sudo git push --force origin main",
        # Compound command: strictest wins.
        "git status && git push --force",
        "git push --force; ls",
        "git fetch || git push --force",
        "git diff | tee /tmp/x && git push --force",
        # Recursive: bash -c '...' / sh -c '...' / eval.
        "bash -c 'git push --force origin main'",
        'sh -c "git push --force origin main"',
        'eval "git push --force origin main"',
    ],
)
def test_force_push_is_detected(command: str) -> None:
    assert map_command(command) == "push.force"


@pytest.mark.parametrize(
    "command",
    [
        "BASH_ENV=/dev/stdin bash -c 'echo SAFE' <<< 'git push --force origin main'",
        "BASH_ENV=/tmp/agent-policy-startup bash -c 'echo SAFE'",
        "env BASH_ENV=/tmp/agent-policy-startup bash -c 'echo SAFE'",
        "BASH_ENV=/tmp/agent-policy-startup",
        "export BASH_ENV=/tmp/agent-policy-startup; bash -c 'echo SAFE'",
        "ENV=/tmp/agent-policy-startup sh -ic 'echo SAFE'",
        "ENV=/tmp/agent-policy-startup dash -ic 'echo SAFE'",
        "ENV=/tmp/agent-policy-startup ksh -ic 'echo SAFE'",
        "ZDOTDIR=/tmp/agent-policy-startup zsh -c 'echo SAFE'",
        "HOME=/tmp/agent-policy-startup zsh -c 'echo SAFE'",
        "HOME=/tmp/agent-policy-startup bash -lc 'echo SAFE'",
        "HOME=/tmp/agent-policy-startup bash -ic 'echo SAFE'",
        "bash --rcfile /tmp/agent-policy-startup -ic 'echo SAFE'",
        "bash --init-file /tmp/agent-policy-startup -ic 'echo SAFE'",
        "set -a",
        "set -o allexport",
        "set -o nounset -a",
        "SHELLOPTS=allexport bash -c 'echo SAFE'",
        "env SHELLOPTS=allexport bash -c 'echo SAFE'",
        "export SHELLOPTS=allexport; bash -c 'echo SAFE'",
        "bash -ac 'echo SAFE'",
        "bash -o allexport -c 'echo SAFE'",
        "bash -ac 'sleep .05 & p=$!; printf SAFE >\"$p\"; "
        "wait -n -p BASH_ENV; bash -c :'",
        "set -a; printf -v BASH_ENV /dev/stdin; bash -c 'echo SAFE'",
        "read BASH_ENV <<< /dev/stdin; bash -c 'echo SAFE'",
    ],
)
def test_shell_startup_selector_assignments_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "A='x[$(git push --force origin main)0]'; true & wait -n -p 'x[A]'",
        "wait -p 'x[A]' -n",
        'wait -p "$target" -n',
        "wait -p -n",
        "wait -p job_id -n",
        "wait -pjob_id -n",
        "wait -nf -p job_id",
    ],
)
def test_wait_p_assignments_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "wait",
        "wait -n",
        "wait -- -p",
    ],
)
def test_wait_without_assignment_remains_shell(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize(
    "command",
    [
        "trap ':' DEBUG",
        "trap 'export BASH_ENV=/tmp/agent-policy-startup' DEBUG; "
        "bash -c 'echo SAFE'",
        "trap 'export BASH_ENV=/tmp/agent-policy-startup' EXIT",
        'trap -p "$signal"',
    ],
)
def test_trap_mutation_and_dynamic_queries_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "trap",
        "trap -p",
        "trap -p DEBUG",
        "trap -lp DEBUG",
        "trap -l",
        "printf '%s\\n' \"trap 'export BASH_ENV=/tmp/agent-policy-startup' DEBUG\"",
    ],
)
def test_literal_trap_queries_and_data_remain_shell(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'echo SAFE' argument",
        "bash -c 'echo SAFE' </dev/null",
    ],
)
def test_shell_wrapper_post_body_tokens_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "bash -c 'echo SAFE'",
    ],
)
def test_shell_wrapper_without_trailing_tokens_remains_shell(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize(
    "command",
    [
        "HOME=/tmp/reviewed-home printf '%s' ok",
        "env HOME=/tmp/reviewed-home printf '%s' ok",
    ],
)
def test_home_assignment_outside_zsh_still_fails_closed(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    ("command", "capability"),
    [
        ("bash -lc \"git push --force origin main\"", "unknown"),
        ("env bash -c \"git push --force origin main\"", "push.force"),
        ("cat <(bash -c \"git push --force origin main\")", "unknown"),
        ("git -C /tmp push --force origin main", "push.force"),
        ("git -c alias.p=push p --force origin main", "unknown"),
        ("git --config-env=alias.p=GIT_ALIAS p --force origin main", "unknown"),
        ("bash <<'EOF'\ngit push --force origin main\nEOF", "unknown"),
        ("bash <<< 'git push --force origin main'", "unknown"),
        ("printf '%s\\n' 'git push --force origin main' | bash", "unknown"),
        ("bash reviewed-script.sh", "unknown"),
        ('cmd="git push --force origin main"; bash -c "$cmd"', "unknown"),
        ("runner=bash; $runner -c 'git push --force origin main'", "unknown"),
        ("builtin eval 'git push --force origin main'", "unknown"),
        ("bash -c 'echo safe'\ngit push --force origin main", "push.force"),
        ("F=--force; git push $F origin main", "unknown"),
        ("REF=+HEAD:main; git push origin $REF", "unknown"),
        ("xargs -n1 git push --force origin main", "unknown"),
        ("> /dev/null git push --force origin main", "unknown"),
        ("2>/dev/null git push --force origin main", "unknown"),
        ("exec git push --force origin main", "unknown"),
        ("time git push --force origin main", "unknown"),
        ("nohup git push --force origin main", "unknown"),
        ("stdbuf -oL git push --force origin main", "unknown"),
        ('runner=git; stdbuf -oL "$runner" push --force origin main', "unknown"),
        ("( git push --force origin main )", "unknown"),
        ("echo hi |& git push --force origin main", "push.force"),
        ("&>/dev/null git push --force origin main", "unknown"),
        ("{fd}>/dev/null git push --force origin main", "unknown"),
        ("sudo FOO=bar git push --force origin main", "unknown"),
        (
            'P=-exec; find . "$P" git push --force origin main \\;',
            "unknown",
        ),
        (
            "GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push "
            "GIT_CONFIG_VALUE_0=+HEAD:refs/heads/main git push origin",
            "unknown",
        ),
        (
            "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=remote.origin.push "
            "GIT_CONFIG_VALUE_0=+HEAD:refs/heads/main git push origin",
            "unknown",
        ),
    ],
)
def test_force_push_execution_forms_do_not_fall_through_to_shell(
    command: str,
    capability: str,
) -> None:
    assert map_command(command) == capability


@pytest.mark.parametrize(
    "command",
    [
        "git push -o +ci.skip origin main",
        "git push -o+ci.skip origin main",
        "git push --push-option +ci.skip origin main",
        "git push --push-option=+ci.skip origin main",
    ],
)
def test_push_option_values_are_not_force_refspecs(command: str) -> None:
    assert map_command(command) == "shell"


def test_command_dispatch_before_shell_wrapper_returns_unknown() -> None:
    assert (
        map_command('command bash -c "git push --force origin main"')
        == "unknown"
    )


# ---------------------------------------------------------------------------
# True positives: merge.pr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "gh pr merge 42 --merge",
        "gh pr merge --squash",
        "sudo gh pr merge 99",
    ],
)
def test_gh_pr_merge_is_detected(command: str) -> None:
    assert map_command(command) == "merge.pr"


# ---------------------------------------------------------------------------
# Compound strictness: push.force must win over merge.pr
# ---------------------------------------------------------------------------


def test_compound_strictest_capability_wins() -> None:
    # merge.pr < push.force on the _STRICTNESS scale — so a pipeline
    # mixing the two must resolve to push.force.
    cmd = "gh pr merge 42 && git push --force origin main"
    assert map_command(cmd) == "push.force"


# ---------------------------------------------------------------------------
# Dynamic argv execution and Git dispatch: fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        r"printf '%s\n' --force | xargs -I{} git push {} origin main",
        "xargs -n1 git push --force origin main",
        "find . -exec printf '%s' {} \\;",
        "find . -execdir printf '%s' {} +",
        "find . -ok printf '%s' {} \\;",
        "find . -okdir printf '%s' {} \\;",
        # Conservatively blocked: this token could be a -name value, but the
        # bounded parser does not model find expression arity.
        "find /tmp -maxdepth 0 -name -exec",
    ],
)
def test_dynamic_argv_execution_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "printf 'x\\n' | mapfile -C 'git push --force origin main' -c 1 arr",
        "printf 'x\\n' | readarray -C 'git push --force origin main' -c 1 arr",
        "compgen -C 'git push --force origin main' x",
    ],
)
def test_callback_builtins_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "PS4='$(git push --force origin main)'; set -x; true",
        "PS4='$(git push --force origin main)' bash -xc true",
        "bash -xc true",
        "bash -o xtrace -c true",
        "VALUE=reviewed printf '%s' ok",
        "env VALUE=reviewed printf '%s' ok",
        "FOO=bar git push --force origin main",
        "FOO=bar bash -c 'echo SAFE'",
        "export VALUE=reviewed",
        "declare value=1",
        "typeset -r value=1",
        "set -u",
        "set -o nounset",
        "value=abc; printf '%s' \"${value:-safe}\"",
        "value=abc; printf '%s' \"${value:-[safe]}\"",
        "option=v; printf '%s' \"-$option\"",
        "PATTERN=* git status",
        "SECOND=? FIRST=* git status",
        "echo hi\nPATTERN=* git status",
    ],
)
def test_unmodeled_shell_state_and_assignments_return_unknown(
    command: str,
) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "GIT_SSH_COMMAND='sh -c \"git push --force origin main\" dummy' "
        "git push origin main",
        "env GIT_SSH_COMMAND='sh -c \"git push --force origin main\" dummy' "
        "git push origin main",
        "GIT_SSH=reviewed-wrapper git push origin main",
        "GIT_SSH_COMMAND='ssh -i reviewed-key' git push --force origin main",
        "GIT_PROXY_COMMAND='sh -c \"git push --force origin main\"' "
        "git fetch origin",
        "export GIT_PROXY_COMMAND='sh -c \"git push --force origin main\"'; "
        "git fetch origin",
    ],
)
def test_command_bearing_git_environment_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "git push --receive-pack='sh -c true' origin main",
        "git push --rece='sh -c true' origin main",
        "git push --exec='sh -c true' origin main",
        "git push --exe='sh -c true' origin main",
        "git fetch --upload-pack='sh -c true' origin",
        "git fetch --upl='sh -c true' origin",
        "git send-pack --receive-pack='sh -c true' origin main",
        "git-push --exec='sh -c true' origin main",
        "git-send-pack --rece='sh -c true' origin main",
        "git push --force --receive-pack='sh -c true' origin main",
        "git push --rece='sh -c true' --force origin main",
        "git send-pack --force --exec='sh -c true' origin main",
        "git-push --mirror --receive-pack='sh -c true' origin main",
        "git push --unknown-option --force origin main",
        "git status --future-command-mode",
        "git push '--{force,force}' origin main",
        'git push "--{force,force}" origin main',
        r"git push --\{force,force\} origin main",
        "git push --fo\\* origin main",
        "git push '--fo[rc]e' origin main",
        "git push '--@(force|force)' origin main",
        "git push --fo\\[rc\\]e origin main",
        "git push --fo[] origin main",
    ],
)
def test_unmodeled_git_subcommand_options_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "git config alias.fp 'push --force'",
        "git fp origin main",
        "git unrecognized-subcommand",
        "git-config alias.fp 'push --force'",
        "git-fp origin main",
    ],
)
def test_git_aliases_and_unrecognized_subcommands_return_unknown(
    command: str,
) -> None:
    assert map_command(command) == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "echo xargs",
        "git commit -m xargs",
        "echo find -exec",
    ],
)
def test_dynamic_executor_words_in_arguments_remain_shell(command: str) -> None:
    assert map_command(command) == "shell"


def test_sensitive_command_words_behind_unmodeled_prefix_fail_closed() -> None:
    assert map_command("printf '%s' git push --force") == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        "{ echo a,b; }",
        "source reviewed-script.sh",
    ],
)
def test_unmodeled_execution_prefixes_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


# ---------------------------------------------------------------------------
# Plain shell commands
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "ls -la",
        "git status",
        "git add README.md",
        "git commit -m wip",
        "git diff --cached",
        "git fetch origin",
        "git push origin main",
        "git push origin main --tags",
        "git commit -m 'wip'",
    ],
)
def test_plain_shell(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize(
    "command",
    [
        ":",
        "[ 1 = 1 ]",
        "cat /dev/null",
        "echo reviewed",
        "false",
        "ls -la",
        "printf '%s' reviewed",
        "pwd",
        "printf reviewed | tee /dev/null",
        "test 1 = 1",
        "true",
    ],
)
def test_modeled_simple_command_allowlist_remains_shell(command: str) -> None:
    assert map_command(command) == "shell"


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        "python -c 'print(1)'",
        "awk 'BEGIN { system(\"true\") }'",
        "make test",
        "/tmp/printf harmless",
        "./cat /dev/null",
        "/attacker/true",
        "/usr/bin/git push --force origin main",
        "/usr/lib/git-core/git-push --force origin main",
        "/usr/local/bin/gh pr merge 1",
        "sudo /usr/bin/git push --force origin main",
        f"git push --{{f.{ESCAPED_BACKSLASH_NEWLINE}.f}}force origin main",
        r"true\\ #x; git push --{force,force} origin main",
    ],
)
def test_unmodeled_command_heads_return_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


# ---------------------------------------------------------------------------
# Defensive fallbacks
# ---------------------------------------------------------------------------


def test_unbalanced_quotes_return_unknown() -> None:
    assert map_command("echo 'unterminated") == "unknown"


@pytest.mark.parametrize(
    "command",
    [
        # The operator has no delimiter word.
        "cat <<\ngit push --force origin main",
        # Delimiter expansion cannot be resolved by the bounded parser.
        "cat <<$EOF\ngit push --force origin main\nEOF",
        # The header has no closing delimiter line.
        "cat <<EOF\ngit push --force origin main",
        # Multiple real heredocs are deliberately rejected as ambiguous.
        "cat <<ONE <<TWO\nignored\nONE\nignored\nTWO",
    ],
)
def test_ambiguous_or_unterminated_heredoc_returns_unknown(command: str) -> None:
    assert map_command(command) == "unknown"


def test_empty_command_is_shell() -> None:
    assert map_command("") == "shell"
    assert map_command("   ") == "shell"


def test_deeply_nested_bash_c_is_capped() -> None:
    # Nest bash -c beyond _MAX_RECURSION — the inner push.force should
    # be hidden but the helper must still terminate and return shell.
    inner = "git push --force origin main"
    nested = inner
    for _ in range(capability_map._MAX_RECURSION + 2):
        nested = f"bash -c {nested!r}"
    # Depth cap kicks in before the innermost classification is reached.
    # The helper may still catch it if the recursion fits under the cap;
    # otherwise it must return the dedicated unknown bucket.
    result = map_command(nested)
    assert result in {"unknown", "push.force"}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def test_main_reads_argv(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["capability_map.py", "git push --force"])
    exit_code = capability_map.main()
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    assert out == "push.force"


def test_main_reads_stdin(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["capability_map.py"])
    monkeypatch.setattr(sys, "stdin", _StubStdin("echo 'git push --force'"))
    exit_code = capability_map.main()
    assert exit_code == 0
    out = capsys.readouterr().out.strip()
    assert out == "shell"


class _StubStdin:
    """Minimal stand-in for sys.stdin exposing ``read()``."""

    def __init__(self, data: str) -> None:
        self._data = data

    def read(self) -> str:
        return self._data
