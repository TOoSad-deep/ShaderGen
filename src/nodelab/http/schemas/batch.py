"""Node Lab 固定 batch suite 的 HTTP 契约."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from nodelab.http.schemas.common import NodeLabHttpModel, NodeLabSuiteId


class NodeLabBatchSuiteListResponse(NodeLabHttpModel):
    """HTTP 可选择的固定 AI-off suite 列表."""

    suite_ids: list[NodeLabSuiteId]


class NodeLabBatchValidateBody(NodeLabHttpModel):
    """只允许固定 suite id，不接受 manifest 路径."""

    suite_id: NodeLabSuiteId


class NodeLabBatchRunBody(NodeLabHttpModel):
    """同步运行一个固定 AI-off suite."""

    suite_id: NodeLabSuiteId
    suite_run_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    )


class NodeLabBatchValidationResponse(NodeLabHttpModel):
    """冻结 manifest 与执行形态的校验摘要."""

    schema_version: Literal["node_lab_benchmark_manifest_v1"]
    pipeline_id: str | None = None
    suite_id: NodeLabSuiteId
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case_count: int
    repetitions: int
    warmups: int
    resource_lifecycle: Literal["cold_per_attempt", "warm_per_suite"]
    renderer_lifecycle: Literal["cold_per_attempt", "warm_per_suite"]
    profiles: list[str]


class NodeLabDurationSummaryResponse(NodeLabHttpModel):
    """按 measured attempt 聚合的耗时统计."""

    p50: float | None
    p95: float | None
    max: float | None


class NodeLabBatchReportResponse(NodeLabHttpModel):
    """可供模块化测试与 benchmark 消费的批处理报告."""

    schema_version: Literal["node_lab_benchmark_report_v1"]
    suite_run_id: str
    pipeline_id: str | None = None
    suite_id: NodeLabSuiteId
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    environment_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    attempt_count: int
    completed_attempt_count: int
    interrupted_attempt_count: int
    passed_attempt_count: int
    failed_attempt_count: int
    correctness_rate: float
    duration_ms: NodeLabDurationSummaryResponse
    failed_attempts: list[str]
    profiles: list[str]
    resource_lifecycle: Literal["cold_per_attempt", "warm_per_suite"]
    renderer_lifecycle: Literal["cold_per_attempt", "warm_per_suite"]
