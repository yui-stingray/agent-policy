"""Where: src/agent_policy/evaluator.py
What: evaluate(policy, repo, capability, context) -> PolicyDecision.
Why: the single public entry point. Pure function. No I/O, no logging, no state.

Contract:
- Caller passes an already-loaded PolicyMatrix (or a dict the model accepts).
- Capability strings are expected to be already normalized by the caller
  (e.g. `git push --force` → "push.force"). The evaluator does not parse
  shell commands — see README for the wrapper pattern.
- Decision order:
    1. Hard guardrails (unconditional, then conditional).
    2. First matching repo_policy entry, gated by optional ownership_class.
    3. default_mode fallback with reason="default_mode".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .decision import PolicyDecision
from .guardrails import _evaluate_hard_guardrails
from .matrix import PolicyMatrix, RepoPolicy


def evaluate(
    policy: PolicyMatrix | Mapping[str, Any],
    repo: str,
    capability: str,
    context: Mapping[str, Any] | None = None,
) -> PolicyDecision:
    """Return the policy decision for a single (repo, capability, context) tuple."""
    matrix = _coerce(policy)
    ctx: dict[str, Any] = dict(context or {})

    guardrail = _evaluate_hard_guardrails(
        repo=repo,
        capability=capability,
        context=ctx,
    )
    if guardrail is not None:
        return guardrail

    for repo_policy in matrix.repo_policy:
        if repo_policy.repo != repo:
            continue
        if not _ownership_matches(repo_policy, ctx):
            continue

        if capability in repo_policy.capabilities:
            return PolicyDecision(
                mode=repo_policy.capabilities[capability],
                reason="repo_policy",
                matched_repo=repo_policy.repo,
            )
        return PolicyDecision(
            mode=matrix.default_mode,
            reason="default_mode",
            matched_repo=repo_policy.repo,
        )

    return PolicyDecision(
        mode=matrix.default_mode,
        reason="default_mode",
        matched_repo=None,
    )


def _coerce(policy: PolicyMatrix | Mapping[str, Any]) -> PolicyMatrix:
    if isinstance(policy, PolicyMatrix):
        return policy
    return PolicyMatrix.model_validate(policy)


def _ownership_matches(repo_policy: RepoPolicy, context: Mapping[str, Any]) -> bool:
    if repo_policy.ownership_class is None:
        return True
    return context.get("ownership_class") == repo_policy.ownership_class
