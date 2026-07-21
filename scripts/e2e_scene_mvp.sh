#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PWCLI="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
SESSION="shadergen-scene-mvp-e2e"
ARTIFACT_DIR="$ROOT/output/playwright"
export npm_config_cache="${npm_config_cache:-${TMPDIR:-/tmp}/shadergen-npm-cache}"
VITE_PORT="${SHADERGEN_SCENE_MVP_E2E_VITE_PORT:-15176}"
API_PORT="${SHADERGEN_SCENE_MVP_E2E_API_PORT:-18091}"
VITE_ORIGIN="http://127.0.0.1:$VITE_PORT"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  "$PWCLI" -s="$SESSION" close >/dev/null 2>&1 || true
  kill "${VITE_PID:-}" "${API_PID:-}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

SHADERGEN_FAKE_API_PORT="$API_PORT" SHADERGEN_E2E_ORIGIN="$VITE_ORIGIN" \
  uv run python "$ROOT/scripts/fake_scene_mvp_api.py" >"$ARTIFACT_DIR/scene-mvp-api.log" 2>&1 &
API_PID=$!
VITE_API_BASE_URL="http://127.0.0.1:$API_PORT" \
  npm --prefix "$ROOT/frontend" run dev -- --host 127.0.0.1 --port "$VITE_PORT" --strictPort >"$ARTIFACT_DIR/scene-mvp-vite.log" 2>&1 &
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

# 选择 scene_mvp 生成模式并上传 PNG
MODE_REF="$(find_role_ref combobox '生成模式')"
"$PWCLI" -s="$SESSION" select "$MODE_REF" scene_mvp >/dev/null
require_text '实验功能：scene_mvp 最小管线'
INSTRUCTION_REF="$(find_role_ref textbox '补充约束')"
"$PWCLI" -s="$SESSION" fill "$INSTRUCTION_REF" '保留纯白背景' >/dev/null
UPLOAD_REF="$(find_upload_ref)"
"$PWCLI" -s="$SESSION" click "$UPLOAD_REF" >/dev/null
"$PWCLI" -s="$SESSION" upload "$ROOT/output/static_pink_glass_orb.png" >/dev/null
RUN_REF="$(find_role_ref button '开始运行')"
"$PWCLI" -s="$SESSION" click "$RUN_REF" >/dev/null
sleep 2

# 第一轮：target_reached=true，质量达标
require_text 'scene_mvp 最小管线'
require_text '质量达标'
require_text '服务端最终 Render'
require_text '0.0800'
require_text '0.1200'
require_text 'prepare 耗时'
require_text '42 ms'
require_text 'uniform 热渲染次数'
require_text 'uniform 热渲染 P95'
require_text '7 ms'
require_text 'prepared 渲染路径：prepared_uniforms_v1'
"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/scene-mvp-reached.png" --full-page >/dev/null

# 第二轮：target_reached=false，流程完成但质量未达标
INSTRUCTION_REF="$(find_role_ref textbox '补充约束')"
RUN_REF="$(find_role_ref button '重新运行')"
"$PWCLI" -s="$SESSION" fill "$INSTRUCTION_REF" '模拟质量未达标' >/dev/null
"$PWCLI" -s="$SESSION" click "$RUN_REF" >/dev/null
sleep 2

require_text '流程完成，质量未达标'
require_text '0.2000'
require_text '服务端最终 Render'
require_text 'prepared 渲染路径：prepared_uniforms_v1'
require_text 'uniform 热渲染次数'
"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/scene-mvp-missed.png" --full-page >/dev/null
printf 'scene_mvp e2e passed\n'
