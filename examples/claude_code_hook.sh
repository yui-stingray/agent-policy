#!/usr/bin/env bash
# Where: examples/claude_code_hook.sh
# What: minimal Claude Code PreToolUse hook that consults agent-policy
#       before allowing a tool call. Reads the hook's JSON payload from
#       stdin, maps the tool to a capability, and shells out to
#       examples/check.py.
# Why: agent-policy is a pure decision function. Wrappers like this one
#      own intent parsing (which Bash command counts as "push --force"?)
#      so the core library stays free of shell-specific heuristics.
#
# Requires: bash 4+, jq, and a python3 on PATH that can `import agent_policy`
#           (i.e. the same interpreter that has yui-agent-policy installed).
#
# Install (excerpt of ~/.claude/settings.json):
#
#   {
#     "hooks": {
#       "PreToolUse": [
#         {
#           "matcher": "",
#           "hooks": [
#             { "type": "command",
#               "command": "/abs/path/to/agent-policy/examples/claude_code_hook.sh" }
#           ]
#         }
#       ]
#     }
#   }
#
# Required environment:
#   AGENT_POLICY_FILE — absolute path to a policy.toml
#   AGENT_POLICY_REPO — repo identifier matching a [[repo_policy]] entry
#                       (e.g. "acme/app")
#
# Optional environment:
#   AGENT_POLICY_OWNERSHIP — "internal" or "external" (unset by default)
#
# Exit codes (Claude Code PreToolUse contract):
#   0 — allow silently
#   2 — block; stderr is shown to Claude
#   1 — hook error; non-blocking, surfaced to the user
#
# Capability mapping (intentionally narrow — extend in your own wrapper):
#   Read / Glob / Grep         → read
#   Edit / Write / NotebookEdit → write
#   Bash:
#     git push ... --force[-with-lease] / -f → push.force
#     gh pr merge ...                        → merge.pr
#     anything else                          → shell
#   any other tool             → write   (fail-closed)
#
# Bash parsing: delegated to examples/capability_map.py, which uses
# shlex tokenization (not full shell semantics). Quoted literals like
# `printf '%s\n' 'git push --force'` are NOT misclassified as
# push.force — see capability_map.py header for the algorithm and
# accepted limitations.
#
# Limitations (fine for an example, not for production):
# - Tokenization is heuristic. Exotic forms such as
#   `git --git-dir=/path push --force` or process substitution are
#   not caught. Compound statements are classified per-statement
#   and the strictest capability wins (fail-closed direction).
# - No caching: every tool call shells out to python3. For high-frequency
#   workflows, wrap check.py in a long-lived subprocess instead.

set -euo pipefail

if ! command -v jq >/dev/null 2>&1; then
    echo "agent-policy hook: jq is required but not installed" >&2
    exit 1
fi

: "${AGENT_POLICY_FILE:?AGENT_POLICY_FILE must be set to a policy.toml path}"
: "${AGENT_POLICY_REPO:?AGENT_POLICY_REPO must be set to a repo identifier}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHECK_PY="${SCRIPT_DIR}/check.py"
CAPABILITY_MAP_PY="${SCRIPT_DIR}/capability_map.py"

# Slurp the entire stdin payload once. The hook contract delivers a
# single JSON object; jq is invoked twice against the same buffer.
PAYLOAD="$(cat)"

TOOL_NAME="$(printf '%s' "$PAYLOAD" | jq -r '.tool_name // empty')"
if [[ -z "$TOOL_NAME" ]]; then
    echo "agent-policy hook: missing tool_name in hook payload" >&2
    exit 1
fi

case "$TOOL_NAME" in
    Read|Glob|Grep)
        CAPABILITY="read"
        ;;
    Edit|Write|NotebookEdit)
        CAPABILITY="write"
        ;;
    Bash)
        COMMAND="$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // empty')"
        if [[ -z "$COMMAND" ]]; then
            echo "agent-policy hook: missing tool_input.command for Bash" >&2
            exit 1
        fi
        # Delegate Bash command → capability to the shlex-based helper.
        # See codex_hook.sh and capability_map.py for rationale.
        set +e
        CAPABILITY="$(python3 "$CAPABILITY_MAP_PY" "$COMMAND")"
        MAP_EXIT=$?
        set -e
        if [[ $MAP_EXIT -ne 0 || -z "$CAPABILITY" ]]; then
            echo "agent-policy hook: capability_map.py failed (exit ${MAP_EXIT})" >&2
            exit 1
        fi
        ;;
    *)
        # Unknown tools fall through to write — fail-closed by default.
        CAPABILITY="write"
        ;;
esac

CHECK_ARGS=(
    --policy "$AGENT_POLICY_FILE"
    --repo "$AGENT_POLICY_REPO"
    --capability "$CAPABILITY"
)
if [[ -n "${AGENT_POLICY_OWNERSHIP:-}" ]]; then
    CHECK_ARGS+=(--ownership-class "$AGENT_POLICY_OWNERSHIP")
fi

# check.py prints the JSON decision on stdout. We forward it to stderr
# only on block, so the auto_allow path stays completely silent.
set +e
DECISION_JSON="$(python3 "$CHECK_PY" "${CHECK_ARGS[@]}")"
CHECK_EXIT=$?
set -e

case "$CHECK_EXIT" in
    0)
        exit 0
        ;;
    2)
        echo "agent-policy: require_approval for ${TOOL_NAME} (capability=${CAPABILITY})" >&2
        echo "decision: ${DECISION_JSON}" >&2
        exit 2
        ;;
    3)
        echo "agent-policy: DENY ${TOOL_NAME} (capability=${CAPABILITY})" >&2
        echo "decision: ${DECISION_JSON}" >&2
        exit 2
        ;;
    *)
        echo "agent-policy hook: check.py failed with exit ${CHECK_EXIT}" >&2
        exit 1
        ;;
esac
