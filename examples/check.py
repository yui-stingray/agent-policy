"""Where: examples/check.py
What: a minimal CLI wrapper around agent_policy.evaluate().
Why: show how a PreToolUse hook or shell wrapper turns a PolicyDecision
     into a JSON payload + a process exit code.

Prerequisite: agent-policy must be importable. Install it with
    pip install yui-agent-policy
or, from a source checkout of this repo, `pip install -e .` from the
repo root. This file deliberately does not manipulate sys.path — it is
meant to be copied verbatim into a downstream project that already has
yui-agent-policy installed (import name remains `agent_policy`).

Usage:
    python examples/check.py \
        --policy examples/policy.toml \
        --repo acme/app \
        --capability shell

    # With context
    python examples/check.py \
        --policy examples/policy.toml \
        --repo someone-else/their-repo \
        --capability write \
        --ownership-class external \
        --first-write

Output (stdout):
    {"mode": "...", "reason": "...", "matched_repo": "..."}
    matched_repo is serialized as JSON null when no repo entry matched.
    With --audit-event, stdout is a deterministic wrapper-owned event
    payload containing repo, capability, context, decision, and any optional
    session/path/command fields supplied by the caller.

Exit codes:
    0 — auto_allow        (let the tool run)
    2 — require_approval  (stop and ask a human)
    3 — deny              (refuse)
    1 — program error     (bad args, unreadable policy file, etc.)

Design notes:
- Exit code 1 is reserved for program errors so judgment (0/2/3) and
  failure (1) never collide. This matches the Unix convention of
  `0 = success / happy path` and lets wrappers branch cleanly.
- The three judgment exit codes do not follow any external standard;
  they are chosen so that `auto_allow` is 0 (safe to chain with `&&`)
  and `deny` is the largest value (easy to grep for in logs).
- Context is kept to two flags on purpose. Anything richer belongs in
  the wrapper layer, not in the example.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
from pathlib import Path, PureWindowsPath
from typing import Any

from agent_policy import (
    PolicyDecision,
    audit_event_to_json,
    build_audit_event,
    evaluate,
    load_policy_file,
)

# Exit codes, centralised so the mapping is obvious at a glance.
EXIT_AUTO_ALLOW = 0
EXIT_PROGRAM_ERROR = 1
EXIT_REQUIRE_APPROVAL = 2
EXIT_DENY = 3

_MODE_EXIT_CODES: dict[str, int] = {
    "auto_allow": EXIT_AUTO_ALLOW,
    "require_approval": EXIT_REQUIRE_APPROVAL,
    "deny": EXIT_DENY,
}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._:@/+~-]+$")
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f]")
_POSIX_ENV_SHORTHAND_RE = re.compile(
    r"^\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[^}\x00-\x1f]+\})"
)
_WINDOWS_ENV_SHORTHAND_RE = re.compile(r"^%[^%=\x00-\x1f]+%")
_WINDOWS_DELAYED_ENV_SHORTHAND_RE = re.compile(r"^![^!\x00-\x1f]+!")


def _has_control_char(value: str) -> bool:
    return bool(_CONTROL_CHAR_RE.search(value))


def _validate_optional_audit_string(
    *,
    field: str,
    value: str | None,
    max_length: int,
) -> str | None:
    if value is None:
        return None
    if not value:
        return f"{field} must not be empty"
    if len(value) > max_length:
        return f"{field} must be at most {max_length} characters"
    if _has_control_char(value):
        return f"{field} must not contain control characters"
    return None


def _validate_audit_event_path(value: str) -> str | None:
    windows_path = PureWindowsPath(value)
    if value.startswith(("/", "\\")) or windows_path.is_absolute() or windows_path.drive:
        return "path must be repository-relative"
    if value == "~" or value.startswith(("~/", "~\\")):
        return "path must not use a local home shorthand"
    if (
        _POSIX_ENV_SHORTHAND_RE.match(value)
        or _WINDOWS_ENV_SHORTHAND_RE.match(value)
        or _WINDOWS_DELAYED_ENV_SHORTHAND_RE.match(value)
    ):
        return "path must not use a local environment shorthand"
    if value.lower().startswith("file:"):
        return "path must not use file URI syntax"
    if ".." in re.split(r"[/\\]+", value):
        return "path must not contain parent traversal components"
    return None


def _validate_audit_event_args(args: argparse.Namespace) -> str | None:
    """Validate optional public audit strings before serializing them.

    build_audit_event() deliberately preserves caller-supplied optional
    strings for backward compatibility. This example wrapper is the producer
    boundary, so it enforces public-safe constraints before emitting evidence.
    """
    if not args.audit_event:
        return None

    session_error = _validate_optional_audit_string(
        field="session_id",
        value=args.session_id,
        max_length=256,
    )
    if session_error is not None:
        return session_error
    if args.session_id is not None and _SESSION_ID_RE.fullmatch(args.session_id) is None:
        return "session_id contains unsupported characters"

    command_error = _validate_optional_audit_string(
        field="command",
        value=args.command,
        max_length=4096,
    )
    if command_error is not None:
        return command_error

    path_error = _validate_optional_audit_string(
        field="path",
        value=args.path,
        max_length=1024,
    )
    if path_error is not None:
        return path_error
    if args.path is not None:
        path_boundary_error = _validate_audit_event_path(args.path)
        if path_boundary_error is not None:
            return path_boundary_error
    return None


def _build_context(args: argparse.Namespace) -> dict[str, Any]:
    """Turn CLI flags into the context dict agent_policy.evaluate expects.

    Keys are only set when the user passed the flag, so downstream
    `context.get("...")` checks see a missing key (not an explicit None)
    when the caller did not opt in.
    """
    context: dict[str, Any] = {}
    if args.ownership_class is not None:
        context["ownership_class"] = args.ownership_class
    if args.first_write:
        context["first_write_to_repo"] = True
    return context


def _decision_to_json(decision: PolicyDecision) -> str:
    """Serialize the decision with matched_repo always present.

    dataclasses.asdict keeps the key even when the value is None,
    and json.dumps renders None as null — this matches the contract
    documented in the module docstring.
    """
    payload = dataclasses.asdict(decision)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate a single (repo, capability, context) tuple against "
            "an agent-policy TOML file and report the decision as JSON."
        ),
    )
    parser.add_argument(
        "--policy",
        required=True,
        type=Path,
        help="Path to the policy TOML file (required).",
    )
    parser.add_argument(
        "--repo",
        required=True,
        help="Repository identifier, e.g. acme/app.",
    )
    parser.add_argument(
        "--capability",
        required=True,
        help="Normalized capability, e.g. read/write/commit/push/shell.",
    )
    parser.add_argument(
        "--ownership-class",
        default=None,
        choices=["internal", "external"],
        help="Optional ownership class for context gating.",
    )
    parser.add_argument(
        "--first-write",
        action="store_true",
        help="Set context.first_write_to_repo = true.",
    )
    parser.add_argument(
        "--audit-event",
        action="store_true",
        help="Print the deterministic audit event schema instead of only the decision.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional wrapper-supplied session identifier for --audit-event.",
    )
    parser.add_argument(
        "--command",
        default=None,
        help="Optional wrapper-supplied command string for --audit-event.",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Optional wrapper-supplied path for --audit-event.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
    except SystemExit as exc:
        # argparse uses exit code 2 for usage errors, which collides with
        # our require_approval judgment. Normalize all arg errors to 1 so
        # wrappers never confuse "bad args" with "needs a human".
        return EXIT_PROGRAM_ERROR if exc.code not in (None, 0) else int(exc.code or 0)

    audit_args_error = _validate_audit_event_args(args)
    if audit_args_error is not None:
        print(f"error: invalid audit event argument: {audit_args_error}", file=sys.stderr)
        return EXIT_PROGRAM_ERROR

    try:
        policy = load_policy_file(args.policy)
    except FileNotFoundError:
        print(f"error: policy file not found: {args.policy}", file=sys.stderr)
        return EXIT_PROGRAM_ERROR
    except Exception as exc:  # pydantic.ValidationError, tomllib decode errors, ...
        print(f"error: failed to load policy file: {exc}", file=sys.stderr)
        return EXIT_PROGRAM_ERROR

    context = _build_context(args)
    decision = evaluate(
        policy,
        repo=args.repo,
        capability=args.capability,
        context=context,
    )

    if args.audit_event:
        event = build_audit_event(
            repo=args.repo,
            capability=args.capability,
            context=context,
            decision=decision,
            session_id=args.session_id,
            command=args.command,
            path=args.path,
        )
        print(audit_event_to_json(event))
    else:
        print(_decision_to_json(decision))

    exit_code = _MODE_EXIT_CODES.get(decision.mode)
    if exit_code is None:
        # Defensive: Mode is a Literal, so this is unreachable unless the
        # public contract changed without updating this script.
        print(
            f"error: unknown decision mode {decision.mode!r}",
            file=sys.stderr,
        )
        return EXIT_PROGRAM_ERROR
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
