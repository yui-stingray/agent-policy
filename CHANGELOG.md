# Changelog

Where: `CHANGELOG.md`  
What: release notes and publication status for `yui-agent-policy` versions.
Why: keep release history and PyPI publication status auditable while the
package is still alpha.

## Unreleased

- Began the next development cycle as `0.1.19.dev0`, distinct from the latest
  public PyPI release `0.1.18`, so unreleased documentation and release-control
  artifacts cannot reuse its immutable published identity.
- Defined the trusted integration contract for one-time operation-bound
  approval and the audited break-glass and release-containment boundaries.

## 0.1.18 - 2026-08-29

- Made the public example mapper apply Bash lexical comment rules before
  `shlex`, preserving word-internal, quoted, and escaped `#` characters while
  removing active comment bodies. It also rejects active direct and exact
  backslash-LF continuation-formed process substitutions after heredoc
  handling, before an allowlisted command can reach `shell` policy.
- Kept unreleased builds on `0.1.18.dev0`, distinct from public `0.1.17`, so
  development artifacts could not reuse a published identity before this final
  `0.1.18` release.

## 0.1.17 - 2026-08-27

- Made the public example hooks fail closed when unresolved parameter
  expansion can become a `wait` option or any argument of a recognized Git
  command, including global options before its subcommand. This prevents
  expansion-time options from reaching Bash
  variable-name evaluation or command-bearing Git program selectors after the
  bounded classifier has approved the visible argv.
- Kept unreleased builds on `0.1.17.dev0`, distinct from public `0.1.16`, so
  development artifacts could not reuse a published identity before this final
  `0.1.17` release.

## 0.1.16 - 2026-08-25

- Made the public example hooks fail closed on active output redirection,
  ANSI-C quoted words, file-writing command heads such as `tee`, and every
  `git push`, `git-push`, or `send-pack` form without an explicit visible force
  option. A command-only classifier cannot prove effective refspec, mirror,
  hook, helper, repository-selection, or TOCTOU state safe enough for `shell`
  auto-allow.
- Kept unreleased builds on `0.1.16.dev0`, distinct from public `0.1.15`, so
  development artifacts could not reuse a published identity before this final
  `0.1.16` release.

## 0.1.15 - 2026-08-24

- Made the public example hooks fail closed on callback-bearing Bash builtins,
  xtrace/`PS4` execution, command-bearing Git environment variables, shell
  assignments, state-mutating builtins, command-bearing Git program options,
  path-qualified command heads, and command heads outside finite command and
  option allowlists before a `shell` or default `auto_allow` policy can apply.
- Kept unreleased builds on `0.1.15.dev0`, distinct from public `0.1.14`, so
  development artifacts could not reuse a published identity before this final
  `0.1.15` release.

## 0.1.14 - 2026-08-24

- Made the example capability mapper fail closed on explicit and implicit Bash
  arithmetic evaluation, trap mutation, `wait -p` assignment, shell startup
  selectors/options/state, and shell-wrapper input following an inspected
  `-c` body so generated force-push commands cannot reach a `shell` or default
  `auto_allow` policy.
- Kept unreleased builds on `0.1.14.dev0`, distinct from public `0.1.13`, so
  development artifacts could not reuse a published identity before this final
  `0.1.14` release.

## 0.1.13 - 2026-08-24

- Made the example capability mapper fail closed on active unquoted Bash
  pathname expansion, selected visible dynamic argv execution, Git alias mutation, and
  configuration-changing `GIT_CONFIG*` environment assignments, unrecognized
  Git subcommands, and unmodeled command-dispatch prefixes before a shell
  auto-allow policy can apply.
- Kept unreleased builds on `0.1.13.dev0`, distinct from public `0.1.12`, so
  development artifacts could not reuse a published identity before this final
  `0.1.13` release.

## 0.1.12 - 2026-08-23

- Made the example capability mapper fail closed on active unquoted Bash brace
  expansion before `shlex` tokenization, including expansions assembled across
  exact backslash-LF continuations and escaped comment boundaries, while
  preserving CR and CRLF as ordinary word content and physical-line endings,
  respectively, and validating Bash-compatible sequence ranges, so
  brace-generated force-push arguments cannot fall through to `shell`; quoted
  or escaped braces, parameter expansion, shell grouping, malformed range
  literals, and heredoc bodies retain their prior handling.
- Validated evaluator context at the public boundary: non-mappings, invalid
  `ownership_class` values, and non-boolean `first_write_to_repo` values now
  raise before policy fallback, while falsey mappings are copied and extra
  context keys remain supported.
- Kept unreleased builds on `0.1.12.dev0`, distinct from public `0.1.11`, so
  development artifacts could not reuse a published identity before this final
  `0.1.12` release.
- Clarified that `agent-guard` is the standalone static entry point and
  `agent-policy` is an optional advanced runtime companion; the toolkit example
  remains a reference integration rather than required setup.
- Rejected contradictory overlapping `repo_policy` entries at validation and
  evaluation boundaries so authorization cannot depend on entry order.
- Revalidated caller-held `PolicyMatrix` instances at every evaluation boundary
  so post-construction mutation cannot introduce an invalid decision mode.
- Hash-locked the complete Python 3.12 Linux release build toolchain and made
  CI and release builds use the same non-isolated dependency set.
- Hash-locked the isolated wheel smoke's runtime dependency closure, installed
  the local wheel without dependency resolution, verified its installed
  dependency metadata, and ensured smoke execution cannot silently change the
  built distribution set or contents.
- Closed `RepoPolicy.ownership_class` to `internal` or `external` (with `None`
  as the wildcard), and made direct `PolicyAuditEvent(...)` construction copy,
  validate, and freeze context like `build_audit_event()`.
- Hardened the primary PyPI release preflight to require an annotated tag and
  compare its peeled commit with `origin/master` before build or publication.
- Raised the development pytest floor to `>=9.0.3,<10`.

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
