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
from types import MappingProxyType
from typing import Any, Final

from .decision import PolicyDecision

# Evaluation reads this private, immutable mapping rather than the public
# compatibility view below. Keeping the evaluator's authority separate from
# the exported name means callers can inspect the documented guardrails but
# cannot weaken force-push denial through a mutable public object.
_EVALUATION_HARD_GUARDRAILS: Final[Mapping[str, str]] = MappingProxyType(
    {"push.force": "deny"}
)

# Public compatibility copy. Existing callers retain the documented ``dict``
# behavior, including JSON serialization, but mutation or rebinding cannot
# affect the private immutable state used by evaluation.
HARD_GUARDRAILS: Final[dict[str, str]] = dict(_EVALUATION_HARD_GUARDRAILS)
"""Inspectable unconditional guardrails; evaluation uses a private copy."""


# Capabilities that mutate repo state. The first-write guardrail must not
# fire for non-mutating operations like `read` — reading an unfamiliar repo
# is not itself a reason to stop for approval.
_MUTATING_CAPABILITIES: frozenset[str] = frozenset(
    {"write", "commit", "push", "push.force", "merge.pr", "shell"}
)


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
    if capability in _EVALUATION_HARD_GUARDRAILS:
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
    # Only applies to mutating capabilities — `read` is safe and must not
    # be blocked just because it is the first interaction with an external
    # repo. Context key name stays `first_write_to_repo` to make the
    # intent obvious at call sites.
    if (
        capability in _MUTATING_CAPABILITIES
        and context.get("ownership_class") == "external"
        and context.get("first_write_to_repo")
    ):
        return PolicyDecision(
            mode="require_approval",
            reason="hard_guardrail",
            matched_repo=repo,
        )

    return None
