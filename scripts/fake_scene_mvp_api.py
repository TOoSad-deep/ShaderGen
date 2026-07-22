"""为 scene_mvp 浏览器 E2E 提供不调用模型、不连数据库的本地假 API."""

from __future__ import annotations

import json
import os
import re
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PROJECT_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
WIDTH = 505
HEIGHT = 527
ORIGIN = os.getenv("SHADERGEN_E2E_ORIGIN", "http://127.0.0.1:5173")
PORT = int(os.getenv("SHADERGEN_FAKE_API_PORT", "8091"))
# 补充约束中出现该关键词时，模拟 target_reached=false 的“质量未达标”响应。
MISS_KEYWORD = "模拟质量未达标"
# generate 延迟，给前端留出轮询运行进度的窗口。
GENERATE_DELAY_MS = int(os.getenv("SHADERGEN_FAKE_SCENE_MVP_DELAY_MS", "8000"))
GLSL = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

void main() {
  gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);
}
"""

_BUDGETS = {
    "render_budget": 96,
    "llm_budget": 4,
    "refine_budget": 2,
    "target_mae": 0.12,
    "target_loss": 0.12,
}

# (出现时刻占 generate 延迟的比例, 事件) 形式的假运行时间线；elapsed/duration 仅供展示。
_PROGRESS_TIMELINE: tuple[tuple[float, dict], ...] = (
    (
        0.0625,
        {
            "node": "initialize_run",
            "status": "completed",
            "phase": "initialize",
            "elapsed_ms": 500,
            "duration_ms": 500,
            "budgets": _BUDGETS,
            "trace": [
                {
                    "phase": "initialize",
                    "status": "completed",
                    "message": "已登记运行并写入参考图。",
                }
            ],
        },
    ),
    (
        0.1875,
        {
            "node": "perceive_target",
            "status": "completed",
            "phase": "perception",
            "elapsed_ms": 1500,
            "duration_ms": 1000,
            "budgets": _BUDGETS,
            "trace": [
                {
                    "phase": "perception",
                    "status": "completed",
                    "message": "505x527，scope=object",
                }
            ],
        },
    ),
    (
        0.3125,
        {
            "node": "author_initial",
            "status": "completed",
            "phase": "author_initial",
            "elapsed_ms": 2500,
            "duration_ms": 1000,
            "budgets": _BUDGETS,
            "counters": {"llm_call_count": 1},
            "trace": [
                {
                    "phase": "author_initial",
                    "status": "completed",
                    "message": "完整 MinScene 已通过严格模型契约。",
                    "author_source": "model",
                    "model_calls": 1,
                    "author_latency_ms": 380,
                    "author_tokens": None,
                }
            ],
        },
    ),
    (
        0.375,
        {
            "node": "materialize_shader",
            "status": "completed",
            "phase": "materialize",
            "elapsed_ms": 3000,
            "duration_ms": 500,
            "budgets": _BUDGETS,
            "trace": [
                {
                    "phase": "materialize_shader",
                    "status": "completed",
                    "message": "template=png_to_shader_min_template_v2",
                }
            ],
        },
    ),
    (
        0.5,
        {
            "node": "render_and_evaluate",
            "status": "completed",
            "phase": "render",
            "elapsed_ms": 4000,
            "duration_ms": 1000,
            "budgets": _BUDGETS,
            "counters": {"render_count": 4, "llm_call_count": 1},
            "best": {"mae": 0.16, "loss": 0.155},
            "trace": [
                {
                    "phase": "render_and_evaluate",
                    "status": "completed",
                    "message": "accepted，候选 loss=0.155000，best loss=0.155000",
                    "selected_source": "working_scene",
                    "working_scene_mae": 0.16,
                }
            ],
        },
    ),
    (
        0.5625,
        {
            "node": "decide_after_render",
            "status": "completed",
            "elapsed_ms": 4500,
            "duration_ms": 500,
            "budgets": _BUDGETS,
            "next_action": "optimize_base",
            "stop_reason": "continue",
        },
    ),
    (
        0.6875,
        {
            "node": "optimize_base",
            "status": "completed",
            "phase": "base",
            "elapsed_ms": 5500,
            "duration_ms": 1000,
            "budgets": _BUDGETS,
            "counters": {"render_count": 36, "llm_call_count": 1},
            "best": {"mae": 0.08, "loss": 0.08},
            "trace": [
                {
                    "phase": "optimize_base",
                    "status": "completed",
                    "message": "accepted，loss 0.155000 → 0.080000",
                    "candidates_evaluated": 32,
                    "accepted_parameter": "object.scale",
                }
            ],
        },
    ),
    (
        0.75,
        {
            "node": "decide_after_base",
            "status": "completed",
            "elapsed_ms": 6000,
            "duration_ms": 500,
            "budgets": _BUDGETS,
            "next_action": "finalize",
            "stop_reason": "target_loss_reached",
        },
    ),
    (
        0.875,
        {
            "node": "finalize",
            "status": "completed",
            "phase": "finalize",
            "elapsed_ms": 7000,
            "duration_ms": 1000,
            "budgets": _BUDGETS,
            "counters": {"render_count": 36, "llm_call_count": 1},
            "best": {"mae": 0.08, "loss": 0.08},
            "trace": [
                {
                    "phase": "finalize",
                    "status": "completed",
                    "message": "已固化 final，loss=0.080000，MAE=0.080000",
                    "renderer_path": "prepared_uniforms_v1",
                }
            ],
        },
    ),
)

# 最近一次 generate 的开始时刻（monotonic）；驱动 progress 事件按时间递增。
_run_started_at: float | None = None
_run_lock = threading.Lock()


def _mark_run_started() -> None:
    global _run_started_at
    with _run_lock:
        _run_started_at = time.monotonic()


def _progress_payload(run_id: str, after: int) -> dict:
    """按距 generate 开始的时长返回可见事件、快照与运行状态."""
    with _run_lock:
        started = _run_started_at
    if started is None:
        return {
            "run_id": run_id,
            "status": "pending",
            "generation_mode": "scene_mvp",
            "quality_preset": "balanced",
            "started_at": None,
            "latest_seq": 0,
            "events": [],
            "snapshot": {
                "budgets": _BUDGETS,
                "counters": {},
                "best": {},
                "current_node": None,
                "render_seq": 0,
            },
        }
    elapsed = time.monotonic() - started
    delay_seconds = GENERATE_DELAY_MS / 1000
    visible = []
    for appear_fraction, event in _PROGRESS_TIMELINE:
        if appear_fraction * delay_seconds <= elapsed:
            visible.append({"seq": len(visible) + 1, **event})
    fresh = [event for event in visible if event["seq"] > after]
    snapshot: dict = {
        "budgets": _BUDGETS,
        "counters": {},
        "best": {},
        "current_node": None,
        "render_seq": 0,
    }
    for event in visible:
        snapshot["current_node"] = event["node"]
        for key in ("counters", "best"):
            if isinstance(event.get(key), dict):
                snapshot[key] = event[key]
        if event["node"] == "render_and_evaluate":
            snapshot["render_seq"] = 1
    finished = elapsed >= GENERATE_DELAY_MS / 1000
    return {
        "run_id": run_id,
        "status": "succeeded" if finished else "running",
        "generation_mode": "scene_mvp",
        "quality_preset": "balanced",
        "started_at": None,
        "latest_seq": len(visible),
        "events": fresh,
        "snapshot": snapshot,
    }


def _chunk(kind: bytes, payload: bytes) -> bytes:
    body = kind + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))


def solid_red_png() -> bytes:
    """生成与 E2E 客户端固定 Shader 对应的确定性 RGBA PNG."""
    signature = b"\x89PNG\r\n\x1a\n"
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 6, 0, 0, 0)
    row = b"\x00" + bytes((255, 0, 0, 255)) * WIDTH
    pixels = zlib.compress(row * HEIGHT, level=9)
    return (
        signature
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", pixels)
        + _chunk(b"IEND", b"")
    )


RENDER_PNG = solid_red_png()


class Handler(BaseHTTPRequestHandler):
    """实现 scene_mvp generate 与 final-render 白名单产物，带严格 CORS 边界."""

    def log_message(self, format: str, *args) -> None:
        """关闭测试服务逐请求日志."""
        return None

    def _log_timing(self, note: str) -> None:
        """向 e2e 日志输出带时间戳的关键节点，便于排查时序."""
        sys.stderr.write(f"{time.monotonic():.2f} {self.command} {self.path} {note}\n")
        sys.stderr.flush()

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _send(self, body: bytes, content_type: str, *, status: int = 200) -> None:
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload: dict, *, status: int = 200) -> None:
        self._send(
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
            status=status,
        )

    def do_OPTIONS(self) -> None:
        """响应跨端口预检."""
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        """校验前端显式 scene_mvp 参数并按关键词返回达标/未达标响应."""
        if self.path != "/api/shader/generate":
            self.send_error(404)
            return
        body = self._read_body()
        if b'name="generation_mode"\r\n\r\nscene_mvp' not in body:
            self._json({"detail": "前端未发送 scene_mvp 生成模式。"}, status=400)
            return
        # 前端可显式携带 run_id；缺省回退到固定 id。
        match = re.search(rb'name="run_id"\r\n\r\n([0-9a-zA-Z-]{36})', body)
        run_id = match.group(1).decode("ascii") if match else RUN_ID
        _mark_run_started()
        self._log_timing("run_started")
        time.sleep(GENERATE_DELAY_MS / 1000)
        self._log_timing("respond")
        target_reached = MISS_KEYWORD.encode() not in body
        artifact_base = f"/api/shader/runs/{run_id}/artifacts"
        self._json(
            {
                "project_id": PROJECT_ID,
                "run_id": run_id,
                "glsl": GLSL,
                "memory_status": "ephemeral",
                "generation_mode": "scene_mvp",
                "quality_preset": "balanced",
                "iterations": 1,
                "stop_reason": "completed",
                "best_candidate_id": "candidate-0001",
                "render_width": WIDTH,
                "render_height": HEIGHT,
                "final_render_url": f"{artifact_base}/final-render",
                "manifest_url": f"{artifact_base}/manifest",
                "score": None,
                "unscored_fallback": False,
                "review": None,
                "min_pipeline": {
                    "mae": 0.08 if target_reached else 0.2,
                    "objective_loss": 0.08 if target_reached else 0.2,
                    "metric_breakdown": {
                        "metric_version": "min_scene_composite_v2",
                        "global_mae": 0.08 if target_reached else 0.2,
                        "foreground_mae": 0.07 if target_reached else 0.22,
                        "background_mae": 0.03 if target_reached else 0.12,
                        "geometry_mask_loss": 0.04 if target_reached else 0.18,
                        "edge_loss": 0.06 if target_reached else 0.2,
                        "worst_tile_mae": 0.09 if target_reached else 0.24,
                    },
                    "template_version": "png_to_shader_min_template_v2",
                    "render_count": 4,
                    "render_budget": 96,
                    "llm_call_count": 2,
                    "llm_budget": 4,
                    "refine_budget": 2,
                    "target_mae": 0.12,
                    "target_loss": 0.12,
                    "target_reached": target_reached,
                    "renderer_path": "prepared_uniforms_v1",
                    "prepare_duration_ms": 42,
                    "uniform_render_count": 3,
                    "uniform_render_p95_ms": 7,
                    "scene": {"background": "white", "subject": "red"},
                    "trace": [
                        {
                            "phase": "prepare",
                            "status": "ok",
                            "duration_ms": 42,
                            "message": None,
                        },
                        {
                            "phase": "render",
                            "status": "ok",
                            "duration_ms": 7,
                            "message": None,
                        },
                    ],
                },
            }
        )

    def do_GET(self) -> None:
        """返回运行进度增量、实时渲染帧和 final-render 白名单产物."""
        parsed = urlparse(self.path)
        progress_match = re.fullmatch(
            r"/api/shader/runs/([0-9a-zA-Z-]+)/progress", parsed.path
        )
        if progress_match:
            after_match = re.search(r"[?&]after=(\d+)", self.path)
            after = int(after_match.group(1)) if after_match else 0
            self._json(_progress_payload(progress_match.group(1), after))
            return
        render_match = re.fullmatch(
            r"/api/shader/runs/([0-9a-zA-Z-]+)/progress/render", parsed.path
        )
        if render_match:
            self._send(RENDER_PNG, "image/png")
            return
        if re.fullmatch(
            r"/api/shader/runs/[0-9a-zA-Z-]+/artifacts/final-render", parsed.path
        ):
            self._send(RENDER_PNG, "image/png")
            return
        self.send_error(404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
