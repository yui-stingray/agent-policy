"""Where: src/agent_policy/audit.py
What: deterministic audit event values for wrapper-owned logging.
Why: wrappers need a stable payload to persist without making the evaluator
     stateful or responsible for clocks, storage, IDs, or approval records.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import json
import math
from types import MappingProxyType
from typing import Any

from .decision import PolicyDecision


@dataclass(frozen=True)
class _FrozenObject:
    items: tuple[tuple[str, Any], ...]


@dataclass(frozen=True)
class _FrozenArray:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class PolicyAuditEvent:
    """Immutable event describing one policy evaluation.

    The event is intentionally a value object only. It does not create IDs,
    read clocks, write logs, hash itself, or verify approvals; wrappers own
    those side effects.
    """

    repo: str
    capability: str
    context: Mapping[str, Any]
    decision: PolicyDecision
    session_id: str | None = None
    command: str | None = None
    path: str | None = None

    def __post_init__(self) -> None:
        """Copy, validate, and freeze context at the public construction boundary."""
        if not isinstance(self.context, Mapping):
            raise TypeError("context must be a mapping")
        object.__setattr__(
            self,
            "context",
            MappingProxyType(_freeze_context(self.context)),
        )


def build_audit_event(
    *,
    repo: str,
    capability: str,
    context: Mapping[str, Any] | None,
    decision: PolicyDecision,
    session_id: str | None = None,
    command: str | None = None,
    path: str | None = None,
) -> PolicyAuditEvent:
    """Build a copied, top-level immutable audit event.

    ``PolicyAuditEvent`` performs the recursive copy, validation, and freeze
    so direct construction has the same behavior.
    """

    return PolicyAuditEvent(
        repo=repo,
        capability=capability,
        context=context if context is not None else {},
        decision=decision,
        session_id=session_id,
        command=command,
        path=path,
    )


def audit_event_asdict(event: PolicyAuditEvent) -> dict[str, Any]:
    """Return the public JSON-shaped schema for an audit event."""

    payload: dict[str, Any] = {
        "repo": event.repo,
        "capability": event.capability,
        "context": {
            key: _thaw_json_value(value)
            for key, value in sorted(event.context.items())
        },
        "decision": asdict(event.decision),
    }
    if event.session_id is not None:
        payload["session_id"] = event.session_id
    if event.command is not None:
        payload["command"] = event.command
    if event.path is not None:
        payload["path"] = event.path
    return payload


def audit_event_to_json(event: PolicyAuditEvent) -> str:
    """Serialize an audit event deterministically for wrapper logs."""

    return json.dumps(
        audit_event_asdict(event),
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
    )


def _freeze_context(context: Mapping[str, Any]) -> dict[str, Any]:
    frozen_items: list[tuple[str, Any]] = []
    for key, value in context.items():
        if not isinstance(key, str):
            raise TypeError(f"context keys must be strings, got {type(key).__name__}")
        frozen_items.append((key, _freeze_json_value(value)))
    return dict(sorted(frozen_items))


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, _FrozenObject):
        return _FrozenObject(tuple(_freeze_context(dict(value.items)).items()))
    if isinstance(value, _FrozenArray):
        return _FrozenArray(tuple(_freeze_json_value(item) for item in value.items))
    if isinstance(value, Mapping):
        return _FrozenObject(tuple(_freeze_context(value).items()))
    if isinstance(value, list):
        return _FrozenArray(tuple(_freeze_json_value(item) for item in value))
    if isinstance(value, float) and not math.isfinite(value):
        raise TypeError("context values must be finite JSON numbers")
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise TypeError(f"context values must be JSON-compatible, got {type(value).__name__}")


def _thaw_json_value(value: Any) -> Any:
    if isinstance(value, _FrozenObject):
        return {key: _thaw_json_value(item) for key, item in value.items}
    if isinstance(value, _FrozenArray):
        return [_thaw_json_value(item) for item in value.items]
    return value
