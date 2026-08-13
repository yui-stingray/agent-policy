"""Tests for fail-closed GitHub Release recovery verification."""

from __future__ import annotations

import hashlib
import stat
import sys
import urllib.error
import zipfile
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import verify_release_recovery as MODULE


RUN_ID = 4_815_162_342
ARTIFACT_ID = 8_675_309
REPOSITORY = "yui-stingray/agent-policy"
TAG = "v0.1.11"
VERSION = "0.1.11"
SHA = "a" * 40
JOB_IDS = {
    MODULE.VALIDATE_JOB: 101,
    MODULE.BUILD_JOB: 102,
    MODULE.ATTEST_JOB: 103,
    MODULE.PUBLISH_JOB: 104,
    MODULE.VERIFY_JOB: 105,
}


def _files(version: str = VERSION) -> dict[str, bytes]:
    return {
        f"yui_agent_policy-{version}-py3-none-any.whl": b"release wheel bytes",
        f"yui_agent_policy-{version}.tar.gz": b"release sdist bytes",
    }


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _run_data(
    conclusion: str = "success",
    *,
    run_id: int = RUN_ID,
    tag: str = TAG,
    sha: str = SHA,
    event: str = "push",
    attempt: int = 1,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "name": MODULE.WORKFLOW_NAME,
        "path": MODULE.WORKFLOW_PATH,
        "event": event,
        "status": "completed",
        "conclusion": conclusion,
        "head_branch": tag,
        "head_sha": sha,
        "run_attempt": attempt,
        "head_repository": {"full_name": REPOSITORY},
    }


def _job(
    name: str,
    conclusion: str,
    *,
    run_id: int = RUN_ID,
    sha: str = SHA,
    steps: list[dict[str, Any]] | None = None,
    job_id: int | None = None,
) -> dict[str, Any]:
    job: dict[str, Any] = {
        "id": job_id if job_id is not None else JOB_IDS[name],
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "run_id": run_id,
        "head_sha": sha,
    }
    if steps is not None:
        job["steps"] = steps
    return job


def _publisher_steps(conclusion: str = "success") -> list[dict[str, Any]]:
    return [
        {"name": "Set up job", "status": "completed", "conclusion": "success"},
        {
            "name": "Prepare publish environment",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "name": MODULE.PUBLISH_STEP,
            "status": "completed",
            "conclusion": conclusion,
        },
        {
            "name": "Complete job",
            "status": "completed",
            "conclusion": "success",
        },
    ]


def _current_jobs_payload(
    *,
    publish: str = "success",
    verify: str = "success",
    publisher: str = "success",
    run_id: int = RUN_ID,
    sha: str = SHA,
) -> dict[str, Any]:
    jobs = [
        _job(MODULE.VALIDATE_JOB, "success", run_id=run_id, sha=sha),
        _job(MODULE.BUILD_JOB, "success", run_id=run_id, sha=sha),
        _job(MODULE.ATTEST_JOB, "success", run_id=run_id, sha=sha),
        _job(
            MODULE.PUBLISH_JOB,
            publish,
            run_id=run_id,
            sha=sha,
            steps=_publisher_steps(publisher),
        ),
        _job(MODULE.VERIFY_JOB, verify, run_id=run_id, sha=sha),
    ]
    return {"total_count": len(jobs), "jobs": jobs}


def _historical_v0_1_9_jobs(sha: str) -> dict[str, Any]:
    """Model the v0.1.9 release: no named publisher step or verify job."""
    jobs = [
        _job(MODULE.VALIDATE_JOB, "success", sha=sha),
        _job(MODULE.BUILD_JOB, "success", sha=sha),
        _job(MODULE.ATTEST_JOB, "success", sha=sha),
        _job(
            MODULE.PUBLISH_JOB,
            "success",
            sha=sha,
            steps=[
                {
                    "name": "Run pypa/gh-action-pypi-publish@release/v1",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "Verify exact PyPI files",
                    "status": "completed",
                    "conclusion": "success",
                },
            ],
        ),
    ]
    return {"total_count": len(jobs), "jobs": jobs}


def _historical_v0_1_6_jobs(sha: str) -> dict[str, Any]:
    """Model the v0.1.6 release, before the attestation job existed."""
    jobs = [
        _job(MODULE.VALIDATE_JOB, "success", sha=sha),
        _job(MODULE.BUILD_JOB, "success", sha=sha),
        _job(
            MODULE.PUBLISH_JOB,
            "success",
            sha=sha,
            steps=[
                {
                    "name": "Run pypa/gh-action-pypi-publish@release/v1",
                    "status": "completed",
                    "conclusion": "success",
                }
            ],
        ),
    ]
    return {"total_count": len(jobs), "jobs": jobs}


def _artifacts_payload(
    *,
    expired: bool = False,
    run_id: int = RUN_ID,
    tag: str = TAG,
    sha: str = SHA,
    artifact_id: int = ARTIFACT_ID,
) -> dict[str, Any]:
    artifacts = [
        {
            "id": artifact_id,
            "name": MODULE.DIST_ARTIFACT,
            "expired": expired,
            "size_in_bytes": 4096,
            "workflow_run": {"id": run_id, "head_branch": tag, "head_sha": sha},
        }
    ]
    return {"total_count": len(artifacts), "artifacts": artifacts}


def _pypi_data(
    files: dict[str, bytes] | None = None,
    *,
    yanked: set[str] | None = None,
) -> dict[str, Any]:
    files = _files() if files is None else files
    yanked = set() if yanked is None else yanked
    return {
        "urls": [
            {
                "filename": filename,
                "packagetype": (
                    "bdist_wheel" if filename.endswith(".whl") else "sdist"
                ),
                "yanked": filename in yanked,
                "digests": {"sha256": _sha256(content)},
                "url": f"https://files.pythonhosted.org/packages/{filename}",
            }
            for filename, content in files.items()
        ]
    }


def _remote_digest(pypi_data: dict[str, Any], files: dict[str, bytes]) -> Any:
    digests = {
        record["url"]: _sha256(files[record["filename"]])
        for record in pypi_data["urls"]
    }
    return lambda url: digests[url]


def _write_zip(
    path: Path,
    entries: list[tuple[str, bytes]] | None = None,
    *,
    symlink_name: str | None = None,
) -> None:
    entries = list(_files().items()) if entries is None else entries
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in entries:
            info = zipfile.ZipInfo(name)
            mode = stat.S_IFLNK if name == symlink_name else stat.S_IFREG
            info.external_attr = (mode | 0o644) << 16
            archive.writestr(info, content)


def _validate_recovery(
    run_data: dict[str, Any],
    jobs_payload: object,
    artifacts_payload: object | None = None,
    *,
    tag: str = TAG,
    sha: str = SHA,
    version: str = VERSION,
) -> int | str | None:
    return MODULE.validate_release_run(
        run_data,
        jobs_payload,
        _artifacts_payload(tag=tag, sha=sha)
        if artifacts_payload is None
        else artifacts_payload,
        mode="recovery",
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        expected_tag=tag,
        expected_sha=sha,
        expected_version=version,
    )


def test_selects_sole_exact_completed_tag_push_run() -> None:
    payload = {"total_count": 1, "workflow_runs": [_run_data("failure")]}

    assert (
        MODULE.select_release_run_id(
            payload,
            expected_repository=REPOSITORY,
            expected_tag=TAG,
            expected_sha=SHA,
        )
        == RUN_ID
    )


@pytest.mark.parametrize(
    ("field", "wrong_value"),
    [
        ("event", "workflow_dispatch"),
        ("head_branch", "v0.1.12"),
        ("head_sha", "b" * 40),
    ],
)
def test_run_selection_rejects_wrong_event_tag_or_sha(
    field: str, wrong_value: str
) -> None:
    run_data = _run_data()
    run_data[field] = wrong_value

    with pytest.raises(MODULE.RecoveryVerificationError, match="exactly one matching"):
        MODULE.select_release_run_id(
            {"total_count": 1, "workflow_runs": [run_data]},
            expected_repository=REPOSITORY,
            expected_tag=TAG,
            expected_sha=SHA,
        )


def test_run_selection_rejects_ambiguous_exact_runs() -> None:
    second = _run_data("failure", run_id=RUN_ID + 1)

    with pytest.raises(MODULE.RecoveryVerificationError, match="exactly one matching"):
        MODULE.select_release_run_id(
            {"total_count": 2, "workflow_runs": [_run_data(), second]},
            expected_repository=REPOSITORY,
            expected_tag=TAG,
            expected_sha=SHA,
        )


def test_automatic_accepts_only_current_success_topology() -> None:
    result = MODULE.validate_release_run(
        _run_data(),
        _current_jobs_payload(),
        None,
        mode="automatic",
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        expected_tag=TAG,
        expected_sha=SHA,
        expected_version=VERSION,
    )

    assert result is None


def test_automatic_rejects_successful_workflow_dispatch_dry_run() -> None:
    with pytest.raises(MODULE.RecoveryVerificationError, match="tag push"):
        MODULE.validate_release_run(
            _run_data(event="workflow_dispatch"),
            _current_jobs_payload(),
            None,
            mode="automatic",
            expected_run_id=RUN_ID,
            expected_repository=REPOSITORY,
            expected_tag=TAG,
            expected_sha=SHA,
            expected_version=VERSION,
        )


@pytest.mark.parametrize(
    "case", ["missing-publish", "missing-verify", "failed-publish", "failed-verify"]
)
def test_automatic_requires_publish_and_verify_job_success(case: str) -> None:
    jobs_payload = _current_jobs_payload()
    if case.startswith("missing-"):
        missing = MODULE.PUBLISH_JOB if case == "missing-publish" else MODULE.VERIFY_JOB
        jobs_payload["jobs"] = [
            job for job in jobs_payload["jobs"] if job["name"] != missing
        ]
        jobs_payload["total_count"] -= 1
    else:
        failed = MODULE.PUBLISH_JOB if case == "failed-publish" else MODULE.VERIFY_JOB
        next(job for job in jobs_payload["jobs"] if job["name"] == failed)[
            "conclusion"
        ] = "failure"

    with pytest.raises(MODULE.RecoveryVerificationError):
        MODULE.validate_release_run(
            _run_data(),
            jobs_payload,
            None,
            mode="automatic",
            expected_run_id=RUN_ID,
            expected_repository=REPOSITORY,
            expected_tag=TAG,
            expected_sha=SHA,
            expected_version=VERSION,
        )


def test_historical_v0_1_9_success_allows_expired_artifact(
    tmp_path: Path,
) -> None:
    tag = "v0.1.9"
    version = "0.1.9"
    sha = "9" * 40
    files = _files(version)
    pypi_data = _pypi_data(files)
    archive_path = tmp_path / "artifact.zip"
    _write_zip(archive_path, list(files.items()))

    recovery_result = _validate_recovery(
        _run_data(tag=tag, sha=sha),
        _historical_v0_1_9_jobs(sha),
        _artifacts_payload(expired=True, tag=tag, sha=sha),
        tag=tag,
        sha=sha,
        version=version,
    )
    digests = MODULE.verify_published_artifact_zip(
        archive_path,
        "yui-agent-policy",
        version,
        pypi_data,
        remote_digest=_remote_digest(pypi_data, files),
    )

    assert recovery_result == MODULE.HISTORICAL_SUCCESS_RESULT
    assert digests == {name: _sha256(content) for name, content in files.items()}


def test_historical_v0_1_6_success_allows_missing_artifact() -> None:
    tag = "v0.1.6"
    version = "0.1.6"
    sha = "6" * 40

    assert (
        _validate_recovery(
            _run_data(tag=tag, sha=sha),
            _historical_v0_1_6_jobs(sha),
            {"total_count": 0, "artifacts": []},
            tag=tag,
            sha=sha,
            version=version,
        )
        == MODULE.HISTORICAL_SUCCESS_RESULT
    )


def test_current_success_requires_and_accepts_current_topology() -> None:
    assert (
        _validate_recovery(_run_data(), _current_jobs_payload())
        == MODULE.CURRENT_SUCCESS_RESULT
    )


def test_current_success_rejects_historical_topology() -> None:
    with pytest.raises(MODULE.RecoveryVerificationError, match="current release job"):
        _validate_recovery(_run_data(), _historical_v0_1_9_jobs(SHA))


def test_unrecognized_old_version_does_not_enter_historical_path() -> None:
    tag = "v0.1.0"
    version = "0.1.0"
    sha = "0" * 40

    with pytest.raises(MODULE.RecoveryVerificationError, match="current release job"):
        _validate_recovery(
            _run_data(tag=tag, sha=sha),
            _historical_v0_1_6_jobs(sha),
            tag=tag,
            sha=sha,
            version=version,
        )


def test_failed_recovery_accepts_publish_success_and_verify_failure() -> None:
    jobs = _current_jobs_payload(publish="success", verify="failure")

    assert _validate_recovery(_run_data("failure"), jobs) == ARTIFACT_ID


@pytest.mark.parametrize("publisher", ["success", "failure"])
def test_failed_recovery_accepts_publish_failure_and_verify_skipped(
    publisher: str,
) -> None:
    jobs = _current_jobs_payload(
        publish="failure", verify="skipped", publisher=publisher
    )

    assert _validate_recovery(_run_data("failure"), jobs) == ARTIFACT_ID


def test_publish_setup_and_post_steps_are_not_treated_as_jobs() -> None:
    jobs = _current_jobs_payload(publish="failure", verify="skipped")
    publish = next(job for job in jobs["jobs"] if job["name"] == MODULE.PUBLISH_JOB)

    assert len(publish["steps"]) > 1
    assert _validate_recovery(_run_data("failure"), jobs) == ARTIFACT_ID


def test_failed_recovery_rejects_unknown_job() -> None:
    jobs = _current_jobs_payload(publish="failure", verify="skipped")
    jobs["jobs"].append(
        _job("Post Publish distributions to PyPI", "success", job_id=999)
    )
    jobs["total_count"] += 1

    with pytest.raises(MODULE.RecoveryVerificationError, match="topology"):
        _validate_recovery(_run_data("failure"), jobs)


@pytest.mark.parametrize(
    "job_name", [MODULE.VALIDATE_JOB, MODULE.BUILD_JOB, MODULE.ATTEST_JOB]
)
@pytest.mark.parametrize("case", ["missing", "failed"])
def test_failed_recovery_requires_validate_build_and_attest_success(
    job_name: str, case: str
) -> None:
    jobs = _current_jobs_payload(publish="failure", verify="skipped")
    if case == "missing":
        jobs["jobs"] = [job for job in jobs["jobs"] if job["name"] != job_name]
        jobs["total_count"] -= 1
    else:
        next(job for job in jobs["jobs"] if job["name"] == job_name)["conclusion"] = (
            "failure"
        )

    with pytest.raises(MODULE.RecoveryVerificationError):
        _validate_recovery(_run_data("failure"), jobs)


@pytest.mark.parametrize(
    ("publish", "verify"),
    [
        ("success", "skipped"),
        ("success", "success"),
        ("failure", "failure"),
        ("failure", "success"),
        ("cancelled", "skipped"),
    ],
)
def test_failed_recovery_rejects_other_job_topologies(
    publish: str, verify: str
) -> None:
    jobs = _current_jobs_payload(publish=publish, verify=verify)

    with pytest.raises(MODULE.RecoveryVerificationError, match="not recoverable"):
        _validate_recovery(_run_data("failure"), jobs)


@pytest.mark.parametrize("publisher", ["skipped", "cancelled", None])
def test_failed_recovery_rejects_publisher_skipped_cancelled_or_missing(
    publisher: str | None,
) -> None:
    jobs = _current_jobs_payload(publish="failure", verify="skipped")
    publish = next(job for job in jobs["jobs"] if job["name"] == MODULE.PUBLISH_JOB)
    if publisher is None:
        publish["steps"] = [
            step for step in publish["steps"] if step["name"] != MODULE.PUBLISH_STEP
        ]
    else:
        next(step for step in publish["steps"] if step["name"] == MODULE.PUBLISH_STEP)[
            "conclusion"
        ] = publisher

    with pytest.raises(MODULE.RecoveryVerificationError, match="publisher step"):
        _validate_recovery(_run_data("failure"), jobs)


def test_recovery_returns_the_validated_artifact_id() -> None:
    jobs = _current_jobs_payload(publish="failure", verify="skipped")

    assert _validate_recovery(_run_data("failure"), jobs) == ARTIFACT_ID


@pytest.mark.parametrize("case", ["missing", "expired", "duplicate", "wrong-run"])
def test_rejects_unavailable_or_ambiguous_artifact(case: str) -> None:
    artifacts = _artifacts_payload()
    if case == "missing":
        artifacts = {"total_count": 0, "artifacts": []}
    elif case == "expired":
        artifacts["artifacts"][0]["expired"] = True
    elif case == "duplicate":
        duplicate = dict(artifacts["artifacts"][0])
        duplicate["id"] = ARTIFACT_ID + 1
        artifacts["artifacts"].append(duplicate)
        artifacts["total_count"] += 1
    else:
        artifacts["artifacts"][0]["workflow_run"] = {
            "id": RUN_ID + 1,
            "head_branch": TAG,
            "head_sha": SHA,
        }

    with pytest.raises(MODULE.RecoveryVerificationError, match="artifact"):
        jobs = _current_jobs_payload(publish="failure", verify="skipped")
        _validate_recovery(_run_data("failure"), jobs, artifacts)


def test_recovery_rejects_rerun_artifact_identity_as_ambiguous() -> None:
    with pytest.raises(MODULE.RecoveryVerificationError, match="multiple attempts"):
        jobs = _current_jobs_payload(publish="failure", verify="skipped")
        _validate_recovery(_run_data("failure", attempt=2), jobs)


def test_accepts_exact_flat_regular_artifact_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / "artifact.zip"
    files = _files()
    pypi_data = _pypi_data(files)
    _write_zip(archive_path)

    assert MODULE.verify_published_artifact_zip(
        archive_path,
        "yui-agent-policy",
        VERSION,
        pypi_data,
        remote_digest=_remote_digest(pypi_data, files),
    ) == {name: _sha256(content) for name, content in files.items()}


@pytest.mark.parametrize(
    "case", ["missing", "extra", "traversal", "duplicate", "nested", "symlink"]
)
def test_rejects_unsafe_or_inexact_artifact_zip(tmp_path: Path, case: str) -> None:
    archive_path = tmp_path / "artifact.zip"
    entries = list(_files().items())
    symlink_name = None
    if case == "missing":
        entries.pop()
    elif case == "extra":
        entries.append(("unexpected.txt", b"unexpected"))
    elif case == "traversal":
        entries[0] = (f"../{entries[0][0]}", entries[0][1])
    elif case == "duplicate":
        entries[1] = entries[0]
    elif case == "nested":
        entries[0] = (f"nested/{entries[0][0]}", entries[0][1])
    else:
        symlink_name = entries[0][0]
    if case == "duplicate":
        with pytest.warns(UserWarning, match="Duplicate name"):
            _write_zip(archive_path, entries, symlink_name=symlink_name)
    else:
        _write_zip(archive_path, entries, symlink_name=symlink_name)

    with pytest.raises(MODULE.RecoveryVerificationError, match="ZIP"):
        MODULE.artifact_zip_digests(archive_path, set(_files()))


@pytest.mark.parametrize("case", ["missing", "extra", "yanked"])
def test_rejects_missing_extra_or_yanked_pypi_files(tmp_path: Path, case: str) -> None:
    archive_path = tmp_path / "artifact.zip"
    _write_zip(archive_path)
    pypi_data = _pypi_data()
    if case == "missing":
        pypi_data["urls"].pop()
    elif case == "extra":
        pypi_data["urls"].append(
            {
                "filename": "unexpected-0.1.11-py3-none-any.whl",
                "packagetype": "bdist_wheel",
                "yanked": False,
                "digests": {"sha256": "0" * 64},
                "url": "https://files.pythonhosted.org/packages/unexpected.whl",
            }
        )
    else:
        pypi_data["urls"][0]["yanked"] = True

    with pytest.raises(
        MODULE.RecoveryVerificationError, match="exact expected non-yanked"
    ):
        MODULE.verify_published_artifact_zip(
            archive_path,
            "yui-agent-policy",
            VERSION,
            pypi_data,
            remote_digest=lambda _url: pytest.fail("unexpected byte download"),
        )


def test_rejects_artifact_hash_mismatch(tmp_path: Path) -> None:
    archive_path = tmp_path / "artifact.zip"
    mismatched = _files()
    first = next(iter(mismatched))
    mismatched[first] = b"different artifact bytes"
    _write_zip(archive_path, list(mismatched.items()))
    pypi_data = _pypi_data()

    with pytest.raises(MODULE.RecoveryVerificationError, match="PyPI metadata"):
        MODULE.verify_published_artifact_zip(
            archive_path,
            "yui-agent-policy",
            VERSION,
            pypi_data,
            remote_digest=lambda _url: pytest.fail("unexpected byte download"),
        )


def test_rejects_published_byte_hash_mismatch(tmp_path: Path) -> None:
    archive_path = tmp_path / "artifact.zip"
    _write_zip(archive_path)

    with pytest.raises(
        MODULE.RecoveryVerificationError, match="published file SHA-256"
    ):
        MODULE.verify_published_artifact_zip(
            archive_path,
            "yui-agent-policy",
            VERSION,
            _pypi_data(),
            remote_digest=lambda _url: "0" * 64,
        )


def test_run_refetch_accepts_unchanged_identity() -> None:
    MODULE.validate_run_unchanged(
        _run_data("failure"),
        _run_data("failure"),
        expected_run_id=RUN_ID,
        expected_repository=REPOSITORY,
        expected_tag=TAG,
        expected_sha=SHA,
    )


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("run_attempt", 2),
        ("status", "in_progress"),
        ("conclusion", "success"),
        ("head_branch", "v0.1.12"),
        ("head_sha", "b" * 40),
    ],
)
def test_run_refetch_rejects_changed_attempt_state_or_head(
    field: str, changed: object
) -> None:
    after = _run_data("failure")
    after[field] = changed

    with pytest.raises(MODULE.RecoveryVerificationError):
        MODULE.validate_run_unchanged(
            _run_data("failure"),
            after,
            expected_run_id=RUN_ID,
            expected_repository=REPOSITORY,
            expected_tag=TAG,
            expected_sha=SHA,
        )


def test_existing_release_state_accepts_absent_or_clean_public_release() -> None:
    assert MODULE.inspect_existing_release([], TAG) == "absent"
    clean = {
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-13T00:00:00Z",
        "assets": [],
    }

    assert MODULE.inspect_existing_release([[clean]], TAG) == "present"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("draft", True),
        ("prerelease", True),
        ("assets", [{"name": "unverified.bin"}]),
    ],
)
def test_existing_release_rejects_unverified_state_or_assets(
    field: str, value: object
) -> None:
    release = {
        "tag_name": TAG,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-13T00:00:00Z",
        "assets": [],
    }
    release[field] = value

    with pytest.raises(MODULE.RecoveryVerificationError, match="clean public"):
        MODULE.inspect_existing_release([release], TAG)


def test_network_failure_does_not_expose_raw_published_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_url = "https://files.pythonhosted.org/packages/private-file.whl"

    def fail_fetch(*_args: object, **_kwargs: object) -> None:
        raise urllib.error.URLError(raw_url)

    monkeypatch.setattr(MODULE.urllib.request, "urlopen", fail_fetch)

    with pytest.raises(MODULE.RecoveryVerificationError) as exc_info:
        MODULE.fetch_published_file_sha256(raw_url)

    assert raw_url not in str(exc_info.value)
    assert "private-file.whl" not in str(exc_info.value)


def test_main_does_not_expose_missing_metadata_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path = tmp_path / "private-run-metadata.json"
    argv = [
        "verify_release_recovery.py",
        "run-attempt",
        "--mode",
        "recovery",
        "--run-json",
        str(private_path),
        "--run-id",
        str(RUN_ID),
        "--repository",
        REPOSITORY,
        "--tag",
        TAG,
        "--sha",
        SHA,
    ]

    assert MODULE.main(argv) == 1
    error = capsys.readouterr().err
    assert "metadata is unavailable" in error
    assert str(private_path) not in error
    assert private_path.name not in error


def test_main_does_not_echo_invalid_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    private_path = tmp_path / "private-token.json"

    assert MODULE.main(["verify_release_recovery.py", str(private_path)]) == 1
    error = capsys.readouterr().err
    assert "arguments are invalid" in error
    assert str(private_path) not in error
    assert private_path.name not in error
