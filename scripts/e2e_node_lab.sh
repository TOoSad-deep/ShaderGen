#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PWCLI="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
SESSION="shadergen-node-lab-e2e"
ARTIFACT_DIR="$ROOT/output/playwright"
export npm_config_cache="${npm_config_cache:-${TMPDIR:-/tmp}/shadergen-npm-cache}"
VITE_PORT="${SHADERGEN_NODE_LAB_E2E_VITE_PORT:-15175}"
API_PORT="${SHADERGEN_NODE_LAB_E2E_API_PORT:-18090}"
VITE_ORIGIN="http://127.0.0.1:$VITE_PORT"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  "$PWCLI" -s="$SESSION" close >/dev/null 2>&1 || true
  kill "${VITE_PID:-}" "${API_PID:-}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

SHADERGEN_FAKE_API_PORT="$API_PORT" SHADERGEN_E2E_ORIGIN="$VITE_ORIGIN" \
  uv run python "$ROOT/scripts/fake_node_lab_api.py" >"$ARTIFACT_DIR/node-lab-api.log" 2>&1 &
API_PID=$!
VITE_NODE_LAB_API_BASE_URL="http://127.0.0.1:$API_PORT" \
  npm --prefix "$ROOT/frontend" run dev -- --host 127.0.0.1 --port "$VITE_PORT" --strictPort >"$ARTIFACT_DIR/node-lab-vite.log" 2>&1 &
VITE_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS "$VITE_ORIGIN/lab" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "$VITE_ORIGIN/lab" >/dev/null

find_role_ref() {
  local role="$1"
  local text="$2"
  local output
  output="$("$PWCLI" -s="$SESSION" find "$text")"
  printf '%s\n' "$output" \
    | sed -nE "/${role} \"${text}\"/s/.*ref=([[:alnum:]]*e[0-9]+).*/\1/p" \
    | head -1
}

require_text() {
  local text="$1"
  "$PWCLI" -s="$SESSION" find "$text" | grep -E 'Found [1-9][0-9]* match' >/dev/null
}

"$PWCLI" -s="$SESSION" open "$VITE_ORIGIN/lab" >/dev/null
"$PWCLI" -s="$SESSION" snapshot >/dev/null
require_text '20 个节点可用'
require_text 'Real Model：关闭'

CREATE_REF="$(find_role_ref button '新建 LabRun')"
"$PWCLI" -s="$SESSION" click "$CREATE_REF" >/dev/null
require_text 'lab-e2e-run-0001'

SEARCH_REF="$(find_role_ref textbox '搜索节点')"
"$PWCLI" -s="$SESSION" fill "$SEARCH_REF" 'decide_after_render' >/dev/null
NODE_REF="$(find_role_ref button '01 decide_after_render routing')"
if [[ -z "$NODE_REF" ]]; then
  SNAPSHOT="$ARTIFACT_DIR/node-lab-search.snapshot.txt"
  "$PWCLI" -s="$SESSION" snapshot >"$SNAPSHOT"
  NODE_REF="$(sed -nE '/button "[^"]*decide_after_render/s/.*ref=([[:alnum:]]*e[0-9]+).*/\1/p' "$SNAPSHOT" | head -1)"
fi
"$PWCLI" -s="$SESSION" click "$NODE_REF" >/dev/null
require_text 'decide-after-render-success-v1'

EXECUTE_REF="$(find_role_ref button '执行节点')"
"$PWCLI" -s="$SESSION" click "$EXECUTE_REF" >/dev/null
require_text 'step-0001'
require_text 'next_action'
require_text 'select'
require_text '1 steps'

ROOT_REF="$(find_role_ref button 'Root')"
"$PWCLI" -s="$SESSION" click "$ROOT_REF" >/dev/null
EXECUTE_REF="$(find_role_ref button '执行节点')"
"$PWCLI" -s="$SESSION" click "$EXECUTE_REF" >/dev/null
require_text 'step-0002'
require_text '2 steps'

UPLOAD_REF="$(find_role_ref button '上传 Artifact')"
if [[ -z "$UPLOAD_REF" ]]; then
  SNAPSHOT="$ARTIFACT_DIR/node-lab-upload.snapshot.txt"
  "$PWCLI" -s="$SESSION" snapshot >"$SNAPSHOT"
  UPLOAD_REF="$(sed -nE '/上传 Artifact/s/.*ref=([[:alnum:]]*e[0-9]+).*/\1/p' "$SNAPSHOT" | tail -1)"
fi
"$PWCLI" -s="$SESSION" click "$UPLOAD_REF" >/dev/null
"$PWCLI" -s="$SESSION" upload "$ROOT/benchmarks/png_to_shader_v1/images/solid_circle.png" >/dev/null
require_text 'reference_png'

RESTORE_REF="$(find_role_ref button '恢复')"
"$PWCLI" -s="$SESSION" click "$RESTORE_REF" >/dev/null
require_text 'step-0002'
require_text 'reference_png'
require_text '2 steps'

"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/node-lab-final.png" --full-page >/dev/null
printf 'node-lab e2e passed\n'
