"""LayerPlanV1 与 ShaderProgramSpecV1 的不可变契约模型.

本包只承载契约数据结构、规范化与哈希语义，与 legacy
``CompiledDslShader``/``GraphProgramKey`` 完全独立：不 import、不派生、
不反向构造。模型语义输入不得包含 attestation 或任何自报哈希字段，
``source_sha256``/``binding_sha256``/``spec_sha256``/``plan_sha256``
一律由可信层在解析后重算。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, Literal, Mapping

LAYER_PLAN_V1_SCHEMA_VERSION = "layer_plan_v1"
SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION = "shader_program_spec_v1"
WEBGL1_RENDERER_CONTRACT_ID = "webgl1_static_no_texture_v1"

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
UNIFORM_NAME_PATTERN = re.compile(r"^u_[A-Za-z0-9_]+$")
SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")

LAYER_ROLES = frozenset(
    {"background", "subject", "highlight", "shadow", "glow", "detail"}
)
UNIFORM_TYPES = frozenset({"float", "vec2", "vec3", "vec4"})
AUTHOR_ROLES = frozenset({"initial", "refine", "repair"})
RESERVED_UNIFORMS = frozenset({"u_image", "u_resolution", "u_time"})
UNIFORM_COMPONENT_COUNTS = {"float": 1, "vec2": 2, "vec3": 3, "vec4": 4}

MAX_LAYER_COUNT = 8
MAX_DOMINANT_COLORS = 4
MAX_LAYER_NOTES_CHARS = 280
MAX_AUTHOR_FIELD_CHARS = 128

LayerRole = Literal["background", "subject", "highlight", "shadow", "glow", "detail"]
UniformType = Literal["float", "vec2", "vec3", "vec4"]
AuthorRole = Literal["initial", "refine", "repair"]


@dataclass(frozen=True)
class RgbaColor:
    """归一化 RGBA 颜色，每个分量都在 [0, 1] 内且有限."""

    r: float
    g: float
    b: float
    a: float

    def to_list(self) -> list[float]:
        """返回 [r, g, b, a] 列表."""
        return [self.r, self.g, self.b, self.a]


@dataclass(frozen=True)
class NormalizedRegion:
    """归一化 bbox，左下角原点，x/y/width/height 都在 [0, 1] 内."""

    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float]:
        """返回可序列化字典."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class LayerSpec:
    """LayerPlanV1 中的一层结构化视觉解读."""

    layer_id: str
    role: LayerRole
    z_index: int
    region: NormalizedRegion
    dominant_colors: tuple[RgbaColor, ...]
    confidence: float
    notes: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回规范化字典."""
        return {
            "layer_id": self.layer_id,
            "role": self.role,
            "z_index": self.z_index,
            "region": self.region.to_dict(),
            "dominant_colors": [color.to_list() for color in self.dominant_colors],
            "confidence": self.confidence,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class LayerAuthorIdentity:
    """生成 LayerPlan 的视觉分析 Author 身份.

    ``sampling_params`` 必须记录 Gateway 实际生效的采样身份（provider、
    实际 temperature/reasoning_effort、response_format、identity source），
    绝不记录请求假值；``instruction_sha256`` 与 ``reference_content_type``
    绑定该次调用真实读取的指令与参考图媒体类型。
    """

    model_ref: str
    prompt_version: str
    schema_version: str
    instruction_sha256: str | None = None
    reference_content_type: str | None = None
    sampling_params: Mapping[str, Any] | None = None
    repair_context_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典."""
        return {
            "model_ref": self.model_ref,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "instruction_sha256": self.instruction_sha256,
            "reference_content_type": self.reference_content_type,
            "sampling_params": (
                dict(self.sampling_params) if self.sampling_params is not None else None
            ),
            "repair_context_sha256": self.repair_context_sha256,
        }


@dataclass(frozen=True)
class LayerPlanV1:
    """对参考图的结构化分层解读，永久 advisory，不参与选择与评分."""

    schema_version: str
    layers: tuple[LayerSpec, ...]
    reference_sha256: str
    author_identity: LayerAuthorIdentity
    observations_ref: str | None
    plan_sha256: str


@dataclass(frozen=True)
class UniformDeclaration:
    """一个 typed uniform 的严格声明：名称、类型、取值域与默认值."""

    name: str
    type: UniformType
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]
    default: tuple[float, ...]

    @property
    def component_count(self) -> int:
        """返回该类型的分量数."""
        return UNIFORM_COMPONENT_COUNTS[self.type]

    def to_dict(self) -> dict[str, Any]:
        """返回规范化字典."""
        return {
            "name": self.name,
            "type": self.type,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            "default": list(self.default),
        }


@dataclass(frozen=True)
class TunableParameter:
    """tunable manifest 中一个可数值优化的参数地址."""

    path: str
    type: UniformType
    minimum: tuple[float, ...]
    maximum: tuple[float, ...]
    step: float

    def to_dict(self) -> dict[str, Any]:
        """返回规范化字典."""
        return {
            "path": self.path,
            "type": self.type,
            "minimum": list(self.minimum),
            "maximum": list(self.maximum),
            "step": self.step,
        }


@dataclass(frozen=True)
class CanvasSpec:
    """渲染画布尺寸，正整数像素."""

    width: int
    height: int

    def to_dict(self) -> dict[str, int]:
        """返回可序列化字典."""
        return {"width": self.width, "height": self.height}


@dataclass(frozen=True)
class AuthorIdentity:
    """可信层绑定的 Spec 作者身份与血缘，不是模型自报内容.

    ``reference_sha256`` 必填；refine/repair 角色必须给出父
    ``parent_spec_sha256``，initial 角色必须没有父 Spec。
    ``sampling_params`` 必须记录 Gateway 实际生效的采样身份而非请求值；
    ``reference_content_type`` 绑定参考图媒体类型，
    ``input_context_sha256`` 绑定角色特定的 canonical 输入上下文
    （refine 含 current_render 哈希与评估上下文）。
    """

    reference_sha256: str
    instruction_sha256: str
    model_ref: str
    prompt_version: str
    role: AuthorRole
    sampling_params: Mapping[str, Any]
    plan_sha256: str | None = None
    parent_spec_sha256: str | None = None
    reference_content_type: str | None = None
    input_context_sha256: str | None = None
    repair_context_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回规范化字典."""
        return {
            "reference_sha256": self.reference_sha256,
            "plan_sha256": self.plan_sha256,
            "instruction_sha256": self.instruction_sha256,
            "model_ref": self.model_ref,
            "prompt_version": self.prompt_version,
            "role": self.role,
            "parent_spec_sha256": self.parent_spec_sha256,
            "sampling_params": dict(self.sampling_params),
            "reference_content_type": self.reference_content_type,
            "input_context_sha256": self.input_context_sha256,
            "repair_context_sha256": self.repair_context_sha256,
        }


@dataclass(frozen=True)
class ExecutionReceipt:
    """一次真实 prepare+draw 成功路径产出的进程内可信执行回执.

    绑定源码哈希、必填 Spec 哈希、RGB/PNG 像素哈希、renderer/GL/GLSL
    运行身份、nonce 与签发时间；``digest`` 是进程本地 key 对上述全部
    字段的 HMAC-SHA256，手造或篡改任一字段都使 digest 失配。只在同
    进程内可验证，不是 durable 证据。
    """

    source_sha256: str
    spec_sha256: str
    rgb_sha256: str
    png_sha256: str | None
    renderer_version: str
    runtime_metadata: Mapping[str, str]
    nonce: str
    issued_at: float
    digest: str

    def payload_dict(self) -> dict[str, Any]:
        """返回参与 HMAC 的规范化字段（不含 digest 本身）."""
        return {
            "schema_version": "execution_receipt_v1",
            "source_sha256": self.source_sha256,
            "spec_sha256": self.spec_sha256,
            "rgb_sha256": self.rgb_sha256,
            "png_sha256": self.png_sha256,
            "renderer_version": self.renderer_version,
            "runtime_metadata": dict(self.runtime_metadata),
            "nonce": self.nonce,
            "issued_at": self.issued_at,
        }

    def to_dict(self) -> dict[str, Any]:
        """返回可持久化字典（只在同进程内可验证，不具 durable 效力）."""
        return {**self.payload_dict(), "digest": self.digest}


@dataclass(frozen=True)
class ValidationAttestation:
    """可信 Validator 在全量校验与真实 compile/link/draw 通过后签发的证明.

    只绑定重算的 ``spec_sha256``、validator version、检查项清单、执行结果
    与可信 ``ExecutionReceipt``；compile/link/draw 结论由 receipt 的存在性
    证明，模型或任何非签发路径组件无法伪造能通过 match 的结构。
    """

    spec_sha256: str
    validator_version: str
    checks: tuple[str, ...]
    compile_ok: bool
    link_ok: bool
    draw_ok: bool
    execution_digest: str
    receipt: ExecutionReceipt

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典（只在同进程内可验证，不具 durable 效力）."""
        return {
            "spec_sha256": self.spec_sha256,
            "validator_version": self.validator_version,
            "checks": list(self.checks),
            "compile_ok": self.compile_ok,
            "link_ok": self.link_ok,
            "draw_ok": self.draw_ok,
            "execution_digest": self.execution_digest,
            "receipt": self.receipt.to_dict(),
        }


@dataclass(frozen=True)
class ShaderProgramSpecV1:
    """模型生成并经安全校验的执行真相.

    ``source_sha256``/``binding_sha256``/``spec_sha256`` 由可信层对规范化
    内容重算；``spec_sha256`` 明确排除 ``validation_attestation``，避免
    自哈希循环。
    """

    schema_version: str
    fragment_source: str
    uniform_schema: tuple[UniformDeclaration, ...]
    uniform_values: Mapping[str, Any]
    tunable_manifest: tuple[TunableParameter, ...]
    canvas: CanvasSpec
    renderer_contract_id: str
    source_sha256: str
    binding_sha256: str
    spec_sha256: str
    author_identity: AuthorIdentity
    validation_attestation: ValidationAttestation | None = None

    def with_attestation(
        self, attestation: ValidationAttestation
    ) -> ShaderProgramSpecV1:
        """返回附带 attestation 的新 Spec，语义字段与 spec_sha256 不变."""
        return replace(self, validation_attestation=attestation)
