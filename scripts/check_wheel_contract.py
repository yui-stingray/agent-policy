"""Where: scripts/check_wheel_contract.py
What: verify the built sdist examples and install the wheel in an isolated venv.
Why: editable installs can hide packaging mistakes; releases must prove both artifacts.
"""

from __future__ import annotations

import hashlib
import subprocess
import tarfile
import tempfile
import textwrap
import tomllib
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
RUNTIME_LOCK = ROOT / "requirements" / "runtime-contract.txt"
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
EXPECTED_SDIST_EXAMPLES = {
    "examples/capability_map.py": False,
    "examples/check.py": False,
    "examples/claude_code_hook.sh": True,
    "examples/codex_hook.sh": True,
    "examples/codex_permission_request_hook.sh": True,
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


def find_sdist(version: str) -> Path:
    """Return the built sdist for the current project version."""
    sdists = sorted(DIST.glob(f"yui_agent_policy-{version}.tar.gz"))
    if len(sdists) != 1:
        raise RuntimeError(
            f"expected exactly one yui_agent_policy {version} sdist in dist, got {len(sdists)}"
        )
    return sdists[0]


def verify_sdist_examples(sdist: Path, version: str) -> None:
    """Require the public examples to match source bytes and hook modes."""
    prefix = f"yui_agent_policy-{version}/"
    with tarfile.open(sdist, mode="r:gz") as archive:
        members = archive.getmembers()
        for relative, executable in EXPECTED_SDIST_EXAMPLES.items():
            archive_name = prefix + relative
            matches = [member for member in members if member.name == archive_name]
            if len(matches) != 1 or not matches[0].isfile():
                raise RuntimeError(
                    f"sdist must contain exactly one required example: {relative}"
                )
            member = matches[0]
            extracted = archive.extractfile(member)
            if extracted is None or extracted.read() != (ROOT / relative).read_bytes():
                raise RuntimeError(f"sdist example differs from source: {relative}")
            if bool(member.mode & 0o111) != executable:
                raise RuntimeError(f"sdist example has unexpected executable mode: {relative}")


def run(command: list[str], *, cwd: Path) -> None:
    """Run a subprocess and fail with its native exit code."""
    subprocess.run(command, cwd=cwd, check=True)


def dist_artifact_digests() -> dict[str, str]:
    """Return SHA-256 digests for the complete top-level dist artifact set."""
    digests: dict[str, str] = {}
    for artifact in sorted(DIST.iterdir()):
        if artifact.is_symlink() or not artifact.is_file():
            raise RuntimeError("distribution artifacts contain an unsupported entry")
        digest = hashlib.sha256()
        with artifact.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        digests[artifact.name] = digest.hexdigest()
    return digests


def verify_dist_artifacts_unchanged(expected: dict[str, str]) -> None:
    """Reject any distribution artifact content or set change during the smoke."""
    if dist_artifact_digests() != expected:
        raise RuntimeError("distribution artifacts changed during wheel contract")


def main() -> int:
    """Verify the built wheel in an isolated environment."""

    version = project_version()
    wheel = find_wheel(version)
    sdist = find_sdist(version)
    artifacts_before_smoke = dist_artifact_digests()
    try:
        verify_sdist_examples(sdist, version)
        with tempfile.TemporaryDirectory(prefix="agent-policy-wheel-") as temp_dir:
            temp = Path(temp_dir)
            venv_dir = temp / "venv"
            venv.EnvBuilder(with_pip=True).create(venv_dir)
            python = venv_dir / "bin" / "python"
            run(
                [
                    str(python),
                    "-m",
                    "pip",
                    "install",
                    "--quiet",
                    "--require-hashes",
                    "--only-binary=:all:",
                    "-r",
                    str(RUNTIME_LOCK),
                ],
                cwd=temp,
            )
            run(
                [str(python), "-m", "pip", "install", "--quiet", "--no-deps", str(wheel)],
                cwd=temp,
            )
            run([str(python), "-m", "pip", "check"], cwd=temp)
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
            for property_name in ("repo", "capability", "context"):
                assert schema_v1_1["properties"][property_name] == schema["properties"][
                    property_name
                ]
            decision_v1 = schema["properties"]["decision"]
            decision_v1_1 = schema_v1_1["properties"]["decision"]
            assert decision_v1_1["required"] == decision_v1["required"]
            assert decision_v1_1["additionalProperties"] == decision_v1["additionalProperties"]
            assert set(decision_v1_1["properties"]) == set(decision_v1["properties"])
            for property_name in ("mode", "reason"):
                assert decision_v1_1["properties"][property_name] == decision_v1["properties"][
                    property_name
                ]
            assert decision_v1_1["properties"]["matched_repo"] == {{
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
    finally:
        verify_dist_artifacts_unchanged(artifacts_before_smoke)

    print(f"distribution contract OK: {wheel.name}, {sdist.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
