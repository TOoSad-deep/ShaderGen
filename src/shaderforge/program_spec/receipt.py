"""进程内可信 ExecutionReceipt 签发器（capability 根）.

威胁模型：防御**进程外**的伪造——手工构造、从持久化反序列化或篡改字段的
receipt 都必须验证失败。Renderer 私有 signer 持有由 ``secrets`` 生成的
进程本地 HMAC key，key 永不导出、永不持久化；没有 key 就无法伪造
``digest``。进程重启后 key 改变，一切旧 receipt（包括从私有 run 目录读回的）
一律验证失败（fail-closed）：attestation/receipt 只是**同进程**执行证明，
绝不是 durable 证据，也不得冒充 durable。

同进程内的对抗性代码不在本机制威胁模型内（Python 无法防御同进程内省）；
本机制保证的是"任何不经过真实签发路径的对象都无法通过 match"。
"""

from __future__ import annotations

import hmac
import secrets
import time
import uuid
from hashlib import sha256
from typing import Any, Mapping

from shaderforge.program_spec.hashing import canonical_json
from shaderforge.program_spec.models import ExecutionReceipt


class ReceiptError(ValueError):
    """receipt 签发或反序列化违反可信边界的 fail-closed 错误."""

    def __init__(self, code: str, message: str) -> None:
        """记录机器可读违规代码与人类可读消息."""
        self.code = code
        super().__init__(message)


def _sha256_hex(value: Any, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or not all(char in "0123456789abcdef" for char in value)
    ):
        raise ReceiptError("invalid_sha256", f"{name} 必须是 64 位小写十六进制。")
    return value


class _TrustedReceiptSigner:
    """持有进程本地 HMAC key 的 Renderer 私有签发 capability.

    该类型和实例均不从 ``shaderforge.program_spec`` 公共包导出。
    """

    def __init__(
        self,
        *,
        key: bytes | None = None,
        issuer_id: str = "process_local",
    ) -> None:
        """生成或显式注入 key；key 只存内存，不提供任何导出途径."""
        self._key = key if key is not None else secrets.token_bytes(32)
        if not isinstance(self._key, bytes) or len(self._key) < 16:
            raise ReceiptError("invalid_key", "receipt key 必须是至少 16 字节。")
        self.issuer_id = issuer_id

    def _digest(self, payload: Mapping[str, Any]) -> str:
        return hmac.new(
            self._key,
            canonical_json(payload).encode("utf-8"),
            sha256,
        ).hexdigest()

    def issue_after_draw(
        self,
        *,
        source_sha256: str,
        rgb_bytes: bytes,
        png_bytes: bytes | None = None,
        spec_sha256: str,
        renderer_version: str,
        runtime_metadata: Mapping[str, str] | None = None,
    ) -> ExecutionReceipt:
        """只在真实 prepare+draw 成功路径上签发绑定像素与运行身份的回执."""
        _sha256_hex(source_sha256, name="source_sha256")
        _sha256_hex(spec_sha256, name="spec_sha256")
        if not isinstance(rgb_bytes, bytes) or not rgb_bytes:
            raise ReceiptError("invalid_rgb", "receipt 必须绑定非空 RGB 像素。")
        metadata = {
            str(key): str(value) for key, value in (runtime_metadata or {}).items()
        }
        rgb_sha256 = sha256(rgb_bytes).hexdigest()
        png_sha256 = sha256(png_bytes).hexdigest() if png_bytes is not None else None
        renderer_version_value = str(renderer_version)
        nonce = uuid.uuid4().hex
        issued_at = time.time()
        payload: dict[str, Any] = {
            "schema_version": "execution_receipt_v1",
            "source_sha256": source_sha256,
            "spec_sha256": spec_sha256,
            "rgb_sha256": rgb_sha256,
            "png_sha256": png_sha256,
            "renderer_version": renderer_version_value,
            "runtime_metadata": metadata,
            "nonce": nonce,
            "issued_at": issued_at,
        }
        return ExecutionReceipt(
            source_sha256=source_sha256,
            spec_sha256=spec_sha256,
            rgb_sha256=rgb_sha256,
            png_sha256=png_sha256,
            renderer_version=renderer_version_value,
            runtime_metadata=metadata,
            nonce=nonce,
            issued_at=issued_at,
            digest=self._digest(payload),
        )

    def verify(self, receipt: ExecutionReceipt) -> bool:
        """用进程本地 key 重算 HMAC；手造/篡改/外进程 receipt 一律失败."""
        if not isinstance(receipt, ExecutionReceipt):
            return False
        expected = self._digest(receipt.payload_dict())
        return hmac.compare_digest(expected, receipt.digest)

    def receipt_from_dict(self, data: Mapping[str, Any]) -> ExecutionReceipt:
        """从持久化字典重建 receipt（不验证；验证由 ``verify`` fail-closed）."""
        if not isinstance(data, Mapping):
            raise ReceiptError("invalid_receipt", "receipt 必须是对象。")
        if data.get("schema_version") != "execution_receipt_v1":
            raise ReceiptError("invalid_receipt", "receipt schema_version 不受支持。")
        runtime_metadata = data.get("runtime_metadata")
        if not isinstance(runtime_metadata, Mapping):
            raise ReceiptError(
                "invalid_receipt", "receipt runtime_metadata 必须是对象。"
            )
        issued_at = data.get("issued_at")
        if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
            raise ReceiptError("invalid_receipt", "receipt issued_at 必须是数值。")
        spec_sha256 = data.get("spec_sha256")
        png_sha256 = data.get("png_sha256")
        return ExecutionReceipt(
            source_sha256=_sha256_hex(data.get("source_sha256"), name="source_sha256"),
            spec_sha256=_sha256_hex(spec_sha256, name="spec_sha256"),
            rgb_sha256=_sha256_hex(data.get("rgb_sha256"), name="rgb_sha256"),
            png_sha256=(
                _sha256_hex(png_sha256, name="png_sha256")
                if png_sha256 is not None
                else None
            ),
            renderer_version=str(data.get("renderer_version") or ""),
            runtime_metadata={
                str(key): str(value) for key, value in runtime_metadata.items()
            },
            nonce=str(data.get("nonce") or ""),
            issued_at=float(issued_at),
            digest=str(data.get("digest") or ""),
        )


class TrustedReceiptVerifier:
    """Runner/attestation 可见的 verify-only capability，无签发方法."""

    def __init__(self, signer: _TrustedReceiptSigner) -> None:
        """绑定同进程 signer，但只转发验证与反序列化能力."""
        self.__signer = signer
        self.issuer_id = signer.issuer_id

    def verify(self, receipt: ExecutionReceipt) -> bool:
        """验证 receipt HMAC，不暴露签发入口."""
        return self.__signer.verify(receipt)

    def receipt_from_dict(self, data: Mapping[str, Any]) -> ExecutionReceipt:
        """重建 receipt；调用方仍须显式 ``verify``."""
        return self.__signer.receipt_from_dict(data)


_PROCESS_SIGNER = _TrustedReceiptSigner(issuer_id="process_local_renderer")
_PROCESS_VERIFIER = TrustedReceiptVerifier(_PROCESS_SIGNER)


def process_receipt_verifier() -> TrustedReceiptVerifier:
    """返回进程级 verify-only capability；key 随进程消亡."""
    return _PROCESS_VERIFIER


def _renderer_receipt_signer() -> _TrustedReceiptSigner:
    """仅供真实 rendering 组合根使用的私有 signer."""
    return _PROCESS_SIGNER


def _test_receipt_capabilities(
    *, key: bytes | None = None, issuer_id: str = "test_only"
) -> tuple[_TrustedReceiptSigner, TrustedReceiptVerifier]:
    """为 fake renderer 创建隔离 signer/verifier 对；仅供测试."""
    signer = _TrustedReceiptSigner(key=key, issuer_id=f"{issuer_id}_signer")
    return signer, TrustedReceiptVerifier(signer)


__all__ = [
    "ReceiptError",
    "TrustedReceiptVerifier",
    "process_receipt_verifier",
]
