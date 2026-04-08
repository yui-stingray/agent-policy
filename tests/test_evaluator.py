"""Where: tests/test_evaluator.py
What: behavioural specs for the agent-policy MVP.
Why: lock the public contract before any wrapper depends on it.

Naming: each test reads as an English sentence describing the rule.
If a test name has to be reworded, the public contract changed —
update ARCHITECTURE.md before changing the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_policy import (
    HARD_GUARDRAILS,
    PolicyDecision,
    PolicyMatrix,
    RepoPolicy,
    evaluate,
    load_policy_file,
)


# ---------------------------------------------------------------------------
# Hard guardrails
# ---------------------------------------------------------------------------


def test_force_push_is_always_denied_even_with_permissive_repo_policy() -> None:
    policy = PolicyMatrix(
        default_mode="auto_allow",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                capabilities={"push.force": "auto_allow"},  # attempted override
            )
        ],
    )

    decision = evaluate(policy, repo="acme/app", capability="push.force")

    assert decision.mode == "deny"
    assert decision.reason == "hard_guardrail"
    assert decision.matched_repo == "acme/app"


def test_hard_guardrails_constant_contains_only_force_push() -> None:
    # If this assertion fails, ARCHITECTURE.md §3 must be updated and v0.x bumped.
    assert HARD_GUARDRAILS == {"push.force": "deny"}


def test_merge_pr_is_always_require_approval_even_when_auto_allowed() -> None:
    policy = PolicyMatrix(
        default_mode="auto_allow",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                capabilities={"merge.pr": "auto_allow"},  # attempted override
            )
        ],
    )

    decision = evaluate(policy, repo="acme/app", capability="merge.pr")

    assert decision.mode == "require_approval"
    assert decision.reason == "hard_guardrail"


def test_external_first_write_requires_approval() -> None:
    policy = PolicyMatrix(default_mode="auto_allow")

    decision = evaluate(
        policy,
        repo="someone-else/their-repo",
        capability="write",
        context={"ownership_class": "external", "first_write_to_repo": True},
    )

    assert decision.mode == "require_approval"
    assert decision.reason == "hard_guardrail"


# ---------------------------------------------------------------------------
# repo_policy matching
# ---------------------------------------------------------------------------


def test_repo_policy_capability_match_returns_repo_policy_reason() -> None:
    policy = PolicyMatrix(
        default_mode="require_approval",
        repo_policy=[
            RepoPolicy(repo="acme/app", capabilities={"commit": "auto_allow"}),
        ],
    )

    decision = evaluate(policy, repo="acme/app", capability="commit")

    assert decision == PolicyDecision(
        mode="auto_allow",
        reason="repo_policy",
        matched_repo="acme/app",
    )


def test_repo_matches_but_capability_missing_falls_back_to_default_mode() -> None:
    policy = PolicyMatrix(
        default_mode="require_approval",
        repo_policy=[
            RepoPolicy(repo="acme/app", capabilities={"commit": "auto_allow"}),
        ],
    )

    decision = evaluate(policy, repo="acme/app", capability="push")

    assert decision.mode == "require_approval"
    assert decision.reason == "default_mode"
    assert decision.matched_repo == "acme/app"


def test_unknown_repo_falls_back_to_default_mode_with_no_match() -> None:
    policy = PolicyMatrix(default_mode="require_approval")

    decision = evaluate(policy, repo="ghost/missing", capability="commit")

    assert decision.mode == "require_approval"
    assert decision.reason == "default_mode"
    assert decision.matched_repo is None


def test_ownership_class_gates_repo_policy_match() -> None:
    policy = PolicyMatrix(
        default_mode="require_approval",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                ownership_class="internal",
                capabilities={"push": "auto_allow"},
            )
        ],
    )

    # Same repo, but ownership_class mismatches → no repo_policy match.
    decision = evaluate(
        policy,
        repo="acme/app",
        capability="push",
        context={"ownership_class": "external"},
    )

    assert decision.mode == "require_approval"
    assert decision.reason == "default_mode"
    assert decision.matched_repo is None


def test_ownership_class_match_uses_repo_policy() -> None:
    policy = PolicyMatrix(
        default_mode="require_approval",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                ownership_class="internal",
                capabilities={"push": "auto_allow"},
            )
        ],
    )

    decision = evaluate(
        policy,
        repo="acme/app",
        capability="push",
        context={"ownership_class": "internal"},
    )

    assert decision.mode == "auto_allow"
    assert decision.reason == "repo_policy"
    assert decision.matched_repo == "acme/app"


# ---------------------------------------------------------------------------
# Caller convenience: dict input + frozen output
# ---------------------------------------------------------------------------


def test_evaluate_accepts_plain_dict_policy() -> None:
    policy = {
        "default_mode": "require_approval",
        "repo_policy": [
            {"repo": "acme/app", "capabilities": {"commit": "auto_allow"}}
        ],
    }

    decision = evaluate(policy, repo="acme/app", capability="commit")

    assert decision.mode == "auto_allow"


def test_policy_decision_is_frozen() -> None:
    decision = PolicyDecision(mode="deny", reason="hard_guardrail", matched_repo="r")

    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        decision.mode = "auto_allow"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TOML loader round-trip
# ---------------------------------------------------------------------------


def test_load_policy_file_round_trip(tmp_path: Path) -> None:
    toml_path = tmp_path / "policy.toml"
    toml_path.write_text(
        """
default_mode = "require_approval"

[[repo_policy]]
repo = "acme/app"
ownership_class = "internal"

[repo_policy.capabilities]
read = "auto_allow"
write = "auto_allow"
commit = "auto_allow"
push = "auto_allow"
""",
        encoding="utf-8",
    )

    policy = load_policy_file(toml_path)

    assert policy.default_mode == "require_approval"
    assert len(policy.repo_policy) == 1
    assert policy.repo_policy[0].repo == "acme/app"
    assert policy.repo_policy[0].capabilities["commit"] == "auto_allow"

    decision = evaluate(
        policy,
        repo="acme/app",
        capability="commit",
        context={"ownership_class": "internal"},
    )
    assert decision.mode == "auto_allow"
    assert decision.reason == "repo_policy"


def test_load_policy_file_rejects_unknown_field(tmp_path: Path) -> None:
    toml_path = tmp_path / "bad.toml"
    toml_path.write_text(
        'default_mode = "require_approval"\nbogus_field = true\n',
        encoding="utf-8",
    )

    with pytest.raises(Exception):  # pydantic.ValidationError
        load_policy_file(toml_path)
