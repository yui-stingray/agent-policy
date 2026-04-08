"""Where: src/agent_policy/decision.py
What: PolicyDecision — frozen dataclass returned by evaluate().
Why: the value object must be immutable and cheap to construct on every tool call.

The triplet (mode, reason, matched_repo) is the entire contract between
agent-policy and its callers. Downstream layers (agent-guard, Mission Control)
branch on these fields, so the literal sets are frozen in the MVP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Mode = Literal["deny", "require_approval", "auto_allow"]
"""Three-valued decision mode. No more, no less."""

Reason = Literal[
    "hard_guardrail",
    "repo_policy",
    "default_mode",
    "condition_match",
    "no_match",
]
"""Why this decision was reached. Frozen at five values for the MVP."""


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating a capability against a policy matrix.

    Attributes:
        mode: deny / require_approval / auto_allow.
        reason: which code path produced this decision.
        matched_repo: the repo string that matched, or None on fallback.
    """

    mode: Mode
    reason: Reason
    matched_repo: str | None = None
