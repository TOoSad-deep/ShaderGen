"""绑定单次运行的本地 V2 Artifact Catalog."""

from __future__ import annotations

import fcntl
import json
from contextlib import contextmanager
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterator

from shaderforge.store.artifacts_v2 import ArtifactRefV2
from shaderforge.store.local_artifacts import RunArtifactStore

_MANIFEST_SCHEMA_VERSION = "artifact_catalog_manifest_v2"
_MANIFEST_PATH = ".artifact-catalog-v2/manifest.json"
_LOCK_PATH = ".artifact-catalog-v2/catalog.lock"
_BLOB_DIRECTORY = ".artifact-catalog-v2/blobs"
_MANIFEST_FIELDS = frozenset({"schema_version", "run_id", "revision", "artifacts"})
_ARTIFACT_ENTRY_FIELDS = frozenset(
    {
        "artifact_id",
        "sha256",
        "kind",
        "schema_version",
        "content_type",
        "size_bytes",
        "relative_path",
    }
)


class ArtifactCatalogError(ValueError):
    """表示 Catalog manifest 或调用契约无效."""


class ArtifactIntegrityError(ArtifactCatalogError):
    """表示 Artifact bytes 与已登记的完整性元数据不一致."""


class LocalArtifactCatalog:
    """把 opaque artifact id 映射到一个 run 内的本地 bytes."""

    def __init__(self, run_store: RunArtifactStore, *, run_id: str) -> None:
        """绑定单个 run，禁止 Catalog 在运行间复用."""
        if not isinstance(run_store, RunArtifactStore):
            raise TypeError("run_store 必须是 RunArtifactStore。")
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError("run_id 不能为空。")
        self._run_store = run_store
        self.run_id = run_id

    def put(
        self,
        *,
        run_id: str,
        kind: str,
        schema_version: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactRefV2:
        """原子登记 bytes；相同 run、内容和元数据产生稳定 id."""
        if run_id != self.run_id:
            raise ValueError("put 的 run_id 与 Catalog 绑定的 run_id 不一致。")
        if not isinstance(data, bytes):
            raise TypeError("Artifact data 必须是 bytes。")

        content_sha256 = sha256(data).hexdigest()
        artifact_id = self._artifact_id(
            kind=kind,
            schema_version=schema_version,
            content_type=content_type,
            content_sha256=content_sha256,
            size_bytes=len(data),
        )
        artifact_ref = ArtifactRefV2(
            artifact_id=artifact_id,
            sha256=content_sha256,
            kind=kind,
            schema_version=schema_version,
            content_type=content_type,
            size_bytes=len(data),
        )

        with self._exclusive_lock():
            manifest = self._load_manifest()
            artifacts = manifest["artifacts"]
            existing_entry = artifacts.get(artifact_id)
            if existing_entry is not None:
                existing_ref, _ = self._parse_entry(artifact_id, existing_entry)
                if existing_ref != artifact_ref:
                    raise ArtifactCatalogError("artifact_id 与已有 manifest 条目冲突。")
                self._read_verified(existing_ref)
                return existing_ref

            relative_path = self._blob_path(artifact_id)
            self._run_store.write_bytes(
                relative_path,
                data,
                content_type=content_type,
            )
            artifacts[artifact_id] = {
                **asdict(artifact_ref),
                "relative_path": relative_path,
            }
            manifest["revision"] += 1
            self._run_store.write_json(_MANIFEST_PATH, manifest)
        return artifact_ref

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        """从 run 级 manifest 解析领域引用."""
        manifest = self._load_manifest()
        entry = manifest["artifacts"].get(artifact_id)
        if entry is None:
            raise FileNotFoundError("未找到对应 artifact_id。")
        artifact_ref, _ = self._parse_entry(artifact_id, entry)
        return artifact_ref

    def read_bytes(self, artifact_id: str) -> bytes:
        """读取 Artifact，并重新校验 size 与 SHA-256."""
        return self._read_verified(self.resolve(artifact_id))

    def list_refs(self) -> tuple[ArtifactRefV2, ...]:
        """校验 manifest 后返回按 artifact_id 排序的完整引用快照."""
        manifest = self._load_manifest()
        return tuple(
            self._parse_entry(artifact_id, manifest["artifacts"][artifact_id])[0]
            for artifact_id in sorted(manifest["artifacts"])
        )

    def total_size_bytes(self) -> int:
        """返回内容寻址去重后的 payload 总字节数."""
        return sum(ref.size_bytes for ref in self.list_refs())

    def _read_verified(self, artifact_ref: ArtifactRefV2) -> bytes:
        relative_path = self._blob_path(artifact_ref.artifact_id)
        try:
            data = self._run_store.read_bytes(relative_path)
        except FileNotFoundError as exc:
            raise ArtifactIntegrityError("Artifact bytes 缺失。") from exc
        if len(data) != artifact_ref.size_bytes:
            raise ArtifactIntegrityError("Artifact size 与 manifest 不一致。")
        if sha256(data).hexdigest() != artifact_ref.sha256:
            raise ArtifactIntegrityError("Artifact SHA-256 与 manifest 不一致。")
        return data

    def _artifact_id(
        self,
        *,
        kind: str,
        schema_version: str,
        content_type: str,
        content_sha256: str,
        size_bytes: int,
    ) -> str:
        identity = json.dumps(
            {
                "content_type": content_type,
                "kind": kind,
                "run_id": self.run_id,
                "schema_version": schema_version,
                "sha256": content_sha256,
                "size_bytes": size_bytes,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"art_{sha256(identity).hexdigest()}"

    def _load_manifest(self) -> dict[str, Any]:
        try:
            raw_manifest = self._run_store.read_bytes(_MANIFEST_PATH)
        except FileNotFoundError:
            return {
                "schema_version": _MANIFEST_SCHEMA_VERSION,
                "run_id": self.run_id,
                "revision": 0,
                "artifacts": {},
            }
        try:
            manifest = json.loads(
                raw_manifest,
                object_pairs_hook=_reject_duplicate_json_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArtifactCatalogError("Artifact manifest 不是合法 JSON。") from exc
        if not isinstance(manifest, dict):
            raise ArtifactCatalogError("Artifact manifest 必须是 JSON object。")
        unknown_fields = set(manifest) - _MANIFEST_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ArtifactCatalogError(f"Artifact manifest 包含未知字段：{names}。")
        if manifest.get("schema_version") != _MANIFEST_SCHEMA_VERSION:
            raise ArtifactCatalogError("Artifact manifest schema_version 不受支持。")
        if manifest.get("run_id") != self.run_id:
            raise ArtifactCatalogError("Artifact manifest 不属于当前 run。")
        revision = manifest.get("revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
            raise ArtifactCatalogError("Artifact manifest revision 无效。")
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            raise ArtifactCatalogError("Artifact manifest artifacts 无效。")
        for artifact_id, entry in artifacts.items():
            self._parse_entry(artifact_id, entry)
        return manifest

    def _parse_entry(
        self, artifact_id: object, entry: object
    ) -> tuple[ArtifactRefV2, str]:
        if not isinstance(artifact_id, str) or not isinstance(entry, dict):
            raise ArtifactCatalogError("Artifact manifest 条目无效。")
        unknown_fields = set(entry) - _ARTIFACT_ENTRY_FIELDS
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ArtifactCatalogError(f"Artifact manifest 条目包含未知字段：{names}。")
        try:
            artifact_ref = ArtifactRefV2(
                artifact_id=entry["artifact_id"],
                sha256=entry["sha256"],
                kind=entry["kind"],
                schema_version=entry["schema_version"],
                content_type=entry["content_type"],
                size_bytes=entry["size_bytes"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ArtifactCatalogError("Artifact manifest 引用元数据无效。") from exc
        if artifact_ref.artifact_id != artifact_id:
            raise ArtifactCatalogError("Artifact manifest key 与 artifact_id 不一致。")
        expected_artifact_id = self._artifact_id(
            kind=artifact_ref.kind,
            schema_version=artifact_ref.schema_version,
            content_type=artifact_ref.content_type,
            content_sha256=artifact_ref.sha256,
            size_bytes=artifact_ref.size_bytes,
        )
        if artifact_ref.artifact_id != expected_artifact_id:
            raise ArtifactCatalogError(
                "Artifact manifest 元数据与 artifact_id 不一致。"
            )
        relative_path = entry.get("relative_path")
        if relative_path != self._blob_path(artifact_id):
            raise ArtifactCatalogError("Artifact manifest 物理映射无效。")
        return artifact_ref, relative_path

    @staticmethod
    def _blob_path(artifact_id: str) -> str:
        return f"{_BLOB_DIRECTORY}/{artifact_id}.blob"

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        lock_path: Path = self._run_store.path_for(_LOCK_PATH)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """构造 JSON object，并拒绝任意层级的重复 key."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactCatalogError(f"Artifact manifest 包含重复 key：{key}。")
        value[key] = item
    return value
