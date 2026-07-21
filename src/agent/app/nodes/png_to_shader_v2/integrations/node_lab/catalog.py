"""把 Node Lab 不透明 Artifact 映射为 V2 ArtifactCatalog。."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256

from agent.app.lab.integration import NodeExecutionHost
from agent.app.lab.models import ArtifactDescriptor, NodeLabError
from shaderforge.store import ArtifactRefV2


def _artifact_ref(value: object) -> ArtifactRefV2 | None:
    """只识别完整 V2 ref 形状，不把普通业务字典猜成 Artifact。."""
    if not isinstance(value, Mapping):
        return None
    required = {
        "artifact_id",
        "sha256",
        "kind",
        "schema_version",
        "content_type",
        "size_bytes",
    }
    if set(value) != required:
        return None
    try:
        return ArtifactRefV2(
            artifact_id=value["artifact_id"],
            sha256=value["sha256"],
            kind=value["kind"],
            schema_version=value["schema_version"],
            content_type=value["content_type"],
            size_bytes=value["size_bytes"],
        )
    except (TypeError, ValueError):
        return None


def collect_artifact_refs(value: object) -> tuple[ArtifactRefV2, ...]:
    """递归恢复 State 中的完整 V2 ref，供跨步骤精确 resolve。."""
    collected: dict[str, ArtifactRefV2] = {}

    def visit(item: object) -> None:
        ref = _artifact_ref(item)
        if ref is not None:
            existing = collected.get(ref.artifact_id)
            if existing is not None and existing != ref:
                raise ValueError("同一 artifact_id 在 State 中绑定了不同 V2 引用。")
            collected[ref.artifact_id] = ref
            return
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, Sequence) and not isinstance(
            item, (str, bytes, bytearray)
        ):
            for nested in item:
                visit(nested)

    visit(value)
    return tuple(collected[key] for key in sorted(collected))


@dataclass(frozen=True)
class _CreatedArtifact:
    descriptor: ArtifactDescriptor
    ref: ArtifactRefV2


class NodeLabArtifactCatalogV2:
    """绑定单个 LabRun/production run 的路径无关 Catalog adapter。.

    Node Lab 的 Artifact descriptor 不携带 V2 ``schema_version``，因此 adapter
    只从完整 State ref 恢复既有元数据；新写入 ref 则在本步骤内登记并作为
    output patch 的完整 ref 跨步骤传递。绝不根据文件名或 payload 猜 schema。
    """

    def __init__(
        self,
        host: NodeExecutionHost,
        *,
        lab_run_id: str,
        run_id: str,
        refs: Sequence[ArtifactRefV2] = (),
    ) -> None:
        """绑定 Lab host、LabRun、production run 与 State 已有 refs。."""
        if not lab_run_id.strip() or not run_id.strip():
            raise ValueError("lab_run_id 和 run_id 不能为空。")
        self._host = host
        self._lab_run_id = lab_run_id
        self.run_id = run_id
        self._refs: dict[str, ArtifactRefV2] = {}
        self._created: list[_CreatedArtifact] = []
        for ref in refs:
            self.seed_ref(ref)

    @property
    def created_descriptors(self) -> tuple[ArtifactDescriptor, ...]:
        """返回本步骤新写入的 Lab Artifact descriptor。."""
        return tuple(item.descriptor for item in self._created)

    def seed_ref(self, ref: ArtifactRefV2) -> None:
        """登记 State 已携带的完整 ref；冲突时 fail closed。."""
        existing = self._refs.get(ref.artifact_id)
        if existing is not None and existing != ref:
            raise ValueError("同一 artifact_id 不能绑定不同 V2 引用。")
        self._refs[ref.artifact_id] = ref

    def put(
        self,
        *,
        run_id: str,
        kind: str,
        schema_version: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactRefV2:
        """通过 Lab host 写 bytes，并返回与 descriptor 完整性一致的 V2 ref。."""
        if run_id != self.run_id:
            raise ValueError("Artifact put 的 run_id 与 Catalog 绑定不一致。")
        if not isinstance(data, bytes):
            raise TypeError("Artifact data 必须是 bytes。")
        digest = sha256(data).hexdigest()
        for existing in self._refs.values():
            if (
                existing.sha256 == digest
                and existing.kind == kind
                and existing.schema_version == schema_version
                and existing.content_type == content_type
                and existing.size_bytes == len(data)
            ):
                # Node Lab 的物理存储会为重复 upload 分配新 id；这里恢复
                # production ArtifactCatalog 的内容寻址语义，确保跨 step 的
                # repeatability/environment closure 复用同一不可变 ref。
                self.resolve(existing.artifact_id)
                return existing
        descriptor = self._host.upload_artifact(
            lab_run_id=self._lab_run_id,
            kind=kind,
            content_type=content_type,
            data=data,
        )
        ref = ArtifactRefV2(
            artifact_id=descriptor.artifact_id,
            sha256=descriptor.sha256,
            kind=kind,
            schema_version=schema_version,
            content_type=descriptor.content_type,
            size_bytes=descriptor.size_bytes,
        )
        self.seed_ref(ref)
        self._created.append(_CreatedArtifact(descriptor=descriptor, ref=ref))
        return ref

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        """只解析由 State 完整 ref 或本步骤 put 登记的 Artifact。."""
        try:
            ref = self._refs[artifact_id]
        except KeyError as exc:
            raise FileNotFoundError("V2 Artifact 元数据未进入当前 State。") from exc
        descriptor, data = self._read_descriptor(artifact_id)
        self._verify(descriptor, data, ref)
        self._seed_nested_refs(ref, data)
        return ref

    def read_bytes(self, artifact_id: str) -> bytes:
        """读取同一 LabRun 的 bytes，并重算 size/SHA-256。."""
        ref = self.resolve(artifact_id)
        descriptor, data = self._read_descriptor(artifact_id)
        self._verify(descriptor, data, ref)
        self._seed_nested_refs(ref, data)
        return data

    def list_refs(self) -> tuple[ArtifactRefV2, ...]:
        """返回按 artifact_id 排序的当前 run 完整引用快照。."""
        return tuple(self._refs[key] for key in sorted(self._refs))

    def total_size_bytes(self) -> int:
        """返回当前 run 按不可变 artifact_id 去重后的 payload 总字节数。."""
        return sum(ref.size_bytes for ref in self.list_refs())

    def _seed_nested_refs(self, parent: ArtifactRefV2, data: bytes) -> None:
        """从已验证 JSON Artifact 内恢复完整 nested refs。.

        这里只接受包含六个身份字段的精确 ref object；不会从业务字段、文件名
        或 payload 内容推断 kind/schema。后续 typed loader 仍负责领域闭包校验。
        """
        if not parent.content_type.startswith("application/json"):
            return
        try:
            value = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        for ref in collect_artifact_refs(value):
            self.seed_ref(ref)

    def _read_descriptor(self, artifact_id: str) -> tuple[ArtifactDescriptor, bytes]:
        try:
            return self._host.read_artifact(self._lab_run_id, artifact_id)
        except NodeLabError:
            raise
        except (FileNotFoundError, ValueError) as exc:
            raise FileNotFoundError("V2 Artifact 无法从当前 LabRun 读取。") from exc

    def _verify(
        self,
        descriptor: ArtifactDescriptor,
        data: bytes,
        ref: ArtifactRefV2,
    ) -> None:
        if descriptor.lab_run_id != self._lab_run_id:
            raise ValueError("Artifact descriptor 与当前 LabRun 不一致。")
        if (
            descriptor.artifact_id != ref.artifact_id
            or descriptor.sha256 != ref.sha256
            or descriptor.content_type != ref.content_type
            or descriptor.size_bytes != ref.size_bytes
            or len(data) != ref.size_bytes
            or sha256(data).hexdigest() != ref.sha256
        ):
            raise ValueError("Lab Artifact 与 V2 ref 完整性元数据不一致。")


__all__ = ["NodeLabArtifactCatalogV2", "collect_artifact_refs"]
