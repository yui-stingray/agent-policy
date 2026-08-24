#!/usr/bin/env bash
# Where: examples/codex_hook.sh
# What: minimal Codex CLI PreToolUse hook that consults agent-policy
#       before allowing a Bash command. Shell guardrail pilot — this
#       wrapper does not normalize apply_patch or MCP tool payloads.
# Why: agent-policy is a pure decision function. This wrapper maps a
#      Bash command to one of three capabilities (push.force, merge.pr,
#      shell) and delegates the policy decision to examples/check.py.
#
# Scope: shell guardrail pilot only. This wrapper only maps Bash commands
#        even though Codex hooks can also match apply_patch and MCP tools.
#        Do not rely on this example for full capability-based access
#        control; add tool-specific normalization before broadening its
#        matcher.
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
#   AGENT_POLICY_FIRST_WRITE — wrapper-owned state for external mutating
#       capabilities: exactly "true" adds --first-write; exactly "false"
#       does not. Missing or other values block. It is never read from the
#       hook payload.
#
# Exit codes (Codex CLI PreToolUse contract):
#   0 — allow silently
#   2 — block; every non-allow outcome uses fixed sanitized stderr
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
# - This example covers only Bash PreToolUse payloads. Broader Codex hook
#   matchers need their own capability normalization.
# - Tokenization is heuristic, not a real shell. Exotic forms such as
#   `git --git-dir=/path push --force` or process substitution are
#   not fully modeled. Ambiguous, unbalanced, or unterminated syntax maps
#   to `unknown` and blocks before policy evaluation. Compound statements
#   are classified per-statement and the strictest capability wins.
# - No caching: every Bash call shells out to python3.

# Bash processes the hook process's inherited startup environment before this
# file runs; that launcher boundary is trusted. Payload commands that assign
# startup-file selectors are rejected by capability_map.py. This must be the
# first executable statement so inherited xtrace cannot expose hook inputs.
set +x
set -Eeuo pipefail

# PreToolUse treats exit 2 as a hard block. Every wrapper failure uses this
# fixed text so payloads, policy paths, and evaluator diagnostics stay private.
readonly BLOCK_MESSAGE="agent-policy hook: blocked"

block() {
    trap - ERR
    printf '%s\n' "$BLOCK_MESSAGE" >&2
    exit 2
}

trap 'block' ERR

is_single_json_object() {
    jq -se 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1
}

if ! command -v jq >/dev/null 2>&1; then
    block
fi
if [[ -z "${AGENT_POLICY_FILE:-}" || -z "${AGENT_POLICY_REPO:-}" ]]; then
    block
fi

SCRIPT_SOURCE="${BASH_SOURCE[0]}"
SCRIPT_DIR="${SCRIPT_SOURCE%/*}"
if [[ "$SCRIPT_DIR" == "$SCRIPT_SOURCE" ]]; then
    SCRIPT_DIR="."
fi
if ! SCRIPT_DIR="$(cd -- "$SCRIPT_DIR" 2>/dev/null && pwd -P 2>/dev/null)"; then
    block
fi
CHECK_PY="${SCRIPT_DIR}/check.py"
CAPABILITY_MAP_PY="${SCRIPT_DIR}/capability_map.py"

if ! PAYLOAD="$(cat 2>/dev/null)"; then
    block
fi
if ! is_single_json_object <<<"$PAYLOAD"; then
    block
fi
if ! COMMAND="$(jq -er '.tool_input.command | strings | select(length > 0 and index("\u0000") == null)' <<<"$PAYLOAD" 2>/dev/null)"; then
    block
fi

# capability_map.py is intentionally allowed to return ``unknown`` for
# syntax it cannot classify safely. Only expected, definite capabilities may
# reach policy evaluation.
if ! CAPABILITY="$(python3 "$CAPABILITY_MAP_PY" "$COMMAND" 2>/dev/null)"; then
    block
fi
case "$CAPABILITY" in
    push.force|merge.pr|shell)
        ;;
    *)
        block
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
                    block
                    ;;
            esac
            ;;
        *)
            block
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
            block
        fi
        # Successful auto_allow is deliberately silent.
        exit 0
        ;;
    2)
        if ! is_expected_decision "require_approval"; then
            block
        fi
        block
        ;;
    3)
        if ! is_expected_decision "deny"; then
            block
        fi
        block
        ;;
    *)
        block
        ;;
esac
