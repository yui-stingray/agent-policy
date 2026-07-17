"""Regression tests for CI and release supply-chain hardening."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = {
    "ci": ROOT / ".github" / "workflows" / "ci.yml",
    "release": ROOT / ".github" / "workflows" / "release.yml",
    "github-release": ROOT / ".github" / "workflows" / "github-release.yml",
}
USES_PATTERN = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)", re.MULTILINE)

PINNED_ACTIONS = {
    "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
    "devops-actions/actionlint@469810fd82c015d3c43815cd2b0e4d02eecc4819",
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/attest@a1948c3f048ba23858d222213b7c278aabede763",
    "pypa/gh-action-pypi-publish@cef221092ed1bacb1cc03d23a2d87d1d172e277b",
}


def test_external_workflow_actions_are_full_sha_pinned() -> None:
    references: list[str] = []
    for path in WORKFLOWS.values():
        references.extend(USES_PATTERN.findall(path.read_text(encoding="utf-8")))

    assert references
    for reference in references:
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference), reference
    for expected in PINNED_ACTIONS:
        assert expected in references


def test_workflows_force_javascript_actions_to_node24() -> None:
    for path in WORKFLOWS.values():
        text = path.read_text(encoding="utf-8")
        assert 'FORCE_JAVASCRIPT_ACTIONS_TO_NODE24: "true"' in text


def test_ci_declares_read_only_permissions() -> None:
    workflow = WORKFLOWS["ci"].read_text(encoding="utf-8")

    assert "permissions:\n  contents: read\n\njobs:" in workflow


def test_checkout_steps_do_not_persist_credentials() -> None:
    checkout = "uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0"
    checkout_count = 0
    persistence_count = 0
    for path in WORKFLOWS.values():
        workflow = path.read_text(encoding="utf-8")
        checkout_count += workflow.count(checkout)
        persistence_count += workflow.count("persist-credentials: false")

    assert checkout_count == 4
    assert persistence_count == checkout_count


def test_release_preflight_requires_current_master_push_ci_success() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    assert "Require current protected master with successful CI" in workflow
    assert "git fetch --no-tags origin master:refs/remotes/origin/master" in workflow
    assert "--workflow ci.yml" in workflow
    assert "--branch master" in workflow
    assert "--commit \"$GITHUB_SHA\"" in workflow
    assert "--event push" in workflow
    assert "--status completed" in workflow
    assert "python scripts/check_release_source.py" in workflow


def test_release_attests_only_publish_paths_between_build_and_publish() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    attest_start = workflow.index("\n  attest:\n") + 1
    publish_start = workflow.index("\n  publish:\n", attest_start) + 1
    attest_job = workflow[attest_start:publish_start]
    attest = "uses: actions/attest@a1948c3f048ba23858d222213b7c278aabede763"
    download = "uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    upload = "uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
    assert workflow.index(upload) < attest_start
    assert attest_job.index(download) < attest_job.index(attest)
    assert "needs: build" in attest_job
    assert "subject-path: dist/*" in attest_job
    assert (
        "if: github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && inputs.publish)"
    ) in attest_job
    assert "needs: [build, attest]" in workflow[publish_start:]


def test_release_permissions_keep_oidc_scoped_to_publish_and_attestation() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    build_start = workflow.index("\n  build:\n") + 1
    attest_start = workflow.index("\n  attest:\n", build_start) + 1
    publish_start = workflow.index("\n  publish:\n", attest_start) + 1
    build_job = workflow[build_start:attest_start]
    attest_job = workflow[attest_start:publish_start]
    publish_job = workflow[publish_start:]
    assert "permissions:\n  actions: read\n  contents: read" in workflow
    assert "id-token: write" not in build_job
    assert "attestations: write" not in build_job
    assert "artifact-metadata: write" not in build_job
    assert "permissions:\n      actions: read\n      contents: read" in build_job
    assert "id-token: write" in attest_job
    assert "attestations: write" in attest_job
    assert "artifact-metadata: write" in attest_job
    assert "id-token: write" in publish_job
    assert "attestations: write" not in publish_job
