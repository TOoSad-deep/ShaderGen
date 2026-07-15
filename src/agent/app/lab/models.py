"""Node Lab 的 transport-free 契约与公共错误."""

from __future__ import annotations

import math
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NODE_LAB_PIPELINE_ID = "png_to_shader_v1"
NODE_LAB_RUN_SCHEMA = "node_lab_run_v1"
NODE_LAB_STEP_REQUEST_SCHEMA = "node_lab_execution_request_v1"
NODE_LAB_STEP_RESPONSE_SCHEMA = "node_lab_execution_response_v1"
NODE_LAB_ARTIFACT_SCHEMA = "node_lab_artifact_v1"

Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
]
Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
ExecutionMode = Literal["deterministic", "fixture", "mock", "real"]
EffectMode = Literal["preview", "lab_commit", "project_commit"]
ExecutionStatus = Literal["completed", "failed"]
ExecutionOutcome = Literal["success", "rejected", "stopped", "failed"]
ImplementationStatus = Literal["available", "partial", "planned"]

_SAFE_CONTENT_TYPE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*/[A-Za-z0-9][A-Za-z0-9!#$&^_.+-]*(?:; charset=utf-8)?$"
)


def ensure_json_value(value: Any, *, path: str = "$", depth: int = 0) -> Any:
    """拒绝 bytes、领域对象、非有限数和过深结构，返回原 JSON 值."""
    if depth > 32:
        raise ValueError(f"{path} 的 JSON 嵌套超过 32 层。")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} 不能包含 NaN 或 Infinity。")
        return value
    if isinstance(value, list):
        return [
            ensure_json_value(item, path=f"{path}[{index}]", depth=depth + 1)
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} 的 JSON object key 必须是字符串。")
            normalized[key] = ensure_json_value(
                item,
                path=f"{path}.{key}",
                depth=depth + 1,
            )
        return normalized
    raise ValueError(f"{path} 包含非 JSON-safe 类型 {type(value).__name__}。")


def ensure_json_object(value: Any, *, path: str = "$") -> dict[str, Any]:
    """校验并返回 JSON object."""
    normalized = ensure_json_value(value, path=path)
    if not isinstance(normalized, dict):
        raise ValueError(f"{path} 必须是 JSON object。")
    return normalized


class NodeLabModel(BaseModel):
    """Node Lab 严格不可变模型基类."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容字典."""
        return self.model_dump(mode="json")


class NodeInputExample(NodeLabModel):
    """可由人工、CLI 或自动化解析的节点调用示例.

    ``base_step_node_id`` 表示示例依赖该节点产生的父快照；
    ``artifact_inputs`` 把输入字段映射为需要先上传的 Artifact kind。
    这让示例不依赖运行时生成的 UUID，同时仍能由调用方机械替换并执行。
    """

    schema_version: Literal["node_lab_node_input_example_v1"] = (
        "node_lab_node_input_example_v1"
    )
    example_id: Identifier
    summary: NonEmptyText
    execution_mode: ExecutionMode
    effect_mode: EffectMode = "lab_commit"
    expected_outcome: ExecutionOutcome = "success"
    base_step_node_id: Identifier | None = None
    fixture_id: Identifier | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    artifact_inputs: dict[str, Identifier] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """示例输入必须可以直接进入 JSON transport."""
        return ensure_json_object(value, path="$.input_examples.inputs")


class NodeDescriptor(NodeLabModel):
    """一个允许执行的生产图节点及其机器可读元数据."""

    schema_version: Literal["node_lab_node_descriptor_v1"] = (
        "node_lab_node_descriptor_v1"
    )
    pipeline_id: Identifier = NODE_LAB_PIPELINE_ID
    node_id: Identifier
    category: Identifier
    summary: NonEmptyText
    prerequisites: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    implementation_status: ImplementationStatus = "planned"
    execution_modes: list[ExecutionMode] = Field(default_factory=list)
    supports_batch: bool = True
    test_profiles: list[str] = Field(min_length=1)
    benchmark_profiles: list[str] = Field(min_length=1)
    default_fixture_ids: list[Identifier] = Field(default_factory=list)
    benchmark_metrics: list[str] = Field(min_length=1)
    cold_start_sensitive: bool = False
    requires_browser: bool = False
    requires_model: bool = False
    source_ref: NonEmptyText
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    input_examples: list[NodeInputExample] = Field(min_length=1)

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_json_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        """保证 descriptor Schema 本身可安全序列化."""
        return ensure_json_object(value)


class CapabilityDescriptor(NodeLabModel):
    """一个可独立执行的确定性领域能力描述."""

    schema_version: Literal["node_lab_capability_descriptor_v1"] = (
        "node_lab_capability_descriptor_v1"
    )
    pipeline_id: Identifier = NODE_LAB_PIPELINE_ID
    capability_id: Identifier
    summary: NonEmptyText
    requires_browser: bool = False
    cold_start_sensitive: bool = False
    benchmark_profiles: list[str] = Field(min_length=1)
    benchmark_metrics: list[str] = Field(min_length=1)
    source_ref: NonEmptyText
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    @field_validator("input_schema", "output_schema")
    @classmethod
    def validate_json_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        """保证 capability Schema 可直接供 HTTP/CLI 复用."""
        return ensure_json_object(value)


class LabRunCreateRequest(NodeLabModel):
    """创建独立 LabRun 的请求."""

    schema_version: Literal["node_lab_run_create_request_v1"] = (
        "node_lab_run_create_request_v1"
    )
    project_id: Identifier | None = None
    initial_state: dict[str, Any] = Field(default_factory=dict)

    @field_validator("initial_state")
    @classmethod
    def validate_initial_state(cls, value: dict[str, Any]) -> dict[str, Any]:
        """拒绝把图片、GLSL bytes 或领域对象写入 State JSON."""
        return ensure_json_object(value, path="$.initial_state")


class LabRunRecord(NodeLabModel):
    """持久化的 LabRun 元数据."""

    schema_version: Literal["node_lab_run_v1"] = "node_lab_run_v1"
    pipeline_id: Identifier = NODE_LAB_PIPELINE_ID
    lab_run_id: Identifier
    project_id: Identifier | None = None
    created_at: NonEmptyText
    root_state_sha256: Sha256Text


class StepExecutionRequest(NodeLabModel):
    """执行单个 allowlist 节点的 Application API 请求."""

    schema_version: Literal["node_lab_execution_request_v1"] = (
        "node_lab_execution_request_v1"
    )
    lab_run_id: Identifier
    node_id: Identifier
    execution_mode: ExecutionMode = "fixture"
    effect_mode: EffectMode = "lab_commit"
    preview_only: Annotated[bool, Field(strict=True)] = False
    allow_model_call: Annotated[bool, Field(strict=True)] = False
    base_step_id: Identifier | None = None
    fixture_id: Identifier | None = None
    mock_response_artifact_id: Identifier | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Node 输入只能是 JSON-safe object."""
        return ensure_json_object(value, path="$.inputs")


class CapabilityExecutionRequest(NodeLabModel):
    """执行单个确定性能力的 Application API 请求."""

    schema_version: Literal["node_lab_capability_request_v1"] = (
        "node_lab_capability_request_v1"
    )
    lab_run_id: Identifier
    capability_id: Identifier
    inputs: dict[str, Any] = Field(default_factory=dict)

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Capability 输入只接受 JSON-safe object 和 Artifact id."""
        return ensure_json_object(value, path="$.inputs")


class StateDiff(NodeLabModel):
    """步骤执行前后的顶层 State 差异."""

    added: dict[str, Any] = Field(default_factory=dict)
    changed: dict[str, Any] = Field(default_factory=dict)
    removed: list[str] = Field(default_factory=list)

    @field_validator("added", "changed")
    @classmethod
    def validate_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        """差异值必须可作为证据 JSON 保存."""
        return ensure_json_object(value)


class ArtifactDescriptor(NodeLabModel):
    """不暴露本地路径的 Lab Artifact 描述."""

    schema_version: Literal["node_lab_artifact_v1"] = "node_lab_artifact_v1"
    artifact_id: Identifier
    lab_run_id: Identifier
    kind: Identifier
    content_type: str
    sha256: Sha256Text
    size_bytes: Annotated[int, Field(strict=True, ge=0)]
    created_at: NonEmptyText

    @field_validator("content_type")
    @classmethod
    def validate_content_type(cls, value: str) -> str:
        """限制为不携带任意参数的常见 MIME 形态."""
        if not _SAFE_CONTENT_TYPE.fullmatch(value):
            raise ValueError("content_type 格式不受支持。")
        return value


class NodeExecutionResult(NodeLabModel):
    """Executor 返回给 Harness 的 transport-free 部分结果."""

    outcome: Literal["success", "rejected", "stopped"] = "success"
    output_patch: dict[str, Any] = Field(default_factory=dict)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)

    @field_validator("output_patch", "diagnostics", "provenance", "usage")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Executor 不得把领域对象或二进制对象泄漏进响应."""
        return ensure_json_object(value)


class StepExecutionResponse(NodeLabModel):
    """一次不可变步骤执行的统一响应."""

    schema_version: Literal["node_lab_execution_response_v1"] = (
        "node_lab_execution_response_v1"
    )
    pipeline_id: Identifier = NODE_LAB_PIPELINE_ID
    lab_run_id: Identifier
    step_id: Identifier
    base_step_id: Identifier | None
    node_id: Identifier
    execution_mode: ExecutionMode
    execution_status: ExecutionStatus
    outcome: ExecutionOutcome
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    state_diff: StateDiff
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    next_action: str | None = None
    duration_ms: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
    execution_fingerprint: Sha256Text
    created_at: NonEmptyText

    @field_validator("input_summary", "output", "diagnostics", "provenance", "usage")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """响应结构化字段必须可稳定写入 JSON."""
        return ensure_json_object(value)


class StepSummary(NodeLabModel):
    """用于列表和 DAG 重建的不可变步骤摘要."""

    schema_version: Literal["node_lab_step_summary_v1"] = "node_lab_step_summary_v1"
    lab_run_id: Identifier
    step_id: Identifier
    base_step_id: Identifier | None
    node_id: Identifier
    execution_mode: ExecutionMode
    execution_status: ExecutionStatus
    outcome: ExecutionOutcome
    artifact_count: Annotated[int, Field(strict=True, ge=0)]
    next_action: str | None = None
    duration_ms: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
    execution_fingerprint: Sha256Text
    created_at: NonEmptyText


class CapabilityExecutionResponse(NodeLabModel):
    """一次确定性能力调用的统一、可 benchmark 响应."""

    schema_version: Literal["node_lab_capability_response_v1"] = (
        "node_lab_capability_response_v1"
    )
    pipeline_id: Identifier = NODE_LAB_PIPELINE_ID
    lab_run_id: Identifier
    capability_execution_id: Identifier
    capability_id: Identifier
    execution_status: ExecutionStatus
    outcome: ExecutionOutcome
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    duration_ms: Annotated[float, Field(strict=True, ge=0, allow_inf_nan=False)]
    execution_fingerprint: Sha256Text
    created_at: NonEmptyText

    @field_validator("input_summary", "output", "diagnostics", "provenance", "usage")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Capability 响应不得内嵌二进制或领域对象."""
        return ensure_json_object(value)


class NodeLabError(RuntimeError):
    """可映射到 HTTP/CLI 的安全稳定错误."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        stage: str,
        retryable: bool = False,
        lab_run_id: str | None = None,
        step_id: str | None = None,
        node_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """保存安全定位字段，不接收供应商原始异常或本地路径."""
        self.code = code
        self.message = message
        self.stage = stage
        self.retryable = retryable
        self.lab_run_id = lab_run_id
        self.step_id = step_id
        self.node_id = node_id
        self.details = ensure_json_object(details or {}, path="$.details")
        super().__init__(message)

    def to_detail(self) -> dict[str, Any]:
        """返回供 transport 使用的稳定错误对象."""
        return {
            "code": self.code,
            "message": self.message,
            "stage": self.stage,
            "retryable": self.retryable,
            "lab_run_id": self.lab_run_id,
            "step_id": self.step_id,
            "node_id": self.node_id,
            **self.details,
        }
