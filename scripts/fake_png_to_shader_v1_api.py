"""为 M4 浏览器 E2E 提供不调用模型的 procedural_v1 HTTP API."""

from __future__ import annotations

import json
import os
import struct
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_ID = "22222222-2222-4222-8222-222222222222"
RUN_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
WIDTH = 505
HEIGHT = 527
ORIGIN = os.getenv("SHADERGEN_E2E_ORIGIN", "http://127.0.0.1:5173")
PORT = int(os.getenv("SHADERGEN_FAKE_API_PORT", "8088"))
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
    """实现 V1 generate、Artifact 和严格 CORS 测试边界."""

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
        """校验前端显式 V1 参数并返回完整 M4 响应."""
        if self.path != "/api/shader/generate":
            self.send_error(404)
            return
        body = self._read_body()
        required = (
            b'name="generation_mode"\r\n\r\nprocedural_v1',
            b'name="quality_preset"\r\n\r\nhigh',
        )
        if any(item not in body for item in required):
            self._json({"detail": "前端未发送完整 procedural_v1 参数。"}, status=400)
            return
        unscored_fallback = "模拟评分不可用 fallback".encode() in body
        if not unscored_fallback and "保留纯白背景和左上高光".encode() not in body:
            self._json({"detail": "前端未发送预期补充约束。"}, status=400)
            return
        artifact_base = f"/api/shader/runs/{RUN_ID}/artifacts"
        score = (
            None
            if unscored_fallback
            else {
                "metric_version": "basic_oracle_v1",
                "total_loss": 0.104,
                "global_rmse": 0.087,
                "global_mae": 0.071,
                "edge_loss": 0.132,
                "geometry_loss": 0.08,
                "representative_pixel_loss": 0.09,
                "roi_losses": {"highlight": 0.11},
                "protected_region_losses": {"subject": 0.07},
                "effective_weights": {"global_rmse": 0.35},
                "diagnostics": [],
            }
        )
        self._json(
            {
                "project_id": PROJECT_ID,
                "run_id": RUN_ID,
                "glsl": GLSL,
                "memory_status": "ephemeral",
                "generation_mode": "procedural_v1",
                "quality_preset": "high",
                "iterations": 2,
                "stop_reason": "stagnation",
                "best_candidate_id": (
                    "candidate-fallback" if unscored_fallback else "candidate-0003"
                ),
                "render_width": WIDTH,
                "render_height": HEIGHT,
                "final_render_url": f"{artifact_base}/final-render",
                "metrics_url": (
                    None if unscored_fallback else f"{artifact_base}/metrics"
                ),
                "manifest_url": f"{artifact_base}/manifest",
                "score": score,
                "unscored_fallback": unscored_fallback,
                "review": None
                if unscored_fallback
                else {
                    "evaluation": "最后一次自动 Review 已完成。",
                    "suggestions": ["保留 current_best 的轮廓和颜色结构"],
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
