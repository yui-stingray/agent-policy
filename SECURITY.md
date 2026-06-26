# Security Policy

`agent-policy` is a guardrail component for agent-operated repositories, so
reports that affect authorization decisions or fail-closed behavior are
security-sensitive.

## Supported versions

The latest published `0.1.x` release is supported while the project is in
alpha. Security fixes may be released as a new patch version without preserving
compatibility for undocumented internals.

## Reporting a vulnerability

If GitHub private vulnerability reporting is available for this repository,
use it. Otherwise, open a public issue with a high-level description and omit
exploit payloads, private logs, credentials, or repository-specific secrets.

Helpful reports include:

- the affected version or commit
- a minimal policy and capability/context input
- the expected safe decision
- the observed unsafe decision
- whether the issue can cause an unintended `auto_allow`, missed `deny`, or
  approval bypass

Do not use this project to test repositories or systems that you do not own or
do not have permission to review.
