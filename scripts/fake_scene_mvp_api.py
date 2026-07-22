"""为 scene_mvp 浏览器 E2E 提供不调用模型、不连数据库的本地假 API."""

from __future__ import annotations

import json
import os
import struct
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_ID = "33333333-3333-4333-8333-333333333333"
RUN_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
WIDTH = 505
HEIGHT = 527
ORIGIN = os.getenv("SHADERGEN_E2E_ORIGIN", "http://127.0.0.1:5173")
PORT = int(os.getenv("SHADERGEN_FAKE_API_PORT", "8091"))
# 补充约束中出现该关键词时，模拟 target_reached=false 的“质量未达标”响应。
MISS_KEYWORD = "模拟质量未达标"
GLSL = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

void main() {
  gl_FragColor = vec4(1.0, 0.0, 0.0, 1.0);
}
"""


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
        target_reached = MISS_KEYWORD.encode() not in body
        artifact_base = f"/api/shader/runs/{RUN_ID}/artifacts"
        self._json(
            {
                "project_id": PROJECT_ID,
                "run_id": RUN_ID,
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
                        "highlight_mae": 0.09 if target_reached else 0.24,
                        "shadow_mae": 0.08 if target_reached else 0.21,
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
        """只返回 E2E 所需的 final-render 白名单产物."""
        if self.path == f"/api/shader/runs/{RUN_ID}/artifacts/final-render":
            self._send(RENDER_PNG, "image/png")
            return
        self.send_error(404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
