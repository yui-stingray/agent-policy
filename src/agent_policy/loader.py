"""Where: src/agent_policy/loader.py
What: load_policy_file — TOML on disk → PolicyMatrix.
Why: keep I/O isolated in one tiny module so the evaluator stays pure.

MVP deliberately supports **TOML only**. YAML was considered and rejected:
adding PyYAML pulls a C extension and a dependency; TOML ships in the
stdlib (tomllib) from Python 3.11+ and covers every shape we need.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from .matrix import PolicyMatrix


def load_policy_file(path: str | Path) -> PolicyMatrix:
    """Parse a TOML policy file and return a validated PolicyMatrix.

    Raises:
        FileNotFoundError: the path does not exist.
        tomllib.TOMLDecodeError: the file is not valid TOML.
        pydantic.ValidationError: the document does not match the schema
            (unknown fields are rejected because extra="forbid").
    """
    with Path(path).open("rb") as handle:
        payload = tomllib.load(handle)
    return PolicyMatrix.model_validate(payload)
