"""LayerPlanV1 与 ShaderProgramSpecV1 的安全契约包.

纯确定性实现：严格解析/规范化、可信层哈希重算、attestation 签发与匹配。
与 legacy ``CompiledDslShader``/``GraphProgramKey`` 完全独立。
"""

from shaderforge.program_spec.attestation import (
    REQUIRED_CHECKS,
    TRUSTED_VALIDATOR_VERSION,
    AttestationError,
    AttestationMatchResult,
    is_executable,
    issue_attestation,
    match_attestation,
)
from shaderforge.program_spec.hashing import (
    canonical_json,
    compute_binding_sha256,
    compute_plan_sha256,
    compute_source_sha256,
    compute_spec_sha256,
    recompute_binding_sha256,
    recompute_plan_sha256,
    recompute_source_sha256,
    recompute_spec_sha256,
    sha256_hex_text,
)
from shaderforge.program_spec.models import (
    LAYER_PLAN_V1_SCHEMA_VERSION,
    SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION,
    WEBGL1_RENDERER_CONTRACT_ID,
    AuthorIdentity,
    CanvasSpec,
    ExecutionReceipt,
    LayerAuthorIdentity,
    LayerPlanV1,
    LayerSpec,
    NormalizedRegion,
    RgbaColor,
    ShaderProgramSpecV1,
    TunableParameter,
    UniformDeclaration,
    ValidationAttestation,
)
from shaderforge.program_spec.parsing import (
    ProgramSpecParseError,
    build_author_identity,
    build_layer_author_identity,
    build_layer_plan,
    build_program_spec,
)
from shaderforge.program_spec.receipt import (
    ReceiptError,
    TrustedReceiptVerifier,
    process_receipt_verifier,
)

__all__ = [
    "LAYER_PLAN_V1_SCHEMA_VERSION",
    "REQUIRED_CHECKS",
    "SHADER_PROGRAM_SPEC_V1_SCHEMA_VERSION",
    "TRUSTED_VALIDATOR_VERSION",
    "WEBGL1_RENDERER_CONTRACT_ID",
    "AttestationError",
    "AttestationMatchResult",
    "AuthorIdentity",
    "CanvasSpec",
    "ExecutionReceipt",
    "LayerAuthorIdentity",
    "LayerPlanV1",
    "LayerSpec",
    "NormalizedRegion",
    "ProgramSpecParseError",
    "ReceiptError",
    "RgbaColor",
    "ShaderProgramSpecV1",
    "TrustedReceiptVerifier",
    "TunableParameter",
    "UniformDeclaration",
    "ValidationAttestation",
    "build_author_identity",
    "build_layer_author_identity",
    "build_layer_plan",
    "build_program_spec",
    "canonical_json",
    "compute_binding_sha256",
    "compute_plan_sha256",
    "compute_source_sha256",
    "compute_spec_sha256",
    "is_executable",
    "issue_attestation",
    "match_attestation",
    "process_receipt_verifier",
    "recompute_binding_sha256",
    "recompute_plan_sha256",
    "recompute_source_sha256",
    "recompute_spec_sha256",
    "sha256_hex_text",
]
