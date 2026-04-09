#!/usr/bin/env bash
# Where: examples/codex_hook.sh
# What: minimal Codex CLI PreToolUse hook that consults agent-policy
#       before allowing a Bash command. Shell guardrail pilot — this
#       does NOT cover read/write/edit (Codex hooks only intercept Bash).
# Why: agent-policy is a pure decision function. This wrapper maps a
#      Bash command to one of three capabilities (push.force, merge.pr,
#      shell) and delegates the policy decision to examples/check.py.
#
# Scope: shell guardrail pilot only. Codex CLI hooks currently intercept
#        Bash commands and nothing else. Read, write, and edit operations
#        are NOT covered by this hook. Do not rely on it for full
#        capability-based access control — use the Claude Code hook
#        (examples/claude_code_hook.sh) if your agent supports it.
#
# Requires: bash 4+, jq, and a python3 on PATH that can `import agent_policy`
#           (i.e. the same interpreter that has yui-agent-policy installed).
#
# Install:
#   1. Enable hooks in your Codex CLI config:
#        features.codex_hooks = true
#
#   2. Place hooks.json in ~/.codex/ or <repo>/.codex/:
#
#      {
#        "hooks": {
#          "PreToolUse": [
#            {
#              "matcher": "Bash",
#              "hooks": [
#                {
#                  "type": "command",
#                  "command": "/abs/path/to/agent-policy/examples/codex_hook.sh",
#                  "statusMessage": "Checking shell command against policy"
#                }
#              ]
#            }
#          ]
#        }
#      }
#
# Required environment:
#   AGENT_POLICY_FILE — absolute path to a policy.toml
#   AGENT_POLICY_REPO — repo identifier matching a [[repo_policy]] entry
#                       (e.g. "acme/app")
#
# Optional environment:
#   AGENT_POLICY_OWNERSHIP — "internal" or "external" (unset by default)
#
# Exit codes (Codex CLI PreToolUse contract):
#   0 — allow silently
#   2 — block; stderr reason is shown to the model
#   1 — hook error; non-blocking
#
# Capability mapping (Bash-only, intentionally narrow):
#   git push ... --force[-with-lease] / -f → push.force
#   gh pr merge ...                        → merge.pr
#   anything else                          → shell
#
# Parsing: command → capability goes through examples/capability_map.py,
# which uses shlex tokenization (not full shell semantics) so that
# quoted literals like `printf '%s\n' 'git push --force'` are NOT
# misclassified as push.force. See capability_map.py header for the
# exact algorithm and known limitations.
#
# Limitations:
# - Codex hooks only intercept Bash — read/write/edit tools are invisible.
# - Tokenization is heuristic, not a real shell. Exotic forms such as
#   `git --git-dir=/path push --force` or process substitution are
#   not matched. Compound statements are classified per-statement and
#   the strictest capability wins (fail-closed direction).
# - No caching: every Bash call shells out to python3.

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

PAYLOAD="$(cat)"

COMMAND="$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // empty')"
if [[ -z "$COMMAND" ]]; then
    echo "agent-policy hook: missing tool_input.command in hook payload" >&2
    exit 1
fi

# Map command → capability via the shlex-based helper. Passing the
# command as argv (not stdin) keeps newlines intact and avoids a
# second pipe. The helper is stdlib-only and exits zero for all
# classifications, so a non-zero exit here is a real failure.
set +e
CAPABILITY="$(python3 "$CAPABILITY_MAP_PY" "$COMMAND")"
MAP_EXIT=$?
set -e
if [[ $MAP_EXIT -ne 0 || -z "$CAPABILITY" ]]; then
    echo "agent-policy hook: capability_map.py failed (exit ${MAP_EXIT})" >&2
    exit 1
fi

CHECK_ARGS=(
    --policy "$AGENT_POLICY_FILE"
    --repo "$AGENT_POLICY_REPO"
    --capability "$CAPABILITY"
)
if [[ -n "${AGENT_POLICY_OWNERSHIP:-}" ]]; then
    CHECK_ARGS+=(--ownership-class "$AGENT_POLICY_OWNERSHIP")
fi

set +e
DECISION_JSON="$(python3 "$CHECK_PY" "${CHECK_ARGS[@]}")"
CHECK_EXIT=$?
set -e

case "$CHECK_EXIT" in
    0)
        exit 0
        ;;
    2)
        echo "agent-policy: require_approval for Bash (capability=${CAPABILITY})" >&2
        echo "decision: ${DECISION_JSON}" >&2
        exit 2
        ;;
    3)
        echo "agent-policy: DENY Bash (capability=${CAPABILITY})" >&2
        echo "decision: ${DECISION_JSON}" >&2
        exit 2
        ;;
    *)
        echo "agent-policy hook: check.py failed with exit ${CHECK_EXIT}" >&2
        exit 1
        ;;
esac
