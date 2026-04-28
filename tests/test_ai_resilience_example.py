"""Where: tests/test_ai_resilience_example.py
What: contract checks for the ai-resilience example policy vocabulary.
Why: keep safety-oriented capabilities documented without expanding evaluator state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from agent_policy import PolicyDecision, evaluate, load_policy_file


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_TOML = REPO_ROOT / "examples" / "ai_resilience_policy.toml"


@pytest.fixture
def decision_for() -> Callable[[str, str], PolicyDecision]:
    policy = load_policy_file(POLICY_TOML)

    def _decision_for(repo: str, capability: str) -> PolicyDecision:
        return evaluate(
            policy,
            repo=repo,
            capability=capability,
            context={"ownership_class": "internal"},
        )

    return _decision_for


def test_ai_resilience_example_requires_approval_for_publication(
    decision_for: Callable[[str, str], PolicyDecision],
) -> None:
    decision = decision_for("yui-stingray/ai-resilience-system", "artifact.publish")

    assert decision.mode == "require_approval"
    assert decision.reason == "repo_policy"


def test_ai_resilience_example_denies_secret_materialization(
    decision_for: Callable[[str, str], PolicyDecision],
) -> None:
    decision = decision_for("yui-stingray/ai-resilience-system", "secret.materialize")

    assert decision.mode == "deny"
    assert decision.reason == "repo_policy"


def test_agent_guard_example_keeps_scanner_policy_updates_human_reviewed(
    decision_for: Callable[[str, str], PolicyDecision],
) -> None:
    decision = decision_for("yui-stingray/agent-guard", "scanner.policy.update")

    assert decision.mode == "require_approval"
    assert decision.reason == "repo_policy"
