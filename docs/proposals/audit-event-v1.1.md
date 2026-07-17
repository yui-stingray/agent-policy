# Audit Event v1.1 Contract

Where: `docs/proposals/audit-event-v1.1.md`  
What: status and constraints for the implemented opt-in audit-event schema revision.
Why: keep `agent-policy.audit_event.v1` backward compatible while documenting the stricter event contract.

## Summary

`agent-policy.audit_event.v1` remains unchanged. It accepts optional
`session_id`, `command`, `path`, and nullable `decision.matched_repo` strings
that older producers may already emit. `agent-policy.audit_event.v1.1` is now
available as a standalone opt-in Draft 2020-12 schema resource with
machine-checkable additive constraints for consumers that want stricter public
evidence validation.

## Implemented Constraints

| Field | Constraint |
| --- | --- |
| `decision.matched_repo` | `null` or a 1-256 character string. |
| `session_id` | 1-256 characters matching `^[A-Za-z0-9._:@/+~-]+$(?![\s\S])`; the final lookahead requires true end-of-input. |
| `command` | 1-4096 characters matching `^[^\x00-\x1f]+$(?![\s\S])` after wrapper redaction. |
| `path` | 1-1024 characters matching `^[^/\x00-\x1f][^\x00-\x1f]*$(?![\s\S])` to exclude a leading POSIX slash and control characters; producers must also reject parent traversal and alternate local-path syntax before treating the value as repository-relative. |

## Availability

1. Keep publishing `agent-policy.audit_event.v1` exactly as-is for existing
   consumers.
2. Add producer-side normalization and enforcement in wrapper examples first,
   without changing `build_audit_event()` semantics. `examples/check.py` now
   enforces these constraints for `--audit-event`; the schema remains
   unchanged for `.v1`.
3. Load the new schema resource with the versioned resource name
   `agent-policy.audit_event.v1.1.schema.json`.
4. Let downstream consumers opt into the stricter schema while retaining `.v1`
   validation for legacy evidence.

The stricter schema does not replace or silently redefine `.v1`. It also does
not perform redaction, secret scanning, parent traversal rejection, alternate
local-path rejection, or repository containment checks. Producers and demo
profiles must enforce those rules before serialization.
