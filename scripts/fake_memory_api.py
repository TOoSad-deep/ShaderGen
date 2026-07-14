"""为浏览器 Memory E2E 提供不调用模型的最小 HTTP API."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PROJECT_ID = "11111111-1111-4111-8111-111111111111"
RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ORIGIN = os.getenv("SHADERGEN_E2E_ORIGIN", "http://127.0.0.1:5173")
PORT = int(os.getenv("SHADERGEN_FAKE_API_PORT", "8088"))
GLSL = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
void main() {
  gl_FragColor = texture2D(u_image, v_uv);
}
"""


class Handler(BaseHTTPRequestHandler):
    """实现 generate、review、clear 和 CORS."""

    def log_message(self, format: str, *args) -> None:
        """关闭测试服务的逐请求 stderr 日志."""
        return None

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _read_body(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        """响应跨端口 DELETE 预检."""
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self) -> None:
        """返回固定 project_id、Shader 和 Review."""
        self._read_body()
        if self.path == "/api/shader/generate":
            self._json(
                {
                    "project_id": PROJECT_ID,
                    "run_id": RUN_ID,
                    "glsl": GLSL,
                    "memory_status": "ephemeral",
                    "generation_mode": "legacy",
                    "iterations": 0,
                }
            )
            return
        if self.path == "/api/shader/review":
            self._json(
                {
                    "project_id": PROJECT_ID,
                    "review": {
                        "evaluation": "浏览器评审完成。",
                        "suggestions": ["保留当前颜色结构"],
                    },
                    "memory_status": "ephemeral",
                }
            )
            return
        self.send_error(404)

    def do_DELETE(self) -> None:
        """清除固定测试项目."""
        if self.path == f"/api/shader/projects/{PROJECT_ID}/memory":
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        self.send_error(404)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
