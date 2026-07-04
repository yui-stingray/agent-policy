"""Where: examples/capability_map.py
What: Map a shell command string to an agent-policy capability for the
      hook wrappers (codex_hook.sh, codex_permission_request_hook.sh,
      claude_code_hook.sh).

Why: Earlier versions of the bash hooks used raw substring matching on
     the command string, e.g. ``*"git push"*"--force"*``. This produced
     false positives when command-like text appeared inside quoted
     arguments — most notably:

         printf '%s\\n' 'git push --force origin master'

     was classified as ``push.force`` because the substring was visible
     to the case statement, even though the quoted literal is never
     executed. This helper avoids that by:

         1. Stripping heredoc bodies so ``cat <<EOF ... git push
            --force ... EOF`` is not scanned as commands.
         2. Using :mod:`shlex` with ``punctuation_chars=True`` so shell
            operators like ``;``, ``&&``, ``||``, ``|``, ``&`` become
            their own tokens and quoted arguments collapse into single
            opaque tokens.
         3. Splitting the token stream into statements on those
            operators and classifying each statement independently.
         4. Within a statement, scanning for the narrow set of
            patterns the hooks care about:

                git push ... --force[-with-lease] / -f  → push.force
                gh pr merge ...                         → merge.pr
                anything else                           → shell

         5. Recursively classifying the embedded command when the
            statement is ``bash -c '...'`` / ``sh -c '...'`` / ``eval
            '...'``, so dropping into a nested shell does not hide a
            ``push.force`` from the hook.

How to use: the bash hooks invoke
``python3 capability_map.py "<command>"`` and read the capability name
from stdout. ``main`` also accepts the command on stdin when no argv
is given, which is convenient for piping from ``jq``.

Scope: this is deliberately narrow. Full shell semantics (command
substitution, process substitution, ``$(...)``, background jobs,
function definitions) are not modeled. The fail-closed default is
``shell``, which policy.toml can still flag as ``require_approval`` or
``deny``, so falling through is safe.

This module is stdlib-only so it does not depend on ``agent_policy``
and can be exercised by unit tests without the package install.
"""

from __future__ import annotations

import os
import re
import shlex
import sys

# Shell operators at which a new command statement starts. shlex with
# punctuation_chars=True emits these as their own tokens when they
# appear unquoted.
_SEPARATORS = frozenset({";", ";;", "&", "&&", "|", "||"})

# Shell wrappers that take an embedded command via ``-c <cmd>``. We
# recurse into their argument so ``bash -c 'git push --force'`` is not
# hidden from the scanner.
_SHELL_WRAPPERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

# Strictness ordering. When a compound command mixes capabilities
# (e.g. ``git status && git push --force``), the strictest one wins —
# this mirrors fail-closed semantics for the wrapper.
_STRICTNESS = {"shell": 0, "merge.pr": 1, "push.force": 2}

# Heredoc header: ``<<WORD``, ``<<-WORD``, ``<<'WORD'``, ``<<"WORD"``.
# The delimiter must be a plain identifier — we do not try to handle
# quoted-variable delimiters because they are vanishingly rare in
# agent-generated commands.
_HEREDOC_HEADER = re.compile(
    r"""<<-?\s*['"`]?([A-Za-z_][A-Za-z0-9_]*)['"`]?"""
)

# Cap recursion for shell wrappers so a pathological nesting cannot
# drive this helper into unbounded work.
_MAX_RECURSION = 4


def map_command(command: str, _depth: int = 0) -> str:
    """Return the agent-policy capability for a Bash command string.

    Parameters
    ----------
    command:
        The raw Bash command as received by the hook. May contain
        pipelines, heredocs, quoted arguments, and shell wrappers.
    _depth:
        Internal recursion counter for ``bash -c '...'`` / ``eval`` —
        callers should not set this.

    Returns
    -------
    str
        One of ``"push.force"``, ``"merge.pr"``, ``"shell"``.
    """
    if _depth > _MAX_RECURSION:
        return "shell"

    stripped = _strip_heredocs(command)

    try:
        tokens = _tokenize(stripped)
    except ValueError:
        # Unbalanced quotes — safest non-escalating bucket. Policy can
        # still flag ``shell`` as require_approval or deny.
        return "shell"

    strictest = "shell"
    current: list[str] = []
    # Trailing sentinel flushes the final statement through the loop.
    for token in (*tokens, ";"):
        if token in _SEPARATORS:
            if current:
                strictest = _stricter(
                    strictest, _classify_statement(current, _depth)
                )
                current = []
            continue
        current.append(token)
    return strictest


def _tokenize(command: str) -> list[str]:
    """Tokenize ``command`` preserving shell operators as distinct tokens."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _strip_heredocs(command: str) -> str:
    """Remove heredoc bodies so their contents are not scanned.

    Handles the common forms ``<<WORD``, ``<<-WORD``, ``<<'WORD'``,
    ``<<"WORD"``. The body — from the newline after the header to the
    closing delimiter on its own line — is elided. An unterminated
    heredoc elides to the end of the string, which is the safest
    direction for a fail-closed gate (nothing beyond the header can
    be used to escalate capability).
    """
    out: list[str] = []
    pos = 0
    while True:
        match = _HEREDOC_HEADER.search(command, pos)
        if match is None:
            out.append(command[pos:])
            return "".join(out)
        out.append(command[pos : match.end()])
        delimiter = match.group(1)

        # The body starts at the next newline after the header.
        newline = command.find("\n", match.end())
        if newline < 0:
            # Header with no following newline — nothing to strip.
            pos = match.end()
            continue
        body_start = newline + 1

        # Find the closing delimiter on its own (possibly indented) line.
        lines = command[body_start:].split("\n")
        end_line = None
        for i, line in enumerate(lines):
            if line.strip() == delimiter:
                end_line = i
                break
        if end_line is None:
            # Unterminated — drop everything from body_start on.
            return "".join(out)
        # Advance past the closing delimiter line (the +1 covers the
        # newline that ``split`` consumed).
        consumed = sum(len(lines[i]) + 1 for i in range(end_line + 1))
        pos = body_start + consumed


def _classify_statement(tokens: list[str], depth: int) -> str:
    """Classify a single statement (already split on operators)."""
    # Skip leading env assignments: ``FOO=bar git push --force``.
    i = 0
    while i < len(tokens) and _is_env_assignment(tokens[i]):
        i += 1
    tokens = tokens[i:]
    if not tokens:
        return "shell"

    # Recurse into shell wrappers and eval. These patterns embed a
    # command in a string argument, so the old substring matcher
    # relied on them being visible in the raw string. We recover the
    # equivalent coverage by tokenizing and recursing.
    head_basename = os.path.basename(tokens[0])
    if head_basename in _SHELL_WRAPPERS:
        nested = _classify_wrapper_c(tokens[1:], depth)
        if nested != "shell":
            return nested
    if head_basename == "eval":
        # ``eval arg1 arg2 ...`` concatenates its args before executing.
        embedded = " ".join(tokens[1:])
        if embedded:
            nested = map_command(embedded, _depth=depth + 1)
            if nested != "shell":
                return nested

    # Scan the statement for the two narrow patterns the hooks care
    # about. We scan anywhere in the token stream so wrappers like
    # ``sudo git push --force`` or ``xargs -n1 git push --force`` are
    # still caught — the old substring behavior matched these true
    # positives and we preserve that coverage.
    for i, tok in enumerate(tokens):
        basename = os.path.basename(tok)
        if basename == "git" and tokens[i + 1 : i + 2] == ["push"]:
            if _has_force_flag(tokens[i + 2 :]):
                return "push.force"
        if basename == "gh" and tokens[i + 1 : i + 3] == ["pr", "merge"]:
            return "merge.pr"
    return "shell"


def _classify_wrapper_c(args: list[str], depth: int) -> str:
    """Handle ``bash -c <cmd>`` / ``sh -c <cmd>`` embedded commands.

    Scans ``args`` for a ``-c`` option and recursively classifies its
    argument. Returns ``"shell"`` when no ``-c`` is present or no
    escalation is found.
    """
    j = 0
    while j < len(args):
        if args[j] == "-c" and j + 1 < len(args):
            return map_command(args[j + 1], _depth=depth + 1)
        j += 1
    return "shell"


def _has_force_flag(push_args: list[str]) -> bool:
    """True if any arg to ``git push`` is a force-style flag.

    Recognizes ``--force``, ``--force-with-lease`` (including the
    ``--force-with-lease=<refname>`` form), ``-f``, and short-option
    clusters that contain ``f`` (e.g. ``-fu``).
    """
    for arg in push_args:
        if arg in ("--force", "-f"):
            return True
        if arg.startswith("--force-with-lease"):
            return True
        if (
            arg.startswith("-")
            and not arg.startswith("--")
            and "f" in arg[1:]
        ):
            return True
    return False


def _is_env_assignment(token: str) -> bool:
    """True if ``token`` looks like a leading ``FOO=bar`` env assignment."""
    if "=" not in token:
        return False
    head = token.split("=", 1)[0]
    if not head or head[0].isdigit():
        return False
    return all(c.isalnum() or c == "_" for c in head)


def _stricter(a: str, b: str) -> str:
    """Return whichever of ``a`` or ``b`` is the stricter capability."""
    return a if _STRICTNESS.get(a, 0) >= _STRICTNESS.get(b, 0) else b


def main() -> int:
    """CLI entry point: ``python3 capability_map.py <command>``.

    With no argv, reads the command from stdin. Prints the resolved
    capability as a single line on stdout and exits zero.
    """
    if len(sys.argv) == 2:
        command = sys.argv[1]
    elif len(sys.argv) > 2:
        command = " ".join(sys.argv[1:])
    else:
        command = sys.stdin.read()
    print(map_command(command))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
