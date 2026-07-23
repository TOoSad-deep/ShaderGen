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
  SHADERGEN_FAKE_SCENE_MVP_DELAY_MS="${SHADERGEN_FAKE_SCENE_MVP_DELAY_MS:-30000}" \
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

# 配置 scene_mvp 并上传 PNG
require_text 'scene_mvp 返回质量指标、预算用量'
QUALITY_REF="$(find_role_ref combobox '质量档位')"
"$PWCLI" -s="$SESSION" select "$QUALITY_REF" manual >/dev/null
"$PWCLI" -s="$SESSION" eval "el => el.value" "$QUALITY_REF" | grep -qF 'manual'

INSTRUCTION_REF="$(find_role_ref textbox '补充约束')"
"$PWCLI" -s="$SESSION" fill "$INSTRUCTION_REF" '保留纯白背景' >/dev/null
UPLOAD_REF="$(find_upload_ref)"
"$PWCLI" -s="$SESSION" click "$UPLOAD_REF" >/dev/null
"$PWCLI" -s="$SESSION" upload "$ROOT/output/static_pink_glass_orb.png" >/dev/null
RUN_REF="$(find_role_ref button '开始运行')"
# DOM click 立即返回；CLI click 会等 network idle，被长 POST 阻塞到运行结束
"$PWCLI" -s="$SESSION" eval "el => el.click()" "$RUN_REF" >/dev/null
sleep 5

# 运行中检查点（单次快照多处断言，避免多次 CLI 调用拖过观测窗口）
LIVE_SNAPSHOT="$("$PWCLI" -s="$SESSION" snapshot)"
require_snapshot_text() {
  local text="$1"
  printf '%s\n' "$LIVE_SNAPSHOT" | grep -qF "$text"
}
require_snapshot_text '节点时间线'
require_snapshot_text '预算用量'
require_snapshot_text '事件流'
require_snapshot_text '初始化运行'
require_snapshot_text '感知目标图'
require_snapshot_text '渲染与评估'
require_snapshot_text '执行中'
require_snapshot_text '实时渲染'
"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/scene-mvp-live.png" --full-page >/dev/null
sleep 26

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
"$PWCLI" -s="$SESSION" eval "el => el.click()" "$RUN_REF" >/dev/null
sleep 31

require_text '流程完成，质量未达标'
require_text '0.2000'
require_text '服务端最终 Render'
require_text 'prepared 渲染路径：prepared_uniforms_v1'
require_text 'uniform 热渲染次数'
"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/scene-mvp-missed.png" --full-page >/dev/null
printf 'scene_mvp e2e passed\n'
