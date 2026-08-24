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

         1. Stripping bodies only for definite, unquoted heredoc
            operators, so ``cat <<EOF ... git push --force ... EOF``
            is not scanned as commands while ``echo '<<EOF'`` remains
            literal data. Expanding bodies containing active command or
            arithmetic expansion are rejected instead of elided, and their
            delimiter lines are matched after backslash-newline folding.
         2. Normalizing active Bash backslash-newline continuations for
            brace, pathname-expansion, and comment analysis, then returning
            ``unknown`` for active unquoted brace expansion or pathname
            expansion before tokenization because ``shlex`` cannot model
            forms such as ``--{force,force}`` or ``--fo*``.
         3. Using :mod:`shlex` with ``punctuation_chars=True`` so shell
            operators like ``;``, ``&&``, ``||``, ``|``, ``&`` become
            their own tokens and quoted arguments collapse into single
            opaque tokens.
         4. Splitting the token stream into statements on those
            operators and classifying each statement independently.
         5. Within a statement, scanning for the narrow set of
            patterns the hooks care about:

                git push ... --force[-with-lease] / -f  → push.force
                gh pr merge ...                         → merge.pr
                anything else                           → shell

         6. Returning ``unknown`` for visible dynamic argv execution
            forms (currently ``xargs`` and ``find -exec``-style
            predicates) instead of guessing about generated arguments.

         7. Recursively classifying the embedded command when the
            statement is ``bash -c '...'`` / ``sh -c '...'`` / ``eval
            '...'``, so dropping into a nested shell does not hide a
            ``push.force`` from the hook.

         8. Returning ``unknown`` for malformed, ambiguous, incomplete, or
            unmodeled execution syntax, including trap mutation and
            ``wait -p`` assignment. Hooks must reject ``unknown`` rather
            than passing it to policy fallback.

         9. Returning unknown for active command and arithmetic expansion.
            The bounded parser does not interpret those active forms;
            single-quoted and escaped forms remain literal data. Options for
            arithmetic-sensitive builtins also fail closed when parameter
            expansion can change the option before execution.

How to use: the bash hooks invoke
``python3 capability_map.py "<command>"`` and read the capability name
from stdout. ``main`` also accepts the command on stdin when no argv
is given, which is convenient for piping from ``jq``.

Scope: this is deliberately narrow. Full shell semantics (background jobs,
function definitions, and trap mutation) are not modeled. Active command and
process substitution, arithmetic expansion, visible dynamic argv execution, and
Git subcommands outside a small builtin allowlist are rejected as ``unknown``
rather than parsed.
Clear commands outside the narrow patterns fall back to ``shell``. Syntax
the helper cannot parse confidently returns ``unknown`` so a wrapper can
fail closed before policy evaluation.

Active unquoted brace and pathname expansion are rejected before ``shlex``
because they can create arguments that are not visible in the raw token
stream. The scanners use Bash logical lines after exact backslash-LF
continuations are removed. Quoted or escaped expansion syntax, comments,
simple parameter expansion, ordinary shell grouping, and quoted-delimiter
heredoc bodies do not trigger this fallback. Arithmetic-bearing and indirect
parameter expansions fail closed.

This module is stdlib-only so it does not depend on ``agent_policy``
and can be exercised by unit tests without the package install.
"""

from __future__ import annotations

import os
import shlex
import sys

# Shell operators at which a new command statement starts. shlex with
# punctuation_chars=True emits these as their own tokens when they
# appear unquoted.
_SEPARATORS = frozenset({";", ";;", "&", "&&", "|", "|&", "||"})

# Bash's lexical blanks and line separator. Python's str.isspace() also
# includes characters such as CR that remain ordinary shell word content.
_BASH_LEXICAL_WHITESPACE = frozenset({" ", "\t", "\n"})

# Shell wrappers that take an embedded command via ``-c <cmd>``. We
# recurse into their argument so ``bash -c 'git push --force'`` is not
# hidden from the scanner.
_SHELL_WRAPPERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})

# Startup-file selectors for the shell wrappers above. BASH_ENV is read by
# non-interactive Bash; ENV covers the modeled sh/dash/ksh family (and
# interactive POSIX-mode Bash); ZDOTDIR selects zsh startup files. This is
# intentionally a finite list:
# arbitrary assignments remain classifiable, but a selector assignment is
# unknown even when it is statement-only because this model has no shell state
# machine to prove it cannot later reach a wrapper.
_SHELL_STARTUP_FILE_SELECTORS = frozenset(
    {"BASH_ENV", "ENV", "ZDOTDIR"}
)

# SHELLOPTS can import allexport into a child Bash process. That state can turn
# a later shell assignment into an exported startup-file selector, which this
# bounded parser cannot track.
_SHELL_STARTUP_STATE_SELECTORS = frozenset({"SHELLOPTS"})

# zsh reads system and user .zshenv files for every invocation, with HOME as
# the fallback directory when ZDOTDIR is unset. That startup path cannot be
# proven from command text, so zsh wrappers always fail closed.
_UNMODELED_STARTUP_SHELLS = frozenset({"zsh"})

# These builtins can stage a selector for a later shell wrapper. The bounded
# parser recognizes only direct selector declarations; dynamically constructed
# declaration names are unknown rather than interpreted.
_SHELL_ASSIGNMENT_BUILTINS = frozenset(
    {"declare", "export", "local", "readonly", "typeset"}
)

# ``let`` always evaluates arithmetic. The declaration builtins below can do
# the same after an integer attribute is requested, including recursive
# evaluation through a previously assigned variable value.
_SHELL_ARITHMETIC_BUILTINS = frozenset({"let"})
_SHELL_INTEGER_DECLARATION_BUILTINS = frozenset(
    {"declare", "local", "readonly", "typeset"}
)

# These builtins interpret variable-name operands or mutate shell state using
# semantics that can reach indexed-array arithmetic or startup selectors.
_SHELL_UNMODELED_VARIABLE_BUILTINS = frozenset({"read", "unset"})

# ``wait -p`` assigns through a variable name. Indexed-array targets evaluate
# arithmetic recursively, while allexport can turn a scalar target into a later
# shell startup selector. The parser therefore does not model any ``wait -p``.

# ``trap -p`` and ``trap -l`` only inspect handler state. Every other trap form
# can mutate later command execution and stays outside this bounded parser.
_TRAP_READ_ONLY_OPTIONS = frozenset({"l", "p"})

# Strictness ordering. When a compound command mixes capabilities
# (e.g. ``git status && git push --force``), the strictest one wins —
# this mirrors fail-closed semantics for the wrapper.
_STRICTNESS = {"shell": 0, "merge.pr": 1, "push.force": 2}

# Cap recursion for shell wrappers so a pathological nesting cannot
# drive this helper into unbounded work.
_MAX_RECURSION = 4

# Bounded subset of Git's global options that precede the subcommand. Unknown
# options produce ``unknown`` rather than risking a force-push bypass.
_GIT_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {
        "-C",
        "--git-dir",
        "--namespace",
        "--super-prefix",
        "--work-tree",
    }
)
# ``-c`` and ``--config-env`` can define aliases that replace the apparent
# subcommand (for example, aliasing ``p`` to ``push``). They deliberately stay
# outside the allowlist so the generic unknown-option branch fails closed.
_GIT_GLOBAL_OPTIONS_WITHOUT_VALUE = frozenset(
    {
        "-p",
        "-P",
        "--bare",
        "--glob-pathspecs",
        "--icase-pathspecs",
        "--literal-pathspecs",
        "--no-pager",
        "--no-replace-objects",
        "--noglob-pathspecs",
        "--paginate",
    }
)
_GIT_GLOBAL_OPTIONS_WITH_ATTACHED_VALUE = tuple(
    f"{option}="
    for option in _GIT_GLOBAL_OPTIONS_WITH_VALUE
    if option.startswith("--")
)
_GIT_SUBCOMMAND_UNKNOWN = -1

# Git aliases and external helpers can make a subcommand's effects differ from
# its visible spelling. Only these builtin subcommands are classified as
# ordinary shell or push.force; every other spelling fails closed.
_GIT_BUILTIN_SUBCOMMANDS = frozenset(
    {"add", "commit", "diff", "fetch", "push", "send-pack", "status"}
)

# These forms can synthesize argv after the helper has tokenized the command.
# Keep the list deliberately small and reject rather than emulate their input
# parsing or generated argument handling.
_DYNAMIC_ARGV_EXECUTORS = frozenset({"xargs"})
_FIND_DYNAMIC_EXECUTION_PREDICATES = frozenset(
    {"-exec", "-execdir", "-ok", "-okdir"}
)

# These command-position forms can dispatch another executable or introduce
# compound shell grammar that this bounded parser does not model. Blocking the
# entire statement is safer than treating their later argv as inert data.
_UNMODELED_EXECUTION_PREFIXES = frozenset(
    {
        "!",
        ".",
        "(",
        ")",
        "{",
        "}",
        "case",
        "chroot",
        "coproc",
        "do",
        "doas",
        "done",
        "elif",
        "else",
        "esac",
        "exec",
        "fi",
        "for",
        "function",
        "if",
        "nice",
        "nohup",
        "runuser",
        "select",
        "setsid",
        "source",
        "stdbuf",
        "su",
        "then",
        "time",
        "timeout",
        "until",
        "while",
    }
)

# Bash short options that do not consume another argv element. Keeping this
# bounded lets us recognize ``bash -lc <command>`` without guessing about
# unknown option syntax.
_SHELL_SHORT_OPTIONS_WITHOUT_ARGUMENT = frozenset(
    "abcefhiklmnptuvxBCEHPT"
)
_SHELL_OPTIONS_WITH_VALUE = frozenset({"-o", "-O"})
_SHELL_STARTUP_OPTIONS_WITH_VALUE = frozenset({"--init-file", "--rcfile"})
# Interactive/login modes read startup files; allexport can export a selector
# assigned later in the inspected command body.
_SHELL_STARTUP_SHORT_OPTIONS = frozenset({"a", "i", "l"})


class _CommandParseError(ValueError):
    """The bounded shell parser cannot determine a safe classification."""


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
        One of ``"push.force"``, ``"merge.pr"``, ``"shell"``, or
        ``"unknown"``. Hooks must block ``"unknown"`` rather than pass it
        to policy evaluation.
    """
    if _depth > _MAX_RECURSION:
        return "unknown"

    try:
        stripped = _strip_heredocs(command)
        active_source = _normalize_active_line_continuations(stripped)
        if _contains_active_arithmetic_expansion(active_source):
            return "unknown"
        if _contains_active_unquoted_brace_expansion(active_source):
            return "unknown"
        if _contains_active_unquoted_pathname_expansion(active_source):
            return "unknown"
        tokens = _tokenize(_separate_unquoted_newlines(active_source))
    except ValueError:
        # An unbalanced quote, unsupported/ambiguous heredoc header, or
        # unterminated heredoc makes the bounded parser uncertain. A hook
        # must block this dedicated result; treating it as policy-controlled
        # ``shell`` could permit malformed input under auto_allow.
        return "unknown"

    strictest = "shell"
    current: list[str] = []
    # Trailing sentinel flushes the final statement through the loop.
    for token in (*tokens, ";"):
        if token in _SEPARATORS:
            if current:
                classified = _classify_statement(current, _depth)
                if classified == "unknown":
                    return "unknown"
                strictest = _stricter(strictest, classified)
                current = []
            continue
        current.append(token)
    return strictest


def _tokenize(command: str) -> list[str]:
    """Tokenize ``command`` preserving shell operators as distinct tokens."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    return list(lexer)


def _separate_unquoted_newlines(command: str) -> str:
    """Mark executable line boundaries without changing quoted newlines."""
    out: list[str] = []
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        out.append(char)
        if quote == "'":
            if char == "'":
                quote = None
            index += 1
            continue
        if char == "\\" and index + 1 < len(command):
            out.append(command[index + 1])
            index += 2
            continue
        if quote == '"':
            if char == '"':
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char == "\n":
            # Put the marker after the newline so it is outside a preceding
            # shell comment. Existing operator tokens still flush harmlessly.
            out.append(";")
        index += 1
    return "".join(out)


def _normalize_active_line_continuations(command: str) -> str:
    """Remove Bash line continuations for brace and comment analysis.

    Bash removes an exact backslash-LF pair outside single quotes before
    recognizing comments or brace expansion. A backslash-CRLF pair is not a
    continuation on Linux, so its LF still ends the physical line. Double
    quotes retain the exact backslash-LF behavior. Escaped backslashes and
    quote characters remain verbatim, and quote characters inside comments
    do not affect later logical lines.

    Heredoc bodies have already been stripped before this helper runs. The
    normalized view is used by the expansion scanners and shlex tokenization
    so all three share Bash's logical-line comment boundaries.
    """
    out: list[str] = []
    quote: str | None = None
    in_comment = False
    previous_escaped = False
    index = 0
    while index < len(command):
        char = command[index]

        if quote == "'":
            out.append(char)
            previous_escaped = False
            if char == quote:
                quote = None
            index += 1
            continue

        if char == "\\":
            if command.startswith("\\\n", index):
                index += 2
                continue
            out.append(char)
            if index + 1 < len(command):
                out.append(command[index + 1])
                previous_escaped = True
                index += 2
            else:
                previous_escaped = False
                index += 1
            continue

        if in_comment:
            out.append(char)
            if char == "\n":
                in_comment = False
                previous_escaped = False
            index += 1
            continue

        previous = out[-1] if out else None
        out.append(char)
        if quote == '"':
            if char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == "#" and (
            previous is None
            or (
                (
                    previous in _BASH_LEXICAL_WHITESPACE
                    or previous in ";|&()"
                )
                and not previous_escaped
            )
        ):
            in_comment = True
        previous_escaped = False
        index += 1
    return "".join(out)


def _contains_active_arithmetic_expansion(command: str) -> bool:
    """Whether active arithmetic reaches the bounded parser before shlex.

    Arithmetic expansion can resolve variables whose values trigger further
    shell evaluation. Rather than distinguish literal-looking arithmetic from
    dynamic forms, reject every active arithmetic expansion and arithmetic
    command form. Quotes, escapes, and comments remain literal according to
    the shell's lexical rules.
    """
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\":
                if (
                    index + 1 < len(command)
                    and command[index + 1] in {"$", "\x60", '"', "\\", "\n"}
                ):
                    index += 2
                else:
                    index += 1
                continue
            if (
                command.startswith("$((", index)
                or command.startswith("$[", index)
            ):
                return True
            if command.startswith("${", index):
                end = _skip_parameter_expansion(command, index)
                if _parameter_expansion_has_arithmetic_context(
                    command[index + 2 : max(index + 2, end - 1)]
                ):
                    return True
                index = end
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "#" and _starts_comment(command, index):
            newline = command.find("\n", index)
            if newline < 0:
                return False
            index = newline + 1
            continue
        if command.startswith("$((", index) or command.startswith("$[", index):
            return True
        if command.startswith("${", index):
            end = _skip_parameter_expansion(command, index)
            if _parameter_expansion_has_arithmetic_context(
                command[index + 2 : max(index + 2, end - 1)]
            ):
                return True
            index = end
            continue
        if command.startswith("((", index) and (
            index == 0 or command[index - 1] != "$"
        ):
            return True
        index += 1
    return False


def _parameter_expansion_has_arithmetic_context(body: str) -> bool:
    """Whether a parameter expansion contains a bounded arithmetic context."""
    if body.startswith("!"):
        # Indirect expansion can resolve a caller-controlled value to an
        # indexed-array reference whose subscript is evaluated arithmetically.
        return True

    # Inspect nested parameter expansions before identifying the outer
    # parameter's operator.
    nested = 0
    while True:
        nested = body.find("${", nested)
        if nested < 0:
            break
        end = _skip_parameter_expansion(body, nested)
        if _parameter_expansion_has_arithmetic_context(
            body[nested + 2 : max(nested + 2, end - 1)]
        ):
            return True
        nested = max(end, nested + 2)

    index = 0
    if index < len(body) and body[index] == "#":
        index += 1
    if index >= len(body):
        return False
    if body[index] in "@*#?$!-":
        index += 1
    elif body[index].isdigit():
        while index < len(body) and body[index].isdigit():
            index += 1
    elif body[index].isalpha() or body[index] == "_":
        index += 1
        while index < len(body) and (body[index].isalnum() or body[index] == "_"):
            index += 1
    else:
        return False

    # An indexed-array subscript immediately following the parameter name is
    # arithmetic. Brackets in a default value or replacement pattern remain
    # ordinary data and are not rejected by this bounded check.
    if index < len(body) and body[index] == "[":
        return True

    remainder = body[index:]
    return bool(remainder.startswith(":")) and (
        len(remainder) == 1 or remainder[1] not in "-=?+"
    )


def _contains_active_unquoted_brace_expansion(command: str) -> bool:
    """Whether ``command`` contains a Bash brace expansion before shlex.

    Brace expansion happens before the token-level handling this helper can
    model. It requires an unquoted brace pair with an unquoted comma or range
    marker. Sequence expressions are limited to Bash's numeric or
    single-character endpoint grammar, while quoted and escaped text remains
    literal.
    """
    quote: str | None = None
    index = 0
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "#" and _starts_comment(command, index):
            newline = command.find("\n", index)
            if newline < 0:
                return False
            index = newline + 1
            continue
        if command.startswith("${", index):
            index = _skip_parameter_expansion(command, index)
            continue
        if char == "{" and _is_active_brace_expansion(command, index):
            return True
        index += 1
    return False


def _contains_active_unquoted_pathname_expansion(command: str) -> bool:
    """Whether command contains unquoted pathname-expansion syntax.

    This bounded scan rejects unquoted *, ?, complete bracket expressions, and
    Bash extended-glob operators before shlex erases the distinction between a
    literal token and an argv pattern. It deliberately skips quotes, escapes,
    comments, heredoc bodies, parameter expansion, and arithmetic expressions.
    """
    quote: str | None = None
    index = 0
    word_start: int | None = None
    command_seen = False
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            if word_start is None:
                word_start = index
            index += 2
            continue
        if char in {"'", '"'}:
            if word_start is None:
                word_start = index
            quote = char
            index += 1
            continue
        if char == "#" and _starts_comment(command, index):
            newline = command.find("\n", index)
            if newline < 0:
                return False
            word_start = None
            command_seen = False
            index = newline + 1
            continue
        if char in _BASH_LEXICAL_WHITESPACE:
            if word_start is not None:
                command_seen = command_seen or not _is_env_assignment(
                    command[word_start:index]
                )
                word_start = None
            if char == "\n":
                command_seen = False
            index += 1
            continue
        if char in ";|&()":
            word_start = None
            command_seen = False
            index += 1
            continue
        if word_start is None:
            word_start = index
        if command.startswith("${", index):
            index = _skip_parameter_expansion(command, index)
            continue
        if command.startswith("$((", index):
            index = _skip_arithmetic_expression(command, index + 3)
            continue
        if command.startswith("$[", index):
            index = _skip_legacy_arithmetic_expression(command, index + 2)
            continue
        if command.startswith("((", index):
            index = _skip_arithmetic_expression(command, index + 2)
            continue
        if char in {"*", "?"}:
            if command_seen or not _is_env_assignment(command[word_start:index]):
                return True
            index += 1
            continue
        if char in {"@", "+", "!"} and command.startswith("(", index + 1):
            # With extglob enabled, these operators can synthesize argv just
            # like ``*(`` and ``?(``, which are covered by the branch above.
            if command_seen or not _is_env_assignment(command[word_start:index]):
                return True
            index += 1
            continue
        if char == "[" and _pathname_bracket_expression_end(command, index):
            if command_seen or not _is_env_assignment(command[word_start:index]):
                return True
        index += 1
    return False


def _skip_arithmetic_expression(command: str, index: int) -> int:
    """Return the offset after a bounded arithmetic expression, if present."""
    group_depth = 0
    quote: str | None = None
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "(":
            group_depth += 1
            index += 1
            continue
        if char == ")":
            if group_depth:
                group_depth -= 1
                index += 1
                continue
            if command.startswith("))", index):
                return index + 2
            return len(command)
        index += 1
    return len(command)


def _skip_legacy_arithmetic_expression(command: str, index: int) -> int:
    """Return the offset after a bounded legacy ``$[...]`` expression."""
    depth = 1
    quote: str | None = None
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(command)


def _pathname_bracket_expression_end(command: str, opening: int) -> int | None:
    """Return the end of one complete, unquoted pathname bracket expression."""
    index = opening + 1
    if index >= len(command):
        return None
    if command[index] in {"!", "^"}:
        index += 1

    has_member = False
    if index < len(command) and command[index] == "]":
        has_member = True
        index += 1

    while index < len(command):
        char = command[index]
        if char == "\\":
            if index + 1 >= len(command):
                return None
            has_member = True
            index += 2
            continue
        if char in _BASH_LEXICAL_WHITESPACE or char in ";|&()<>":
            return None
        if char == "[" and index + 1 < len(command) and command[index + 1] in ".=:":
            terminator = command[index + 1] + "]"
            end = command.find(terminator, index + 2)
            if end < 0 or end == index + 2:
                return None
            has_member = True
            index = end + 2
            continue
        if char == "]":
            return index + 1 if has_member else None
        has_member = True
        index += 1
    return None


def _skip_parameter_expansion(command: str, start: int) -> int:
    """Return the offset after a parameter expansion, or end of input.

    Parameter expansion has its own brace grammar and is not Bash brace
    expansion. Nested braces are skipped as a unit so commas and ranges in
    forms such as ``${value:-{left,right}}`` cannot be mistaken for argv
    expansion.
    """
    depth = 1
    quote: str | None = None
    index = start + 2
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return len(command)


def _is_active_brace_expansion(command: str, opening: int) -> bool:
    """Whether an unquoted opening brace starts a bounded expansion form."""
    # A grouping brace is a shell word of its own, which requires whitespace
    # after the opening delimiter. Brace expansion stays within one word.
    if (
        opening + 1 >= len(command)
        or command[opening + 1] in _BASH_LEXICAL_WHITESPACE
    ):
        return False

    depth = 1
    quote: str | None = None
    saw_comma = False
    index = opening + 1
    while index < len(command):
        char = command[index]
        if quote == "'":
            if char == quote:
                quote = None
            index += 1
            continue
        if quote == '"':
            if char == "\\" and index + 1 < len(command):
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char == "\\":
            index += 2
            continue
        if char in {"'", '"'}:
            quote = char
            index += 1
            continue
        if char in _BASH_LEXICAL_WHITESPACE:
            return False
        if command.startswith("${", index):
            index = _skip_parameter_expansion(command, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                body = command[opening + 1 : index]
                return saw_comma or _is_valid_brace_sequence(body)
        elif char == ",":
            saw_comma = True
        index += 1
    return False


def _is_valid_brace_sequence(body: str) -> bool:
    """Return whether ``body`` is a bounded Bash sequence expression."""
    if "\\" in body:
        return False

    parts = body.split("..")
    if len(parts) not in {2, 3}:
        return False

    start, end = parts[:2]
    start_is_integer = _is_brace_integer(start)
    end_is_integer = _is_brace_integer(end)
    if start_is_integer or end_is_integer:
        if not (start_is_integer and end_is_integer):
            return False
    elif len(start) != 1 or len(end) != 1:
        return False

    return len(parts) == 2 or _is_brace_integer(parts[2])


def _is_brace_integer(value: str) -> bool:
    """Return whether ``value`` is a Bash brace-sequence integer."""
    if value.startswith(("+", "-")):
        value = value[1:]
    return bool(value) and value.isascii() and value.isdigit()


def _strip_heredocs(command: str) -> str:
    """Remove heredoc bodies from normal command-statement scanning.

    Handles the common forms ``<<WORD``, ``<<-WORD``, ``<<'WORD'``,
    ``<<"WORD"`` only when the operator is outside shell quotes. The body
    — from the newline after the header to the closing delimiter on its own
    line — is elided. Expanding bodies are checked only for active command
    substitution. Ambiguous, unbalanced, or unterminated input raises so
    ``map_command()`` can return the dedicated ``unknown`` classification.
    """
    out: list[str] = []
    retained_start = 0
    pos = 0
    quote: str | None = None
    arithmetic_depth = 0

    while pos < len(command):
        char = command[pos]
        if quote is not None:
            if quote == "'":
                if char == quote:
                    quote = None
                pos += 1
                continue
            if char == "\\":
                if pos + 1 >= len(command):
                    raise _CommandParseError("unterminated escape")
                pos += 2
                continue
            if char == quote:
                quote = None
                pos += 1
                continue
            if _starts_command_substitution(command, pos):
                raise _CommandParseError("active command substitution")
            pos += 1
            continue

        if char == "\\":
            if pos + 1 >= len(command):
                raise _CommandParseError("unterminated escape")
            pos += 2
            continue
        if char in {"'", '"'}:
            quote = char
            pos += 1
            continue
        if char == "#" and _starts_comment(command, pos):
            newline = command.find("\n", pos)
            if newline < 0:
                break
            pos = newline + 1
            continue
        if _starts_command_substitution(command, pos):
            raise _CommandParseError("active command substitution")
        if _starts_process_substitution(command, pos):
            raise _CommandParseError("active process substitution")
        if command.startswith("((", pos):
            arithmetic_depth += 1
            pos += 2
            continue
        if arithmetic_depth and command.startswith("))", pos):
            arithmetic_depth -= 1
            pos += 2
            continue
        if arithmetic_depth and command.startswith("<<", pos):
            # ``<<`` is an arithmetic left shift inside ``(( ... ))``, not
            # a heredoc operator.
            pos += 2
            continue
        if command.startswith("<<<", pos):
            # A here-string is not a heredoc operator. Leave it intact.
            pos += 3
            continue
        if not command.startswith("<<", pos):
            pos += 1
            continue

        header = _parse_heredoc_header(command, pos)
        if header is None:
            pos += 2
            continue
        header_end, delimiter, strip_tabs, expand_body = header
        newline = command.find("\n", header_end)
        if newline < 0:
            raise _CommandParseError("unterminated heredoc header")
        if _contains_unquoted_heredoc_operator(command, header_end, newline):
            raise _CommandParseError("multiple heredocs are unsupported")

        body_start = newline + 1
        body_end = _find_heredoc_end(
            command,
            body_start,
            delimiter,
            strip_tabs=strip_tabs,
            expand_body=expand_body,
        )
        # Keep the header and line ending so subsequent commands cannot be
        # merged into the header's final token.
        out.append(command[retained_start:body_start])
        retained_start = body_end
        pos = body_end

    if quote is not None:
        raise _CommandParseError("unterminated quote")
    if arithmetic_depth:
        raise _CommandParseError("unterminated arithmetic expression")
    out.append(command[retained_start:])
    return "".join(out)


def _parse_heredoc_header(
    command: str,
    operator_start: int,
) -> tuple[int, str, bool, bool] | None:
    """Parse one unquoted simple heredoc header after the operator.

    None means the text is a here-string, not a heredoc. Other syntax the
    bounded parser cannot represent raises instead of guessing whether
    following lines are command text or heredoc data.
    """
    pos = operator_start + 2
    if pos < len(command) and command[pos] == "<":
        return None

    strip_tabs = False
    if pos < len(command) and command[pos] == "-":
        strip_tabs = True
        pos += 1
    while pos < len(command) and command[pos] in " \t":
        pos += 1
    if pos >= len(command) or command[pos] in "\r\n":
        raise _CommandParseError("missing heredoc delimiter")

    expand_body = command[pos] not in {"'", '"'}
    if not expand_body:
        quote = command[pos]
        pos += 1
        delimiter_start = pos
        while pos < len(command) and command[pos] != quote:
            if command[pos] == "\\" and quote == '"':
                if pos + 1 >= len(command):
                    raise _CommandParseError("unterminated heredoc delimiter")
                pos += 2
                continue
            pos += 1
        if pos >= len(command):
            raise _CommandParseError("unterminated heredoc delimiter")
        delimiter = command[delimiter_start:pos]
        pos += 1
    else:
        delimiter_start = pos
        if not (command[pos].isalpha() or command[pos] == "_"):
            raise _CommandParseError("unsupported heredoc delimiter")
        pos += 1
        while pos < len(command) and (
            command[pos].isalnum() or command[pos] == "_"
        ):
            pos += 1
        delimiter = command[delimiter_start:pos]

    if not delimiter:
        raise _CommandParseError("empty heredoc delimiter")
    if pos < len(command) and not _is_heredoc_boundary(command[pos]):
        raise _CommandParseError("ambiguous heredoc delimiter")
    return pos, delimiter, strip_tabs, expand_body


def _find_heredoc_end(
    command: str,
    body_start: int,
    delimiter: str,
    *,
    strip_tabs: bool,
    expand_body: bool,
) -> int:
    """Return the offset after a matching heredoc delimiter line."""
    line_start = body_start
    while True:
        line, after_line, has_newline = _read_heredoc_logical_line(
            command,
            line_start,
            fold_continuations=expand_body,
        )
        candidate = line.lstrip("\t") if strip_tabs else line
        if candidate == delimiter:
            if expand_body and _contains_active_heredoc_expansion(
                command[body_start:line_start]
            ):
                raise _CommandParseError(
                    "active expansion in heredoc"
                )
            return after_line
        if not has_newline:
            raise _CommandParseError("unterminated heredoc")
        line_start = after_line


def _read_heredoc_logical_line(
    command: str,
    line_start: int,
    *,
    fold_continuations: bool,
) -> tuple[str, int, bool]:
    """Read one heredoc logical line for delimiter recognition."""
    parts: list[str] = []
    current = line_start
    while True:
        newline = command.find("\n", current)
        has_newline = newline >= 0
        if has_newline:
            line = command[current:newline]
            after_line = newline + 1
        else:
            line = command[current:]
            after_line = len(command)
        comparable = line.removesuffix("\r")
        if (
            fold_continuations
            and has_newline
            and not line.endswith("\r")
            and _has_active_trailing_backslash(comparable)
        ):
            parts.append(comparable[:-1])
            current = after_line
            continue
        parts.append(comparable)
        return "".join(parts), after_line, has_newline


def _has_active_trailing_backslash(line: str) -> bool:
    """Whether a physical heredoc line ends in an unescaped backslash."""
    count = 0
    for char in reversed(line):
        if char != "\\":
            break
        count += 1
    return count % 2 == 1


def _contains_unquoted_heredoc_operator(
    command: str,
    start: int,
    end: int,
) -> bool:
    """Whether one header line contains another unquoted heredoc operator."""
    quote: str | None = None
    pos = start
    while pos < end:
        char = command[pos]
        if quote is not None:
            if quote == "'":
                if char == quote:
                    quote = None
                pos += 1
                continue
            if char == "\\":
                pos += 2
                continue
            if char == quote:
                quote = None
                pos += 1
                continue
            if _starts_command_substitution(command, pos):
                raise _CommandParseError("active command substitution")
            pos += 1
            continue
        if char == "\\":
            pos += 2
            continue
        if char in {"'", '"'}:
            quote = char
            pos += 1
            continue
        if char == "#" and _starts_comment(command, pos):
            return False
        if _starts_command_substitution(command, pos):
            raise _CommandParseError("active command substitution")
        if _starts_process_substitution(command, pos):
            raise _CommandParseError("active process substitution")
        if command.startswith("<<", pos) and not command.startswith("<<<", pos):
            return True
        pos += 1
    if quote is not None:
        raise _CommandParseError("unterminated quote in heredoc header")
    return False


def _starts_command_substitution(command: str, index: int) -> bool:
    """Return whether an active backtick or ``$()`` starts at ``index``.

    Arithmetic expansion starts with ``$((`` and is not command
    substitution. Shell line continuations between ``$`` and ``(`` are
    ignored just as Bash ignores them. The surrounding scanners handle shell
    quote and other escape state before calling this helper.
    """
    if command[index] == "\x60":
        return True
    if command[index] != "$":
        return False

    open_paren = _skip_line_continuations(command, index + 1)
    if open_paren >= len(command) or command[open_paren] != "(":
        return False
    second_char = _skip_line_continuations(command, open_paren + 1)
    return second_char >= len(command) or command[second_char] != "("


def _starts_process_substitution(command: str, index: int) -> bool:
    """Return whether an active process substitution starts at ``index``.

    Process substitutions execute their bodies, but their nested shell syntax
    is outside this helper's bounded model. Callers fail closed on the
    resulting ``unknown`` classification.
    """
    return command.startswith("<(", index) or command.startswith(">(", index)


def _skip_line_continuations(command: str, index: int) -> int:
    """Return the first index after consecutive Bash line continuations."""
    while True:
        if command.startswith("\\\r\n", index):
            index += 3
        elif command.startswith("\\\n", index):
            index += 2
        else:
            return index


def _contains_active_heredoc_expansion(line: str) -> bool:
    """Return whether an expanding heredoc line runs active expansion.

    Arithmetic expansion is rejected without interpreting its contents.

    Quote characters are literal inside a heredoc body. Backslash can quote
    ``\\``, ``$``, and backtick when the delimiter itself was unquoted.
    """
    line = _normalize_expanding_heredoc_continuations(line)
    pos = 0
    while pos < len(line):
        if (
            line[pos] == "\\"
            and pos + 1 < len(line)
            and line[pos + 1] in {"\\", "$", "\x60"}
        ):
            pos += 2
            continue
        if _starts_command_substitution(line, pos):
            return True
        if line.startswith("$((", pos) or line.startswith("$[", pos):
            return True
        pos += 1
    return False


def _normalize_expanding_heredoc_continuations(body: str) -> str:
    """Remove active backslash-LF pairs using expanding-heredoc rules."""
    out: list[str] = []
    index = 0
    while index < len(body):
        if body.startswith("\\\n", index):
            index += 2
            continue
        if body[index] == "\\" and index + 1 < len(body):
            out.append(body[index : index + 2])
            index += 2
            continue
        out.append(body[index])
        index += 1
    return "".join(out)


def _starts_comment(command: str, index: int) -> bool:
    """Return whether an unquoted # starts a shell comment here."""
    if index == 0:
        return True

    boundary = index - 1
    while (
        boundary >= 1
        and command[boundary] == "\n"
        and _is_backslash_escaped(command, boundary)
    ):
        # Bash removes an active backslash-LF pair before recognizing
        # comments, so inspect the preceding logical-line character.
        boundary -= 2
    if boundary < 0:
        return True
    return (
        command[boundary] in _BASH_LEXICAL_WHITESPACE
        or command[boundary] in ";|&()"
    ) and not _is_backslash_escaped(command, boundary)


def _is_backslash_escaped(command: str, index: int) -> bool:
    """Return whether the character at ``index`` has an active backslash."""
    backslashes = 0
    index -= 1
    while index >= 0 and command[index] == "\\":
        backslashes += 1
        index -= 1
    return backslashes % 2 == 1


def _is_heredoc_boundary(char: str) -> bool:
    """True when a simple delimiter word ends before char."""
    return char.isspace() or char in ";|&()<>"


def _classify_statement(tokens: list[str], depth: int) -> str:
    """Classify a single statement (already split on operators)."""
    # Skip leading env assignments: ``FOO=bar git push --force``.
    i = 0
    while i < len(tokens):
        if _is_shell_startup_selector_assignment(tokens[i]):
            return "unknown"
        if not _is_env_assignment(tokens[i]):
            break
        if _is_git_config_assignment(tokens[i]):
            # Git reads GIT_CONFIG* assignments before it resolves push
            # defaults and aliases. Their effects are outside this static argv
            # model and may hide a force refspec from the visible command.
            return "unknown"
        i += 1
    tokens = tokens[i:]
    if not tokens:
        return "shell"

    # Unwrap the one common command prefix this example models. Unknown sudo
    # option forms fail closed because they can consume following arguments.
    while os.path.basename(tokens[0]) == "sudo":
        sudo_args = tokens[1:]
        if sudo_args and sudo_args[0] == "--":
            sudo_args = sudo_args[1:]
        elif not sudo_args or sudo_args[0].startswith("-"):
            return "unknown"
        tokens = sudo_args
        if not tokens:
            return "unknown"
        if _is_env_assignment(tokens[0]):
            # sudo accepts VAR=value before its command. Re-tokenizing that
            # mini-language is outside this bounded model.
            return "unknown"

    if _starts_with_redirection(tokens):
        return "unknown"

    # Recurse into shell wrappers and eval. These patterns embed a
    # command in a string argument, so the old substring matcher
    # relied on them being visible in the raw string. We recover the
    # equivalent coverage by tokenizing and recursing.
    head_basename = os.path.basename(tokens[0])
    if "$" in tokens[0]:
        # A parameter-expanded command name can resolve to an interpreter or
        # Git executable after classification.
        return "unknown"
    if (
        head_basename in _SHELL_ASSIGNMENT_BUILTINS
        and _has_shell_startup_selector_declaration(tokens[1:])
    ):
        return "unknown"
    if head_basename == "set" and _changes_allexport_state(tokens[1:]):
        return "unknown"
    if head_basename == "wait" and _wait_has_unmodeled_variable_target(
        tokens[1:]
    ):
        return "unknown"
    if head_basename == "trap":
        return "shell" if _is_literal_trap_query(tokens[1:]) else "unknown"
    if head_basename in _SHELL_ARITHMETIC_BUILTINS:
        return "unknown"
    if (
        head_basename in _SHELL_INTEGER_DECLARATION_BUILTINS
        and (
            _has_evaluating_declaration_option(tokens[1:])
            or _has_parameter_expanded_option(tokens[1:], leading_only=True)
        )
    ):
        return "unknown"
    if (
        head_basename in _SHELL_ASSIGNMENT_BUILTINS
        and _has_arithmetic_variable_target(tokens[1:])
    ):
        return "unknown"
    if head_basename in _SHELL_UNMODELED_VARIABLE_BUILTINS:
        return "unknown"
    if head_basename == "printf" and (
        _printf_uses_variable_target(tokens[1:])
        or _has_parameter_expanded_option(tokens[1:], leading_only=True)
    ):
        return "unknown"
    if head_basename in {"test", "["} and (
        any(option in {"-R", "-v"} for option in tokens[1:])
        or _has_parameter_expanded_option(tokens[1:])
    ):
        return "unknown"
    if head_basename == "[[":
        return "unknown"
    if head_basename == "builtin":
        # ``printf`` only formats its remaining arguments; it cannot dispatch
        # another command. Other builtin targets (notably ``eval``, ``source``,
        # and ``exec``) remain outside this bounded model and fail closed.
        if len(tokens) >= 2 and tokens[1] == "printf":
            if _printf_uses_variable_target(
                tokens[2:]
            ) or _has_parameter_expanded_option(tokens[2:], leading_only=True):
                return "unknown"
            return "shell"
        return "unknown"
    if head_basename == "command":
        # ``command`` changes executable resolution and its bounded option
        # forms are not modeled. Failing closed prevents it from hiding a
        # nested shell wrapper from the force-push guardrail.
        return "unknown"
    if head_basename in _UNMODELED_STARTUP_SHELLS:
        return "unknown"
    if head_basename in _SHELL_WRAPPERS:
        return _classify_wrapper_c(tokens[1:], depth)
    if head_basename == "env":
        return _classify_env(tokens[1:], depth)
    if head_basename == "eval":
        # ``eval arg1 arg2 ...`` concatenates its args before executing.
        embedded = " ".join(tokens[1:])
        if embedded:
            if "$" in embedded:
                return "unknown"
            nested = map_command(embedded, _depth=depth + 1)
            if nested != "shell":
                return nested

    # Classify only the executable position. Treating arbitrary argument text
    # as a nested executor creates false positives such as ``echo xargs``.
    basename = os.path.basename(tokens[0])
    if basename in _UNMODELED_EXECUTION_PREFIXES:
        return "unknown"
    if basename in _DYNAMIC_ARGV_EXECUTORS:
        return "unknown"
    if basename == "find":
        if any(
            arg in _FIND_DYNAMIC_EXECUTION_PREDICATES for arg in tokens[1:]
        ) or any("$" in arg for arg in tokens[1:]):
            return "unknown"
    if basename == "git":
        subcommand_index = _git_subcommand_index(tokens, 0)
        if (
            subcommand_index is None
            or subcommand_index == _GIT_SUBCOMMAND_UNKNOWN
        ):
            return "unknown"
        subcommand = tokens[subcommand_index]
        if subcommand not in _GIT_BUILTIN_SUBCOMMANDS:
            return "unknown"
        if subcommand in {"push", "send-pack"}:
            push_args = tokens[subcommand_index + 1 :]
            if _has_force_flag(push_args):
                return "push.force"
            if any("$" in arg for arg in push_args):
                return "unknown"
        return "shell"
    if basename in {"git-push", "git-send-pack"}:
        push_args = tokens[1:]
        if _has_force_flag(push_args):
            return "push.force"
        if any("$" in arg for arg in push_args):
            return "unknown"
        return "shell"
    elif basename.startswith("git-"):
        # Any other direct helper may be an external program or alias-like
        # dispatcher with effects that differ from its visible spelling.
        return "unknown"
    if basename == "gh":
        if tokens[1:3] == ["pr", "merge"]:
            return "merge.pr"
        return "shell"
    if _contains_later_sensitive_command_token(tokens):
        # Preserve the old scan-anywhere guard conservatively without treating
        # ordinary xargs/find argument text as an executor. A literal Git, gh,
        # or shell executable behind an unmodeled prefix must not auto-allow.
        return "unknown"
    return "shell"


def _starts_with_redirection(tokens: list[str]) -> bool:
    """Whether a statement begins with an unmodeled shell redirection."""
    first = tokens[0]
    if first.startswith(("<", ">", "&>")):
        return True
    if (
        first.startswith("{")
        and first.endswith("}")
        and len(tokens) > 1
        and tokens[1].startswith(("<", ">"))
    ):
        return True
    return (
        first.isdecimal()
        and len(tokens) > 1
        and tokens[1].startswith(("<", ">"))
    )


def _contains_later_sensitive_command_token(tokens: list[str]) -> bool:
    """Whether argv visibly contains a sensitive executable after its head."""
    for token in tokens[1:]:
        basename = os.path.basename(token)
        if (
            basename in {"git", "gh", "git-push", "git-send-pack"}
            or basename.startswith("git-")
            or basename in _SHELL_WRAPPERS
        ):
            return True
    return False


def _classify_wrapper_c(args: list[str], depth: int) -> str:
    """Handle ``bash -c <cmd>`` / ``bash -lc <cmd>`` embedded commands.

    Recognizes bounded short-option clusters that contain ``c`` and skips
    known option arguments before recursively classifying the command.
    Unsupported option forms return ``unknown`` rather than guessing.
    """
    j = 0
    while j < len(args):
        arg = args[j]
        if arg == "--":
            # A following ``-c`` is a script filename, not a shell option.
            return "unknown"
        if arg in _SHELL_STARTUP_OPTIONS_WITH_VALUE:
            return "unknown"
        if _shell_short_option_cluster_reads_startup_files(arg):
            return "unknown"
        if _is_shell_short_option_cluster_with_c(arg):
            if j + 1 >= len(args):
                return "unknown"
            # Positional parameters, redirections, and stdin after the body
            # can change the nested shell's behavior outside this model.
            if j + 2 != len(args):
                return "unknown"
            return map_command(args[j + 1], _depth=depth + 1)
        if arg in _SHELL_OPTIONS_WITH_VALUE:
            if j + 1 >= len(args):
                return "unknown"
            if arg == "-o" and args[j + 1] == "allexport":
                return "unknown"
            j += 2
            continue
        if _is_shell_short_option_cluster(arg):
            j += 1
            continue
        if arg.startswith(("-", "+")):
            return "unknown"
        # The first non-option is the script filename, so later ``-c`` text
        # cannot introduce a nested command string. Its contents are outside
        # this parser, so permitting it as generic shell would be fail-open.
        return "unknown"
    # A bare interpreter can read executable input from a pipe, heredoc, or
    # here-string. Only an explicit, inspected ``-c`` form is classifiable.
    return "unknown"


def _is_shell_short_option_cluster_with_c(arg: str) -> bool:
    """True for a bounded Bash short-option cluster containing ``c``."""
    return _is_shell_short_option_cluster(arg) and "c" in arg[1:]


def _shell_short_option_cluster_reads_startup_files(arg: str) -> bool:
    """Whether a short-option cluster enables unmodeled startup input."""
    return _is_shell_short_option_cluster(arg) and bool(
        _SHELL_STARTUP_SHORT_OPTIONS.intersection(arg[1:])
    )


def _is_shell_short_option_cluster(arg: str) -> bool:
    """True when ``arg`` contains only modeled no-argument shell flags."""
    return (
        len(arg) > 1
        and arg.startswith("-")
        and not arg.startswith("--")
        and all(flag in _SHELL_SHORT_OPTIONS_WITHOUT_ARGUMENT for flag in arg[1:])
    )


def _classify_env(args: list[str], depth: int) -> str:
    """Classify the command executed by a bounded ``env`` invocation."""
    command_start = _env_command_start(args)
    if command_start is None:
        return "unknown"
    if command_start == len(args):
        return "shell"
    return _classify_statement(args[command_start:], depth + 1)


def _env_command_start(args: list[str]) -> int | None:
    """Return the command offset after supported ``env`` options and vars."""
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return index + 1
        if _is_shell_startup_selector_assignment(arg):
            return None
        if _is_env_assignment(arg):
            if _is_git_config_assignment(arg):
                return None
            index += 1
            continue
        if arg in {"-0", "-i", "--ignore-environment", "--null"}:
            index += 1
            continue
        if arg in {"-C", "-u", "--chdir", "--unset"}:
            if index + 1 >= len(args):
                return None
            index += 2
            continue
        if arg.startswith(("--chdir=", "--unset=")):
            index += 1
            continue
        # ``-S`` needs a second shell-like parse; unknown flags may have
        # arguments, so neither is safe to skip speculatively.
        if arg.startswith("-"):
            return None
        return index
    return index


def _git_subcommand_index(tokens: list[str], git_index: int) -> int | None:
    """Return the Git subcommand offset or a fail-closed sentinel."""
    index = git_index + 1
    while index < len(tokens):
        arg = tokens[index]
        if arg in _GIT_GLOBAL_OPTIONS_WITH_VALUE:
            if index + 1 >= len(tokens):
                return _GIT_SUBCOMMAND_UNKNOWN
            index += 2
            continue
        if arg in _GIT_GLOBAL_OPTIONS_WITHOUT_VALUE:
            index += 1
            continue
        if arg.startswith(_GIT_GLOBAL_OPTIONS_WITH_ATTACHED_VALUE):
            index += 1
            continue
        if arg == "--" or arg.startswith("-"):
            return _GIT_SUBCOMMAND_UNKNOWN
        return index
    return None


def _has_force_flag(push_args: list[str]) -> bool:
    """True if any arg to ``git push`` is a force-style flag.

    Recognizes ``--force``, ``--force-with-lease`` (including the
    ``--force-with-lease=<refname>`` form), ``--mirror``, ``-f``, short-option
    clusters that contain ``f`` (e.g. ``-fu``), and ``+`` force refspecs.
    """
    index = 0
    while index < len(push_args):
        arg = push_args[index]
        if arg in {"-o", "--push-option"}:
            index += 2
            continue
        if arg.startswith(("-o", "--push-option=")):
            index += 1
            continue
        if arg in ("--force", "--mirror", "-f"):
            return True
        if arg.startswith("--force-with-lease"):
            return True
        if len(arg) > 2 and any(
            option.startswith(arg)
            for option in ("--force", "--force-with-lease", "--mirror")
        ):
            # Git accepts unique long-option abbreviations. Treat every
            # force-related prefix conservatively rather than reproducing
            # version-specific abbreviation resolution.
            return True
        if (
            arg.startswith("-")
            and not arg.startswith("--")
            and "f" in arg[1:]
        ):
            return True
        if len(arg) > 1 and arg.startswith("+"):
            return True
        index += 1
    return False


def _is_shell_startup_selector_assignment(token: str) -> bool:
    """Whether an assignment directly sets a modeled startup selector."""
    name = _shell_assignment_name(token)
    return name in (
        _SHELL_STARTUP_FILE_SELECTORS | _SHELL_STARTUP_STATE_SELECTORS
    )


def _has_evaluating_declaration_option(tokens: list[str]) -> bool:
    """Whether a declaration enables array, integer, or nameref evaluation."""
    for token in tokens:
        if token == "--":
            return False
        if token.startswith(("-", "+")) and not token.startswith(("--", "++")):
            if {"A", "a", "i", "n"}.intersection(token[1:]):
                return True
            continue
        return False
    return False


def _has_arithmetic_variable_target(tokens: list[str]) -> bool:
    """Whether a declaration names an indexed-array or dynamic target."""
    options = True
    for token in tokens:
        if options and token == "--":
            options = False
            continue
        if options and token.startswith(("-", "+")):
            continue
        options = False
        target = token.split("=", 1)[0]
        if any(marker in target for marker in ("[", "]", "$")):
            return True
    return False


def _wait_has_unmodeled_variable_target(tokens: list[str]) -> bool:
    """Whether ``wait`` assigns through the unmodeled ``-p`` option."""
    for token in tokens:
        if token == "--":
            return False
        if not token.startswith("-") or token == "-":
            continue
        if "p" in token[1:]:
            return True
    return False


def _is_literal_trap_query(tokens: list[str]) -> bool:
    """Whether ``trap`` only lists handlers using literal arguments."""
    if not tokens:
        return True
    option = tokens[0]
    return (
        len(option) > 1
        and option.startswith("-")
        and set(option[1:]).issubset(_TRAP_READ_ONLY_OPTIONS)
        and all("$" not in token for token in tokens[1:])
    )


def _printf_uses_variable_target(tokens: list[str]) -> bool:
    """Whether Bash printf uses its variable-name destination option."""
    for token in tokens:
        if token == "--":
            return False
        if token == "-v" or token.startswith("-v"):
            return True
        if not token.startswith("-"):
            return False
    return False


def _has_parameter_expanded_option(
    tokens: list[str],
    *,
    leading_only: bool = False,
) -> bool:
    """Whether an option token can change after parameter expansion."""
    for token in tokens:
        if token == "--":
            return False
        if "$" in token and token.startswith(("-", "$")):
            return True
        if leading_only and not token.startswith("-"):
            return False
    return False


def _changes_allexport_state(tokens: list[str]) -> bool:
    """Whether set changes allexport, which can export a staged selector."""
    for index, token in enumerate(tokens):
        if token in {"-o", "+o"}:
            if index + 1 < len(tokens) and tokens[index + 1] == "allexport":
                return True
            continue
        if (
            len(token) > 1
            and token.startswith(("-", "+"))
            and not token.startswith(("--", "++"))
            and "a" in token[1:]
        ):
            return True
    return False


def _has_shell_startup_selector_declaration(tokens: list[str]) -> bool:
    """Whether an assignment builtin directly or dynamically names a selector."""
    for token in tokens:
        if _is_shell_startup_selector_declaration(token):
            return True
        if "$" in token.split("=", 1)[0]:
            return True
    return False


def _is_shell_startup_selector_declaration(token: str) -> bool:
    """Whether a declaration token names a modeled startup selector."""
    return (
        _shell_assignment_name(token) or token
    ) in (_SHELL_STARTUP_FILE_SELECTORS | _SHELL_STARTUP_STATE_SELECTORS)


def _shell_assignment_name(token: str) -> str | None:
    """Return a shell assignment name, including the append-assignment form."""
    if "=" not in token:
        return None
    name = token.split("=", 1)[0]
    if name.endswith("+"):
        name = name[:-1]
    if not name or name[0].isdigit():
        return None
    if not all(char.isalnum() or char == "_" for char in name):
        return None
    return name


def _is_env_assignment(token: str) -> bool:
    """True if ``token`` looks like a leading ``FOO=bar`` env assignment."""
    if "=" not in token:
        return False
    head = token.split("=", 1)[0]
    if not head or head[0].isdigit():
        return False
    return all(c.isalnum() or c == "_" for c in head)


def _is_git_config_assignment(token: str) -> bool:
    """Whether a leading assignment can mutate Git's effective config."""
    name = token.split("=", 1)[0]
    return name == "GIT_CONFIG" or name.startswith("GIT_CONFIG_")


def _stricter(a: str, b: str) -> str:
    """Return whichever of ``a`` or ``b`` is the stricter capability."""
    if a == "unknown" or b == "unknown":
        return "unknown"
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
