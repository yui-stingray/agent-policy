"""Where: scripts/check_wheel_contract.py
What: install the built wheel into an isolated venv and verify the public contract.
Why: editable installs can hide packaging mistakes; releases must prove the wheel works.
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
import tomllib
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXPECTED_EXPORTS = {
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
}


def project_version() -> str:
    """Return pyproject.toml [project].version."""
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def find_wheel(version: str) -> Path:
    """Return the built wheel for the current project version."""
    wheels = sorted(DIST.glob(f"yui_agent_policy-{version}-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(
            f"expected exactly one yui_agent_policy {version} wheel in {DIST}, got {len(wheels)}"
        )
    return wheels[0]


def run(command: list[str], *, cwd: Path) -> None:
    """Run a subprocess and fail with its native exit code."""
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    version = project_version()
    wheel = find_wheel(version)
    with tempfile.TemporaryDirectory(prefix="agent-policy-wheel-") as temp_dir:
        temp = Path(temp_dir)
        venv_dir = temp / "venv"
        venv.EnvBuilder(with_pip=True).create(venv_dir)
        python = venv_dir / "bin" / "python"
        run([str(python), "-m", "pip", "install", "--quiet", str(wheel)], cwd=temp)
        smoke = textwrap.dedent(
            f"""
            import agent_policy
            from importlib import resources
            import json
            from pathlib import Path
            import sysconfig
            from agent_policy import (
                PolicyDecision,
                PolicyMatrix,
                RepoPolicy,
                audit_event_to_json,
                build_audit_event,
                evaluate,
            )

            purelib = Path(sysconfig.get_path("purelib")).resolve()
            assert Path(agent_policy.__file__).resolve().is_relative_to(purelib)
            expected_exports = {sorted(EXPECTED_EXPORTS)!r}
            assert sorted(agent_policy.__all__) == expected_exports
            assert agent_policy.__version__ == {version!r}
            decision = evaluate(
                PolicyMatrix(
                    default_mode="deny",
                    repo_policy=[RepoPolicy(repo="myorg/myrepo", capabilities={{"read": "auto_allow"}})],
                ),
                repo="myorg/myrepo",
                capability="read",
                context={{}},
            )
            assert decision == PolicyDecision(
                mode="auto_allow",
                reason="repo_policy",
                matched_repo="myorg/myrepo",
            )
            event = build_audit_event(
                repo="myorg/myrepo",
                capability="read",
                context={{"ownership_class": "internal"}},
                decision=decision,
            )
            assert json.loads(audit_event_to_json(event))["decision"]["mode"] == "auto_allow"
            schema = json.loads(
                resources.files("agent_policy.schemas")
                .joinpath("agent-policy.audit_event.v1.schema.json")
                .read_text(encoding="utf-8")
            )
            assert schema["title"] == "agent-policy audit event v1"
            assert schema["properties"]["session_id"] == {{"type": "string"}}
            assert schema["properties"]["command"] == {{"type": "string"}}
            assert schema["properties"]["path"] == {{"type": "string"}}
            assert schema["properties"]["decision"]["properties"]["mode"]["enum"] == [
                "deny",
                "require_approval",
                "auto_allow",
            ]
            schema_v1_1 = json.loads(
                resources.files("agent_policy.schemas")
                .joinpath("agent-policy.audit_event.v1.1.schema.json")
                .read_text(encoding="utf-8")
            )
            assert schema_v1_1["title"] == "agent-policy audit event v1.1"
            assert schema_v1_1["required"] == schema["required"]
            assert schema_v1_1["additionalProperties"] == schema["additionalProperties"]
            assert set(schema_v1_1["properties"]) == set(schema["properties"])
            assert schema_v1_1["properties"]["decision"]["required"] == schema["properties"][
                "decision"
            ]["required"]
            assert schema_v1_1["properties"]["decision"]["additionalProperties"] == schema[
                "properties"
            ]["decision"]["additionalProperties"]
            assert schema_v1_1["properties"]["decision"]["properties"]["mode"][
                "enum"
            ] == schema["properties"]["decision"]["properties"]["mode"]["enum"]
            assert schema_v1_1["properties"]["decision"]["properties"]["reason"][
                "enum"
            ] == schema["properties"]["decision"]["properties"]["reason"]["enum"]
            assert schema_v1_1["properties"]["decision"]["properties"]["matched_repo"] == {{
                "type": ["string", "null"],
                "minLength": 1,
                "maxLength": 256,
            }}
            assert schema_v1_1["properties"]["session_id"] == {{
                "type": "string",
                "minLength": 1,
                "maxLength": 256,
                "pattern": "^[A-Za-z0-9._:@/+~-]+$(?![\\\\s\\\\S])",
            }}
            assert schema_v1_1["properties"]["command"] == {{
                "type": "string",
                "minLength": 1,
                "maxLength": 4096,
                "pattern": "^[^\\\\u0000-\\\\u001F]+$(?![\\\\s\\\\S])",
            }}
            assert schema_v1_1["properties"]["path"] == {{
                "type": "string",
                "minLength": 1,
                "maxLength": 1024,
                "pattern": "^[^/\\\\u0000-\\\\u001F][^\\\\u0000-\\\\u001F]*$(?![\\\\s\\\\S])",
            }}
            """
        )
        run([str(python), "-I", "-c", smoke], cwd=temp)

    print(f"wheel contract OK: {wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
