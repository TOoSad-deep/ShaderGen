"""Node Lab HTTP/Swagger 请求与响应契约."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

NodeLabSuiteId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]

NODE_LAB_RUN_OPENAPI_EXAMPLES: dict[str, dict[str, Any]] = {
    "ephemeral": {
        "summary": "创建不读取项目 Memory 的临时实验",
        "value": {"project_id": None, "initial_state": {}},
    },
    "project_scoped": {
        "summary": "创建允许 prepare_context 只读同项目 Memory 的实验",
        "value": {"project_id": "project-node-lab-demo", "initial_state": {}},
    },
}

NODE_LAB_STEP_OPENAPI_EXAMPLES: dict[str, dict[str, Any]] = {
    "initialize": {
        "summary": "使用已上传图片初始化实验",
        "value": {
            "node_id": "initialize_run",
            "execution_mode": "deterministic",
            "inputs": {
                "source_artifact_id": "replace-with-uploaded-artifact-id",
                "quality_preset": "balanced",
                "instruction": "复刻参考图的形状、颜色和高光。",
            },
        },
    },
    "fixture_from_parent": {
        "summary": "从测量步骤的不可变快照重放 VisualAnalysis Fixture",
        "value": {
            "node_id": "visual_analysis",
            "execution_mode": "fixture",
            "fixture_id": "visual-analysis-success-v1",
            "base_step_id": "replace-with-measure-step-id",
            "inputs": {},
        },
    },
    "prompt_preview": {
        "summary": "预览模型 Prompt 和预算，不调用 Gateway",
        "value": {
            "node_id": "author_initial",
            "execution_mode": "fixture",
            "effect_mode": "preview",
            "base_step_id": "replace-with-analysis-step-id",
            "inputs": {},
        },
    },
    "mock_parser": {
        "summary": "让用户上传的原始模型响应经过真实 Parser",
        "value": {
            "node_id": "visual_analysis",
            "execution_mode": "mock",
            "mock_response_artifact_id": "replace-with-mock-artifact-id",
            "base_step_id": "replace-with-measure-step-id",
            "inputs": {},
        },
    },
}


class NodeLabHttpModel(BaseModel):
    """Node Lab transport 严格模型基类."""

    model_config = ConfigDict(extra="forbid")


class NodeLabRunCreateBody(NodeLabHttpModel):
    """创建 LabRun 的 HTTP body."""

    project_id: str | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)


class NodeLabStepBody(NodeLabHttpModel):
    """执行一个 allowlist 节点的 HTTP body."""

    node_id: str
    execution_mode: Literal["deterministic", "fixture", "mock", "real"] = "fixture"
    effect_mode: Literal["preview", "lab_commit", "project_commit"] = "lab_commit"
    preview_only: bool = False
    allow_model_call: bool = False
    base_step_id: str | None = None
    fixture_id: str | None = None
    mock_response_artifact_id: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)


class NodeLabCapabilityBody(NodeLabHttpModel):
    """执行一个确定性 capability 的 HTTP body."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class NodeLabArtifactResponse(NodeLabHttpModel):
    """不包含文件路径的 Artifact descriptor."""

    schema_version: str
    artifact_id: str
    lab_run_id: str
    kind: str
    content_type: str
    sha256: str
    size_bytes: int
    created_at: str


class NodeLabRunResponse(NodeLabHttpModel):
    """LabRun 元数据响应."""

    schema_version: str
    pipeline_id: str
    lab_run_id: str
    project_id: str | None
    created_at: str
    root_state_sha256: str


class NodeLabNodeInputExampleResponse(NodeLabHttpModel):
    """可机械替换父步骤和 Artifact 后执行的节点输入示例."""

    schema_version: Literal["node_lab_node_input_example_v1"]
    example_id: str
    summary: str
    execution_mode: Literal["deterministic", "fixture", "mock", "real"]
    effect_mode: Literal["preview", "lab_commit", "project_commit"]
    expected_outcome: Literal["success", "rejected", "stopped", "failed"]
    base_step_node_id: str | None
    fixture_id: str | None
    inputs: dict[str, Any]
    artifact_inputs: dict[str, str]


class NodeLabNodeDescriptorResponse(NodeLabHttpModel):
    """生产图节点 descriptor 响应."""

    schema_version: str
    pipeline_id: str
    node_id: str
    category: str
    summary: str
    prerequisites: list[str]
    side_effects: list[str]
    implementation_status: Literal["available", "partial", "planned"]
    execution_modes: list[str]
    supports_batch: bool
    test_profiles: list[str]
    benchmark_profiles: list[str]
    default_fixture_ids: list[str]
    benchmark_metrics: list[str]
    cold_start_sensitive: bool
    requires_browser: bool
    requires_model: bool
    source_ref: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    input_examples: list[NodeLabNodeInputExampleResponse]


class NodeLabCapabilityDescriptorResponse(NodeLabHttpModel):
    """确定性 capability descriptor 响应."""

    schema_version: str
    pipeline_id: str
    capability_id: str
    summary: str
    requires_browser: bool
    cold_start_sensitive: bool
    benchmark_profiles: list[str]
    benchmark_metrics: list[str]
    source_ref: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class NodeLabStateDiffResponse(NodeLabHttpModel):
    """顶层 State 差异响应."""

    added: dict[str, Any]
    changed: dict[str, Any]
    removed: list[str]


class NodeLabStepResponse(NodeLabHttpModel):
    """节点步骤执行响应."""

    schema_version: str
    pipeline_id: str
    lab_run_id: str
    step_id: str
    base_step_id: str | None
    node_id: str
    execution_mode: str
    execution_status: Literal["completed", "failed"]
    outcome: Literal["success", "rejected", "stopped", "failed"]
    input_summary: dict[str, Any]
    output: dict[str, Any]
    state_diff: NodeLabStateDiffResponse
    artifacts: list[NodeLabArtifactResponse]
    diagnostics: dict[str, Any]
    provenance: dict[str, Any]
    usage: dict[str, Any]
    next_action: str | None
    duration_ms: float
    execution_fingerprint: str
    created_at: str


class NodeLabCapabilityResponse(NodeLabHttpModel):
    """确定性 capability 执行响应."""

    schema_version: str
    pipeline_id: str
    lab_run_id: str
    capability_execution_id: str
    capability_id: str
    execution_status: Literal["completed", "failed"]
    outcome: Literal["success", "rejected", "stopped", "failed"]
    input_summary: dict[str, Any]
    output: dict[str, Any]
    artifacts: list[NodeLabArtifactResponse]
    diagnostics: dict[str, Any]
    provenance: dict[str, Any]
    usage: dict[str, Any]
    duration_ms: float
    execution_fingerprint: str
    created_at: str


class NodeLabStepSummaryResponse(NodeLabHttpModel):
    """足以重建步骤 DAG 的安全摘要."""

    schema_version: Literal["node_lab_step_summary_v1"]
    lab_run_id: str
    step_id: str
    base_step_id: str | None
    node_id: str
    execution_mode: str
    execution_status: Literal["completed", "failed"]
    outcome: Literal["success", "rejected", "stopped", "failed"]
    artifact_count: int
    next_action: str | None
    duration_ms: float
    execution_fingerprint: str
    created_at: str


class NodeLabStepListResponse(NodeLabHttpModel):
    """LabRun 已提交步骤 id 与 DAG 摘要列表."""

    lab_run_id: str
    step_ids: list[str]
    steps: list[NodeLabStepSummaryResponse]


class NodeLabArtifactListResponse(NodeLabHttpModel):
    """同一 LabRun 的 Artifact descriptor 列表."""

    lab_run_id: str
    artifacts: list[NodeLabArtifactResponse]


class NodeLabHealthResponse(NodeLabHttpModel):
    """Node Lab 本地实验边界状态."""

    status: Literal["ok"] = "ok"
    enabled: Literal[True] = True
    real_model_enabled: bool = False


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


class NodeLabErrorDetail(NodeLabHttpModel):
    """稳定的 Node Lab HTTP 错误 detail."""

    model_config = ConfigDict(extra="allow")

    message: str
    code: str
    stage: str
    retryable: bool
    lab_run_id: str | None = None
    step_id: str | None = None
    node_id: str | None = None


class NodeLabErrorResponse(NodeLabHttpModel):
    """FastAPI detail envelope."""

    detail: NodeLabErrorDetail
