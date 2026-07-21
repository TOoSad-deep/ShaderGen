"""把 V1 relative-path Artifact 只读适配为 V2 opaque 引用."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256

from shaderforge.store.artifact_catalog import ArtifactIntegrityError
from shaderforge.store.artifacts_v2 import ArtifactRefV2
from shaderforge.store.local_artifacts import ArtifactRef, RunArtifactStore

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class _LegacyBinding:
    artifact_ref: ArtifactRefV2
    relative_path: str


class LegacyArtifactRefAdapter:
    """校验并读取 V1 Artifact，不向 Catalog 或 blob 目录复制内容."""

    def __init__(self, run_store: RunArtifactStore, *, run_id: str) -> None:
        """绑定 V1 Artifact 所属的单个运行."""
        if not isinstance(run_store, RunArtifactStore):
            raise TypeError("run_store 必须是 RunArtifactStore。")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 不能为空。")
        self._run_store = run_store
        self.run_id = run_id
        self._bindings: dict[str, _LegacyBinding] = {}

    def adapt(
        self,
        legacy_ref: ArtifactRef,
        *,
        kind: str,
        schema_version: str,
    ) -> ArtifactRefV2:
        """验证 V1 引用和 bytes，并建立当前进程内的只读映射."""
        self._validate_legacy_ref(legacy_ref)
        data = self._read_legacy_verified(legacy_ref)
        artifact_id = self._artifact_id(
            legacy_ref=legacy_ref,
            kind=kind,
            schema_version=schema_version,
        )
        artifact_ref = ArtifactRefV2(
            artifact_id=artifact_id,
            sha256=legacy_ref.sha256,
            kind=kind,
            schema_version=schema_version,
            content_type=legacy_ref.content_type,
            size_bytes=len(data),
        )
        binding = _LegacyBinding(
            artifact_ref=artifact_ref,
            relative_path=legacy_ref.relative_path,
        )
        existing = self._bindings.get(artifact_id)
        if existing is not None and existing != binding:
            raise ValueError("legacy artifact_id 与已有绑定冲突。")
        self._bindings[artifact_id] = binding
        return artifact_ref

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        """解析已适配的 V1 Artifact 引用."""
        try:
            return self._bindings[artifact_id].artifact_ref
        except (KeyError, TypeError) as exc:
            raise FileNotFoundError("未找到对应 legacy artifact_id。") from exc

    def read_bytes(self, artifact_id: str) -> bytes:
        """直接读取原 V1 文件，并在每次读取时复核 size 与 SHA."""
        try:
            binding = self._bindings[artifact_id]
        except (KeyError, TypeError) as exc:
            raise FileNotFoundError("未找到对应 legacy artifact_id。") from exc
        legacy_ref = ArtifactRef(
            relative_path=binding.relative_path,
            sha256=binding.artifact_ref.sha256,
            size_bytes=binding.artifact_ref.size_bytes,
            content_type=binding.artifact_ref.content_type,
        )
        return self._read_legacy_verified(legacy_ref)

    def _read_legacy_verified(self, legacy_ref: ArtifactRef) -> bytes:
        try:
            data = self._run_store.read_bytes(legacy_ref.relative_path)
        except (FileNotFoundError, ValueError) as exc:
            raise ArtifactIntegrityError("Legacy Artifact bytes 缺失或路径无效。") from exc
        if len(data) != legacy_ref.size_bytes:
            raise ArtifactIntegrityError("Legacy Artifact size 与引用不一致。")
        if sha256(data).hexdigest() != legacy_ref.sha256:
            raise ArtifactIntegrityError("Legacy Artifact SHA-256 与引用不一致。")
        return data

    def _artifact_id(
        self,
        *,
        legacy_ref: ArtifactRef,
        kind: str,
        schema_version: str,
    ) -> str:
        identity = json.dumps(
            {
                "content_type": legacy_ref.content_type,
                "kind": kind,
                "legacy_relative_path": legacy_ref.relative_path,
                "run_id": self.run_id,
                "schema_version": schema_version,
                "sha256": legacy_ref.sha256,
                "size_bytes": legacy_ref.size_bytes,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"legacy_{sha256(identity).hexdigest()}"

    @staticmethod
    def _validate_legacy_ref(legacy_ref: ArtifactRef) -> None:
        if not isinstance(legacy_ref, ArtifactRef):
            raise TypeError("legacy_ref 必须是 V1 ArtifactRef。")
        if (
            not isinstance(legacy_ref.relative_path, str)
            or not legacy_ref.relative_path
        ):
            raise ValueError("Legacy Artifact relative_path 无效。")
        if not isinstance(legacy_ref.sha256, str) or not _SHA256_PATTERN.fullmatch(
            legacy_ref.sha256
        ):
            raise ValueError("Legacy Artifact sha256 无效。")
        if (
            isinstance(legacy_ref.size_bytes, bool)
            or not isinstance(legacy_ref.size_bytes, int)
            or legacy_ref.size_bytes < 0
        ):
            raise ValueError("Legacy Artifact size_bytes 无效。")
        if (
            not isinstance(legacy_ref.content_type, str)
            or not legacy_ref.content_type.strip()
        ):
            raise ValueError("Legacy Artifact content_type 无效。")
