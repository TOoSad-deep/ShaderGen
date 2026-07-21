"""V2.2 Deterministic Compiler 的冻结输入输出契约。."""

from __future__ import annotations

from hashlib import sha256
from typing import Literal

from pydantic import Field, computed_field, model_validator

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.taxonomy import RequiredLayerTaxon
from shaderforge.genome.models import NodeKind, PortType
from shaderforge.store import ArtifactRefV2

DETERMINISTIC_COMPILER_VERSION: Literal["deterministic_compiler_v2_1"] = (
    "deterministic_compiler_v2_1"
)
DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD = 8
DIAGNOSTIC_OWNERSHIP_POLICY_VERSION: Literal[
    "stable_instance_ordinal_first_match_v1"
] = "stable_instance_ordinal_first_match_v1"


class CompilerDefectError(RuntimeError):
    """表示 Genome 无法安全编译，或 Compiler 产生了非法 GLSL。."""

    def __init__(self, code: str, diagnostics: tuple[str, ...]) -> None:
        """保存稳定错误码与安全诊断。."""
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(f"compiler_defect:{code}")


class CompilerAstInput(FrozenModel):
    """一个已解析、类型闭合的 AST 输入边。."""

    input_port: NonEmptyString
    source_canonical_node_id: NonEmptyString
    source_port: NonEmptyString
    value_type: PortType
    sdf_to_mask_conversion: Literal["analytic_fixed_width_v1"] | None = None

    @model_validator(mode="after")
    def _validate_conversion(self) -> CompilerAstInput:
        if self.sdf_to_mask_conversion is not None and self.value_type != "mask":
            raise ValueError("SDF→mask conversion 的 AST 输入类型必须是 mask。")
        return self


class CompilerAstBinding(FrozenModel):
    """Node binding 到稳定 GLSL 参数符号的映射。."""

    binding_name: NonEmptyString
    parameter_path: NonEmptyString
    parameter_symbol: NonEmptyString


class CompilerAstNode(FrozenModel):
    """与 record node id 解耦的 typed AST Node。."""

    canonical_node_id: NonEmptyString
    kind: NodeKind
    node_version: Literal["1"] = "1"
    semantic_role: NonEmptyString
    sibling_ordinal: int = Field(ge=0)
    inputs: tuple[CompilerAstInput, ...]
    bindings: tuple[CompilerAstBinding, ...]
    output_port: NonEmptyString
    output_type: PortType


class CompilerAst(FrozenModel):
    """Deterministic Compiler 的完整稳定 AST。."""

    schema_version: Literal["compiler_ast_v1"] = "compiler_ast_v1"
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    nodes: tuple[CompilerAstNode, ...] = Field(min_length=1)
    output_canonical_node_id: NonEmptyString

    @model_validator(mode="after")
    def _validate_ast(self) -> CompilerAst:
        ids = [node.canonical_node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("Compiler AST canonical node id 不得重复。")
        if self.output_canonical_node_id not in set(ids):
            raise ValueError("Compiler AST output node 不存在。")
        return self


class NodeLineEntry(FrozenModel):
    """一个 canonical node 对应的 GLSL 行区间。."""

    canonical_node_id: NonEmptyString
    kind: NodeKind
    semantic_role: NonEmptyString
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate_range(self) -> NodeLineEntry:
        if self.end_line < self.start_line:
            raise ValueError("Node line range 结束行不得早于开始行。")
        return self


class NodeLineSourceMap(FrozenModel):
    """只使用 canonical node identity 的稳定源码映射。."""

    schema_version: Literal["compiler_node_line_map_v1"] = "compiler_node_line_map_v1"
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    entries: tuple[NodeLineEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_entries(self) -> NodeLineSourceMap:
        ids = [entry.canonical_node_id for entry in self.entries]
        if len(ids) != len(set(ids)):
            raise ValueError("Node line map canonical node id 不得重复。")
        if any(
            current.start_line <= previous.end_line
            for previous, current in zip(self.entries, self.entries[1:])
        ):
            raise ValueError("Node line map 必须按互不重叠的源码顺序排列。")
        return self


class CompilerParameterEntry(FrozenModel):
    """参数 path、类型、源码符号和声明行的确定性绑定。."""

    parameter_path: NonEmptyString
    parameter_symbol: NonEmptyString
    dtype: Literal["float", "int", "bool", "vec2", "vec3", "vec4"]
    declaration_line: int = Field(ge=1)
    optimizable: bool
    quantization: float | None = Field(default=None, gt=0.0)


class CompilerParameterTable(FrozenModel):
    """Compiler 生成源码的参数定位表。."""

    schema_version: Literal["compiler_parameter_table_v1"] = (
        "compiler_parameter_table_v1"
    )
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    entries: tuple[CompilerParameterEntry, ...]

    @model_validator(mode="after")
    def _validate_entries(self) -> CompilerParameterTable:
        paths = [entry.parameter_path for entry in self.entries]
        symbols = [entry.parameter_symbol for entry in self.entries]
        if len(paths) != len(set(paths)) or len(symbols) != len(set(symbols)):
            raise ValueError("Compiler parameter path/symbol 不得重复。")
        if paths != sorted(paths):
            raise ValueError("Compiler parameter table 必须按 path 排序。")
        return self


class CompilationProduct(FrozenModel):
    """渲染前的纯内存确定性编译结果。."""

    schema_version: Literal["compilation_product_v1"] = "compilation_product_v1"
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    glsl_source: str = Field(min_length=1)
    glsl_sha256: Sha256Hex
    ast: CompilerAst
    node_line_map: NodeLineSourceMap
    compiler_parameter_table: CompilerParameterTable
    estimated_ops: int = Field(ge=0)
    numerical_risks: tuple[NonEmptyString, ...]
    diagnostics: tuple[NonEmptyString, ...]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def glsl_size_bytes(self) -> int:
        """返回 UTF-8 GLSL 的确定性字节数。."""
        return len(self.glsl_source.encode("utf-8"))

    @model_validator(mode="after")
    def _validate_bindings(self) -> CompilationProduct:
        hashes = {
            self.semantic_genome_hash,
            self.ast.semantic_genome_hash,
            self.node_line_map.semantic_genome_hash,
            self.compiler_parameter_table.semantic_genome_hash,
        }
        if len(hashes) != 1:
            raise ValueError("Compilation product 的 semantic genome hash 不闭合。")
        if sha256(self.glsl_source.encode("utf-8")).hexdigest() != self.glsl_sha256:
            raise ValueError("Compilation product 的 GLSL SHA-256 不一致。")
        ast_projection = tuple(
            (node.canonical_node_id, node.kind, node.semantic_role)
            for node in self.ast.nodes
        )
        line_projection = tuple(
            (entry.canonical_node_id, entry.kind, entry.semantic_role)
            for entry in self.node_line_map.entries
        )
        if ast_projection != line_projection:
            raise ValueError("Compilation product 的 AST 与 line map 不闭合。")
        return self


class CompilationBundle(FrozenModel):
    """内容寻址物化后的 Compiler 交付包。."""

    schema_version: Literal["compilation_bundle_v1"] = "compilation_bundle_v1"
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    glsl_ref: ArtifactRefV2
    glsl_sha256: Sha256Hex
    node_line_map_ref: ArtifactRefV2
    compiler_parameter_table_ref: ArtifactRefV2
    ast_ref: ArtifactRefV2
    estimated_ops: int = Field(ge=0)
    numerical_risks: tuple[NonEmptyString, ...]
    diagnostics: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def _validate_refs(self) -> CompilationBundle:
        expected = (
            (
                self.glsl_ref,
                "compiled_glsl",
                "compiled_glsl_es_100_v1",
                "text/x-glsl; charset=utf-8",
            ),
            (
                self.node_line_map_ref,
                "compiler_node_line_map",
                "compiler_node_line_map_v1",
                "application/json",
            ),
            (
                self.compiler_parameter_table_ref,
                "compiler_parameter_table",
                "compiler_parameter_table_v1",
                "application/json",
            ),
            (
                self.ast_ref,
                "compiler_ast",
                "compiler_ast_v1",
                "application/json",
            ),
        )
        for ref, kind, schema_version, content_type in expected:
            if (
                ref.kind != kind
                or ref.schema_version != schema_version
                or ref.content_type != content_type
            ):
                raise ValueError("Compilation bundle ArtifactRef 契约不一致。")
        if self.glsl_ref.sha256 != self.glsl_sha256:
            raise ValueError("Compilation bundle GLSL ref/hash 不一致。")
        return self


class DiagnosticPassSourceV3(FrozenModel):
    """从 Genome AST 节点确定性导出的单个结构诊断 Shader。."""

    schema_version: Literal["diagnostic_pass_source_v3"] = "diagnostic_pass_source_v3"
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    pass_id: NonEmptyString
    pass_kind: Literal[
        "subject_visible_delta", "instance_visible_delta", "layer_visible_delta"
    ]
    canonical_node_id: NonEmptyString
    node_kind: NodeKind
    node_output_type: PortType
    instance_index: int | None = Field(default=None, ge=0)
    layer: RequiredLayerTaxon | None = None
    glsl_source: str = Field(min_length=1)
    glsl_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_identity(self) -> DiagnosticPassSourceV3:
        if self.pass_kind == "subject_visible_delta":
            if self.instance_index is not None or self.layer is not None:
                raise ValueError("subject visible-delta pass 不得绑定 instance/layer。")
            if self.node_output_type not in {"sdf", "mask"}:
                raise ValueError("subject visible-delta pass 必须绑定结构 root。")
        elif self.pass_kind == "instance_visible_delta":
            if self.instance_index is None or self.layer is not None:
                raise ValueError(
                    "instance visible-delta pass 必须且只能绑定 instance_index。"
                )
            if self.node_output_type not in {"sdf", "mask"}:
                raise ValueError(
                    "instance visible-delta pass 必须来自 Genome 的 sdf/mask 输出。"
                )
        elif self.layer is None or self.instance_index is not None:
            raise ValueError(
                "layer visible-delta pass 必须且只能绑定 layer。"
            )
        elif self.node_output_type != "color":
            raise ValueError("layer visible-delta pass 必须来自 color node。")
        if sha256(self.glsl_source.encode("utf-8")).hexdigest() != self.glsl_sha256:
            raise ValueError("Diagnostic pass GLSL SHA-256 不一致。")
        return self


class DiagnosticCompilationProductV3(FrozenModel):
    """与 beauty CompilationProduct 分离的确定性结构诊断集合。."""

    schema_version: Literal["diagnostic_compilation_product_v3"] = (
        "diagnostic_compilation_product_v3"
    )
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    passes: tuple[DiagnosticPassSourceV3, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_passes(self) -> DiagnosticCompilationProductV3:
        ids = [item.pass_id for item in self.passes]
        if ids != sorted(set(ids)):
            raise ValueError("Diagnostic pass 必须按 pass_id 唯一且稳定排序。")
        if any(
            item.semantic_genome_hash != self.semantic_genome_hash
            for item in self.passes
        ):
            raise ValueError("Diagnostic pass semantic Genome hash 不闭合。")
        if any(
            item.ownership_policy_version != self.ownership_policy_version
            for item in self.passes
        ):
            raise ValueError("Diagnostic pass ownership policy 不闭合。")
        return self


class DiagnosticPassArtifactV3(FrozenModel):
    """一个 diagnostic source 的内容寻址身份。."""

    pass_id: NonEmptyString
    pass_kind: Literal[
        "subject_visible_delta", "instance_visible_delta", "layer_visible_delta"
    ]
    canonical_node_id: NonEmptyString
    node_kind: NodeKind
    node_output_type: PortType
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    instance_index: int | None = Field(default=None, ge=0)
    layer: RequiredLayerTaxon | None = None
    source_ref: ArtifactRefV2
    source_sha256: Sha256Hex

    @model_validator(mode="after")
    def _validate_ref(self) -> DiagnosticPassArtifactV3:
        if (
            self.source_ref.kind != "diagnostic_glsl"
            or self.source_ref.schema_version != "diagnostic_glsl_es_100_v3"
            or self.source_ref.content_type != "text/x-glsl; charset=utf-8"
            or self.source_ref.sha256 != self.source_sha256
        ):
            raise ValueError("Diagnostic source ArtifactRef 契约不一致。")
        if self.pass_kind == "subject_visible_delta":
            if self.instance_index is not None or self.layer is not None:
                raise ValueError("Subject diagnostic Artifact identity 不完整。")
        elif self.pass_kind == "instance_visible_delta":
            if self.instance_index is None or self.layer is not None:
                raise ValueError("Instance diagnostic Artifact identity 不完整。")
        elif self.layer is None or self.instance_index is not None:
            raise ValueError("Layer diagnostic Artifact identity 不完整。")
        return self


class DiagnosticCompilationBundleV3(FrozenModel):
    """可供 Renderer 逐 request 执行的 typed diagnostic bundle。."""

    schema_version: Literal["diagnostic_compilation_bundle_v3"] = (
        "diagnostic_compilation_bundle_v3"
    )
    compiler_version: Literal["deterministic_compiler_v2_1"] = (
        DETERMINISTIC_COMPILER_VERSION
    )
    semantic_genome_hash: Sha256Hex
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    passes: tuple[DiagnosticPassArtifactV3, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_passes(self) -> DiagnosticCompilationBundleV3:
        ids = [item.pass_id for item in self.passes]
        if ids != sorted(set(ids)):
            raise ValueError("Diagnostic bundle pass 必须唯一且稳定排序。")
        if any(
            item.ownership_policy_version != self.ownership_policy_version
            for item in self.passes
        ):
            raise ValueError("Diagnostic bundle ownership policy 不闭合。")
        return self


# 兼容 import 名只指向新 Schema；它们不能加载旧 v2 payload。
DiagnosticPassSourceV2 = DiagnosticPassSourceV3
DiagnosticCompilationProductV2 = DiagnosticCompilationProductV3
DiagnosticPassArtifactV2 = DiagnosticPassArtifactV3
DiagnosticCompilationBundleV2 = DiagnosticCompilationBundleV3


__all__ = [
    "DETERMINISTIC_COMPILER_VERSION",
    "DIAGNOSTIC_OWNERSHIP_POLICY_VERSION",
    "DIAGNOSTIC_VISIBLE_DELTA_BYTE_THRESHOLD",
    "CompilationBundle",
    "CompilationProduct",
    "DiagnosticCompilationBundleV2",
    "DiagnosticCompilationBundleV3",
    "DiagnosticCompilationProductV2",
    "DiagnosticCompilationProductV3",
    "DiagnosticPassArtifactV2",
    "DiagnosticPassArtifactV3",
    "DiagnosticPassSourceV2",
    "DiagnosticPassSourceV3",
    "CompilerAst",
    "CompilerAstBinding",
    "CompilerAstInput",
    "CompilerAstNode",
    "CompilerDefectError",
    "CompilerParameterEntry",
    "CompilerParameterTable",
    "NodeLineEntry",
    "NodeLineSourceMap",
]
