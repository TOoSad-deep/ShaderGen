#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PWCLI="${CODEX_HOME:-$HOME/.codex}/skills/playwright/scripts/playwright_cli.sh"
SESSION="shadergen-memory-e2e"
PROJECT_ID="11111111-1111-4111-8111-111111111111"
ARTIFACT_DIR="$ROOT/output/playwright"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  "$PWCLI" -s="$SESSION" close >/dev/null 2>&1 || true
  kill "${VITE_PID:-}" "${API_PID:-}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

uv run python "$ROOT/scripts/fake_memory_api.py" >"$ARTIFACT_DIR/memory-api.log" 2>&1 &
API_PID=$!
npm --prefix "$ROOT/frontend" run dev -- --host 127.0.0.1 >"$ARTIFACT_DIR/memory-vite.log" 2>&1 &
VITE_PID=$!

for _ in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:5173/ >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done
curl -fsS http://127.0.0.1:5173/ >/dev/null

find_ref() {
  local text="$1"
  local output
  output="$("$PWCLI" -s="$SESSION" find "$text")"
  printf '%s\n' "$output" | grep -Eo 'e[0-9]+' | head -1
}

require_text() {
  local text="$1"
  "$PWCLI" -s="$SESSION" find "$text" | grep -F "$text" >/dev/null
}

"$PWCLI" -s="$SESSION" open http://127.0.0.1:5173 >/dev/null
"$PWCLI" -s="$SESSION" snapshot >/dev/null

UPLOAD_REF="$(find_ref '拖拽图片到这里')"
"$PWCLI" -s="$SESSION" click "$UPLOAD_REF" >/dev/null
"$PWCLI" -s="$SESSION" upload "$ROOT/output/static_pink_glass_orb.png" >/dev/null
RUN_REF="$(find_ref '开始运行')"
"$PWCLI" -s="$SESSION" click "$RUN_REF" >/dev/null

for _ in $(seq 1 40); do
  if "$PWCLI" -s="$SESSION" find '保留当前颜色结构' 2>/dev/null | grep -F '保留当前颜色结构' >/dev/null; then
    break
  fi
  sleep 0.25
done
require_text '保留当前颜色结构'
require_text "$PROJECT_ID"
require_text '当前使用临时记忆'

"$PWCLI" -s="$SESSION" reload >/dev/null
require_text "$PROJECT_ID"

NEW_REF="$(find_ref '新建项目')"
"$PWCLI" -s="$SESSION" click "$NEW_REF" >/dev/null
"$PWCLI" -s="$SESSION" dialog-accept >/dev/null
require_text '下一次运行会创建新的 project_id'

RECENT_REF="$(find_ref '最近项目')"
"$PWCLI" -s="$SESSION" select "$RECENT_REF" "$PROJECT_ID" >/dev/null
require_text '已恢复项目记忆范围'

CLEAR_REF="$(find_ref '清除记忆')"
"$PWCLI" -s="$SESSION" click "$CLEAR_REF" >/dev/null
"$PWCLI" -s="$SESSION" dialog-accept >/dev/null
require_text '当前项目记忆已清除'

"$PWCLI" -s="$SESSION" screenshot --filename "$ARTIFACT_DIR/memory-final.png" --full-page >/dev/null
printf 'memory e2e passed\n'
