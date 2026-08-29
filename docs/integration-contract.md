# Integration Contract

`agent-policy` evaluates a caller-supplied normalized tuple. It does not discover
repository identity, maintain an approval store, or execute an operation. This
document defines the obligations of a production integration that applies a
decision to execution.

## Trusted Inputs

The integration, not the model or tool payload, owns these values:

- canonical repository identity;
- normalized operation and capability;
- `ownership_class` and `first_write_to_repo`;
- immutable policy revision;
- context revision for decision-relevant runtime state;
- one-time request identity.

Missing or unverifiable state for a mutating operation must not fall through to
`auto_allow`. The integration must require approval or deny the operation.
Environment variables in the example hooks are trusted launcher configuration;
they are not proof that an ownership claim is true. Do not allow an agent command
or tool payload to set them.

## Bound Operation

Before evaluation, construct one immutable, versioned normalized operation with:

| Field | Contract |
| --- | --- |
| `repository_identity` | Canonical identity obtained from trusted integration state |
| `operation` | Tool, capability, target, arguments/options, and mutating intent |
| `payload_digest` | Domain-separated digest of canonical operation bytes |
| `policy_revision` | Identity of the immutable policy used for evaluation |
| `context_revision` | Identity of trusted decision-relevant state |
| `request_id` | Integration-generated single-use identifier |

Evaluate the frozen repository, capability, and context from that object. Bind
the resulting immutable `PolicyDecision` to the same object. Do not construct an
approval record by combining a decision with independently supplied repository,
capability, or context values.

Immediately before execution, compare repository identity, payload digest,
policy revision, and context revision with the evaluated values. Any mutation
requires reevaluation. A side-effecting approval must atomically consume its
request identity; replay must fail closed.

## Current Compatibility Surface

`PolicyDecision`, `PolicyAuditEvent`, `build_audit_event()`, and the v1/v1.1 audit
schemas are deterministic evidence primitives. They are not approval tokens and
do not carry a repository revision, operation digest, policy/context revision,
or one-time identity. The public example hooks classify, evaluate, and enforce
the current callback payload synchronously. They do not persist or replay a
`require_approval` result.

An integration that adds persisted approval must introduce a new versioned
envelope/API/schema. It must not add required fields to the existing v1 event or
infer approval binding from an audit event. Existing event fields remain useful
for public-safe review evidence only.

## Canonicalization Boundary

This project intentionally does not define a universal repository-URL or shell
canonicalizer. The host integration must define its supported identity and
operation vocabulary, reject ambiguous aliases and dynamic arguments, and test
that equivalent spellings cannot cross ownership or capability boundaries. A
new canonicalization algorithm is a versioned integration contract.

## Required Negative Tests

A production wrapper or approval service must test at least:

- repository substitution;
- operation or argument mutation after approval;
- policy and context revision changes;
- false or missing ownership/first-write state;
- reuse of a consumed request identity;
- decision/audit tuple substitution;
- unknown or dynamic operation input.

The current example-hook tests cover immediate fail-closed classification and
enforcement. They are not evidence that an external approval store satisfies
this contract.
