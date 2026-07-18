"""Where: src/agent_policy/__init__.py
What: agent-policy public API.
Why: this is the entire surface that downstream code is allowed to import.
     If a name is not re-exported here, it is private.
"""

from __future__ import annotations

from .audit import (
    PolicyAuditEvent,
    audit_event_asdict,
    audit_event_to_json,
    build_audit_event,
)
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
    "PolicyAuditEvent",
    "build_audit_event",
    "audit_event_asdict",
    "audit_event_to_json",
    "HARD_GUARDRAILS",
    "Mode",
    "Reason",
]

__version__ = "0.1.9"
