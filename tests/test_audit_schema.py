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


V1_SCHEMA_RESOURCE = "agent-policy.audit_event.v1.schema.json"
V1_1_SCHEMA_RESOURCE = "agent-policy.audit_event.v1.1.schema.json"


def _load_schema(resource: str = V1_SCHEMA_RESOURCE) -> dict[str, Any]:
    schema_path = resources.files("agent_policy.schemas").joinpath(resource)
    return json.loads(schema_path.read_text(encoding="utf-8"))


def _schema_validator(resource: str = V1_SCHEMA_RESOURCE) -> Draft202012Validator:
    schema = _load_schema(resource)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _valid_payload() -> dict[str, Any]:
    return {
        "repo": "example/repo",
        "capability": "shell",
        "context": {"ownership_class": "internal"},
        "decision": {
            "mode": "require_approval",
            "reason": "repo_policy",
            "matched_repo": "example/repo",
        },
        "session_id": "session-123",
        "command": "bash scripts/check.sh",
        "path": "scripts/check.sh",
    }


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


def test_packaged_audit_schema_v1_1_keeps_shape_and_adds_optional_constraints() -> None:
    v1 = _load_schema(V1_SCHEMA_RESOURCE)
    v1_1 = _load_schema(V1_1_SCHEMA_RESOURCE)

    assert v1_1["title"] == "agent-policy audit event v1.1"
    assert v1_1["required"] == v1["required"]
    assert v1_1["additionalProperties"] is False
    assert set(v1_1["properties"]) == set(v1["properties"])
    assert v1_1["properties"]["decision"]["required"] == v1["properties"]["decision"]["required"]
    assert v1_1["properties"]["decision"]["additionalProperties"] is False
    assert (
        v1_1["properties"]["decision"]["properties"]["mode"]["enum"]
        == v1["properties"]["decision"]["properties"]["mode"]["enum"]
    )
    assert (
        v1_1["properties"]["decision"]["properties"]["reason"]["enum"]
        == v1["properties"]["decision"]["properties"]["reason"]["enum"]
    )
    assert v1_1["properties"]["decision"]["properties"]["matched_repo"] == {
        "type": ["string", "null"],
        "minLength": 1,
        "maxLength": 256,
    }
    assert v1_1["properties"]["session_id"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 256,
        "pattern": "^[A-Za-z0-9._:@/+~-]+$",
    }
    assert v1_1["properties"]["command"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 4096,
        "pattern": "^[^\\u0000-\\u001F]+$",
    }
    assert v1_1["properties"]["path"] == {
        "type": "string",
        "minLength": 1,
        "maxLength": 1024,
        "pattern": "^[^/\\u0000-\\u001F][^\\u0000-\\u001F]*$",
    }


def test_packaged_audit_schemas_are_valid_draft_2020_12() -> None:
    for resource in [V1_SCHEMA_RESOURCE, V1_1_SCHEMA_RESOURCE]:
        Draft202012Validator.check_schema(_load_schema(resource))


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


def test_packaged_audit_schema_v1_1_validates_current_event_payload() -> None:
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

    _schema_validator(V1_1_SCHEMA_RESOURCE).validate(payload)


def test_packaged_audit_schema_v1_1_accepts_boundary_values() -> None:
    payload = {
        **_valid_payload(),
        "decision": {
            **_valid_payload()["decision"],
            "matched_repo": "r" * 256,
        },
        "session_id": "s" * 256,
        "command": "c" * 4096,
        "path": "p" * 1024,
    }

    _schema_validator(V1_1_SCHEMA_RESOURCE).validate(payload)

    null_match_payload = {
        **payload,
        "decision": {**payload["decision"], "matched_repo": None},
    }
    _schema_validator(V1_1_SCHEMA_RESOURCE).validate(null_match_payload)


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


def test_packaged_audit_schema_v1_keeps_legacy_optional_strings_permissive() -> None:
    payload = {
        **_valid_payload(),
        "decision": {
            **_valid_payload()["decision"],
            "matched_repo": "",
        },
        "session_id": "session with space",
        "command": "",
        "path": "/home/user/x",
    }

    _schema_validator(V1_SCHEMA_RESOURCE).validate(payload)


def test_packaged_audit_schema_v1_1_rejects_stricter_optional_boundaries() -> None:
    valid_payload = _valid_payload()
    invalid_payloads = [
        {
            **valid_payload,
            "decision": {**valid_payload["decision"], "matched_repo": ""},
        },
        {
            **valid_payload,
            "decision": {**valid_payload["decision"], "matched_repo": "r" * 257},
        },
        {**valid_payload, "session_id": ""},
        {**valid_payload, "session_id": "s" * 257},
        {**valid_payload, "session_id": "session with space"},
        {**valid_payload, "command": ""},
        {**valid_payload, "command": "c" * 4097},
        {**valid_payload, "command": "echo\nsecret"},
        {**valid_payload, "path": ""},
        {**valid_payload, "path": "p" * 1025},
        {**valid_payload, "path": "/absolute/path"},
        {**valid_payload, "path": "dir/\tfile"},
    ]

    validator = _schema_validator(V1_1_SCHEMA_RESOURCE)
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
