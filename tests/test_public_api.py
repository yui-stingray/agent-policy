"""Where: tests/test_public_api.py
What: pin the agent-policy public API surface and one canonical evaluate() case.
Why: this file is the source of truth for release-time smoke. When tagging a
     new version, re-run these exact assertions against the installed wheel
     (e.g. `pip install dist/*.whl && pytest tests/test_public_api.py`) to
     catch wheel regressions without having to retype member names from
     memory. A typo in an ad-hoc smoke command looks identical to a real
     packaging failure; pinning the contract here separates the two.

Scope: intentionally narrow. Behavioural rules belong in test_evaluator.py;
this file exists purely to stabilise the import surface and one happy path.
"""

from __future__ import annotations

import agent_policy
from agent_policy import (
    HARD_GUARDRAILS,
    Mode,
    PolicyAuditEvent,
    PolicyDecision,
    PolicyMatrix,
    Reason,
    RepoPolicy,
    audit_event_asdict,
    audit_event_to_json,
    build_audit_event,
    evaluate,
    load_policy_file,
)

# The v0.1 public surface. Adding or removing an entry here is a deliberate
# contract change for downstream callers — bump the package version and
# update this set in the same commit.
EXPECTED_EXPORTS: frozenset[str] = frozenset(
    {
        "evaluate",
        "load_policy_file",
        "PolicyMatrix",
        "RepoPolicy",
        "PolicyDecision",
        "PolicyAuditEvent",
        "build_audit_event",
        "audit_event_asdict",
        "audit_event_to_json",
        "HARD_GUARDRAILS",
        "Mode",
        "Reason",
    }
)


def test_all_matches_expected_exports() -> None:
    # __all__ is the contract. If this fails, either the change is intentional
    # (bump EXPECTED_EXPORTS and the package version together) or it is an
    # accident (back it out before tagging).
    assert frozenset(agent_policy.__all__) == EXPECTED_EXPORTS


def test_all_exported_symbols_are_bound() -> None:
    # The top-level explicit `from agent_policy import ...` already raises
    # ImportError at collection time if any name is missing. Touching each
    # symbol here makes that guarantee local and also satisfies linters that
    # flag unused imports.
    symbols = (
        evaluate,
        load_policy_file,
        PolicyMatrix,
        RepoPolicy,
        PolicyDecision,
        PolicyAuditEvent,
        build_audit_event,
        audit_event_asdict,
        audit_event_to_json,
        HARD_GUARDRAILS,
        Mode,
        Reason,
    )
    assert all(s is not None for s in symbols)


def test_canonical_evaluate_case_repo_policy_hit() -> None:
    # One fixed happy path: repo_policy hit on the `read` capability against
    # an exact-match repo. This is the single evaluate() scenario a clean-
    # install release smoke should re-run. Keep it small so any drift in the
    # public contract produces a failing assertion on a concrete value.
    policy = PolicyMatrix(
        default_mode="deny",
        repo_policy=[
            RepoPolicy(repo="myorg/myrepo", capabilities={"read": "auto_allow"}),
        ],
    )
    decision = evaluate(
        policy,
        repo="myorg/myrepo",
        capability="read",
        context={},
    )
    assert decision == PolicyDecision(
        mode="auto_allow",
        reason="repo_policy",
        matched_repo="myorg/myrepo",
    )
