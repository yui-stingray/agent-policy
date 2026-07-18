"""Where: scripts/check_pypi_release_state.py
What: verify package release state on PyPI.
Why: keep pre-upload immutability checks and post-upload presence checks predictable.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


def load_project_metadata(pyproject_path: Path) -> tuple[str, str]:
    """Return the declared package name and version from pyproject.toml."""
    with pyproject_path.open("rb") as handle:
        project = tomllib.load(handle)["project"]
    return str(project["name"]), str(project["version"])


def fetch_pypi_project(project_name: str) -> dict[str, Any] | None:
    """Return PyPI JSON metadata, or None when the project does not exist yet."""
    url = f"https://pypi.org/pypi/{project_name}/json"
    try:
        with urllib.request.urlopen(url, timeout=20) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def check_release_state(project_name: str, version: str, pypi_data: dict[str, Any] | None) -> tuple[bool, str]:
    """Return whether a release may proceed and a human-readable reason."""
    if pypi_data is None:
        return (
            True,
            f"PyPI project {project_name!r} does not exist yet; pending Trusted Publisher setup must exist.",
        )

    releases = pypi_data.get("releases", {})
    if isinstance(releases, dict) and version in releases:
        return False, f"PyPI version already exists: {project_name}=={version}"

    latest = pypi_data.get("info", {}).get("version", "unknown")
    return True, f"PyPI project exists; latest={latest}, candidate={version}"


def release_files(
    pypi_data: dict[str, Any] | None, version: str
) -> list[tuple[str, str, bool]]:
    """Return filename, package type, and yanked state for an exact version."""
    if pypi_data is None:
        return []

    releases = pypi_data.get("releases", {})
    if not isinstance(releases, dict):
        return []

    files = releases.get(version, [])
    if not isinstance(files, list):
        return []

    return [
        (
            str(file_info.get("filename")),
            str(file_info.get("packagetype")),
            bool(file_info.get("yanked", False)),
        )
        for file_info in files
        if isinstance(file_info, dict)
        and file_info.get("filename") is not None
        and file_info.get("packagetype") is not None
    ]


def expected_release_files(project_name: str, version: str) -> list[tuple[str, str]]:
    """Return the exact wheel and sdist names produced by this project."""
    distribution_name = re.sub(r"[-_.]+", "_", project_name).lower()
    return [
        (f"{distribution_name}-{version}-py3-none-any.whl", "bdist_wheel"),
        (f"{distribution_name}-{version}.tar.gz", "sdist"),
    ]


def require_release_present(
    project_name: str, version: str, pypi_data: dict[str, Any] | None
) -> tuple[bool, str]:
    """Return whether PyPI has exactly the expected non-yanked release files."""
    files = release_files(pypi_data, version)
    observed = sorted((filename, package_type) for filename, package_type, _yanked in files)
    expected = sorted(expected_release_files(project_name, version))
    if observed != expected:
        return False, f"PyPI release files do not match expected set: {project_name}=={version}"
    if any(yanked for _filename, _package_type, yanked in files):
        return False, f"PyPI release files are yanked: {project_name}=={version}"
    return True, f"PyPI release present: {project_name}=={version}; exact wheel and sdist found"


def main(argv: list[str]) -> int:
    if len(argv) not in {1, 3} or (len(argv) == 3 and argv[1] != "--require-present"):
        print("usage: check_pypi_release_state.py [--require-present VERSION]", file=sys.stderr)
        return 2

    project_name, declared_version = load_project_metadata(Path("pyproject.toml"))
    pypi_data = fetch_pypi_project(project_name)
    if len(argv) == 3:
        ok, message = require_release_present(project_name, argv[2], pypi_data)
    else:
        ok, message = check_release_state(project_name, declared_version, pypi_data)
    stream = sys.stdout if ok else sys.stderr
    print(message, file=stream)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
