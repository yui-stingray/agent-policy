"""Where: src/agent_policy/matrix.py
What: PolicyMatrix / RepoPolicy — the pydantic models a caller loads from TOML.
Why: keep schema validation at the edge; the evaluator trusts these models.

Design notes:
- `extra="forbid"` rejects typos in policy files loudly (fail-closed).
- `default_mode` defaults to `"require_approval"` on purpose. A missing
  `default_mode` field must never silently degrade to auto_allow.
- `RepoPolicy.capabilities` is a raw dict[str, str]; we do NOT enumerate
  the MVP taxonomy here, because the wrapper normalizes inputs and we
  want new capabilities in v0.2 to land without schema churn.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .decision import Mode


OwnershipClass = Literal["internal", "external"]
"""Closed ownership vocabulary for RepoPolicy gates."""


class RepoPolicy(BaseModel):
    """One repo's capability → mode mapping, plus optional ownership gate.

    ``ownership_class`` accepts only ``internal`` or ``external``. ``None``
    leaves the entry as an ownership wildcard.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    repo: str
    ownership_class: OwnershipClass | None = None
    capabilities: dict[str, Mode] = Field(default_factory=dict)


class PolicyMatrix(BaseModel):
    """The full policy document a caller loads once and reuses per call."""

    model_config = ConfigDict(extra="forbid")

    default_mode: Mode = "require_approval"
    repo_policy: list[RepoPolicy] = Field(default_factory=list)
