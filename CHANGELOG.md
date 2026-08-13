# Changelog

Where: `CHANGELOG.md`  
What: release notes and publication status for `yui-agent-policy` versions.
Why: keep release history and PyPI publication status auditable while the
package is still alpha.

## Unreleased

## 0.1.11 - 2026-08-13

- Made hard guardrail evaluation read private immutable state instead of the
  exported mutable `HARD_GUARDRAILS` dictionary, so public mutation or rebinding
  cannot weaken unconditional force-push denial.
- Made the example PreToolUse and PermissionRequest wrappers fail closed on
  initialization, payload, classifier, and evaluator failures; unknown Claude
  tools and ambiguous shell syntax now block before policy fallback.
- Added wrapper-owned external first-write state to the example integrations so
  the existing first-write guardrail is not skipped under `auto_allow` policy.
- Hardened the documented PyPI provenance flow with isolated temporary
  downloads, bounded requests, exact non-yanked artifact checks,
  redirect-final HTTPS host validation, exclusive file creation, and cleanup
  on success or failure. Also clarified the audit-event v1.1 wrapper-validation
  boundary and added a public issue-data safety reminder.
- Updated the PyPI publisher action and release-recovery workflow for Core
  Metadata 2.5 compatibility after the prior publisher rejected valid metadata
  before upload. Successful historical runs retain their prior recovery contract
  without requiring later-added attestation jobs or retained artifacts, while
  legacy lightweight tags and pre-changelog releases are handled explicitly;
  failed current runs keep the stricter annotated, artifact-bound recovery checks.

## 0.1.10 - 2026-08-13 (tag-only; not published)

- `v0.1.10` is an immutable tag-only failed release attempt. No files were
  published to PyPI and no GitHub Release was published because the old
  publisher rejected valid Core Metadata 2.5 before upload.

## 0.1.9 - 2026-07-18

- Changed both the PyPI publish and GitHub Release post-publish gates to use the
  exact-version JSON API, with bounded retries for CDN propagation, while
  preserving the existing pre-upload immutability check and requiring the exact
  non-yanked wheel/sdist set before either workflow succeeds.

## 0.1.8 - 2026-07-18

- Hardened GitHub Release publication so tag inputs are validated before shell
  use, release notes are generated from the verified tag commit, manual retries
  must start from the current default branch and require the matching successful
  tag-push release workflow, and GitHub Release creation requires the exact
  non-yanked wheel and sdist set on PyPI and rechecks the remote tag commit
  immediately before publication.
- Hardened the isolated wheel contract smoke to prove installed-package isolation
  and verify the packaged `agent-policy.audit_event.v1.1` schema shape.
- Hardened the provenance verification example so response-supplied filenames
  cannot select local output paths and unexpected artifact sets fail closed.

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
