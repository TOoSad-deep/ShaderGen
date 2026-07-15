"""Node Lab HTTP transport 到 Agent Application API 的后端编排层."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from agent.app.services.node_lab import (
    CapabilityExecutionRequest,
    EffectMode,
    ExecutionMode,
    LabRunCreateRequest,
    NodeLabApplication,
    NodeLabError,
    StepExecutionRequest,
    create_default_model_node_lab_application,
    describe_suites,
    run_registered_suite,
    validate_registered_suite,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_NODE_LAB_ROOT = ROOT / "output/node-lab/http"
DEFAULT_NODE_LAB_BATCH_ROOT = ROOT / "output/benchmarks/node-lab-http"
_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _env_enabled(name: str) -> bool:
    """只把显式 true 值视为启用，缺失或拼写错误均 fail closed."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


class NodeLabBackendService:
    """保持 Route 只处理 HTTP，所有执行语义下沉到 Agent service."""

    def __init__(
        self,
        application: NodeLabApplication,
        *,
        batch_output_root: str | Path = DEFAULT_NODE_LAB_BATCH_ROOT,
        real_model_enabled: bool = False,
    ) -> None:
        """绑定共享 Application 生命周期."""
        self.application = application
        self.batch_output_root = Path(batch_output_root).resolve()
        self.real_model_enabled = real_model_enabled
        self._batch_locks: dict[str, asyncio.Lock] = {}
        self._batch_lock_users: dict[str, int] = {}

    def describe_suites(self) -> tuple[str, ...]:
        """列出 HTTP 可运行的固定 AI-off suite id."""
        return describe_suites()

    def validate_batch_suite(self, suite_id: str) -> dict[str, object]:
        """校验 allowlist suite，不接受客户端 manifest 路径."""
        return validate_registered_suite(suite_id, application=self.application)

    async def run_batch(
        self,
        *,
        suite_id: str,
        suite_run_id: str | None,
    ) -> dict[str, object]:
        """同步运行固定 AI-off suite，并把恢复冲突收敛为稳定错误."""
        run_id = suite_run_id or f"node-lab-http-{uuid4().hex[:12]}"
        lock = self._batch_locks.setdefault(run_id, asyncio.Lock())
        self._batch_lock_users[run_id] = self._batch_lock_users.get(run_id, 0) + 1
        try:
            async with lock:
                try:
                    return await run_registered_suite(
                        suite_id,
                        output_root=self.batch_output_root,
                        suite_run_id=run_id,
                        application=self.application,
                    )
                except ValueError as exc:
                    if "config hash" in str(exc):
                        raise NodeLabError(
                            "batch_conflict",
                            "Node Lab batch 已存在且配置不一致，禁止覆盖或恢复。",
                            stage="benchmark_config",
                        ) from exc
                    raise NodeLabError(
                        "batch_suite_invalid",
                        "Node Lab 固定 batch suite 未通过运行前校验。",
                        stage="benchmark_validation",
                    ) from exc
        finally:
            remaining = self._batch_lock_users[run_id] - 1
            if remaining == 0:
                self._batch_lock_users.pop(run_id, None)
                self._batch_locks.pop(run_id, None)
            else:
                self._batch_lock_users[run_id] = remaining

    def get_batch_report(self, suite_run_id: str) -> dict[str, Any]:
        """按受限 run id 读取报告，不接受路径且不暴露 output root."""
        if not _BATCH_ID_PATTERN.fullmatch(suite_run_id) or suite_run_id in {".", ".."}:
            raise NodeLabError(
                "batch_not_found",
                "Node Lab batch report 不存在。",
                stage="benchmark_report",
            )
        path = (self.batch_output_root / suite_run_id / "report.json").resolve()
        if not path.is_relative_to(self.batch_output_root) or not path.is_file():
            raise NodeLabError(
                "batch_not_found",
                "Node Lab batch report 不存在。",
                stage="benchmark_report",
            )
        try:
            value = json.loads(path.read_bytes())
        except (OSError, json.JSONDecodeError) as exc:
            raise NodeLabError(
                "batch_report_invalid",
                "Node Lab batch report 无法读取。",
                stage="benchmark_report",
            ) from exc
        if not isinstance(value, dict) or not all(
            isinstance(key, str) for key in value
        ):
            raise NodeLabError(
                "batch_report_invalid",
                "Node Lab batch report 不是 JSON object。",
                stage="benchmark_report",
            )
        return value

    def describe_nodes(self, node_id: str | None = None) -> list[dict[str, Any]]:
        """返回节点目录的 JSON 形态."""
        return [item.to_dict() for item in self.application.describe_nodes(node_id)]

    def describe_capabilities(
        self,
        capability_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """返回确定性能力目录的 JSON 形态."""
        return [
            item.to_dict()
            for item in self.application.describe_capabilities(capability_id)
        ]

    def create_run(
        self,
        *,
        project_id: str | None,
        initial_state: dict[str, Any],
    ) -> dict[str, Any]:
        """创建独立 LabRun."""
        return self.application.create_run(
            LabRunCreateRequest(
                project_id=project_id,
                initial_state=initial_state,
            )
        ).to_dict()

    def get_run(self, lab_run_id: str) -> dict[str, Any]:
        """读取 LabRun 元数据."""
        return self.application.get_run(lab_run_id).to_dict()

    def list_step_ids(self, lab_run_id: str) -> tuple[str, ...]:
        """读取已提交步骤 id."""
        return self.application.list_step_ids(lab_run_id)

    def list_steps(self, lab_run_id: str) -> list[dict[str, Any]]:
        """返回可直接重建不可变步骤 DAG 的摘要."""
        return [
            item.to_dict() for item in self.application.list_step_summaries(lab_run_id)
        ]

    def list_artifacts(self, lab_run_id: str) -> list[dict[str, Any]]:
        """按提交顺序返回 Artifact descriptor，不读取 payload."""
        return [item.to_dict() for item in self.application.list_artifacts(lab_run_id)]

    async def execute_step(
        self,
        *,
        lab_run_id: str,
        node_id: str,
        execution_mode: ExecutionMode,
        effect_mode: EffectMode = "lab_commit",
        preview_only: bool = False,
        allow_model_call: bool = False,
        base_step_id: str | None,
        fixture_id: str | None,
        mock_response_artifact_id: str | None = None,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """构造严格 Agent 请求并执行节点步骤."""
        return (
            await self.application.execute_step(
                StepExecutionRequest(
                    lab_run_id=lab_run_id,
                    node_id=node_id,
                    execution_mode=execution_mode,
                    effect_mode=effect_mode,
                    preview_only=preview_only,
                    allow_model_call=allow_model_call,
                    base_step_id=base_step_id,
                    fixture_id=fixture_id,
                    mock_response_artifact_id=mock_response_artifact_id,
                    inputs=inputs,
                )
            )
        ).to_dict()

    def get_step(self, lab_run_id: str, step_id: str) -> dict[str, Any]:
        """读取已提交步骤响应."""
        return self.application.get_step(lab_run_id, step_id).to_dict()

    async def execute_capability(
        self,
        *,
        lab_run_id: str,
        capability_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """执行确定性 capability."""
        return (
            await self.application.execute_capability(
                CapabilityExecutionRequest(
                    lab_run_id=lab_run_id,
                    capability_id=capability_id,
                    inputs=inputs,
                )
            )
        ).to_dict()

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> dict[str, Any]:
        """保存上传内容并返回不透明 descriptor."""
        return self.application.upload_artifact(
            lab_run_id=lab_run_id,
            kind=kind,
            content_type=content_type,
            data=data,
        ).to_dict()

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[dict[str, Any], bytes]:
        """读取同一 LabRun 的私有 Artifact."""
        descriptor, data = self.application.read_artifact(lab_run_id, artifact_id)
        return descriptor.to_dict(), data


def create_default_node_lab_backend_service() -> NodeLabBackendService:
    """按受控环境变量创建本地 Node Lab Service."""
    root = Path(os.getenv("SHADERGEN_NODE_LAB_ROOT", str(DEFAULT_NODE_LAB_ROOT)))
    batch_root = Path(
        os.getenv(
            "SHADERGEN_NODE_LAB_BATCH_ROOT",
            str(DEFAULT_NODE_LAB_BATCH_ROOT),
        )
    )
    real_model_enabled = _env_enabled("SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED")
    return NodeLabBackendService(
        create_default_model_node_lab_application(
            root=root,
            real_model_enabled=real_model_enabled,
        ),
        batch_output_root=batch_root,
        real_model_enabled=real_model_enabled,
    )


__all__ = [
    "NodeLabBackendService",
    "NodeLabError",
    "create_default_node_lab_backend_service",
]
