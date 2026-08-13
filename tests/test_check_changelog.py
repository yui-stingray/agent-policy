"""Where: tests/test_check_changelog.py
What: pin the changelog readiness guard used by CI and release workflows.
Why: missing release notes should be caught before a version tag is pushed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_changelog.py"
SPEC = importlib.util.spec_from_file_location("check_changelog", SCRIPT)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_changelog_versions_extract_semver_headings(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## 0.1.4 - 2026-04-30\n\n"
        "- Current release.\n\n"
        "## Not a version\n\n"
        "## 0.1.3\n",
        encoding="utf-8",
    )

    assert MODULE.changelog_versions(changelog) == ["0.1.4", "0.1.3"]


def test_current_project_version_has_changelog_entry() -> None:
    root = Path(__file__).resolve().parents[1]
    version = MODULE.load_project_version(root / "pyproject.toml")

    assert version in MODULE.changelog_versions(root / "CHANGELOG.md")


def test_extract_release_notes_returns_selected_body(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n"
        "## 0.1.4 - 2026-04-30\n\n"
        "- Current release.\n"
        "- Second item.\n\n"
        "## 0.1.3\n\n"
        "- Older release.\n",
        encoding="utf-8",
    )

    assert (
        MODULE.extract_release_notes(changelog, "0.1.4")
        == "- Current release.\n- Second item.\n"
    )


def test_main_extracts_notes_from_explicit_release_changelog(tmp_path: Path) -> None:
    changelog = tmp_path / "release-changelog.md"
    notes = tmp_path / "release-notes.md"
    changelog.write_text(
        "# Changelog\n\n## 0.1.6 - 2026-05-01\n\n- Historical release.\n",
        encoding="utf-8",
    )

    assert (
        MODULE.main(
            [
                "check_changelog.py",
                "--changelog",
                str(changelog),
                "--version",
                "0.1.6",
                "--write-notes",
                str(notes),
            ]
        )
        == 0
    )
    assert notes.read_text(encoding="utf-8") == "- Historical release.\n"


def test_main_rejects_empty_notes_without_write_mode(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "private-location" / "release-changelog.md"
    changelog.parent.mkdir()
    changelog.write_text(
        "# Changelog\n\n## 0.1.6 - 2026-05-01\n\n## 0.1.5\n\n- Older.\n",
        encoding="utf-8",
    )

    assert (
        MODULE.main(
            [
                "check_changelog.py",
                "--changelog",
                str(changelog),
                "--version",
                "0.1.6",
            ]
        )
        == 1
    )
    error = capsys.readouterr().err
    assert "selected changelog has an empty release notes section" in error
    assert str(changelog) not in error
    assert changelog.name not in error
