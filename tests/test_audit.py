"""Where: tests/test_audit.py
What: contract tests for deterministic PolicyAuditEvent payloads.
Why: approval wrappers need stable evidence without moving state into the
     pure evaluator.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
import math

import pytest

from agent_policy import (
    PolicyDecision,
    audit_event_asdict,
    audit_event_to_json,
    build_audit_event,
)


def test_build_audit_event_copies_and_freezes_context() -> None:
    context = {"z": True, "a": "internal", "nested": {"items": ["one"]}}
    decision = PolicyDecision(
        mode="require_approval",
        reason="repo_policy",
        matched_repo="acme/app",
    )

    event = build_audit_event(
        repo="acme/app",
        capability="shell",
        context=context,
        decision=decision,
    )
    context["z"] = False
    context["new"] = "later"
    context["nested"]["items"].append("two")  # type: ignore[index]

    assert audit_event_asdict(event)["context"] == {
        "a": "internal",
        "nested": {"items": ["one"]},
        "z": True,
    }
    with pytest.raises(TypeError):
        event.context["new"] = "blocked"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.repo = "other/repo"  # type: ignore[misc]


def test_audit_event_asdict_includes_decision_and_optional_fields() -> None:
    decision = PolicyDecision(
        mode="auto_allow",
        reason="repo_policy",
        matched_repo="acme/app",
    )

    event = build_audit_event(
        repo="acme/app",
        capability="commit",
        context={"ownership_class": "internal"},
        decision=decision,
        session_id="session-123",
        command="git status --short",
        path="README.md",
    )

    assert audit_event_asdict(event) == {
        "repo": "acme/app",
        "capability": "commit",
        "context": {"ownership_class": "internal"},
        "decision": {
            "mode": "auto_allow",
            "reason": "repo_policy",
            "matched_repo": "acme/app",
        },
        "session_id": "session-123",
        "command": "git status --short",
        "path": "README.md",
    }


def test_audit_event_json_is_stable_and_key_sorted() -> None:
    decision = PolicyDecision(
        mode="require_approval",
        reason="default_mode",
        matched_repo=None,
    )
    event = build_audit_event(
        repo="ghost/missing",
        capability="write",
        context={"z": True, "a": "external"},
        decision=decision,
    )

    expected = {
        "capability": "write",
        "context": {"a": "external", "z": True},
        "decision": {
            "matched_repo": None,
            "mode": "require_approval",
            "reason": "default_mode",
        },
        "repo": "ghost/missing",
    }
    assert audit_event_to_json(event) == json.dumps(
        expected,
        ensure_ascii=False,
        sort_keys=True,
    )


def test_audit_event_preserves_json_arrays_of_pairs() -> None:
    decision = PolicyDecision(
        mode="auto_allow",
        reason="repo_policy",
        matched_repo="acme/app",
    )
    event = build_audit_event(
        repo="acme/app",
        capability="read",
        context={"pairs": [["a", 1], ["b", 2]]},
        decision=decision,
    )

    assert audit_event_asdict(event)["context"] == {
        "pairs": [["a", 1], ["b", 2]],
    }


def test_audit_event_rejects_non_json_context_values() -> None:
    decision = PolicyDecision(
        mode="require_approval",
        reason="default_mode",
        matched_repo=None,
    )

    with pytest.raises(TypeError, match="JSON-compatible"):
        build_audit_event(
            repo="acme/app",
            capability="write",
            context={"bad": object()},
            decision=decision,
        )

    with pytest.raises(TypeError, match="context keys must be strings"):
        build_audit_event(
            repo="acme/app",
            capability="write",
            context={1: "bad-key"},  # type: ignore[dict-item]
            decision=decision,
        )

    for value in (math.nan, math.inf, -math.inf):
        with pytest.raises(TypeError, match="finite JSON numbers"):
            build_audit_event(
                repo="acme/app",
                capability="write",
                context={"bad": value},
                decision=decision,
            )
