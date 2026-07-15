# Changelog

Where: `CHANGELOG.md`  
What: release notes for published `yui-agent-policy` versions.  
Why: keep PyPI releases auditable while the package is still alpha.

## Unreleased

- Hardened `examples/check.py --audit-event` so optional wrapper-supplied
  `session_id`, `command`, and `path` values are validated before
  serialization and invalid values fail as program errors without echoing the
  supplied value, including path traversal and alternate local-path syntax.

## 0.1.6 - 2026-07-05

- Updated Codex hook documentation to match current hook matcher coverage,
  default feature behavior, and `PermissionRequest` delegation semantics.
- Added a Codex `PermissionRequest` wrapper example that delegates
  `require_approval` decisions back to Codex's normal approval prompt.
- Kept the packaged `agent-policy.audit_event.v1` schema unchanged for backward
  compatibility; optional-string constraints are published as operator
  recommendations pending a future `v1.1` schema.

## 0.1.5 - 2026-06-26

- Added a deterministic `PolicyAuditEvent` schema and JSON serializer for
  wrapper-owned policy logs and approval records.
- Added `examples/check.py --audit-event` so wrappers can opt in to the event
  payload without changing the default decision JSON contract.

## 0.1.4 - 2026-04-30

- Documented the approval-wrapper checklist for `require_approval` decisions.
- Added README status drift coverage so the advertised alpha version matches package metadata.
- Published the first PyPI release consumed by `ai-resilience-system` final gates.
