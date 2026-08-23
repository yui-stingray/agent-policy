"""Where: src/agent_policy/matrix.py
What: PolicyMatrix / RepoPolicy — the pydantic models a caller loads from TOML.
Why: reject invalid policy state at load and evaluation boundaries.

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

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


def _validate_repo_policy_consistency(repo_policy: list[RepoPolicy]) -> None:
    """Reject order-dependent decisions from overlapping repo rules."""
    for left_index, left in enumerate(repo_policy):
        for right_index, right in enumerate(
            repo_policy[left_index + 1 :], start=left_index + 1
        ):
            if left.repo != right.repo:
                continue
            if (
                left.ownership_class is not None
                and right.ownership_class is not None
                and left.ownership_class != right.ownership_class
            ):
                continue

            for capability in sorted(
                left.capabilities.keys() & right.capabilities.keys()
            ):
                if left.capabilities[capability] == right.capabilities[capability]:
                    continue
                raise ValueError(
                    "repo_policy entries "
                    f"{left_index} and {right_index} overlap with conflicting "
                    f"modes for repo {left.repo!r} and capability {capability!r}"
                )


class PolicyMatrix(BaseModel):
    """The full policy document a caller loads once and reuses per call."""

    model_config = ConfigDict(extra="forbid")

    default_mode: Mode = "require_approval"
    repo_policy: list[RepoPolicy] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_conflicting_overlapping_rules(self) -> PolicyMatrix:
        _validate_repo_policy_consistency(self.repo_policy)
        return self
