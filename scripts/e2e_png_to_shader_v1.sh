#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PWCLI="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
SESSION="shadergen-procedural-v1-e2e"
ARTIFACT_DIR="$ROOT/output/playwright"
export npm_config_cache="${npm_config_cache:-${TMPDIR:-/tmp}/shadergen-npm-cache}"
VITE_PORT="${SHADERGEN_E2E_VITE_PORT:-15173}"
API_PORT="${SHADERGEN_E2E_API_PORT:-18088}"
VITE_ORIGIN="http://127.0.0.1:$VITE_PORT"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  "$PWCLI" -s="$SESSION" close >/dev/null 2>&1 || true
  kill "${VITE_PID:-}" "${API_PID:-}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

SHADERGEN_FAKE_API_PORT="$API_PORT" SHADERGEN_E2E_ORIGIN="$VITE_ORIGIN" \
  uv run python "$ROOT/scripts/fake_png_to_shader_v1_api.py" >"$ARTIFACT_DIR/procedural-v1-api.log" 2>&1 &
API_PID=$!
VITE_API_BASE_URL="http://127.0.0.1:$API_PORT" \
  npm --prefix "$ROOT/frontend" run dev -- --host 127.0.0.1 --port "$VITE_PORT" --strictPort >"$ARTIFACT_DIR/procedural-v1-vite.log" 2>&1 &
VITE_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS "$VITE_ORIGIN/" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS "$VITE_ORIGIN/" >/dev/null

find_role_ref() {
  local role="$1"
  local text="$2"
  local output
  output="$("$PWCLI" -s="$SESSION" find "$text")"
  printf '%s\n' "$output" \
    | sed -nE "/${role} \"${text}\"/s/.*ref=([[:alnum:]]*e[0-9]+).*/\1/p" \
    | head -1
}

find_upload_ref() {
  local output
  output="$("$PWCLI" -s="$SESSION" find '拖拽图片到这里')"
  printf '%s\n' "$output" \
    | sed -nE '/cursor=pointer/s/.*ref=([[:alnum:]]*e[0-9]+).*/\1/p' \
    | head -1
}

require_text() {
  local text="$1"
  "$PWCLI" -s="$SESSION" find "$text" | grep -E 'Found [1-9][0-9]* match' >/dev/null
}

"$PWCLI" -s="$SESSION" open "$VITE_ORIGIN" >/dev/null
"$PWCLI" -s="$SESSION" snapshot >/dev/null

INSTRUCTION_REF="$(find_role_ref textbox '补充约束')"
"$PWCLI" -s="$SESSION" eval 'el => el.disabled' "$INSTRUCTION_REF" | grep -q 'false'
require_text '程序化闭环 V1'
QUALITY_REF="$(find_role_ref combobox '质量档位')"
"$PWCLI" -s="$SESSION" select "$QUALITY_REF" ultra >/dev/null
"$PWCLI" -s="$SESSION" fill "$INSTRUCTION_REF" '保留纯白背景和左上高光' >/dev/null
UPLOAD_REF="$(find_upload_ref)"
"$PWCLI" -s="$SESSION" click "$UPLOAD_REF" >/dev/null
"$PWCLI" -s="$SESSION" upload "$ROOT/output/static_pink_glass_orb.png" >/dev/null
RUN_REF="$(find_role_ref button '开始运行')"
"$PWCLI" -s="$SESSION" click "$RUN_REF" >/dev/null
sleep 2

require_text '客户端与服务端渲染一致'
require_text '连续修订无提升'
require_text 'candidate-0003'
require_text '0.1040'
require_text '最后一次自动 Review 已完成'
require_text '服务端最终 Render'

"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/procedural-v1-final.png" --full-page >/dev/null

INSTRUCTION_REF="$(find_role_ref textbox '补充约束')"
RUN_REF="$(find_role_ref button '重新运行')"
"$PWCLI" -s="$SESSION" fill "$INSTRUCTION_REF" '模拟评分不可用 fallback' >/dev/null
"$PWCLI" -s="$SESSION" click "$RUN_REF" >/dev/null
sleep 2
"$PWCLI" -s="$SESSION" snapshot >"$ARTIFACT_DIR/procedural-v1-fallback.snapshot.txt"
require_text 'WebGL 已通过，但评分不可用；以 fallback 完成'
require_text 'WebGL fallback'
require_text 'candidate-fallback'
"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/procedural-v1-fallback-final.png" --full-page >/dev/null
printf 'procedural_v1 e2e passed\n'
