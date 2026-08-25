"""Where: tests/test_packaging.py
What: packaging-level invariants that would otherwise drift silently.
Why: version is declared in two places (pyproject.toml [project].version and
     agent_policy.__version__), and PEP 561's py.typed marker is a single
     empty file whose absence breaks downstream type-checking without any
     runtime failure. Both are easy to forget during a release.

These tests do not exercise the evaluator — they exist purely to make
publish-time mistakes into test failures instead of silent regressions.
"""

from __future__ import annotations

import io
import sys
import tarfile
import tomllib
from pathlib import Path

import agent_policy
import pytest

from scripts import check_wheel_contract

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"
PACKAGE_DIR = REPO_ROOT / "src" / "agent_policy"
SCHEMA_DIR = PACKAGE_DIR / "schemas"
CURRENT_SOURCE_VERSION = "0.1.17.dev0"
LATEST_PUBLIC_VERSION = "0.1.16"


def _pyproject_version() -> str:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


def test_package_version_matches_pyproject() -> None:
    # Single source of truth is aspirational here; what matters at release
    # time is that the two declared values agree. If this fails, bump one
    # side to match the other before tagging.
    assert agent_policy.__version__ == _pyproject_version()


def test_unreleased_source_identity_matches_readme_install_contract() -> None:
    readme = README.read_text(encoding="utf-8")

    assert _pyproject_version() == CURRENT_SOURCE_VERSION
    assert agent_policy.__version__ == CURRENT_SOURCE_VERSION
    assert (
        f"**Status**: Unreleased source `{CURRENT_SOURCE_VERSION}`. "
        "The latest public PyPI release is"
    ) in readme
    assert f"`yui-agent-policy=={LATEST_PUBLIC_VERSION}`" in readme
    assert readme.count(f"pip install yui-agent-policy=={LATEST_PUBLIC_VERSION}") == 2
    assert "\npip install yui-agent-policy\n" not in readme


def test_readme_provenance_download_uses_expected_local_filenames() -> None:
    readme = README.read_text(encoding="utf-8")
    version = LATEST_PUBLIC_VERSION

    assert version != _pyproject_version()
    assert f'version = "{version}"' in readme
    assert readme.count(f"--source-ref refs/tags/v{version}") == 2
    assert "(\nset -euo pipefail\nverify_dir=\"$(mktemp -d" in readme
    assert "trap 'rm -rf -- \"$verify_dir\"' EXIT" in readme
    assert 'python - "$verify_dir"' in readme
    assert (
        f'gh attestation verify "$verify_dir/yui_agent_policy-{version}-py3-none-any.whl"'
        in readme
    )
    assert f'gh attestation verify "$verify_dir/yui_agent_policy-{version}.tar.gz"' in readme
    assert f"--source-ref refs/tags/v{version}\n)\n```" in readme
    assert 'target / file_info["filename"]' not in readme
    assert 'f"yui_agent_policy-{version}-py3-none-any.whl": "bdist_wheel"' in readme
    assert 'f"yui_agent_policy-{version}.tar.gz": "sdist"' in readme
    assert "if not isinstance(release, dict):" in readme
    assert 'file_info.get("packagetype") != expected[filename]' in readme
    assert 'file_info.get("yanked") is not False' in readme
    assert 'parsed.scheme != "https"' in readme
    assert 'parsed.hostname != "files.pythonhosted.org"' in readme
    assert "set(by_name) != set(expected)" in readme
    assert "for filename in sorted(expected):" in readme
    assert "request_timeout_seconds = 20" in readme
    assert readme.count("timeout=request_timeout_seconds") == 2
    assert "final_metadata_url = urlparse(response.geturl())" in readme
    assert 'final_metadata_url.scheme != "https"' in readme
    assert 'final_metadata_url.hostname != "pypi.org"' in readme
    assert "final_artifact_url = urlparse(response.geturl())" in readme
    assert 'final_artifact_url.scheme != "https"' in readme
    assert 'final_artifact_url.hostname != "files.pythonhosted.org"' in readme
    assert 'with (target / filename).open("xb") as destination:' in readme
    assert "shutil.copyfileobj(response, destination)" in readme
    assert "target / filename" in readme

    metadata_final = readme.index("final_metadata_url = urlparse(response.geturl())")
    metadata_rejection = readme.index(
        'raise SystemExit("PyPI release metadata URL is not an expected HTTPS host")'
    )
    metadata_parse = readme.index("release = json.load(response)")
    artifact_final = readme.index("final_artifact_url = urlparse(response.geturl())")
    artifact_rejection = readme.index(
        'raise SystemExit("Downloaded artifact URL is not an expected HTTPS host")'
    )
    destination_open = readme.index('with (target / filename).open("xb") as destination:')
    artifact_copy = readme.index("shutil.copyfileobj(response, destination)")
    assert metadata_final < metadata_rejection < metadata_parse
    assert artifact_final < artifact_rejection < destination_open < artifact_copy


def test_readme_positions_policy_as_optional_runtime_companion() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "`agent-guard`](https://github.com/yui-stingray/agent-guard) is the standalone" in readme
    assert "`agent-policy` is an optional advanced runtime companion" in readme
    assert "It is not required by `agent-guard` or a basic static setup." in readme
    assert "is a reference integration for projects that intentionally use both tools;" in readme
    assert "is not required setup." in readme


def test_readme_documents_wrapper_contract_summary() -> None:
    readme = README.read_text(encoding="utf-8")

    assert "### Wrapper contract summary" in readme
    assert "`evaluate()` performs no I/O" in readme
    assert "Wrappers own command parsing" in readme
    assert "`0` for `auto_allow`" in readme
    assert "`1` for wrapper/program errors" in readme
    assert "`2` for `require_approval`" in readme
    assert "`3` for" in readme and "`deny`" in readme
    assert "`--audit-event` emits deterministic evidence" in readme
    assert "it is not itself an approval record" in readme
    assert "agent_policy.schemas/agent-policy.audit_event.v1.schema.json" in readme
    assert "agent_policy.schemas/agent-policy.audit_event.v1.1.schema.json" in readme
    assert "importlib.resources" in readme
    assert "redacted before calling `build_audit_event()`" in readme
    assert "absolute local paths" in readme
    assert "Schema validation does not redact values" in readme
    assert "prove repository containment" in readme
    assert "audit-event schema validation" in readme


def test_readme_codex_hook_docs_match_current_contract() -> None:
    readme = README.read_text(encoding="utf-8")
    normalized = " ".join(readme.split())

    assert "features.codex_hooks" not in readme
    assert "features.hooks" in readme
    assert "default enabled" in readme
    assert "Bash, `apply_patch`, and MCP tool calls" in readme
    assert 'permissionDecision: "ask"' in readme
    assert "parsed but not supported" in readme
    assert "PermissionRequest" in readme
    assert "delegates to Codex's normal approval prompt" in readme
    assert "pathname expansion" in readme
    assert "bounded builtin allowlist" in readme
    assert "finite simple-command allowlist" in readme
    assert "`xargs` and `find -exec`-style argv generation" in readme
    assert "Active arithmetic is not interpreted" in readme
    assert "All leading assignments" in readme
    assert "callback-bearing builtins" in readme
    assert "xtrace" in readme
    assert "`GIT_SSH_COMMAND`" in readme
    assert "general interpreters and build/test runners" in readme
    assert "trailing arguments" in readme
    assert "redirections are not" in readme
    assert "Active output redirection at any statement position" in readme
    assert "ANSI-C" in readme and "quoted words" in readme
    assert "Every `push` or `send-pack` without an explicit visible" in readme
    assert "complete argv of a recognized Git command" in normalized


def test_packaged_audit_event_schemas_are_present() -> None:
    assert (SCHEMA_DIR / "__init__.py").is_file()
    for resource, title in [
        (
            "agent-policy.audit_event.v1.schema.json",
            '"agent-policy audit event v1"',
        ),
        (
            "agent-policy.audit_event.v1.1.schema.json",
            '"agent-policy audit event v1.1"',
        ),
    ]:
        schema = SCHEMA_DIR / resource
        assert schema.is_file(), f"missing packaged audit event schema: {schema}"
        assert title in schema.read_text(encoding="utf-8")


def test_py_typed_marker_is_present() -> None:
    # PEP 561: downstream type checkers only read inline types when a
    # zero-byte py.typed marker ships inside the package. If this file
    # disappears, `Typing :: Typed` in pyproject becomes a lie.
    marker = PACKAGE_DIR / "py.typed"
    assert marker.is_file(), f"missing PEP 561 marker: {marker}"
    # Must be empty per PEP 561; a non-empty file is technically allowed
    # but conventionally signals "partial" typing, which is not the intent.
    assert marker.stat().st_size == 0


def test_wheel_contract_installs_runtime_lock_before_local_wheel_without_deps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class TemporaryDirectory:
        def __enter__(self) -> str:
            return str(tmp_path)

        def __exit__(self, *_args: object) -> None:
            return None

    class EnvBuilder:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def create(self, _venv_dir: Path) -> None:
            pass

    commands: list[tuple[list[str], Path]] = []
    wheel = tmp_path / "dist" / "yui_agent_policy-0.0.0-py3-none-any.whl"
    sdist = tmp_path / "dist" / "yui_agent_policy-0.0.0.tar.gz"
    runtime_lock = tmp_path / "requirements" / "runtime-contract.txt"
    verified_sdists: list[tuple[Path, str]] = []

    def fake_run(command: list[str], *, cwd: Path) -> None:
        commands.append((command, cwd))

    monkeypatch.setattr(
        check_wheel_contract.tempfile,
        "TemporaryDirectory",
        lambda **_kwargs: TemporaryDirectory(),
    )
    monkeypatch.setattr(check_wheel_contract.venv, "EnvBuilder", EnvBuilder)
    monkeypatch.setattr(check_wheel_contract, "project_version", lambda: "0.0.0")
    monkeypatch.setattr(check_wheel_contract, "find_wheel", lambda _version: wheel)
    monkeypatch.setattr(check_wheel_contract, "find_sdist", lambda _version: sdist)
    monkeypatch.setattr(
        check_wheel_contract,
        "verify_sdist_examples",
        lambda path, version: verified_sdists.append((path, version)),
    )
    monkeypatch.setattr(check_wheel_contract, "RUNTIME_LOCK", runtime_lock)
    monkeypatch.setattr(
        check_wheel_contract,
        "dist_artifact_digests",
        lambda: {"yui_agent_policy-0.0.0-py3-none-any.whl": "digest"},
    )
    monkeypatch.setattr(check_wheel_contract, "run", fake_run)

    assert check_wheel_contract.main() == 0
    assert verified_sdists == [(sdist, "0.0.0")]

    python = tmp_path / "venv" / "bin" / "python"
    assert len(commands) == 4
    assert commands[0][0] == [
        str(python),
        "-m",
        "pip",
        "install",
        "--quiet",
        "--require-hashes",
        "--only-binary=:all:",
        "-r",
        str(runtime_lock),
    ]
    assert commands[1][0] == [
        str(python),
        "-m",
        "pip",
        "install",
        "--quiet",
        "--no-deps",
        str(wheel),
    ]
    assert commands[2][0] == [str(python), "-m", "pip", "check"]
    assert commands[3][0][:3] == [str(python), "-I", "-c"]
    assert all(cwd == tmp_path for _command, cwd in commands)


def test_distribution_contract_verifies_sdist_examples(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    version = "0.0.0"
    source_root = tmp_path / "source"
    sdist = tmp_path / f"yui_agent_policy-{version}.tar.gz"
    payloads: dict[str, bytes] = {}
    for relative, executable in check_wheel_contract.EXPECTED_SDIST_EXAMPLES.items():
        source = source_root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        payload = f"public example: {relative}\n".encode()
        source.write_bytes(payload)
        source.chmod(0o755 if executable else 0o644)
        payloads[relative] = payload

    with tarfile.open(sdist, mode="w:gz") as archive:
        for relative, payload in payloads.items():
            member = tarfile.TarInfo(f"yui_agent_policy-{version}/{relative}")
            member.size = len(payload)
            member.mode = (
                0o755
                if check_wheel_contract.EXPECTED_SDIST_EXAMPLES[relative]
                else 0o644
            )
            archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(check_wheel_contract, "ROOT", source_root)
    check_wheel_contract.verify_sdist_examples(sdist, version)


@pytest.mark.parametrize("mutation", ("missing", "content", "mode", "duplicate"))
@pytest.mark.parametrize(
    "mutation_target", ("examples/capability_map.py", "examples/check.py")
)
def test_distribution_contract_rejects_invalid_sdist_examples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mutation: str,
    mutation_target: str,
) -> None:
    version = "0.0.0"
    source_root = tmp_path / "source"
    sdist = tmp_path / f"yui_agent_policy-{version}.tar.gz"
    entries: list[tuple[str, bytes, int]] = []
    for relative, executable in check_wheel_contract.EXPECTED_SDIST_EXAMPLES.items():
        source = source_root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        payload = f"public example: {relative}\n".encode()
        source.write_bytes(payload)
        if mutation == "missing" and relative == mutation_target:
            continue
        archived = (
            b"changed\n"
            if mutation == "content" and relative == mutation_target
            else payload
        )
        mode = 0o755 if executable else 0o644
        if mutation == "mode" and relative == mutation_target:
            mode = 0o755 if mode == 0o644 else 0o644
        entries.append((relative, archived, mode))
        if mutation == "duplicate" and relative == mutation_target:
            entries.append((relative, b"duplicate\n", mode))

    with tarfile.open(sdist, mode="w:gz") as archive:
        for relative, payload, mode in entries:
            member = tarfile.TarInfo(f"yui_agent_policy-{version}/{relative}")
            member.size = len(payload)
            member.mode = mode
            archive.addfile(member, io.BytesIO(payload))

    monkeypatch.setattr(check_wheel_contract, "ROOT", source_root)
    with pytest.raises(RuntimeError, match="sdist"):
        check_wheel_contract.verify_sdist_examples(sdist, version)


@pytest.mark.parametrize("mutation", ("content", "set"))
def test_wheel_contract_rejects_dist_artifact_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutation: str
) -> None:
    monkeypatch.setattr(check_wheel_contract, "DIST", tmp_path)
    artifact = tmp_path / "yui_agent_policy-0.0.0-py3-none-any.whl"
    artifact.write_bytes(b"original artifact")
    before = check_wheel_contract.dist_artifact_digests()

    if mutation == "content":
        artifact.write_bytes(b"altered artifact")
    else:
        (tmp_path / "added-artifact.tar.gz").write_bytes(b"unexpected artifact")

    with pytest.raises(
        RuntimeError, match="distribution artifacts changed during wheel contract"
    ):
        check_wheel_contract.verify_dist_artifacts_unchanged(before)
