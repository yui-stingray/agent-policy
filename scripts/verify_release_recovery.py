"""Fail-closed validation for automatic and manual GitHub Release publication.

The validator consumes structured GitHub/PyPI metadata and an artifact ZIP
downloaded by validated artifact ID. Diagnostics intentionally omit URLs,
tokens, API payloads, and local paths because workflow logs are public.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

if __package__:
    from .check_pypi_release_state import (
        expected_release_files,
        fetch_pypi_release,
        load_project_metadata,
        require_release_present,
    )
else:
    from check_pypi_release_state import (  # type: ignore[no-redef]
        expected_release_files,
        fetch_pypi_release,
        load_project_metadata,
        require_release_present,
    )


WORKFLOW_NAME = "release"
WORKFLOW_PATH = ".github/workflows/release.yml"
VALIDATE_JOB = "validate release request"
BUILD_JOB = "build sdist + wheel"
ATTEST_JOB = "attest release distributions"
PUBLISH_JOB = "publish to PyPI (OIDC)"
VERIFY_JOB = "verify published package"
PUBLISH_STEP = "Publish distributions to PyPI"
DIST_ARTIFACT = "dist"
PYPI_FILE_HOST = "files.pythonhosted.org"
CURRENT_JOBS = {VALIDATE_JOB, BUILD_JOB, ATTEST_JOB, PUBLISH_JOB, VERIFY_JOB}
HISTORICAL_SUCCESS_JOBS = {BUILD_JOB, PUBLISH_JOB}
HISTORICAL_SUCCESS_RESULT = "historical-success"
CURRENT_SUCCESS_RESULT = "current-success"
HISTORICAL_SUCCESS_VERSIONS = frozenset(f"0.1.{patch}" for patch in range(1, 10))
TAG_PATTERN = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class RecoveryVerificationError(ValueError):
    """A sanitized release validation failure."""


class SanitizedArgumentParser(argparse.ArgumentParser):
    """Reject malformed CLI input without echoing paths or other arguments."""

    def error(self, _message: str) -> None:
        raise RecoveryVerificationError("release verifier arguments are invalid")


@dataclass(frozen=True)
class PublishedFile:
    """Integrity evidence for one expected file in exact-release PyPI JSON."""

    filename: str
    package_type: str
    sha256: str
    url: str


def _is_positive_int(value: object) -> bool:
    return type(value) is int and value > 0


def _validate_expected_identity(
    *, run_id: int | None, repository: str, tag: str, sha: str
) -> None:
    if run_id is not None and not _is_positive_int(run_id):
        raise RecoveryVerificationError("release run identifier is invalid")
    if REPOSITORY_PATTERN.fullmatch(repository) is None:
        raise RecoveryVerificationError("release repository is invalid")
    if TAG_PATTERN.fullmatch(tag) is None:
        raise RecoveryVerificationError("release tag is invalid")
    if SHA_PATTERN.fullmatch(sha) is None:
        raise RecoveryVerificationError("release commit is invalid")


def _payload_items(payload: object, key: str, label: str) -> list[dict[str, Any]]:
    """Flatten one or more paginated object responses and prove completeness."""
    if isinstance(payload, dict):
        pages = [payload]
    elif (
        isinstance(payload, list)
        and payload
        and all(isinstance(page, dict) for page in payload)
    ):
        pages = payload
    else:
        raise RecoveryVerificationError(f"{label} metadata is unavailable or malformed")

    items: list[dict[str, Any]] = []
    total_count: int | None = None
    for page in pages:
        page_total = page.get("total_count")
        page_items = page.get(key)
        if (
            type(page_total) is not int
            or page_total < 0
            or not isinstance(page_items, list)
            or not all(isinstance(item, dict) for item in page_items)
        ):
            raise RecoveryVerificationError(
                f"{label} metadata is unavailable or malformed"
            )
        if total_count is None:
            total_count = page_total
        elif page_total != total_count:
            raise RecoveryVerificationError(f"{label} metadata is ambiguous")
        items.extend(page_items)

    if total_count != len(items):
        raise RecoveryVerificationError(f"{label} metadata is incomplete")
    return items


def _array_pages(payload: object, label: str) -> list[dict[str, Any]]:
    """Flatten a paginated array response from ``gh api --slurp``."""
    if not isinstance(payload, list):
        raise RecoveryVerificationError(f"{label} metadata is unavailable or malformed")
    if all(isinstance(item, dict) for item in payload):
        return payload
    if all(isinstance(page, list) for page in payload):
        items = [item for page in payload for item in page]
        if all(isinstance(item, dict) for item in items):
            return items
    raise RecoveryVerificationError(f"{label} metadata is unavailable or malformed")


def _run_identity(
    run_data: object,
    *,
    expected_run_id: int,
    expected_repository: str,
    expected_tag: str,
    expected_sha: str,
    allowed_conclusions: set[str],
) -> tuple[int, str]:
    _validate_expected_identity(
        run_id=expected_run_id,
        repository=expected_repository,
        tag=expected_tag,
        sha=expected_sha,
    )
    if not isinstance(run_data, dict):
        raise RecoveryVerificationError(
            "release run metadata is unavailable or malformed"
        )

    workflow_path = run_data.get("path")
    head_repository = run_data.get("head_repository")
    if run_data.get("id") != expected_run_id:
        raise RecoveryVerificationError("release run identifier does not match")
    if run_data.get("name") != WORKFLOW_NAME:
        raise RecoveryVerificationError("release run uses the wrong workflow")
    if (
        not isinstance(workflow_path, str)
        or workflow_path.partition("@")[0] != WORKFLOW_PATH
    ):
        raise RecoveryVerificationError("release run uses the wrong workflow path")
    if (
        not isinstance(head_repository, dict)
        or head_repository.get("full_name") != expected_repository
    ):
        raise RecoveryVerificationError("release run uses the wrong repository")
    if run_data.get("event") != "push":
        raise RecoveryVerificationError("release run was not triggered by a tag push")
    if run_data.get("status") != "completed":
        raise RecoveryVerificationError("release run is not completed")
    conclusion = run_data.get("conclusion")
    if conclusion not in allowed_conclusions:
        raise RecoveryVerificationError("release run conclusion is not allowed")
    if run_data.get("head_branch") != expected_tag:
        raise RecoveryVerificationError("release run tag does not match")
    if run_data.get("head_sha") != expected_sha:
        raise RecoveryVerificationError("release run commit does not match")
    run_attempt = run_data.get("run_attempt")
    if not _is_positive_int(run_attempt):
        raise RecoveryVerificationError("release run attempt is unavailable")
    return run_attempt, str(conclusion)


def select_release_run_id(
    runs_payload: object,
    *,
    expected_repository: str,
    expected_tag: str,
    expected_sha: str,
) -> int:
    """Return the sole exact completed tag-push release run from a list response."""
    _validate_expected_identity(
        run_id=None,
        repository=expected_repository,
        tag=expected_tag,
        sha=expected_sha,
    )
    runs = _payload_items(runs_payload, "workflow_runs", "release run selection")
    matches: list[dict[str, Any]] = []
    for run in runs:
        run_id = run.get("id")
        if not _is_positive_int(run_id):
            continue
        try:
            _run_identity(
                run,
                expected_run_id=run_id,
                expected_repository=expected_repository,
                expected_tag=expected_tag,
                expected_sha=expected_sha,
                allowed_conclusions={"success", "failure"},
            )
        except RecoveryVerificationError:
            continue
        matches.append(run)
    if len(matches) != 1:
        raise RecoveryVerificationError(
            "manual recovery requires exactly one matching completed tag-push release run"
        )
    return int(matches[0]["id"])


def inspect_run_attempt(
    run_data: object,
    *,
    mode: str,
    expected_run_id: int,
    expected_repository: str,
    expected_tag: str,
    expected_sha: str,
) -> int:
    """Validate basic identity and return the attempt used for an exact jobs query."""
    allowed = {"success"} if mode == "automatic" else {"success", "failure"}
    attempt, _conclusion = _run_identity(
        run_data,
        expected_run_id=expected_run_id,
        expected_repository=expected_repository,
        expected_tag=expected_tag,
        expected_sha=expected_sha,
        allowed_conclusions=allowed,
    )
    return attempt


def _single_job(
    jobs: list[dict[str, Any]], name: str, expected_run_id: int, expected_sha: str
) -> dict[str, Any]:
    matches = [job for job in jobs if job.get("name") == name]
    if len(matches) != 1:
        raise RecoveryVerificationError(
            f"required release job is missing or ambiguous: {name}"
        )
    job = matches[0]
    if (
        not _is_positive_int(job.get("id"))
        or job.get("run_id") != expected_run_id
        or job.get("head_sha") != expected_sha
        or job.get("status") != "completed"
    ):
        raise RecoveryVerificationError(
            f"required release job identity is invalid: {name}"
        )
    return job


def _require_job_conclusion(job: dict[str, Any], name: str, conclusion: str) -> None:
    if job.get("conclusion") != conclusion:
        raise RecoveryVerificationError(
            f"required release job has the wrong conclusion: {name}"
        )


def _publisher_step(publish_job: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    steps = publish_job.get("steps")
    if not isinstance(steps, list) or not all(isinstance(step, dict) for step in steps):
        raise RecoveryVerificationError("publisher step metadata is unavailable")
    matches = [step for step in steps if step.get("name") == PUBLISH_STEP]
    if len(matches) != 1:
        raise RecoveryVerificationError("publisher step is missing or ambiguous")
    step = matches[0]
    if step.get("status") != "completed" or step.get("conclusion") not in allowed:
        raise RecoveryVerificationError(
            "publisher step did not reach an allowed conclusion"
        )
    return step


def _validate_current_success(
    jobs: list[dict[str, Any]], expected_run_id: int, expected_sha: str
) -> None:
    observed_names = [job.get("name") for job in jobs]
    if len(jobs) != len(CURRENT_JOBS) or set(observed_names) != CURRENT_JOBS:
        raise RecoveryVerificationError("current release job topology does not match")
    current = {
        name: _single_job(jobs, name, expected_run_id, expected_sha)
        for name in CURRENT_JOBS
    }
    for name, job in current.items():
        _require_job_conclusion(job, name, "success")
    _publisher_step(current[PUBLISH_JOB], {"success"})


def _validate_historical_success(
    jobs: list[dict[str, Any]], expected_run_id: int, expected_sha: str
) -> None:
    """Accept successful old runs that predate attestation and verify jobs."""
    for name in HISTORICAL_SUCCESS_JOBS:
        job = _single_job(jobs, name, expected_run_id, expected_sha)
        _require_job_conclusion(job, name, "success")


def _validate_current_failure(
    jobs: list[dict[str, Any]], expected_run_id: int, expected_sha: str
) -> None:
    """Accept only the two current post-upload failure topologies."""
    observed_names = [job.get("name") for job in jobs]
    if len(jobs) != len(CURRENT_JOBS) or set(observed_names) != CURRENT_JOBS:
        raise RecoveryVerificationError("failed release job topology does not match")
    current = {
        name: _single_job(jobs, name, expected_run_id, expected_sha)
        for name in CURRENT_JOBS
    }
    for name in (VALIDATE_JOB, BUILD_JOB, ATTEST_JOB):
        _require_job_conclusion(current[name], name, "success")

    publish_conclusion = current[PUBLISH_JOB].get("conclusion")
    verify_conclusion = current[VERIFY_JOB].get("conclusion")
    if publish_conclusion == "success" and verify_conclusion == "failure":
        _publisher_step(current[PUBLISH_JOB], {"success"})
        return
    if publish_conclusion == "failure" and verify_conclusion == "skipped":
        _publisher_step(current[PUBLISH_JOB], {"success", "failure"})
        return
    raise RecoveryVerificationError("failed release topology is not recoverable")


def _artifact_id(
    artifacts_payload: object,
    *,
    expected_run_id: int,
    expected_tag: str,
    expected_sha: str,
) -> int:
    artifacts = _payload_items(artifacts_payload, "artifacts", "release artifact")
    matches = [
        artifact for artifact in artifacts if artifact.get("name") == DIST_ARTIFACT
    ]
    if len(matches) != 1:
        raise RecoveryVerificationError(
            "release run dist artifact is missing or ambiguous"
        )
    artifact = matches[0]
    artifact_run = artifact.get("workflow_run")
    if (
        artifact.get("expired") is not False
        or not _is_positive_int(artifact.get("id"))
        or not _is_positive_int(artifact.get("size_in_bytes"))
        or not isinstance(artifact_run, dict)
        or artifact_run.get("id") != expected_run_id
        or artifact_run.get("head_branch") != expected_tag
        or artifact_run.get("head_sha") != expected_sha
    ):
        raise RecoveryVerificationError("release run dist artifact is unavailable")
    return int(artifact["id"])


def validate_release_run(
    run_data: dict[str, Any],
    jobs_payload: object,
    artifacts_payload: object | None,
    *,
    mode: str = "recovery",
    expected_run_id: int,
    expected_repository: str,
    expected_tag: str,
    expected_sha: str,
    expected_version: str,
) -> int | str | None:
    """Validate current automatic or historical/current recovery run evidence."""
    if expected_version != expected_tag.removeprefix("v"):
        raise RecoveryVerificationError("release tag and version do not match")
    allowed = {"success"} if mode == "automatic" else {"success", "failure"}
    run_attempt, conclusion = _run_identity(
        run_data,
        expected_run_id=expected_run_id,
        expected_repository=expected_repository,
        expected_tag=expected_tag,
        expected_sha=expected_sha,
        allowed_conclusions=allowed,
    )
    jobs = _payload_items(jobs_payload, "jobs", "release job")
    if mode == "automatic":
        _validate_current_success(jobs, expected_run_id, expected_sha)
        return None
    if mode != "recovery":
        raise RecoveryVerificationError("release validation mode is invalid")
    if conclusion == "success":
        if expected_version in HISTORICAL_SUCCESS_VERSIONS:
            _validate_historical_success(jobs, expected_run_id, expected_sha)
            return HISTORICAL_SUCCESS_RESULT
        _validate_current_success(jobs, expected_run_id, expected_sha)
        return CURRENT_SUCCESS_RESULT

    _validate_current_failure(jobs, expected_run_id, expected_sha)
    if run_attempt != 1:
        raise RecoveryVerificationError(
            "release run has multiple attempts and artifact identity is ambiguous"
        )
    if artifacts_payload is None:
        raise RecoveryVerificationError("release artifact metadata is unavailable")
    return _artifact_id(
        artifacts_payload,
        expected_run_id=expected_run_id,
        expected_tag=expected_tag,
        expected_sha=expected_sha,
    )


def validate_run_unchanged(
    before: object,
    after: object,
    *,
    expected_run_id: int,
    expected_repository: str,
    expected_tag: str,
    expected_sha: str,
) -> None:
    """Require immutable run identity and state after artifact verification."""
    before_attempt, before_conclusion = _run_identity(
        before,
        expected_run_id=expected_run_id,
        expected_repository=expected_repository,
        expected_tag=expected_tag,
        expected_sha=expected_sha,
        allowed_conclusions={"success", "failure"},
    )
    after_attempt, after_conclusion = _run_identity(
        after,
        expected_run_id=expected_run_id,
        expected_repository=expected_repository,
        expected_tag=expected_tag,
        expected_sha=expected_sha,
        allowed_conclusions={"success", "failure"},
    )
    if before_attempt != after_attempt or before_conclusion != after_conclusion:
        raise RecoveryVerificationError(
            "release run changed during artifact verification"
        )


def _validate_pypi_file_url(url: str, filename: str) -> None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        raise RecoveryVerificationError("published file location is invalid") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != PYPI_FILE_HOST
        or port not in {None, 443}
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/packages/")
        or parsed.query
        or parsed.fragment
        or unquote(PurePosixPath(parsed.path).name) != filename
    ):
        raise RecoveryVerificationError("published file location is not authoritative")


def published_files(
    project_name: str, version: str, pypi_data: dict[str, Any] | None
) -> dict[str, PublishedFile]:
    """Return exact non-yanked PyPI files with complete SHA-256 evidence."""
    present, _message = require_release_present(project_name, version, pypi_data)
    if not present or pypi_data is None:
        raise RecoveryVerificationError(
            "PyPI release does not contain the exact expected non-yanked wheel and sdist"
        )

    expected = dict(expected_release_files(project_name, version))
    records: dict[str, PublishedFile] = {}
    raw_files = pypi_data.get("urls")
    if not isinstance(raw_files, list):
        raise RecoveryVerificationError("PyPI release metadata is malformed")
    for raw_file in raw_files:
        if not isinstance(raw_file, dict):
            raise RecoveryVerificationError("PyPI release metadata is malformed")
        filename = raw_file.get("filename")
        package_type = raw_file.get("packagetype")
        digests = raw_file.get("digests")
        url = raw_file.get("url")
        if (
            not isinstance(filename, str)
            or expected.get(filename) != package_type
            or not isinstance(digests, dict)
            or not isinstance(url, str)
        ):
            raise RecoveryVerificationError(
                "PyPI release integrity metadata is incomplete"
            )
        sha256 = digests.get("sha256")
        if (
            not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", sha256) is None
        ):
            raise RecoveryVerificationError("PyPI release SHA-256 metadata is invalid")
        _validate_pypi_file_url(url, filename)
        if filename in records:
            raise RecoveryVerificationError("PyPI release file metadata is ambiguous")
        records[filename] = PublishedFile(
            filename, str(package_type), sha256.lower(), url
        )

    if set(records) != set(expected):
        raise RecoveryVerificationError(
            "PyPI release does not contain the exact expected non-yanked wheel and sdist"
        )
    return records


def fetch_published_file_sha256(url: str) -> str:
    """Hash bytes fetched from an authoritative PyPI URL without logging it."""
    filename = unquote(PurePosixPath(urlparse(url).path).name)
    _validate_pypi_file_url(url, filename)
    request = urllib.request.Request(
        url, headers={"User-Agent": "yui-agent-policy-release-verifier"}
    )
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final_url = response.geturl()
            if not isinstance(final_url, str) or final_url != url:
                raise RecoveryVerificationError(
                    "published file redirected unexpectedly"
                )
            _validate_pypi_file_url(final_url, filename)
            for chunk in iter(lambda: response.read(1024 * 1024), b""):
                digest.update(chunk)
    except RecoveryVerificationError:
        raise
    except Exception:
        raise RecoveryVerificationError(
            "published release bytes are unavailable"
        ) from None
    return digest.hexdigest()


def artifact_zip_digests(
    archive_path: Path, expected_names: set[str]
) -> dict[str, str]:
    """Hash an exact flat regular-file ZIP without extracting it."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            members = archive.infolist()
            names = [member.filename for member in members]
            if len(members) != len(expected_names) or set(names) != expected_names:
                raise RecoveryVerificationError(
                    "release artifact ZIP has an unexpected or duplicate file set"
                )
            digests: dict[str, str] = {}
            for member in members:
                mode = member.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if (
                    member.is_dir()
                    or member.flag_bits & 0x1
                    or file_type not in {0, stat.S_IFREG}
                    or PurePosixPath(member.filename).name != member.filename
                ):
                    raise RecoveryVerificationError(
                        "release artifact ZIP contains a non-regular or unsafe entry"
                    )
                digest = hashlib.sha256()
                with archive.open(member) as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
                digests[member.filename] = digest.hexdigest()
            return digests
    except RecoveryVerificationError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile):
        raise RecoveryVerificationError(
            "release artifact ZIP is unavailable or invalid"
        ) from None


def verify_published_artifact_zip(
    archive_path: Path,
    project_name: str,
    version: str,
    pypi_data: dict[str, Any] | None,
    *,
    remote_digest: Callable[[str], str] = fetch_published_file_sha256,
) -> dict[str, str]:
    """Require artifact ZIP, PyPI metadata, and PyPI bytes to hash equally."""
    remote_files = published_files(project_name, version, pypi_data)
    artifact_digests = artifact_zip_digests(archive_path, set(remote_files))
    for filename, remote_file in remote_files.items():
        artifact_digest = artifact_digests[filename]
        if artifact_digest != remote_file.sha256:
            raise RecoveryVerificationError(
                f"release artifact SHA-256 does not match PyPI metadata: {filename}"
            )
        try:
            published_digest = remote_digest(remote_file.url)
        except RecoveryVerificationError:
            raise
        except Exception:
            raise RecoveryVerificationError(
                "published release bytes are unavailable"
            ) from None
        if (
            not isinstance(published_digest, str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", published_digest) is None
            or published_digest.lower() != remote_file.sha256
            or published_digest.lower() != artifact_digest
        ):
            raise RecoveryVerificationError(
                f"published file SHA-256 does not match release evidence: {filename}"
            )
    return artifact_digests


def inspect_existing_release(releases_payload: object, expected_tag: str) -> str:
    """Return absent/present only for an absent or clean public asset-free release."""
    if TAG_PATTERN.fullmatch(expected_tag) is None:
        raise RecoveryVerificationError("release tag is invalid")
    releases = _array_pages(releases_payload, "GitHub Release")
    matches = [
        release for release in releases if release.get("tag_name") == expected_tag
    ]
    if not matches:
        return "absent"
    if len(matches) != 1:
        raise RecoveryVerificationError("existing GitHub Release state is ambiguous")
    release = matches[0]
    assets = release.get("assets")
    if (
        release.get("draft") is not False
        or release.get("prerelease") is not False
        or not isinstance(release.get("published_at"), str)
        or not release["published_at"]
        or not isinstance(assets, list)
        or assets
    ):
        raise RecoveryVerificationError(
            "existing GitHub Release is not a clean public asset-free release"
        )
    return "present"


def _load_json(path: Path, label: str) -> object:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        raise RecoveryVerificationError(
            f"{label} metadata is unavailable or malformed"
        ) from None


def _identity_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha", required=True)


def _parser() -> argparse.ArgumentParser:
    parser = SanitizedArgumentParser(
        description="verify GitHub Release publication evidence"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, parser_class=SanitizedArgumentParser
    )

    select = subparsers.add_parser("select-run")
    select.add_argument("--runs-json", type=Path, required=True)
    select.add_argument("--repository", required=True)
    select.add_argument("--tag", required=True)
    select.add_argument("--sha", required=True)

    attempt = subparsers.add_parser("run-attempt")
    attempt.add_argument("--run-json", type=Path, required=True)
    attempt.add_argument("--mode", choices=["automatic", "recovery"], required=True)
    _identity_args(attempt)

    validate = subparsers.add_parser("validate-run")
    validate.add_argument("--run-json", type=Path, required=True)
    validate.add_argument("--jobs-json", type=Path, required=True)
    validate.add_argument("--artifacts-json", type=Path)
    validate.add_argument("--mode", choices=["automatic", "recovery"], required=True)
    validate.add_argument("--version", required=True)
    _identity_args(validate)

    artifact = subparsers.add_parser("verify-artifact")
    artifact.add_argument("--artifact-zip", type=Path, required=True)
    artifact.add_argument("--version", required=True)

    stable = subparsers.add_parser("verify-run-stable")
    stable.add_argument("--before-json", type=Path, required=True)
    stable.add_argument("--after-json", type=Path, required=True)
    _identity_args(stable)

    release = subparsers.add_parser("release-state")
    release.add_argument("--releases-json", type=Path, required=True)
    release.add_argument("--tag", required=True)
    return parser


def main(argv: list[str]) -> int:
    try:
        args = _parser().parse_args(argv[1:])
        if args.command == "select-run":
            result: int | str | None = select_release_run_id(
                _load_json(args.runs_json, "release run selection"),
                expected_repository=args.repository,
                expected_tag=args.tag,
                expected_sha=args.sha,
            )
        elif args.command == "run-attempt":
            result = inspect_run_attempt(
                _load_json(args.run_json, "release run"),
                mode=args.mode,
                expected_run_id=args.run_id,
                expected_repository=args.repository,
                expected_tag=args.tag,
                expected_sha=args.sha,
            )
        elif args.command == "validate-run":
            artifacts = (
                _load_json(args.artifacts_json, "release artifact")
                if args.artifacts_json is not None
                else None
            )
            result = validate_release_run(
                _load_json(args.run_json, "release run"),
                _load_json(args.jobs_json, "release job"),
                artifacts,
                mode=args.mode,
                expected_run_id=args.run_id,
                expected_repository=args.repository,
                expected_tag=args.tag,
                expected_sha=args.sha,
                expected_version=args.version,
            )
        elif args.command == "verify-artifact":
            project_name, _declared_version = load_project_metadata(
                Path("pyproject.toml")
            )
            try:
                pypi_data = fetch_pypi_release(project_name, args.version)
            except Exception:
                raise RecoveryVerificationError(
                    "PyPI release metadata is unavailable"
                ) from None
            verify_published_artifact_zip(
                args.artifact_zip, project_name, args.version, pypi_data
            )
            result = "release artifact matches authoritative published files"
        elif args.command == "verify-run-stable":
            validate_run_unchanged(
                _load_json(args.before_json, "initial release run"),
                _load_json(args.after_json, "final release run"),
                expected_run_id=args.run_id,
                expected_repository=args.repository,
                expected_tag=args.tag,
                expected_sha=args.sha,
            )
            result = "release run remained stable"
        else:
            result = inspect_existing_release(
                _load_json(args.releases_json, "GitHub Release"), args.tag
            )
    except RecoveryVerificationError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    except Exception:
        print("::error::release verification failed unexpectedly", file=sys.stderr)
        return 1

    if result is not None:
        print(result)
    else:
        print("automatic release run verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
