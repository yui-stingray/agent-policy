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
LOCKED_REQUIREMENT_PATTERN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s\\]+)(?:\s+\\)?$"
)
SHA256_HASH_PATTERN = re.compile(r"^--hash=sha256:[0-9a-f]{64}(?:\s+\\)?$")
RELEASE_TOOLS_INPUT = ROOT / "requirements" / "release-tools.in"
RELEASE_TOOLS_LOCK = ROOT / "requirements" / "release-tools.txt"
RUNTIME_CONTRACT_INPUT = ROOT / "requirements" / "runtime-contract.in"
RUNTIME_CONTRACT_LOCK = ROOT / "requirements" / "runtime-contract.txt"
RELEASE_DIRECT_TOOL_VERSIONS = {
    "build": "1.5.0",
    "twine": "7.0.0",
    "hatchling": "1.31.0",
}
RUNTIME_DIRECT_DEPENDENCY_VERSIONS = {"pydantic": "2.13.4"}
RUNTIME_CONTRACT_DEPENDENCIES = {
    "annotated-types",
    "pydantic",
    "pydantic-core",
    "typing-extensions",
    "typing-inspection",
}
TOOLKIT_COMPATIBILITY_COMMIT = "8ea48dc9926c55ac70af7a623c3ebcd8b35178c9"

PINNED_ACTIONS = {
    "actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0",
    "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1",
    "devops-actions/actionlint@469810fd82c015d3c43815cd2b0e4d02eecc4819",
    "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
    "actions/attest@a1948c3f048ba23858d222213b7c278aabede763",
    "pypa/gh-action-pypi-publish@dc37677b2e1c63e2034f94d8a5b11f265b73ba33",
}


def _canonical_requirement_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_requirements(path: Path) -> dict[str, tuple[str, list[str]]]:
    entries: dict[str, tuple[str, list[str]]] = {}
    current_name: str | None = None
    current_version: str | None = None
    current_hashes: list[str] = []

    def add_current_entry() -> None:
        if current_name is not None and current_version is not None:
            assert current_name not in entries
            entries[current_name] = (current_version, current_hashes)

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        requirement_match = LOCKED_REQUIREMENT_PATTERN.fullmatch(line)
        if requirement_match is not None:
            add_current_entry()
            current_name = _canonical_requirement_name(requirement_match["name"])
            current_version = requirement_match["version"]
            current_hashes = []
        elif SHA256_HASH_PATTERN.fullmatch(line) is not None:
            assert current_name is not None
            current_hashes.append(line)
        elif not line or line.startswith("#") or line == "--only-binary :all:":
            continue
        else:
            raise AssertionError(f"unexpected release tool lock directive: {line}")
    add_current_entry()
    return entries


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

    assert checkout_count == 8
    assert persistence_count == checkout_count


def test_release_tool_lock_hash_pins_the_release_build_toolchain() -> None:
    input_requirements = {
        _canonical_requirement_name(name): version
        for raw_line in RELEASE_TOOLS_INPUT.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
        for name, version in [line.split("==", 1)]
    }
    lock_text = RELEASE_TOOLS_LOCK.read_text(encoding="utf-8")
    lock_entries = _locked_requirements(RELEASE_TOOLS_LOCK)

    assert input_requirements == RELEASE_DIRECT_TOOL_VERSIONS
    assert "--only-binary :all:" in lock_text
    assert {
        name: lock_entries[name][0] for name in RELEASE_DIRECT_TOOL_VERSIONS
    } == RELEASE_DIRECT_TOOL_VERSIONS
    assert lock_entries
    assert all(hashes for _version, hashes in lock_entries.values())


def test_runtime_contract_lock_hash_pins_complete_wheel_dependencies() -> None:
    input_requirements = {
        _canonical_requirement_name(name): version
        for raw_line in RUNTIME_CONTRACT_INPUT.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
        for name, version in [line.split("==", 1)]
    }
    lock_text = RUNTIME_CONTRACT_LOCK.read_text(encoding="utf-8")
    lock_entries = _locked_requirements(RUNTIME_CONTRACT_LOCK)

    assert input_requirements == RUNTIME_DIRECT_DEPENDENCY_VERSIONS
    assert "# This file is autogenerated by pip-compile with Python 3.12" in lock_text
    assert "--only-binary :all:" in lock_text
    assert {
        name: lock_entries[name][0] for name in RUNTIME_DIRECT_DEPENDENCY_VERSIONS
    } == RUNTIME_DIRECT_DEPENDENCY_VERSIONS
    assert set(lock_entries) == RUNTIME_CONTRACT_DEPENDENCIES
    assert all(hashes for _version, hashes in lock_entries.values())


def test_ci_and_release_builds_use_the_hashed_nonisolated_toolchain() -> None:
    ci_workflow = WORKFLOWS["ci"].read_text(encoding="utf-8")
    release_workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    ci_job = ci_workflow[
        ci_workflow.index("\n  release-contract:\n") : ci_workflow.index(
            "\n  test:\n", ci_workflow.index("\n  release-contract:\n")
        )
    ]
    release_job = release_workflow[
        release_workflow.index("\n  build:\n") : release_workflow.index(
            "\n  attest:\n", release_workflow.index("\n  build:\n")
        )
    ]

    for workflow_job, build_step in (
        (ci_job, "Build sdist + wheel"),
        (release_job, "Build distributions"),
    ):
        install_command = (
            "python -m pip install --require-hashes --only-binary=:all:\n"
            "          -r requirements/release-tools.txt"
        )
        assert "python-version: '3.12'" in workflow_job
        assert install_command in workflow_job
        assert workflow_job.count("python -m pip install") == 1
        assert "python -m pip check" in workflow_job
        assert "python -m build --no-isolation" in workflow_job
        assert "python -m build\n" not in workflow_job
        assert "pip install --upgrade build twine" not in workflow_job
        assert workflow_job.index(install_command) < workflow_job.index(
            "python -m pip check"
        ) < workflow_job.index("python -m build --no-isolation")
        assert workflow_job.index(build_step) < workflow_job.index(
            "Verify metadata (twine check)"
        ) < workflow_job.index("Verify distribution public contract")


def test_candidate_wheel_gate_cannot_replace_validated_release_artifact() -> None:
    ci_workflow = WORKFLOWS["ci"].read_text(encoding="utf-8")
    release_workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    ci_job = ci_workflow[
        ci_workflow.index("\n  release-contract:\n") : ci_workflow.index(
            "\n  test:\n", ci_workflow.index("\n  release-contract:\n")
        )
    ]
    release_job = release_workflow[
        release_workflow.index("\n  build:\n") : release_workflow.index(
            "\n  attest:\n", release_workflow.index("\n  build:\n")
        )
    ]
    checkout_contract = (
        "repository: yui-stingray/agent-safety-toolkit-example\n"
        f"          ref: {TOOLKIT_COMPATIBILITY_COMMIT}\n"
        "          path: .candidate-toolkit\n"
        "          persist-credentials: false"
    )
    gate_command = (
        "python .candidate-toolkit/scripts/check_candidate_wheel_compatibility.py\n"
        "          --wheel dist/yui_agent_policy-*.whl"
    )

    for workflow_job in (ci_job, release_job):
        assert workflow_job.count("Checkout exact Toolkit compatibility contract") == 1
        assert workflow_job.count(checkout_contract) == 1
        assert workflow_job.count("Verify Toolkit candidate compatibility") == 1
        assert workflow_job.count(gate_command) == 1
        assert workflow_job.index("Verify distribution public contract") < workflow_job.index(
            "Checkout exact Toolkit compatibility contract"
        ) < workflow_job.index("Verify Toolkit candidate compatibility")

    contract_index = release_job.index("Verify distribution public contract")
    upload_index = release_job.index("Upload validated distributions")
    checkout_index = release_job.index("Checkout exact Toolkit compatibility contract")
    gate_index = release_job.index("Verify Toolkit candidate compatibility")
    assert contract_index < upload_index < checkout_index < gate_index
    assert release_job.count("Upload validated distributions") == 1
    assert release_job.count("actions/upload-artifact@") == 1


def test_ci_exposes_one_stable_required_aggregate() -> None:
    workflow = WORKFLOWS["ci"].read_text(encoding="utf-8")
    required_job = workflow[workflow.index("\n  required-ci:\n") :]

    assert "name: agent-policy required CI" in required_job
    assert "if: ${{ always() }}" in required_job
    for dependency in ("actionlint", "release-contract", "test"):
        assert f"      - {dependency}" in required_job
    for result in (
        "needs.actionlint.result",
        "needs.release-contract.result",
        "needs.test.result",
    ):
        assert result in required_job
    assert required_job.count('= "success"') == 3


def test_release_preflight_requires_current_master_push_ci_success() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    assert "Require annotated tag at current protected master with successful CI" in workflow
    assert 'tag_ref="$GITHUB_REF"' in workflow
    assert '[[ ! "$tag_ref" =~ ^refs/tags/v[0-9]+\\.[0-9]+\\.[0-9]+$ ]]' in workflow
    assert (
        'tag_object_sha="$(git rev-parse -q --verify "$tag_ref" 2>/dev/null)"'
        in workflow
    )
    assert 'tag_object_type="$(git cat-file -t "$tag_object_sha" 2>/dev/null)"' in workflow
    assert (
        'tag_sha="$(git rev-parse -q --verify "${tag_ref}^{commit}" 2>/dev/null)"'
        in workflow
    )
    assert "git fetch --no-tags origin master:refs/remotes/origin/master" in workflow
    assert 'master_sha="$(git rev-parse -q --verify "origin/master^{commit}" 2>/dev/null)"' in workflow
    assert 'if [ "$tag_sha" != "$GITHUB_SHA" ]; then' in workflow
    assert "--workflow ci.yml" in workflow
    assert "--branch master" in workflow
    assert '--commit "$tag_sha"' in workflow
    assert "--event push" in workflow
    assert "--status completed" in workflow
    assert "python scripts/check_release_source.py" in workflow
    assert '--tag-object-type "$tag_object_type"' in workflow
    assert '--tag-sha "$tag_sha"' in workflow
    assert '--master-sha "$master_sha"' in workflow
    assert "v0.1.6" not in workflow


def test_release_workflow_does_not_echo_untrusted_refs_in_request_errors() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")

    assert "manual publish=true must be run against a v* tag ref, not" not in workflow
    assert 'echo "release request accepted for ${GITHUB_REF}"' not in workflow


def test_release_attests_only_publish_paths_between_build_and_publish() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    attest_start = workflow.index("\n  attest:\n") + 1
    publish_start = workflow.index("\n  publish:\n", attest_start) + 1
    attest_job = workflow[attest_start:publish_start]
    attest = "uses: actions/attest@a1948c3f048ba23858d222213b7c278aabede763"
    download = (
        "uses: actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
    )
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
    verify_start = workflow.index("\n  verify-published:\n", publish_start) + 1
    build_job = workflow[build_start:attest_start]
    attest_job = workflow[attest_start:publish_start]
    publish_job = workflow[publish_start:verify_start]
    verify_job = workflow[verify_start:]
    assert "permissions:\n  actions: read\n  contents: read" in workflow
    assert "id-token: write" not in build_job
    assert "attestations: write" not in build_job
    assert "artifact-metadata: write" not in build_job
    assert "permissions:\n      actions: read\n      contents: read" in build_job
    assert "id-token: write" in attest_job
    assert "attestations: write" in attest_job
    assert "artifact-metadata: write" in attest_job
    assert "id-token: write" in publish_job
    assert "contents: read" in publish_job
    assert "attestations: write" not in publish_job
    assert "needs: publish" in verify_job
    assert "# Read-only post-publication verification" in verify_job
    assert "actions: read" in verify_job
    assert "contents: read" in verify_job
    assert "id-token: write" not in verify_job
    assert "attestations: write" not in verify_job


def test_release_publisher_is_terminal_and_uses_reviewed_v1_14_2_identity() -> None:
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    publish_start = workflow.index("\n  publish:\n")
    verify_start = workflow.index("\n  verify-published:\n", publish_start)
    publish_job = workflow[publish_start:verify_start]

    publisher = (
        "uses: pypa/gh-action-pypi-publish@"
        "dc37677b2e1c63e2034f94d8a5b11f265b73ba33  # v1.14.2"
    )
    assert "Reviewed identity: v1.14.2" in publish_job
    assert "Metadata-Version 2.5 compatibility" in publish_job
    assert publisher in publish_job
    assert publish_job.rstrip().endswith(publisher)
    assert "actions/setup-python@" not in publish_job
    assert "check_pypi_release_state.py" not in publish_job
    assert "pip install" not in publish_job


def test_release_verifies_exact_file_set_and_install_after_publish_without_oidc() -> (
    None
):
    workflow = WORKFLOWS["release"].read_text(encoding="utf-8")
    verify_job = workflow[workflow.index("\n  verify-published:\n") :]

    exact_check = (
        'python scripts/check_pypi_release_state.py --require-present "$version"'
    )
    install_smoke = 'python -m pip install --no-cache-dir --target "$target"'
    assert exact_check in verify_job
    assert (
        'python -m pip install --quiet --no-cache-dir --target "$target"' in verify_job
    )
    assert verify_job.index(exact_check) < verify_job.index("python -m pip install")
    assert "for attempt in {1..10}; do" in verify_job
    assert "waiting for propagation" in verify_job
    assert "needs: publish" in verify_job
    assert "id-token: write" not in verify_job
    assert install_smoke not in verify_job


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
    recovery_check = workflow.index(
        "Verify exact manual recovery run and publication evidence"
    )
    assert default_branch_guard < checkout < tag_resolution < recovery_check
    assert "DEFAULT_BRANCH: ${{ github.event.repository.default_branch }}" in workflow
    assert 'if [ "$GITHUB_REF" != "refs/heads/${DEFAULT_BRANCH}" ]; then' in workflow
    assert "manual GitHub Release retry must run from the default branch" in workflow
    assert "ref: ${{ github.sha }}" in workflow
    assert "not $GITHUB_REF" not in workflow
    assert "release_run_id:" in workflow
    run_input = workflow[
        workflow.index("release_run_id:") : workflow.index("workflow_run:")
    ]
    assert "required: false" in run_input
    assert "default: ''" in run_input


def test_github_release_verifies_peeled_tag_sha_against_release_source() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    assert '"refs/tags/${tag}:${source_tag_ref}" >/dev/null 2>&1' in workflow
    assert (
        'tag_object_sha="$(git rev-parse -q --verify "$source_tag_ref" 2>/dev/null)"'
        in workflow
    )
    assert (
        'tag_object_type="$(git cat-file -t "$tag_object_sha" 2>/dev/null)"' in workflow
    )
    assert (
        'if [ "$tag_object_type" != "tag" ] && [ "$tag_object_type" != "commit" ]; then'
        in workflow
    )
    assert 'echo "tag_object_sha=${tag_object_sha}"' in workflow
    assert 'echo "tag_object_type=${tag_object_type}"' in workflow
    assert (
        'tag_sha="$(git rev-parse -q --verify "${source_tag_ref}^{commit}" 2>/dev/null)"'
        in workflow
    )
    assert 'echo "sha=${tag_sha}"' in workflow
    assert "Verify successful automatic release source" in workflow
    assert 'if [ "$TAG_OBJECT_TYPE" != "tag" ]; then' in workflow
    assert "automatic GitHub Release requires an annotated release tag" in workflow
    assert 'if [ "$RELEASE_SHA" != "$WORKFLOW_RUN_HEAD_SHA" ]; then' in workflow
    assert "actions: read\n  contents: write" in workflow


def test_github_release_manual_recovery_uses_exact_run_and_structured_verifier() -> (
    None
):
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    recovery = workflow.index(
        "Verify exact manual recovery run and publication evidence"
    )
    release_step = workflow.index("Create or verify GitHub release")
    assert recovery < release_step
    assert "INPUT_RUN_ID: ${{ inputs.release_run_id }}" in workflow
    assert (
        '[ -n "$INPUT_RUN_ID" ] && [[ ! "$INPUT_RUN_ID" =~ ^[1-9][0-9]*$ ]]' in workflow
    )
    assert "Resolve exact manual recovery run" in workflow
    assert "actions/workflows/release.yml/runs" in workflow
    assert "verify_release_recovery.py select-run" in workflow
    assert 'run_id="$(python scripts/verify_release_recovery.py select-run' in workflow
    assert "RELEASE_RUN_ID: ${{ steps.recovery.outputs.run_id }}" in workflow
    assert 'actions/runs/${RELEASE_RUN_ID}"' in workflow
    assert (
        "actions/runs/${RELEASE_RUN_ID}/attempts/${run_attempt}/jobs?per_page=100"
        in workflow
    )
    assert "actions/runs/${RELEASE_RUN_ID}/artifacts?per_page=100" in workflow
    assert (
        '"repos/${GITHUB_REPOSITORY}/actions/artifacts/${artifact_id}/zip"' in workflow
    )
    assert 'if [ "$recovery_result" = "historical-success" ]; then' in workflow
    assert '[ "$RELEASE_TAG" != "v0.1.6" ]' in workflow
    assert "lightweight tag recovery is not allowed for this release" in workflow
    assert 'echo "kind=historical-success" >> "$GITHUB_OUTPUT"' in workflow
    assert 'elif [ "$recovery_result" = "current-success" ]; then' in workflow
    assert "current release recovery requires an annotated tag" in workflow
    assert 'echo "kind=current-success" >> "$GITHUB_OUTPUT"' in workflow
    assert "failed current release recovery requires an annotated tag" in workflow
    assert 'artifact_id="$recovery_result"' in workflow
    assert 'echo "kind=failed-current" >> "$GITHUB_OUTPUT"' in workflow
    assert 'gh run download "$RELEASE_RUN_ID"' not in workflow
    assert "verify_release_recovery.py verify-artifact" in workflow
    assert "verify_release_recovery.py verify-run-stable" in workflow
    assert '--run-id "$RELEASE_RUN_ID"' in workflow
    assert '--tag "$RELEASE_TAG"' in workflow
    assert '--sha "$RELEASE_SHA"' in workflow
    assert "gh run list" not in workflow


def test_github_release_checks_publication_evidence_before_release_notes() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    recovery_check = workflow.index(
        "Verify exact manual recovery run and publication evidence"
    )
    pypi_check = workflow.index("Verify exact PyPI release is present")
    github_release = workflow.index("Create or verify GitHub release")
    assert recovery_check < pypi_check < github_release
    assert (
        'python scripts/check_pypi_release_state.py --require-present "$RELEASE_VERSION"'
        in workflow
    )
    assert "for attempt in {1..5}; do" in workflow
    assert "waiting for API propagation" in workflow
    assert "sleep 10" in workflow
    assert 'git checkout --detach "$RELEASE_SHA"' not in workflow
    assert 'git show "${RELEASE_SHA}:CHANGELOG.md" > release-changelog.md' in workflow
    assert "python scripts/check_changelog.py \\" in workflow
    assert "--changelog release-changelog.md" in workflow


def test_historical_recovery_keeps_current_verifier_and_bounds_note_fallback() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    release_step = workflow.index("Create or verify GitHub release")
    publication = workflow[release_step:]
    assert "git checkout --detach" not in workflow
    assert "verify_release_recovery.py release-state" in publication
    assert publication.index("verify_release_recovery.py release-state") < (
        publication.index('git show "${RELEASE_SHA}:CHANGELOG.md"')
    )
    assert "python scripts/check_changelog.py" in publication
    assert '[ "$GITHUB_EVENT_NAME" = "workflow_dispatch" ]' in publication
    assert '[ "$RECOVERY_KIND" = "historical-success" ]' in publication
    assert "The tagged tree predates CHANGELOG.md." in publication


def test_github_release_automatic_path_requires_fully_successful_release_run() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    assert "github.event_name == 'workflow_run'" in workflow
    assert "github.event.workflow_run.event == 'push'" in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.conclusion == 'failure'" not in workflow
    assert r'[[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]' in workflow
    assert "Verify successful automatic release source" in workflow
    assert "Verify current automatic release topology" in workflow
    assert "verify_release_recovery.py validate-run" in workflow
    assert "--mode automatic" in workflow
    assert 'if [ "$RELEASE_SHA" != "$WORKFLOW_RUN_HEAD_SHA" ]; then' in workflow


def test_successful_release_workflow_dispatch_dry_run_cannot_publish_release() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")
    job_start = workflow.index("  publish-github-release:")
    job_header = workflow[job_start : workflow.index("    runs-on:", job_start)]

    assert "github.event_name == 'workflow_dispatch' ||" in job_header
    assert "github.event_name == 'workflow_run'" in job_header
    assert "github.event.workflow_run.event == 'push'" in job_header
    assert "github.event.workflow_run.conclusion == 'success'" in job_header


def test_github_release_has_no_publication_command_before_all_checks() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    release_step = workflow.index("Create or verify GitHub release")
    assert "gh release create" not in workflow[:release_step]
    assert "gh release edit" not in workflow[:release_step]
    assert workflow.index("verify_release_recovery.py") < release_step
    assert (
        workflow.index("check_pypi_release_state.py --require-present") < release_step
    )


def test_github_release_rechecks_remote_tag_immediately_before_publication() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")

    publish_step = workflow[workflow.index("Create or verify GitHub release") :]
    assert "git fetch --force --no-tags origin" in publish_step
    assert '"refs/tags/${TAG_NAME}:${remote_tag_ref}" >/dev/null 2>&1' in publish_step
    assert (
        'remote_tag_object_sha="$(git rev-parse -q --verify "$remote_tag_ref" 2>/dev/null)"'
        in publish_step
    )
    assert (
        'remote_tag_object_type="$(git cat-file -t "$remote_tag_object_sha" 2>/dev/null)"'
        in publish_step
    )
    assert (
        'remote_tag_sha="$(git rev-parse -q --verify "${remote_tag_ref}^{commit}" 2>/dev/null)"'
        in publish_step
    )
    assert 'remote_tag_object_sha" != "$TAG_OBJECT_SHA' in publish_step
    assert 'remote_tag_object_type" != "$TAG_OBJECT_TYPE' in publish_step
    assert 'remote_tag_sha" != "$RELEASE_SHA' in publish_step
    assert publish_step.index(
        "release tag object or commit changed after verification"
    ) < publish_step.index("verify_release_recovery.py release-state")


def test_existing_release_is_validated_and_clean_public_state_is_a_noop() -> None:
    workflow = WORKFLOWS["github-release"].read_text(encoding="utf-8")
    publish_step = workflow[workflow.index("Create or verify GitHub release") :]

    assert "gh release edit" not in workflow
    assert "verify_release_recovery.py release-state" in publish_step
    assert 'if [ "$release_state" = "present" ]; then' in publish_step
    assert publish_step.index('if [ "$release_state" = "present" ]; then') < (
        publish_step.index('gh release create "$TAG_NAME"')
    )
