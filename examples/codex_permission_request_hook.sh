#!/usr/bin/env bash
# Where: examples/codex_permission_request_hook.sh
# What: minimal Codex CLI PermissionRequest hook that consults agent-policy.
# Why: PermissionRequest hooks can delegate back to Codex's normal approval
#      prompt by returning no decision, which is a better fit for
#      require_approval than a block-style PreToolUse hook.
#
# Scope: shell guardrail pilot only. This wrapper only maps Bash commands
#        even though Codex hooks can also match apply_patch and MCP tools.
#        Broader matchers need tool-specific capability normalization.
#
# Requires: bash 4+, jq, and a python3 on PATH that can `import agent_policy`
#           (i.e. the same interpreter that has yui-agent-policy installed).
#
# Install:
#   1. Hooks are default enabled in current Codex. If you need to set the
#      feature explicitly, use:
#        features.hooks = true
#
#   2. Place hooks.json in ~/.codex/ or <repo>/.codex/:
#
#      {
#        "hooks": {
#          "PermissionRequest": [
#            {
#              "matcher": "Bash",
#              "hooks": [
#                {
#                  "type": "command",
#                  "command": "/abs/path/to/agent-policy/examples/codex_permission_request_hook.sh",
#                  "statusMessage": "Checking approval request against policy"
#                }
#              ]
#            }
#          ]
#        }
#      }
#
# Required environment:
#   AGENT_POLICY_FILE - absolute path to a policy.toml
#   AGENT_POLICY_REPO - repo identifier matching a [[repo_policy]] entry
#                       (e.g. "acme/app")
#
# Optional environment:
#   AGENT_POLICY_OWNERSHIP - "internal" or "external" (unset by default)
#
# Decision mapping:
#   auto_allow        -> {"permissionDecision":"allow", ...}
#   deny              -> {"permissionDecision":"deny", ...}
#   require_approval  -> no stdout decision; Codex shows its normal prompt
#
# Capability mapping (Bash-only, intentionally narrow):
#   git push ... --force[-with-lease] / -f -> push.force
#   gh pr merge ...                         -> merge.pr
#   anything else                           -> shell

set -euo pipefail

emit_permission_decision() {
    local decision="$1"
    local reason="$2"
    jq -cn \
        --arg decision "$decision" \
        --arg reason "$reason" \
        '{permissionDecision: $decision, permissionDecisionReason: $reason}'
}

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

set +e
COMMAND="$(printf '%s' "$PAYLOAD" | jq -r '.tool_input.command // empty')"
JQ_EXIT=$?
set -e
if [[ $JQ_EXIT -ne 0 ]]; then
    emit_permission_decision "deny" "agent-policy hook: invalid PermissionRequest JSON payload"
    exit 0
fi
if [[ -z "$COMMAND" ]]; then
    emit_permission_decision "deny" "agent-policy hook: missing tool_input.command"
    exit 0
fi

set +e
CAPABILITY="$(python3 "$CAPABILITY_MAP_PY" "$COMMAND")"
MAP_EXIT=$?
set -e
if [[ $MAP_EXIT -ne 0 || -z "$CAPABILITY" ]]; then
    emit_permission_decision "deny" "agent-policy hook: capability_map.py failed"
    exit 0
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
CHECK_OUTPUT="$(python3 "$CHECK_PY" "${CHECK_ARGS[@]}" 2>&1)"
CHECK_EXIT=$?
set -e

case "$CHECK_EXIT" in
    0)
        emit_permission_decision "allow" "agent-policy: auto_allow for Bash (capability=${CAPABILITY})"
        ;;
    2)
        echo "agent-policy: require_approval for Bash (capability=${CAPABILITY}); delegating to Codex approval prompt" >&2
        ;;
    3)
        emit_permission_decision "deny" "agent-policy: DENY for Bash (capability=${CAPABILITY})"
        ;;
    *)
        echo "agent-policy hook: check.py failed with exit ${CHECK_EXIT}" >&2
        if [[ -n "$CHECK_OUTPUT" ]]; then
            echo "$CHECK_OUTPUT" >&2
        fi
        emit_permission_decision "deny" "agent-policy hook failed closed for Bash (capability=${CAPABILITY})"
        ;;
esac
