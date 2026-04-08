"""Where: src/agent_policy/__init__.py
What: agent-policy public API.
Why: this is the entire surface that downstream code is allowed to import.
     If a name is not re-exported here, it is private.
"""

from __future__ import annotations

from .decision import Mode, PolicyDecision, Reason
from .evaluator import evaluate
from .guardrails import HARD_GUARDRAILS
from .loader import load_policy_file
from .matrix import PolicyMatrix, RepoPolicy

__all__ = [
    "evaluate",
    "load_policy_file",
    "PolicyMatrix",
    "RepoPolicy",
    "PolicyDecision",
    "HARD_GUARDRAILS",
    "Mode",
    "Reason",
]

__version__ = "0.1.1"
