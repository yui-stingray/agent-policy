# Changelog

Where: `CHANGELOG.md`  
What: release notes for published `yui-agent-policy` versions.  
Why: keep PyPI releases auditable while the package is still alpha.

## 0.1.6 - 2026-07-05

- Updated Codex hook documentation to match current hook matcher coverage,
  default feature behavior, and `PermissionRequest` delegation semantics.
- Added a Codex `PermissionRequest` wrapper example that delegates
  `require_approval` decisions back to Codex's normal approval prompt.
- Tightened packaged audit-event schema constraints for optional public evidence
  strings while preserving the existing `.v1` field set.

## 0.1.5 - 2026-06-26

- Added a deterministic `PolicyAuditEvent` schema and JSON serializer for
  wrapper-owned policy logs and approval records.
- Added `examples/check.py --audit-event` so wrappers can opt in to the event
  payload without changing the default decision JSON contract.

## 0.1.4 - 2026-04-30

- Documented the approval-wrapper checklist for `require_approval` decisions.
- Added README status drift coverage so the advertised alpha version matches package metadata.
- Published the first PyPI release consumed by `ai-resilience-system` final gates.
