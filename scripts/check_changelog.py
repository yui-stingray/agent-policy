"""Where: scripts/check_changelog.py
What: ensure CHANGELOG.md has notes for the current package version.
Why: fail releases before tagging when user-visible release notes are missing.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


VERSION_HEADING_RE = re.compile(r"^##\s+(?P<version>\d+\.\d+\.\d+)(?:\s+-\s+.*)?$")
DEVELOPMENT_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+\.dev\d+$")


def load_project_version(pyproject_path: Path) -> str:
    """Return the declared package version from pyproject.toml."""
    with pyproject_path.open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


def changelog_versions(changelog_path: Path) -> list[str]:
    """Return semantic versions that have a second-level changelog heading."""
    versions: list[str] = []
    for line in changelog_path.read_text(encoding="utf-8").splitlines():
        match = VERSION_HEADING_RE.match(line)
        if match:
            versions.append(match.group("version"))
    return versions


def is_development_version(version: str) -> bool:
    """Return whether version is a PEP 440 development release."""
    return DEVELOPMENT_VERSION_RE.fullmatch(version) is not None


def extract_release_notes(changelog_path: Path, version: str) -> str:
    """Return the changelog body for one version heading."""
    lines = changelog_path.read_text(encoding="utf-8").splitlines()
    heading = re.compile(rf"^##\s+{re.escape(version)}(?:\s+-\s+.*)?$")
    start: int | None = None
    for index, line in enumerate(lines):
        if heading.match(line):
            start = index + 1
            break
    if start is None:
        raise ValueError(
            f"selected changelog is missing release notes for version {version}"
        )

    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)

    notes = "\n".join(body).strip()
    if not notes:
        raise ValueError(
            f"selected changelog has an empty release notes section for version {version}"
        )
    return notes + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--version",
        help="version to check; defaults to pyproject.toml [project].version",
    )
    parser.add_argument(
        "--write-notes",
        help="write extracted notes for the selected version to this path",
    )
    parser.add_argument(
        "--changelog",
        type=Path,
        default=Path("CHANGELOG.md"),
        help="changelog file to read; defaults to CHANGELOG.md",
    )
    args = parser.parse_args(argv[1:])

    version = args.version or load_project_version(Path("pyproject.toml"))
    notes_version = "Unreleased" if is_development_version(version) else version
    try:
        notes = extract_release_notes(args.changelog, notes_version)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, UnicodeError):
        print("selected changelog could not be read", file=sys.stderr)
        return 1

    if args.write_notes:
        try:
            Path(args.write_notes).write_text(notes, encoding="utf-8")
        except OSError:
            print("release notes could not be written", file=sys.stderr)
            return 1
        print(f"wrote release notes for version {version}")
        return 0

    print(f"selected changelog contains release notes for version {version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
