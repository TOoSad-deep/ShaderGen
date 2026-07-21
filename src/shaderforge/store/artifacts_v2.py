"""V2 Artifact 的路径无关领域引用与存取协议."""

from __future__ import annotations

import re
from typing import Protocol

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass

_OPAQUE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, config=ConfigDict(extra="forbid", strict=True))
class ArtifactRefV2:
    """描述 Artifact 身份与完整性，不暴露物理存储位置."""

    artifact_id: str
    sha256: str
    kind: str
    schema_version: str
    content_type: str
    size_bytes: int

    def __post_init__(self) -> None:
        """拒绝路径形态的 id 和不完整的完整性元数据."""
        if not isinstance(self.artifact_id, str) or not _OPAQUE_ID_PATTERN.fullmatch(
            self.artifact_id
        ):
            raise ValueError("artifact_id 必须是 opaque identifier。")
        if not isinstance(self.sha256, str) or not _SHA256_PATTERN.fullmatch(
            self.sha256
        ):
            raise ValueError("sha256 必须是 64 位小写十六进制摘要。")
        for field_name in ("kind", "schema_version", "content_type"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} 不能为空。")
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise ValueError("size_bytes 必须是非负整数。")


class ArtifactResolver(Protocol):
    """按 opaque artifact id 解析和读取 Artifact."""

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        """返回领域引用."""
        ...

    def read_bytes(self, artifact_id: str) -> bytes:
        """返回通过完整性校验的内容."""
        ...


class ArtifactCatalog(ArtifactResolver, Protocol):
    """在 Resolver 能力上增加 Artifact 登记."""

    def put(
        self,
        *,
        run_id: str,
        kind: str,
        schema_version: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactRefV2:
        """登记并返回路径无关引用."""
        ...

    def list_refs(self) -> tuple[ArtifactRefV2, ...]:
        """返回当前 run 的完整、稳定排序引用快照."""
        ...

    def total_size_bytes(self) -> int:
        """返回当前 run 去重后的 Artifact payload 总字节数."""
        ...
