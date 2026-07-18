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


def test_github_release_does_not_interpolate_manual_tag_inside_shell_body() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    assert "INPUT_TAG: ${{ inputs.tag }}" in workflow
    assert workflow.count("${{ inputs.tag }}") == 1
    assert '[[ ! "$INPUT_TAG" =~ ^v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in workflow
    assert "manual release tag must match vX.Y.Z" in workflow
    assert "not ${tag}" not in workflow
    assert "not $INPUT_TAG" not in workflow


def test_github_release_manual_retry_uses_current_default_branch_verifier() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    default_branch_guard = workflow.index("Require default branch for manual retry")
    checkout = workflow.index("uses: actions/checkout@")
    tag_resolution = workflow.index("Resolve release tag")
    pypi_check = workflow.index("Verify PyPI release is present")
    assert default_branch_guard < checkout < tag_resolution < pypi_check
    assert "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}" in workflow
    assert 'if [ "$GITHUB_REF" != "refs/heads/${DEFAULT_BRANCH}" ]; then' in workflow
    assert "manual GitHub Release retry must run from the default branch" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "not $GITHUB_REF" not in workflow


def test_github_release_verifies_peeled_tag_sha_against_release_source() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    assert 'tag_sha="$(git rev-parse -q --verify "refs/tags/${tag}^{commit}")"' in workflow
    assert "echo \"sha=${tag_sha}\"" in workflow
    assert "Verify release source run" in workflow
    assert 'if [ "$RELEASE_SHA" != "$WORKFLOW_RUN_HEAD_SHA" ]; then' in workflow
    assert "--workflow release.yml" in workflow
    assert '--branch "$RELEASE_TAG"' in workflow
    assert '--commit "$RELEASE_SHA"' in workflow
    assert "--event push" in workflow
    assert "--status completed" in workflow
    assert 'select(.conclusion == "success")' in workflow
    assert "actions: read\n  contents: write" in workflow


def test_github_release_checks_pypi_before_detaching_for_release_notes() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    pypi_check = workflow.index("Verify PyPI release is present")
    detach = workflow.index("Detach checkout to release commit")
    changelog = workflow.index("Extract release notes")
    github_release = workflow.index("Create or update GitHub release")
    assert pypi_check < detach < changelog < github_release
    assert 'python scripts/check_pypi_release_state.py --require-present "$RELEASE_VERSION"' in workflow
    assert "for attempt in {1..5}; do" in workflow
    assert "waiting for API propagation" in workflow
    assert "sleep 10" in workflow
    assert 'git checkout --detach "$RELEASE_SHA"' in workflow
    assert 'python scripts/check_changelog.py --version "$RELEASE_VERSION" --write-notes release-notes.md' in workflow


def test_github_release_rechecks_remote_tag_immediately_before_publication() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    publish_step = workflow[workflow.index("Create or update GitHub release") :]
    assert 'git fetch --force --no-tags origin "refs/tags/${TAG_NAME}:${remote_tag_ref}"' in publish_step
    assert 'remote_tag_sha="$(git rev-parse "${remote_tag_ref}^{commit}")"' in publish_step
    assert 'if [ "$remote_tag_sha" != "$RELEASE_SHA" ]; then' in publish_step
    assert publish_step.index("release tag changed after verification") < publish_step.index(
        'gh release view "$TAG_NAME"'
    )
