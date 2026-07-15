"""Node Lab 前端 E2E 使用的本地假 API；不触发模型、Renderer 或 Memory."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

from agent.app.nodes.integrations.node_lab import build_png_to_shader_v1_registry

PORT = int(os.getenv("SHADERGEN_FAKE_API_PORT", "18090"))
ORIGIN = os.getenv("SHADERGEN_E2E_ORIGIN", "http://127.0.0.1:15175")
RUN_ID = "lab-e2e-run-0001"
STEPS: list[dict[str, Any]] = []
ARTIFACTS: list[dict[str, Any]] = []


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _run() -> dict[str, Any]:
    return {
        "schema_version": "node_lab_run_v1",
        "pipeline_id": "png_to_shader_v1",
        "lab_run_id": RUN_ID,
        "project_id": "node-lab-e2e",
        "created_at": _now(),
        "root_state_sha256": "a" * 64,
    }


def _artifact(index: int, *, kind: str = "step_json") -> dict[str, Any]:
    return {
        "schema_version": "node_lab_artifact_v1",
        "artifact_id": f"artifact-{index:04d}",
        "lab_run_id": RUN_ID,
        "kind": kind,
        "content_type": "application/json" if kind == "step_json" else "image/png",
        "sha256": f"{index:064x}",
        "size_bytes": 128 + index,
        "created_at": _now(),
    }


class Handler(BaseHTTPRequestHandler):
    """只实现 Node Lab 页面 E2E 所需的稳定端点."""

    def _headers(
        self, status: int = 200, content_type: str = "application/json"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", ORIGIN)
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.end_headers()

    def _json(self, value: object, status: int = 200) -> None:
        self._headers(status)
        self.wfile.write(json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not raw or "application/json" not in self.headers.get("Content-Type", ""):
            return {}
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}

    def do_OPTIONS(self) -> None:  # noqa: N802
        """响应浏览器 CORS 预检."""
        self._headers(204)

    def do_GET(self) -> None:  # noqa: N802
        """返回 discovery、run、step 和 Artifact 假数据."""
        path = urlparse(self.path).path
        if path == "/api/lab/v1/health":
            self._json({"status": "ok", "enabled": True, "real_model_enabled": False})
            return
        if path == "/api/lab/v1/nodes":
            descriptors = build_png_to_shader_v1_registry().describe_nodes()
            self._json([descriptor.to_dict() for descriptor in descriptors])
            return
        if path == f"/api/lab/v1/runs/{RUN_ID}":
            self._json(_run())
            return
        if path == f"/api/lab/v1/runs/{RUN_ID}/steps":
            self._json(
                {"lab_run_id": RUN_ID, "step_ids": [step["step_id"] for step in STEPS]}
            )
            return
        step_prefix = f"/api/lab/v1/runs/{RUN_ID}/steps/"
        if path.startswith(step_prefix):
            step_id = path.removeprefix(step_prefix)
            step = next((item for item in STEPS if item["step_id"] == step_id), None)
            if step is not None:
                self._json(step)
                return
        if path == f"/api/lab/v1/runs/{RUN_ID}/artifacts":
            self._json({"lab_run_id": RUN_ID, "artifacts": ARTIFACTS})
            return
        artifact_prefix = f"/api/lab/v1/runs/{RUN_ID}/artifacts/"
        if path.startswith(artifact_prefix):
            self._headers(200, "application/octet-stream")
            self.wfile.write(b"node-lab-e2e-artifact")
            return
        self._json({"detail": {"code": "not_found", "message": "not found"}}, 404)

    def do_POST(self) -> None:  # noqa: N802
        """创建假 LabRun、不可变步骤和上传 Artifact."""
        path = urlparse(self.path).path
        if path == "/api/lab/v1/runs":
            self._body()
            STEPS.clear()
            ARTIFACTS.clear()
            self._json(_run())
            return
        if path == f"/api/lab/v1/runs/{RUN_ID}/steps":
            body = self._body()
            index = len(STEPS) + 1
            artifact = _artifact(index)
            ARTIFACTS.append(artifact)
            step = {
                "schema_version": "node_lab_step_v1",
                "pipeline_id": "png_to_shader_v1",
                "lab_run_id": RUN_ID,
                "step_id": f"step-{index:04d}",
                "base_step_id": body.get("base_step_id"),
                "node_id": str(body.get("node_id", "decide_after_render")),
                "execution_mode": str(body.get("execution_mode", "fixture")),
                "execution_status": "completed",
                "outcome": "success",
                "input_summary": body.get("inputs", {}),
                "output": {
                    "next_action": "select",
                    "fixture_used": body.get("fixture_id"),
                },
                "state_diff": {
                    "added": {"next_action": "select"},
                    "changed": {},
                    "removed": [],
                },
                "artifacts": [artifact],
                "diagnostics": {"fixture": True},
                "provenance": {"implementation": "fake_node_lab_api"},
                "usage": {"model_call_count": 0, "input_tokens": 0, "output_tokens": 0},
                "next_action": "select",
                "duration_ms": 1.25,
                "execution_fingerprint": f"{index + 20:064x}",
                "created_at": _now(),
            }
            STEPS.append(step)
            self._json(step)
            return
        if path == f"/api/lab/v1/runs/{RUN_ID}/artifacts":
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            artifact = _artifact(len(ARTIFACTS) + 20, kind="reference_png")
            ARTIFACTS.append(artifact)
            self._json(artifact)
            return
        self._json({"detail": {"code": "not_found", "message": "not found"}}, 404)

    def log_message(self, format: str, *args: object) -> None:
        """E2E 日志保持安静."""


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
