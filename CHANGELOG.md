# Changelog

Where: `CHANGELOG.md`  
What: release notes for published `yui-agent-policy` versions.  
Why: keep PyPI releases auditable while the package is still alpha.

## Unreleased

- Hardened GitHub Release publication so tag inputs are validated before shell
  use, release notes are generated from the verified tag commit, manual retries
  must start from the current default branch and require the matching successful
  tag-push release workflow, and GitHub Release creation requires the exact
  non-yanked wheel and sdist set on PyPI and rechecks the remote tag commit
  immediately before publication.
- Hardened the isolated wheel contract smoke to prove installed-package isolation
  and verify the packaged `agent-policy.audit_event.v1.1` schema shape.

## 0.1.7 - 2026-07-17

- Added an opt-in `agent-policy.audit_event.v1.1` JSON Schema resource with
  stricter optional-field constraints while leaving the existing `.v1` schema
  and `build_audit_event()` semantics unchanged.
- Hardened `examples/check.py --audit-event` so optional wrapper-supplied
  `session_id`, `command`, and `path` values are validated before
  serialization and invalid values fail as program errors without echoing the
  supplied value, including path traversal and alternate local-path syntax.
- Documented that schema validation is resource-only evidence checking; it does
  not prove human approval, public safety, redaction, secret scanning, local
  path rejection, or repository containment.
- Hardened release provenance by requiring tags to point at the current
  `master` commit with successful CI, pinning external Actions to reviewed
  commits, and generating GitHub attestations for the built wheel and sdist
  before PyPI Trusted Publishing.

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
