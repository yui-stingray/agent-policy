"""Where: tests/test_ai_resilience_example.py
What: contract checks for the ai-resilience example policy vocabulary.
Why: keep safety-oriented capabilities documented without expanding evaluator state.
"""

from __future__ import annotations

from pathlib import Path

from agent_policy import evaluate, load_policy_file


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_TOML = REPO_ROOT / "examples" / "ai_resilience_policy.toml"


def test_ai_resilience_example_requires_approval_for_publication() -> None:
    policy = load_policy_file(POLICY_TOML)

    decision = evaluate(
        policy,
        repo="yui-stingray/ai-resilience-system",
        capability="artifact.publish",
        context={"ownership_class": "internal"},
    )

    assert decision.mode == "require_approval"
    assert decision.reason == "repo_policy"


def test_ai_resilience_example_denies_secret_materialization() -> None:
    policy = load_policy_file(POLICY_TOML)

    decision = evaluate(
        policy,
        repo="yui-stingray/ai-resilience-system",
        capability="secret.materialize",
        context={"ownership_class": "internal"},
    )

    assert decision.mode == "deny"
    assert decision.reason == "repo_policy"


def test_agent_guard_example_keeps_scanner_policy_updates_human_reviewed() -> None:
    policy = load_policy_file(POLICY_TOML)

    decision = evaluate(
        policy,
        repo="yui-stingray/agent-guard",
        capability="scanner.policy.update",
        context={"ownership_class": "internal"},
    )

    assert decision.mode == "require_approval"
    assert decision.reason == "repo_policy"
