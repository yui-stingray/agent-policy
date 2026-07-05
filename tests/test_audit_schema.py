"""Where: tests/test_audit_schema.py
What: contract tests for the packaged audit event JSON Schema.
Why: downstream wrappers should be able to validate installed audit evidence.
"""

from __future__ import annotations

from importlib import resources
import json
from typing import Any

from jsonschema import Draft202012Validator

from agent_policy import (
    PolicyDecision,
    audit_event_asdict,
    build_audit_event,
)


SCHEMA_RESOURCE = "agent-policy.audit_event.v1.schema.json"


def _load_schema() -> dict[str, Any]:
    schema_path = resources.files("agent_policy.schemas").joinpath(SCHEMA_RESOURCE)
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _schema_validator() -> Draft202012Validator:
    schema = _load_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_packaged_audit_schema_matches_current_event_shape() -> None:
    schema = _load_schema()

    assert schema["title"] == "agent-policy audit event v1"
    assert schema["required"] == ["repo", "capability", "context", "decision"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "repo",
        "capability",
        "context",
        "decision",
        "session_id",
        "command",
        "path",
    }


def test_packaged_audit_schema_pins_decision_enums() -> None:
    decision = _load_schema()["properties"]["decision"]

    assert decision["required"] == ["mode", "reason", "matched_repo"]
    assert decision["additionalProperties"] is False
    assert decision["properties"]["mode"]["enum"] == ["deny", "require_approval", "auto_allow"]
    assert decision["properties"]["reason"]["enum"] == [
        "hard_guardrail",
        "repo_policy",
        "default_mode",
        "condition_match",
        "no_match",
    ]
    assert decision["properties"]["matched_repo"]["type"] == ["string", "null"]


def test_packaged_audit_schema_keeps_v1_optional_strings_unconstrained() -> None:
    properties = _load_schema()["properties"]

    assert properties["session_id"] == {"type": "string"}
    assert properties["command"] == {"type": "string"}
    assert properties["path"] == {"type": "string"}
    assert properties["decision"]["properties"]["matched_repo"] == {
        "type": ["string", "null"]
    }


def test_packaged_audit_schema_accepts_public_audit_event_shape() -> None:
    decision = PolicyDecision(
        mode="require_approval",
        reason="repo_policy",
        matched_repo="example/repo",
    )
    event = build_audit_event(
        repo="example/repo",
        capability="shell",
        context={"ownership_class": "internal"},
        decision=decision,
        session_id="session-123",
        command="bash scripts/check.sh",
        path="scripts/check.sh",
    )
    payload = audit_event_asdict(event)
    schema = _load_schema()

    assert set(schema["required"]) <= payload.keys()
    assert set(payload) <= set(schema["properties"])
    assert set(payload["decision"]) <= set(schema["properties"]["decision"]["properties"])
    assert payload["decision"]["mode"] in schema["properties"]["decision"]["properties"]["mode"]["enum"]
    assert payload["decision"]["reason"] in schema["properties"]["decision"]["properties"]["reason"]["enum"]


def test_packaged_audit_schema_validates_current_event_payload() -> None:
    decision = PolicyDecision(
        mode="require_approval",
        reason="repo_policy",
        matched_repo="example/repo",
    )
    payload = audit_event_asdict(
        build_audit_event(
            repo="example/repo",
            capability="shell",
            context={"ownership_class": "internal"},
            decision=decision,
            session_id="session-123",
            command="bash scripts/check.sh",
            path="scripts/check.sh",
        )
    )

    _schema_validator().validate(payload)


def test_packaged_audit_schema_rejects_unsupported_payload_shapes() -> None:
    valid_payload: dict[str, Any] = {
        "repo": "example/repo",
        "capability": "shell",
        "context": {"ownership_class": "internal"},
        "decision": {
            "mode": "require_approval",
            "reason": "repo_policy",
            "matched_repo": "example/repo",
        },
    }
    invalid_payloads = [
        {key: value for key, value in valid_payload.items() if key != "decision"},
        {**valid_payload, "extra": "not part of audit_event.v1"},
        {**valid_payload, "decision": {**valid_payload["decision"], "mode": "review_later"}},
        {**valid_payload, "decision": {**valid_payload["decision"], "reason": "unknown_reason"}},
        {**valid_payload, "decision": {**valid_payload["decision"], "extra": "not allowed"}},
        {**valid_payload, "repo": ""},
        {**valid_payload, "capability": ""},
    ]

    validator = _schema_validator()
    for payload in invalid_payloads:
        assert not validator.is_valid(payload), payload


def test_audit_event_intentionally_omits_generated_metadata() -> None:
    decision = PolicyDecision(
        mode="auto_allow",
        reason="repo_policy",
        matched_repo="example/repo",
    )
    payload = audit_event_asdict(
        build_audit_event(
            repo="example/repo",
            capability="read",
            context={},
            decision=decision,
        )
    )

    assert "event_id" not in payload
    assert "timestamp" not in payload
    assert "schema_version" not in payload


def test_audit_event_v1_preserves_legacy_optional_string_values() -> None:
    decision = PolicyDecision(
        mode="require_approval",
        reason="repo_policy",
        matched_repo="example repo",
    )
    payload = audit_event_asdict(
        build_audit_event(
            repo="example/repo",
            capability="shell",
            context={},
            decision=decision,
            session_id="session with space",
            command="",
            path="/home/user/x",
        )
    )

    assert payload["decision"]["matched_repo"] == "example repo"
    assert payload["session_id"] == "session with space"
    assert payload["command"] == ""
    assert payload["path"] == "/home/user/x"
