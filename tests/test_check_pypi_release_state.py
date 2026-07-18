"""Where: tests/test_check_pypi_release_state.py
What: unit tests for the PyPI release-state preflight.
Why: keep immutable-version checks predictable without live network calls.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


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
            "releases": {
                "0.1.6": [
                    {
                        "filename": "yui_agent_policy-0.1.6-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                    },
                    {"filename": "yui_agent_policy-0.1.6.tar.gz", "packagetype": "sdist"},
                ],
                "0.1.7": [
                    {
                        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                    },
                    {"filename": "yui_agent_policy-0.1.7.tar.gz", "packagetype": "sdist"},
                ],
            }
        },
    )

    assert ok is True
    assert "yui-agent-policy==0.1.7" in message
    assert "exact wheel and sdist found" in message


def test_require_present_rejects_missing_exact_version() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "releases": {
                "0.1.6": [
                    {
                        "filename": "yui_agent_policy-0.1.6-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                    },
                    {"filename": "yui_agent_policy-0.1.6.tar.gz", "packagetype": "sdist"},
                ]
            }
        },
    )

    assert ok is False
    assert "do not match expected set" in message


def test_require_present_rejects_partial_distribution_set() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "releases": {
                "0.1.7": [
                    {
                        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                    }
                ]
            }
        },
    )

    assert ok is False
    assert "do not match expected set" in message


def test_require_present_rejects_wrong_or_extra_distribution_files() -> None:
    expected_sdist = {"filename": "yui_agent_policy-0.1.7.tar.gz", "packagetype": "sdist"}
    wrong_wheel = {
        "filename": "yui_agent_policy-0.1.7-cp312-cp312-manylinux_x86_64.whl",
        "packagetype": "bdist_wheel",
    }
    extra_wheel = {
        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
        "packagetype": "bdist_wheel",
    }

    wrong_ok, _wrong_message = require_release_present(
        "yui-agent-policy", "0.1.7", {"releases": {"0.1.7": [wrong_wheel, expected_sdist]}}
    )
    extra_ok, _extra_message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {"releases": {"0.1.7": [wrong_wheel, extra_wheel, expected_sdist]}},
    )

    assert wrong_ok is False
    assert extra_ok is False


def test_require_present_rejects_yanked_expected_file() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "releases": {
                "0.1.7": [
                    {
                        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                        "yanked": True,
                    },
                    {"filename": "yui_agent_policy-0.1.7.tar.gz", "packagetype": "sdist"},
                ]
            }
        },
    )

    assert ok is False
    assert "files are yanked" in message


def test_require_present_messages_do_not_emit_urls_or_response_content() -> None:
    ok, message = require_release_present(
        "yui-agent-policy",
        "0.1.7",
        {
            "releases": {
                "0.1.7": [
                    {
                        "filename": "yui_agent_policy-0.1.7-py3-none-any.whl",
                        "packagetype": "bdist_wheel",
                        "url": "https://files.pythonhosted.org/packages/private.whl",
                    }
                ]
            }
        },
    )

    assert ok is False
    assert "https://" not in message
    assert "private.whl" not in message
