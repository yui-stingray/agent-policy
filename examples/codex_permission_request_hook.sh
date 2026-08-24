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
#   AGENT_POLICY_FIRST_WRITE - wrapper-owned state for external mutating
#       capabilities: exactly "true" adds --first-write; exactly "false"
#       does not. Missing or other values deny. It is never read from the
#       hook payload.
#
# Decision mapping:
#   auto_allow        -> {"permissionDecision":"allow", ...}
#   deny              -> {"permissionDecision":"deny", ...}
#   require_approval  -> no stdout decision; Codex shows its normal prompt
#   hook failure       -> fixed protocol-valid deny JSON and exit 0
#
# Capability mapping (Bash-only, intentionally narrow):
#   git push ... --force[-with-lease] / -f -> push.force
#   gh pr merge ...                         -> merge.pr
#   anything else                           -> shell

# Bash processes the hook process's inherited startup environment before this
# file runs; that launcher boundary is trusted. Payload commands that assign
# startup-file selectors are rejected by capability_map.py. This must be the
# first executable statement so inherited xtrace cannot expose hook inputs.
set +x
set -Eeuo pipefail

# This fallback is intentionally literal: it must remain valid protocol JSON
# even when jq is unavailable or every later dependency has failed.
readonly DENY_JSON='{"permissionDecision":"deny","permissionDecisionReason":"agent-policy hook: denied"}'
readonly ALLOW_JSON='{"permissionDecision":"allow","permissionDecisionReason":"agent-policy: auto_allow"}'

deny() {
    printf '%s\n' "$DENY_JSON"
    exit 0
}

trap 'deny' ERR

is_single_json_object() {
    jq -se 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1
}

if ! command -v jq >/dev/null 2>&1; then
    deny
fi
if [[ -z "${AGENT_POLICY_FILE:-}" || -z "${AGENT_POLICY_REPO:-}" ]]; then
    deny
fi

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
SCRIPT_DIR="${SCRIPT_SOURCE%/*}"
if [[ "$SCRIPT_DIR" == "$SCRIPT_SOURCE" ]]; then
    SCRIPT_DIR="."
fi
if ! SCRIPT_DIR="$(cd -- "$SCRIPT_DIR" 2>/dev/null && pwd -P 2>/dev/null)"; then
    deny
fi
CHECK_PY="${SCRIPT_DIR}/check.py"
CAPABILITY_MAP_PY="${SCRIPT_DIR}/capability_map.py"

if ! PAYLOAD="$(cat 2>/dev/null)"; then
    deny
fi
if ! is_single_json_object <<<"$PAYLOAD"; then
    deny
fi
if ! COMMAND="$(jq -er '.tool_input.command | strings | select(length > 0 and index("\u0000") == null)' <<<"$PAYLOAD" 2>/dev/null)"; then
    deny
fi
if ! CAPABILITY="$(python3 "$CAPABILITY_MAP_PY" "$COMMAND" 2>/dev/null)"; then
    deny
fi
case "$CAPABILITY" in
    push.force|merge.pr|shell)
        ;;
    *)
        # Includes the classifier's dedicated ``unknown`` result.
        deny
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

# This state belongs to the wrapper environment, never to the tool payload.
# External mutating actions must say whether this is the first write; a true
# value activates the evaluator's hard guardrail, while false leaves it off.
if [[ "${AGENT_POLICY_OWNERSHIP:-}" == "external" ]]; then
    case "$CAPABILITY" in
        read)
            ;;
        write|commit|push|push.force|merge.pr|shell)
            case "${AGENT_POLICY_FIRST_WRITE:-}" in
                true)
                    CHECK_ARGS+=(--first-write)
                    ;;
                false)
                    ;;
                *)
                    deny
                    ;;
            esac
            ;;
        *)
            deny
            ;;
    esac
fi

if DECISION_JSON="$(python3 "$CHECK_PY" "${CHECK_ARGS[@]}" 2>/dev/null)"; then
    CHECK_EXIT=0
else
    CHECK_EXIT=$?
fi

is_expected_decision() {
    local expected_mode="$1"
    jq -se --arg expected_mode "$expected_mode" '
        length == 1
        and (.[0] | type == "object")
        and (.[0] | keys == ["matched_repo", "mode", "reason"])
        and .[0].mode == $expected_mode
        and (.[0].reason | type == "string")
        and (.[0].matched_repo == null or (.[0].matched_repo | type == "string"))
    ' <<<"$DECISION_JSON" >/dev/null 2>&1
}

case "$CHECK_EXIT" in
    0)
        if ! is_expected_decision "auto_allow"; then
            deny
        fi
        printf '%s\n' "$ALLOW_JSON"
        ;;
    2)
        if ! is_expected_decision "require_approval"; then
            deny
        fi
        # No stdout decision delegates to Codex's normal approval prompt.
        exit 0
        ;;
    3)
        if ! is_expected_decision "deny"; then
            deny
        fi
        deny
        ;;
    *)
        deny
        ;;
esac
