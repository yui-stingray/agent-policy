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

import sys
import tomllib
from pathlib import Path

import agent_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"
PACKAGE_DIR = REPO_ROOT / "src" / "agent_policy"
SCHEMA_DIR = PACKAGE_DIR / "schemas"


def _pyproject_version() -> str:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return data["project"]["version"]


def test_package_version_matches_pyproject() -> None:
    # Single source of truth is aspirational here; what matters at release
    # time is that the two declared values agree. If this fails, bump one
    # side to match the other before tagging.
    assert agent_policy.__version__ == _pyproject_version()


def test_readme_status_matches_pyproject_version() -> None:
    # README is the first thing PyPI users see. Keep the advertised alpha
    # version aligned with the package metadata so release notes do not drift.
    assert f"**Status**: `{_pyproject_version()}` alpha." in README.read_text(encoding="utf-8")


def test_readme_provenance_download_uses_expected_local_filenames() -> None:
    readme = README.read_text(encoding="utf-8")
    version = _pyproject_version()

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

    assert "features.codex_hooks" not in readme
    assert "features.hooks" in readme
    assert "default enabled" in readme
    assert "Bash, `apply_patch`, and MCP tool calls" in readme
    assert 'permissionDecision: "ask"' in readme
    assert "parsed but not supported" in readme
    assert "PermissionRequest" in readme
    assert "delegates to Codex's normal approval prompt" in readme


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
