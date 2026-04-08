"""Where: src/agent_policy/guardrails.py
What: hard guardrails — capability → mode entries that repo_policy cannot override.
Why: a small, auditable list is the whole point. If this file grows, reject the PR.

Scope rules (do not expand without a v0.2 bump):
- Only add a guardrail when the operation is unambiguously dangerous
  regardless of repo, actor, or condition.
- Conditional guardrails (evaluated in _evaluate_hard_guardrails) must stay
  small and documented; they exist because some risks depend on context
  (e.g. first write by an external actor).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .decision import PolicyDecision

HARD_GUARDRAILS: dict[str, str] = {
    "push.force": "deny",
}
"""Unconditional guardrails. Repo policies cannot override these."""


def _evaluate_hard_guardrails(
    repo: str,
    capability: str,
    context: Mapping[str, Any],
) -> PolicyDecision | None:
    """Return a guardrail decision, or None to fall through to repo_policy.

    Order matters: unconditional entries first, then conditional.
    Conditional guardrails always yield `require_approval`, never `deny`,
    because context can be wrong or stale and a hard block is unrecoverable.
    """
    if capability in HARD_GUARDRAILS:
        return PolicyDecision(
            mode="deny",  # only "deny" lives in HARD_GUARDRAILS today
            reason="hard_guardrail",
            matched_repo=repo,
        )

    # merge.pr is always a human decision in the MVP.
    if capability == "merge.pr":
        return PolicyDecision(
            mode="require_approval",
            reason="hard_guardrail",
            matched_repo=repo,
        )

    # First write by an external-ownership actor gets a human in the loop.
    if (
        context.get("ownership_class") == "external"
        and context.get("first_write_to_repo")
    ):
        return PolicyDecision(
            mode="require_approval",
            reason="hard_guardrail",
            matched_repo=repo,
        )

    return None
