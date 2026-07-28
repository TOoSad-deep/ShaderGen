"""可信 ValidationAttestation 的签发与匹配.

可执行真相是 "Spec + 匹配 attestation + 可信 ExecutionReceipt" 的组合。
``issue_attestation`` 只接受由真实 prepare+draw 成功路径产出、并由
Renderer 私有 signer（进程本地 HMAC key）签发的 ``ExecutionReceipt``；
任何手工构造、反序列化后进程已重启、或篡改过字段的 attestation/receipt
都无法通过 ``match_attestation``（fail-closed）。attestation 只是同进程
执行证明，不是 durable 证据。
"""

from __future__ import annotations

from dataclasses import dataclass

from shaderforge.program_spec.hashing import (
    recompute_spec_sha256,
)
from shaderforge.program_spec.models import (
    ExecutionReceipt,
    ShaderProgramSpecV1,
    ValidationAttestation,
)
from shaderforge.program_spec.receipt import (
    TrustedReceiptVerifier,
    process_receipt_verifier,
)

TRUSTED_VALIDATOR_VERSION = "program_spec_validator_v1_1"
REQUIRED_CHECKS = (
    "schema_static_safety",
    "webgl1_compile",
    "webgl1_link",
    "webgl1_draw",
)


class AttestationError(ValueError):
    """attestation 签发或匹配违反可信边界的 fail-closed 错误."""

    def __init__(self, code: str, message: str) -> None:
        """记录机器可读的违规代码与人类可读消息."""
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class AttestationMatchResult:
    """attestation 与 Spec 内容重算、receipt 验证的匹配结论."""

    ok: bool
    reasons: tuple[str, ...]


def _receipt_mismatch_reasons(
    spec: ShaderProgramSpecV1,
    receipt: ExecutionReceipt,
    issuer: TrustedReceiptVerifier,
) -> list[str]:
    """核对 receipt 的 HMAC、源码与 Spec 绑定；任何失配都列出原因."""
    reasons: list[str] = []
    if not issuer.verify(receipt):
        reasons.append("receipt_mismatch")
        return reasons
    if receipt.source_sha256 != spec.source_sha256:
        reasons.append("receipt_source_mismatch")
    if receipt.spec_sha256 != spec.spec_sha256:
        reasons.append("receipt_spec_mismatch")
    if receipt.png_sha256 is None:
        reasons.append("receipt_png_missing")
    if not receipt.renderer_version:
        reasons.append("receipt_renderer_missing")
    required_runtime = ("browser_version", "gl_version", "glsl_version")
    if any(not receipt.runtime_metadata.get(key) for key in required_runtime):
        reasons.append("receipt_runtime_incomplete")
    return reasons


def issue_attestation(
    spec: ShaderProgramSpecV1,
    *,
    receipt: ExecutionReceipt,
    static_ok: bool,
    issuer: TrustedReceiptVerifier | None = None,
    validator_version: str = TRUSTED_VALIDATOR_VERSION,
    checks: tuple[str, ...] = REQUIRED_CHECKS,
) -> ValidationAttestation:
    """在静态校验与真实 prepare+draw（receipt 为证）通过后签发 attestation.

    compile/link/draw 三个结论由 receipt 的存在性证明：receipt 只能由
    真实成功路径经可信 issuer 产出，调用方不得也无从手工填写执行结论；
    ``static_ok`` 是调用方刚执行的 ``validate_program_spec_safety`` 结论。
    任何一步不满足即 fail-closed。
    """
    effective_issuer = issuer or process_receipt_verifier()
    if recompute_spec_sha256(spec) != spec.spec_sha256:
        raise AttestationError(
            "spec_hash_mismatch", "Spec 内容哈希与重算不一致，拒绝签发。"
        )
    receipt_reasons = _receipt_mismatch_reasons(spec, receipt, effective_issuer)
    if receipt_reasons:
        raise AttestationError(
            receipt_reasons[0],
            "ExecutionReceipt 伪造、篡改或与 Spec 不匹配，拒绝签发。",
        )
    missing_checks = [item for item in REQUIRED_CHECKS if item not in checks]
    if missing_checks:
        raise AttestationError(
            "missing_checks", f"签发缺少必需检查项 {missing_checks}。"
        )
    if not static_ok:
        raise AttestationError(
            "static_validation_failed", "静态安全校验未通过，拒绝签发。"
        )
    return ValidationAttestation(
        spec_sha256=spec.spec_sha256,
        validator_version=validator_version,
        checks=tuple(checks),
        compile_ok=True,
        link_ok=True,
        draw_ok=True,
        execution_digest=receipt.digest,
        receipt=receipt,
    )


def match_attestation(
    spec: ShaderProgramSpecV1,
    attestation: ValidationAttestation,
    *,
    issuer: TrustedReceiptVerifier | None = None,
    trusted_validator_versions: tuple[str, ...] = (TRUSTED_VALIDATOR_VERSION,),
) -> AttestationMatchResult:
    """重算 Spec 哈希、验证 receipt HMAC 并核对 attestation 的全部绑定.

    无 attestation、哈希与内容重算不匹配、receipt 伪造/篡改/外进程、
    validator version 不受信任、检查项缺失或执行结果缺失的组合一律
    判定不可执行。
    """
    effective_issuer = issuer or process_receipt_verifier()
    reasons: list[str] = []
    if recompute_spec_sha256(spec) != spec.spec_sha256:
        reasons.append("spec_hash_mismatch")
    if attestation.spec_sha256 != spec.spec_sha256:
        reasons.append("attestation_spec_mismatch")
    if attestation.validator_version not in trusted_validator_versions:
        reasons.append("untrusted_validator_version")
    missing_checks = [
        item for item in REQUIRED_CHECKS if item not in attestation.checks
    ]
    if missing_checks:
        reasons.append(f"missing_checks:{','.join(missing_checks)}")
    if not (attestation.compile_ok and attestation.link_ok and attestation.draw_ok):
        reasons.append("execution_result_incomplete")
    if attestation.execution_digest != attestation.receipt.digest:
        reasons.append("execution_digest_mismatch")
    reasons.extend(
        _receipt_mismatch_reasons(spec, attestation.receipt, effective_issuer)
    )
    return AttestationMatchResult(ok=not reasons, reasons=tuple(reasons))


def is_executable(
    spec: ShaderProgramSpecV1,
    *,
    issuer: TrustedReceiptVerifier | None = None,
    trusted_validator_versions: tuple[str, ...] = (TRUSTED_VALIDATOR_VERSION,),
) -> bool:
    """判断 "Spec + 匹配 attestation + 可信 receipt" 组合是否可渲染为候选."""
    if spec.validation_attestation is None:
        return False
    return match_attestation(
        spec,
        spec.validation_attestation,
        issuer=issuer,
        trusted_validator_versions=trusted_validator_versions,
    ).ok


__all__ = [
    "REQUIRED_CHECKS",
    "TRUSTED_VALIDATOR_VERSION",
    "AttestationError",
    "AttestationMatchResult",
    "is_executable",
    "issue_attestation",
    "match_attestation",
]
