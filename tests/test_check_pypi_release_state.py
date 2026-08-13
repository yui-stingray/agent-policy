"""Where: tests/test_check_pypi_release_state.py
What: unit tests for the PyPI release-state preflight.
Why: keep immutable-version checks predictable without live network calls.
"""

from __future__ import annotations

import importlib.util
import urllib.error
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_pypi_release_state.py"
SPEC = importlib.util.spec_from_file_location("check_pypi_release_state", SCRIPT)
assert SPEC is not None
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
check_release_state = MODULE.check_release_state
require_release_present = MODULE.require_release_present


def test_missing_project_is_allowed_with_pending_publisher_note() -> None:
    ok, message = check_release_state("yui-agent-policy", "0.1.0", None)

    assert ok is True
    assert "does not exist yet" in message
    assert "Trusted Publisher" in message


def test_existing_version_blocks_release() -> None:
    ok, message = check_release_state(
        "yui-agent-policy",
        "0.1.3",
        {"info": {"version": "0.1.3"}, "releases": {"0.1.3": [{}]}},
    )

    assert ok is False
    assert "already exists" in message


def test_new_version_for_existing_project_is_allowed() -> None:
    ok, message = check_release_state(
        "yui-agent-policy",
        "0.1.4",
        {"info": {"version": "0.1.3"}, "releases": {"0.1.3": [{}]}},
    )

    assert ok is True
    assert "candidate=0.1.4" in message


def test_require_present_accepts_exact_version_with_wheel_and_sdist() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "urls": [
                {
                    "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "yanked": False,
                },
                {
                    "filename": "yui_agent_policy-0.1.7.tar.gz",
                    "packagetype": "sdist",
                    "yanked": False,
                },
            ]
        },
    )

    assert ok is True
    assert "yui-agent-policy==0.1.7" in message
    assert "exact wheel and sdist found" in message


def test_require_present_rejects_missing_exact_version() -> None:
    ok, message = require_release_present("yui-agent-policy", "0.1.7", None)

    assert ok is False
    assert "do not match expected set" in message


def test_require_present_rejects_partial_distribution_set() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "urls": [
                {
                    "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "yanked": False,
                }
            ]
        },
    )

    assert ok is False
    assert "do not match expected set" in message


def test_require_present_rejects_wrong_or_extra_distribution_files() -> None:
    expected_sdist = {
        "filename": "yui_agent_policy-0.1.7.tar.gz",
        "packagetype": "sdist",
        "yanked": False,
    }
    wrong_wheel = {
        "filename": "yui_agent_policy-0.1.7-cp312-cp312-manylinux_x86_64.whl",
        "packagetype": "bdist_wheel",
        "yanked": False,
    }
    extra_wheel = {
        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
        "packagetype": "bdist_wheel",
        "yanked": False,
    }

    wrong_ok, _wrong_message = require_release_present(
        "yui-agent-policy", "0.1.7", {"urls": [wrong_wheel, expected_sdist]}
    )
    extra_ok, _extra_message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {"urls": [wrong_wheel, extra_wheel, expected_sdist]},
    )

    assert wrong_ok is False
    assert extra_ok is False


def test_require_present_rejects_yanked_expected_file() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "urls": [
                {
                    "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "yanked": True,
                },
                {
                    "filename": "yui_agent_policy-0.1.7.tar.gz",
                    "packagetype": "sdist",
                    "yanked": False,
                },
            ]
        },
    )

    assert ok is False
    assert "files are yanked" in message


def test_require_present_messages_do_not_emit_urls_or_response_content() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "urls": [
                {
                    "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "yanked": False,
                    "url": "https://files.pythonhosted.org/packages/private.whl",
                }
            ]
        },
    )

    assert ok is False
    assert "https://" not in message
    assert "private.whl" not in message


def test_require_present_rejects_project_level_release_shape() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "releases": {
                "0.1.7": [
                    {
                        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                    },
                    {
                        "filename": "yui_agent_policy-0.1.7.tar.gz",
                        "packagetype": "sdist",
                    },
                ]
            }
        },
    )

    assert ok is False
    assert "do not match expected set" in message


@pytest.mark.parametrize(
    "malformed_file",
    [
        "not-a-file-object",
        {},
        {"filename": "unexpected.whl", "packagetype": "bdist_wheel"},
        {"filename": 7, "packagetype": "bdist_wheel", "yanked": False},
        {"filename": "unexpected.whl", "packagetype": 7, "yanked": False},
        {"filename": "unexpected.whl", "packagetype": "bdist_wheel", "yanked": "false"},
    ],
)
def test_require_present_rejects_malformed_additional_file(
    malformed_file: object,
) -> None:
    expected_files = [
        {
            "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
            "packagetype": "bdist_wheel",
            "yanked": False,
        },
        {
            "filename": "yui_agent_policy-0.1.7.tar.gz",
            "packagetype": "sdist",
            "yanked": False,
        },
    ]

    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {"urls": [*expected_files, malformed_file]},
    )

    assert ok is False
    assert "do not match expected set" in message
    assert "unexpected.whl" not in message


def test_main_uses_exact_release_endpoint_for_presence_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        MODULE,
        "load_project_metadata",
        lambda _path: ("yui-agent-policy", "0.1.8"),
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_pypi_project",
        lambda _project: pytest.fail(
            "project endpoint must not serve post-release checks"
        ),
    )
    observed: list[tuple[str, str]] = []

    def fake_fetch_release(project_name: str, version: str) -> dict[str, object]:
        observed.append((project_name, version))
        return {
            "urls": [
                {
                    "filename": "yui_agent_policy-0.1.8-py3-none-any.whl",
                    "packagetype": "bdist_wheel",
                    "yanked": False,
                },
                {
                    "filename": "yui_agent_policy-0.1.8.tar.gz",
                    "packagetype": "sdist",
                    "yanked": False,
                },
            ]
        }

    monkeypatch.setattr(MODULE, "fetch_pypi_release", fake_fetch_release)

    assert (
        MODULE.main(["check_pypi_release_state.py", "--require-present", "0.1.8"]) == 0
    )
    assert observed == [("yui-agent-policy", "0.1.8")]
    assert "exact wheel and sdist found" in capsys.readouterr().out


def test_main_keeps_project_endpoint_for_pre_upload_check(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        MODULE,
        "load_project_metadata",
        lambda _path: ("yui-agent-policy", "0.1.10"),
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_pypi_release",
        lambda _project, _version: pytest.fail(
            "release endpoint must not be primed before upload"
        ),
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_pypi_project",
        lambda _project: {"info": {"version": "0.1.9"}, "releases": {"0.1.9": []}},
    )

    assert MODULE.main(["check_pypi_release_state.py"]) == 0
    assert "candidate=0.1.10" in capsys.readouterr().out


def test_main_sanitizes_network_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    raw_url = "https://files.pythonhosted.org/packages/private-token.whl"
    monkeypatch.setattr(
        MODULE,
        "load_project_metadata",
        lambda _path: ("yui-agent-policy", "0.1.11"),
    )
    monkeypatch.setattr(
        MODULE,
        "fetch_pypi_release",
        lambda _project, _version: (_ for _ in ()).throw(
            urllib.error.URLError(raw_url)
        ),
    )

    assert (
        MODULE.main(["check_pypi_release_state.py", "--require-present", "0.1.11"]) == 1
    )
    captured = capsys.readouterr()
    assert "could not be verified" in captured.err
    assert raw_url not in captured.err
    assert "private-token.whl" not in captured.err
