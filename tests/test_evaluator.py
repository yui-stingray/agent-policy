"""Where: tests/test_evaluator.py
What: behavioural specs for the agent-policy MVP.
Why: lock the public contract before any wrapper depends on it.

Naming: each test reads as an English sentence describing the rule.
If a test name has to be reworded, the public contract changed —
update ARCHITECTURE.md before changing the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

import agent_policy.guardrails as guardrails_module
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
    assert isinstance(HARD_GUARDRAILS, dict)
    assert HARD_GUARDRAILS == {"push.force": "deny"}
    assert json.loads(json.dumps(HARD_GUARDRAILS)) == HARD_GUARDRAILS


def test_public_guardrails_mutation_and_rebinding_cannot_weaken_evaluation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = dict(HARD_GUARDRAILS)
    try:
        HARD_GUARDRAILS.clear()
        # The evaluator must read its private immutable state, not the public
        # compatibility copy that an in-process caller can mutate or rebind.
        monkeypatch.setattr(guardrails_module, "HARD_GUARDRAILS", {})
        decision = evaluate(
            PolicyMatrix(default_mode="auto_allow"),
            repo="acme/app",
            capability="push.force",
        )
    finally:
        HARD_GUARDRAILS.update(original)

    assert decision.mode == "deny"
    assert decision.reason == "hard_guardrail"


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


def test_external_first_write_does_not_block_read() -> None:
    # Regression (P2): first_write_to_repo guardrail must not fire for
    # non-mutating capabilities. Reading an unfamiliar external repo is
    # not itself a sensitive action — it should fall through to the
    # normal repo_policy / default_mode path.
    policy = PolicyMatrix(default_mode="auto_allow")

    decision = evaluate(
        policy,
        repo="someone-else/their-repo",
        capability="read",
        context={"ownership_class": "external", "first_write_to_repo": True},
    )

    assert decision.mode == "auto_allow"
    assert decision.reason == "default_mode"
    assert decision.matched_repo is None


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


def test_split_repo_policy_finds_capability_in_later_entry() -> None:
    # Regression (P1): when the same repo is split across multiple
    # [[repo_policy]] entries, scanning must continue past an earlier
    # entry that matches the repo but omits the capability. Otherwise
    # a late `shell = require_approval` constraint would be silently
    # dropped by an earlier general-purpose block.
    policy = PolicyMatrix(
        default_mode="auto_allow",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                capabilities={"read": "auto_allow", "commit": "auto_allow"},
            ),
            RepoPolicy(
                repo="acme/app",
                capabilities={"shell": "require_approval"},
            ),
        ],
    )

    decision = evaluate(policy, repo="acme/app", capability="shell")

    assert decision.mode == "require_approval"
    assert decision.reason == "repo_policy"
    assert decision.matched_repo == "acme/app"


def test_split_repo_policy_missing_capability_uses_default_mode() -> None:
    # Regression (P1): if no entry for this repo declares the capability,
    # we still fall back to default_mode, but matched_repo should reflect
    # that some entry did match the repo (so observers know we looked).
    policy = PolicyMatrix(
        default_mode="require_approval",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                capabilities={"read": "auto_allow"},
            ),
            RepoPolicy(
                repo="acme/app",
                capabilities={"commit": "auto_allow"},
            ),
        ],
    )

    decision = evaluate(policy, repo="acme/app", capability="push")

    assert decision.mode == "require_approval"
    assert decision.reason == "default_mode"
    assert decision.matched_repo == "acme/app"


@pytest.mark.parametrize("broad_rule_first", [True, False])
def test_conflicting_wildcard_and_external_rules_are_rejected_in_either_order(
    broad_rule_first: bool,
) -> None:
    broad_rule = RepoPolicy(
        repo="acme/app",
        capabilities={"shell": "auto_allow"},
    )
    external_rule = RepoPolicy(
        repo="acme/app",
        ownership_class="external",
        capabilities={"shell": "deny"},
    )
    rules = (
        [broad_rule, external_rule]
        if broad_rule_first
        else [external_rule, broad_rule]
    )

    with pytest.raises(ValidationError, match="overlap with conflicting modes"):
        PolicyMatrix(repo_policy=rules)


def test_conflicting_rules_with_the_same_ownership_class_are_rejected() -> None:
    with pytest.raises(ValidationError, match="overlap with conflicting modes"):
        PolicyMatrix(
            repo_policy=[
                RepoPolicy(
                    repo="acme/app",
                    ownership_class="internal",
                    capabilities={"shell": "auto_allow"},
                ),
                RepoPolicy(
                    repo="acme/app",
                    ownership_class="internal",
                    capabilities={"shell": "require_approval"},
                ),
            ]
        )


def test_overlapping_rules_with_the_same_mode_remain_valid() -> None:
    policy = PolicyMatrix(
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                capabilities={"read": "auto_allow"},
            ),
            RepoPolicy(
                repo="acme/app",
                ownership_class="external",
                capabilities={"read": "auto_allow"},
            ),
        ]
    )

    decision = evaluate(
        policy,
        repo="acme/app",
        capability="read",
        context={"ownership_class": "external"},
    )

    assert decision.mode == "auto_allow"
    assert decision.reason == "repo_policy"


def test_conflicting_modes_for_disjoint_ownership_classes_remain_valid() -> None:
    policy = PolicyMatrix(
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                ownership_class="internal",
                capabilities={"shell": "auto_allow"},
            ),
            RepoPolicy(
                repo="acme/app",
                ownership_class="external",
                capabilities={"shell": "deny"},
            ),
        ]
    )

    internal = evaluate(
        policy,
        repo="acme/app",
        capability="shell",
        context={"ownership_class": "internal"},
    )
    external = evaluate(
        policy,
        repo="acme/app",
        capability="shell",
        context={"ownership_class": "external"},
    )

    assert internal.mode == "auto_allow"
    assert external.mode == "deny"


def test_evaluate_rejects_conflict_introduced_after_matrix_validation() -> None:
    internal_rule = RepoPolicy(
        repo="acme/app",
        ownership_class="internal",
        capabilities={"shell": "auto_allow"},
    )
    policy = PolicyMatrix(
        repo_policy=[
            internal_rule,
            RepoPolicy(
                repo="acme/app",
                ownership_class="external",
                capabilities={"shell": "deny"},
            ),
        ]
    )
    internal_rule.ownership_class = None

    with pytest.raises(ValueError, match="overlap with conflicting modes"):
        evaluate(
            policy,
            repo="acme/app",
            capability="shell",
            context={"ownership_class": "external"},
        )


def test_evaluate_rejects_invalid_capability_mode_introduced_after_matrix_validation() -> None:
    policy = PolicyMatrix(
        repo_policy=[
            RepoPolicy(repo="acme/app", capabilities={"commit": "auto_allow"})
        ]
    )
    policy.repo_policy[0].capabilities["commit"] = "invalid"  # type: ignore[assignment]

    with pytest.raises(ValidationError, match="capabilities"):
        evaluate(policy, repo="acme/app", capability="commit")


def test_evaluate_rejects_invalid_default_mode_introduced_after_matrix_validation() -> None:
    policy = PolicyMatrix(default_mode="require_approval")
    policy.default_mode = "invalid"  # type: ignore[assignment]

    with pytest.raises(ValidationError, match="default_mode"):
        evaluate(policy, repo="acme/app", capability="read")


def test_evaluate_accepts_valid_capability_mode_introduced_after_matrix_validation() -> None:
    policy = PolicyMatrix(
        repo_policy=[
            RepoPolicy(repo="acme/app", capabilities={"commit": "auto_allow"})
        ]
    )
    policy.repo_policy[0].capabilities["commit"] = "deny"

    decision = evaluate(policy, repo="acme/app", capability="commit")

    assert decision.mode == "deny"
    assert decision.reason == "repo_policy"


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


@pytest.mark.parametrize("ownership_class", ["internal", "external", None])
def test_repo_policy_accepts_closed_ownership_class_vocabulary(
    ownership_class: str | None,
) -> None:
    policy = RepoPolicy(
        repo="acme/app",
        ownership_class=ownership_class,
        capabilities={"push": "auto_allow"},
    )

    assert policy.ownership_class == ownership_class


def test_repo_policy_rejects_misspelled_ownership_class() -> None:
    with pytest.raises(ValidationError, match="ownership_class"):
        RepoPolicy(
            repo="acme/app",
            ownership_class="interanl",
            capabilities={"push": "auto_allow"},
        )


def test_repo_policy_rejects_misspelled_ownership_class_on_assignment() -> None:
    policy = PolicyMatrix(
        default_mode="auto_allow",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                ownership_class="internal",
                capabilities={"shell": "deny"},
            )
        ],
    )

    with pytest.raises(ValidationError, match="ownership_class"):
        policy.repo_policy[0].ownership_class = "interanl"  # type: ignore[assignment]

    decision = evaluate(
        policy,
        repo="acme/app",
        capability="shell",
        context={"ownership_class": "internal"},
    )
    assert decision.mode == "deny"
    assert decision.reason == "repo_policy"


def test_none_ownership_class_remains_a_repo_policy_wildcard() -> None:
    policy = PolicyMatrix(
        default_mode="require_approval",
        repo_policy=[
            RepoPolicy(
                repo="acme/app",
                ownership_class=None,
                capabilities={"push": "auto_allow"},
            )
        ],
    )

    decision = evaluate(
        policy,
        repo="acme/app",
        capability="push",
        context={"ownership_class": "external"},
    )

    assert decision.mode == "auto_allow"
    assert decision.reason == "repo_policy"


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


def test_evaluate_rejects_misspelled_ownership_before_auto_allow_default() -> None:
    with pytest.raises(ValidationError, match="ownership_class"):
        evaluate(
            {
                "default_mode": "auto_allow",
                "repo_policy": [
                    {
                        "repo": "acme/app",
                        "ownership_class": "interanl",
                        "capabilities": {"commit": "auto_allow"},
                    }
                ],
            },
            repo="acme/app",
            capability="push",
        )


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


def test_load_policy_file_rejects_misspelled_ownership_before_auto_allow_default(
    tmp_path: Path,
) -> None:
    toml_path = tmp_path / "bad-ownership.toml"
    toml_path.write_text(
        """
default_mode = "auto_allow"

[[repo_policy]]
repo = "acme/app"
ownership_class = "interanl"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="ownership_class"):
        load_policy_file(toml_path)


def test_load_policy_file_rejects_conflicting_overlapping_rules(
    tmp_path: Path,
) -> None:
    toml_path = tmp_path / "conflicting-policy.toml"
    toml_path.write_text(
        """
[[repo_policy]]
repo = "acme/app"

[repo_policy.capabilities]
shell = "auto_allow"

[[repo_policy]]
repo = "acme/app"
ownership_class = "external"

[repo_policy.capabilities]
shell = "deny"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError, match="overlap with conflicting modes"):
        load_policy_file(toml_path)
