"""D095 engine rollout 的 attempt 读取与父公开 Artifact 原子发布服务."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast

from shaderforge.store import ArtifactRef, LocalArtifactStore

PARENT_MANIFEST_SCHEMA_VERSION = "png_to_shader_manifest_v2"
EngineId = Literal["shader_graph_v1", "direct_glsl_layerplan_v1"]
Representation = Literal["shader_document_v1", "shader_program_spec_v1"]
_REPRESENTATION_BY_ENGINE: dict[str, str] = {
    "shader_graph_v1": "shader_document_v1",
    "direct_glsl_layerplan_v1": "shader_program_spec_v1",
}


class EngineRolloutArtifactError(ValueError):
    """attempt 或父公开 Artifact 不满足 D095 契约."""


def _json_object(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise EngineRolloutArtifactError(f"{label} 必须是合法 JSON。") from exc
    if not isinstance(value, dict):
        raise EngineRolloutArtifactError(f"{label} 必须是 JSON object。")
    return cast(dict[str, Any], value)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SelectedEngineArtifacts:
    """选中 child attempt 可提升到父 run 的三个内存 Artifact."""

    final_render: bytes
    metrics_json: bytes
    engine_manifest_json: bytes

    def __post_init__(self) -> None:
        """在进入父协调器前冻结基础格式，拒绝非 PNG/JSON."""
        if not self.final_render.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EngineRolloutArtifactError("final_render 必须是 PNG。")
        _json_object(self.metrics_json, label="metrics")
        _json_object(self.engine_manifest_json, label="engine manifest")


@dataclass(frozen=True, slots=True)
class PublishedParentArtifacts:
    """父 run 三个公开 Artifact 的内容寻址引用."""

    final_render: ArtifactRef
    metrics: ArtifactRef
    manifest: ArtifactRef


class EngineRolloutArtifactService:
    """隔离读取 child store，并只向 public store 原子发布选中父结果."""

    def __init__(
        self,
        *,
        public_store: LocalArtifactStore,
        private_attempt_store: LocalArtifactStore,
    ) -> None:
        """绑定相互独立的 public parent 与 private attempt 根."""
        if public_store.base_root == private_attempt_store.base_root:
            raise EngineRolloutArtifactError(
                "public parent 与 private attempt Artifact 根必须隔离。"
            )
        if not private_attempt_store.restrictive_permissions:
            raise EngineRolloutArtifactError(
                "private attempt store 必须启用 restrictive permissions。"
            )
        self.public_store = public_store
        self.private_attempt_store = private_attempt_store

    def read_private_attempt(self, attempt_id: str) -> SelectedEngineArtifacts:
        """从独立 private store 读取 child 的三个候选发布文件."""
        try:
            run = self.private_attempt_store.resolve_run(attempt_id)
            return SelectedEngineArtifacts(
                final_render=run.read_bytes("final/render.png"),
                metrics_json=run.read_bytes("final/metrics.json"),
                engine_manifest_json=run.read_bytes("final/manifest.json"),
            )
        except (FileNotFoundError, ValueError) as exc:
            raise EngineRolloutArtifactError(
                "选中 child attempt Artifact 不完整。"
            ) from exc

    def publish_parent(
        self,
        *,
        project_id: str,
        parent_run_id: str,
        engine: EngineId,
        representation: Representation,
        engine_run: dict[str, Any],
        selected: SelectedEngineArtifacts,
    ) -> PublishedParentArtifacts:
        """构造 v2 discriminator manifest 并原子发布三个父白名单文件."""
        if _REPRESENTATION_BY_ENGINE.get(engine) != representation:
            raise EngineRolloutArtifactError("engine/representation 配对非法。")
        selected_attempt_id = engine_run.get("selected_attempt_id")
        if not isinstance(selected_attempt_id, str) or not selected_attempt_id:
            raise EngineRolloutArtifactError(
                "engine_run 必须绑定 selected_attempt_id。"
            )
        if engine_run.get("selected_engine") != engine:
            raise EngineRolloutArtifactError("engine_run selected_engine 漂移。")
        if engine_run.get("selected_representation") != representation:
            raise EngineRolloutArtifactError(
                "engine_run selected_representation 漂移。"
            )
        engine_manifest = _json_object(
            selected.engine_manifest_json,
            label="engine manifest",
        )
        _json_object(selected.metrics_json, label="metrics")
        manifest = {
            "schema_version": PARENT_MANIFEST_SCHEMA_VERSION,
            "project_id": project_id,
            "run_id": parent_run_id,
            "engine": engine,
            "representation": representation,
            "engine_run": engine_run,
            "engine_manifest": engine_manifest,
            "public_artifacts": {
                "final-render": {
                    "sha256": sha256(selected.final_render).hexdigest(),
                    "size_bytes": len(selected.final_render),
                },
                "metrics": {
                    "sha256": sha256(selected.metrics_json).hexdigest(),
                    "size_bytes": len(selected.metrics_json),
                },
            },
        }
        files = {
            "render.png": selected.final_render,
            "metrics.json": selected.metrics_json,
            "manifest.json": _json_bytes(manifest),
        }
        try:
            refs = self.public_store.publish_public_final_bundle(
                project_id,
                parent_run_id,
                files,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise EngineRolloutArtifactError(
                "父 run 公开 Artifact 原子发布失败。"
            ) from exc
        return PublishedParentArtifacts(
            final_render=refs["render.png"],
            metrics=refs["metrics.json"],
            manifest=refs["manifest.json"],
        )

    def verify_parent(self, parent_run_id: str) -> dict[str, Any]:
        """复验已公开父 run 的文件集合、manifest discriminator 与内容哈希."""
        try:
            files = self.public_store.verify_public_final_bundle(parent_run_id)
            final_render = files["render.png"]
            metrics_json = files["metrics.json"]
            manifest_json = files["manifest.json"]
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise EngineRolloutArtifactError("父 run 公开 Artifact 不完整。") from exc
        if not final_render.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EngineRolloutArtifactError("父 run final-render 不是 PNG。")
        _json_object(metrics_json, label="parent metrics")
        manifest = _json_object(manifest_json, label="parent manifest")
        if manifest.get("schema_version") != PARENT_MANIFEST_SCHEMA_VERSION:
            raise EngineRolloutArtifactError("父 manifest schema_version 非法。")
        if manifest.get("run_id") != parent_run_id:
            raise EngineRolloutArtifactError("父 manifest run_id 漂移。")
        engine = manifest.get("engine")
        representation = manifest.get("representation")
        if (
            not isinstance(engine, str)
            or _REPRESENTATION_BY_ENGINE.get(engine) != representation
        ):
            raise EngineRolloutArtifactError("父 manifest engine discriminator 漂移。")
        engine_run = manifest.get("engine_run")
        if (
            not isinstance(engine_run, dict)
            or engine_run.get("selected_engine") != engine
            or engine_run.get("selected_representation") != representation
        ):
            raise EngineRolloutArtifactError("父 manifest engine_run 漂移。")
        selected_attempt_id = engine_run.get("selected_attempt_id")
        attempt_refs = engine_run.get("attempt_refs")
        if (
            not isinstance(selected_attempt_id, str)
            or not isinstance(attempt_refs, list)
            or not any(
                isinstance(item, dict)
                and item.get("attempt_id") == selected_attempt_id
                and item.get("engine") == engine
                and item.get("status") == "succeeded"
                for item in attempt_refs
            )
        ):
            raise EngineRolloutArtifactError("父 manifest selected attempt 漂移。")
        public_artifacts = manifest.get("public_artifacts")
        if not isinstance(public_artifacts, dict):
            raise EngineRolloutArtifactError("父 manifest 缺少 public_artifacts。")
        expected = {
            "final-render": final_render,
            "metrics": metrics_json,
        }
        for name, data in expected.items():
            item = public_artifacts.get(name)
            if not isinstance(item, dict) or item != {
                "sha256": sha256(data).hexdigest(),
                "size_bytes": len(data),
            }:
                raise EngineRolloutArtifactError(f"父 manifest {name} hash 漂移。")
        return manifest


def create_engine_rollout_artifact_service(
    *,
    public_service: Any,
    private_attempt_root: Path,
) -> EngineRolloutArtifactService:
    """从 Agent 公共 service 构造隔离的 parent/attempt Artifact 边界."""
    public_store = getattr(public_service, "artifacts", None)
    if not isinstance(public_store, LocalArtifactStore):
        raise EngineRolloutArtifactError("public service 未暴露可信 Artifact store。")
    return EngineRolloutArtifactService(
        public_store=public_store,
        private_attempt_store=LocalArtifactStore(
            private_attempt_root,
            restrictive_permissions=True,
        ),
    )


__all__ = [
    "EngineId",
    "EngineRolloutArtifactError",
    "EngineRolloutArtifactService",
    "PARENT_MANIFEST_SCHEMA_VERSION",
    "PublishedParentArtifacts",
    "Representation",
    "SelectedEngineArtifacts",
    "create_engine_rollout_artifact_service",
]
