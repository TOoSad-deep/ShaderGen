"""Node Lab 的不可变步骤快照与不透明 Artifact 存储."""

from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from nodelab.models import (
    ArtifactDescriptor,
    LabRunRecord,
    NodeLabError,
    StepExecutionRequest,
    StepExecutionResponse,
    ensure_json_object,
)
from shaderforge.store import RunArtifactStore

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_STEPS_INDEX_SCHEMA = "node_lab_steps_index_v1"
_ARTIFACTS_INDEX_SCHEMA = "node_lab_artifacts_index_v1"
MAX_LAB_ARTIFACT_BYTES = 8 * 1024 * 1024


def _safe_identifier(value: str, field_name: str) -> str:
    """限制内部目录标识符，防止路径穿越."""
    if not _IDENTIFIER_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise NodeLabError(
            "input_contract_invalid",
            f"{field_name} 包含非法字符。",
            stage="store_boundary",
            details={"field": field_name},
        )
    return value


def _load_json_object(
    run_store: RunArtifactStore, relative_path: str
) -> dict[str, Any]:
    """从隔离 run 目录读取 JSON object."""
    try:
        value = json.loads(run_store.read_bytes(relative_path))
    except FileNotFoundError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NodeLabError(
            "artifact_integrity_failed",
            "Node Lab 元数据 JSON 已损坏。",
            stage="store_read",
        ) from exc
    return ensure_json_object(value)


class NodeLabStore:
    """按 LabRun 隔离、只通过不透明 id 访问的本地 Store."""

    def __init__(self, base_root: str | Path) -> None:
        """绑定独立于产品 run 的 Node Lab 根目录."""
        root = Path(base_root)
        root.mkdir(parents=True, exist_ok=True)
        self.base_root = root.resolve()

    def _candidate_root(self, lab_run_id: str) -> Path:
        """解析 LabRun 根并拒绝 symlink 逃逸."""
        run_id = _safe_identifier(lab_run_id, "lab_run_id")
        candidate = self.base_root / run_id
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.base_root) or resolved == self.base_root:
            raise NodeLabError(
                "artifact_integrity_failed",
                "LabRun 目录越过 Node Lab 根目录。",
                stage="store_boundary",
                lab_run_id=run_id,
            )
        return candidate

    def _existing_run_store(self, lab_run_id: str) -> RunArtifactStore:
        """打开已提交 LabRun；目录存在但无 run.json 仍视为无效."""
        candidate = self._candidate_root(lab_run_id)
        if not candidate.is_dir() or not (candidate / "run.json").is_file():
            raise NodeLabError(
                "lab_run_not_found",
                "未找到 Node Lab 运行。",
                stage="store_read",
                lab_run_id=lab_run_id,
            )
        return RunArtifactStore(candidate)

    def create_run(
        self,
        record: LabRunRecord,
        initial_state: dict[str, Any],
    ) -> LabRunRecord:
        """创建 LabRun，并冻结 root State 与空索引."""
        candidate = self._candidate_root(record.lab_run_id)
        if candidate.exists():
            raise NodeLabError(
                "lab_run_conflict",
                "lab_run_id 已存在，不能覆盖历史实验。",
                stage="store_write",
                lab_run_id=record.lab_run_id,
            )
        run_store = RunArtifactStore(candidate)
        safe_state = ensure_json_object(initial_state, path="$.initial_state")
        run_store.write_json("root-state.json", safe_state)
        run_store.write_json(
            "indexes/steps.json",
            {"schema_version": _STEPS_INDEX_SCHEMA, "step_ids": []},
        )
        run_store.write_json(
            "indexes/artifacts.json",
            {"schema_version": _ARTIFACTS_INDEX_SCHEMA, "artifacts": {}},
        )
        run_store.write_json("run.json", record.to_dict())
        return record

    def load_run(self, lab_run_id: str) -> LabRunRecord:
        """读取并验证 LabRun 元数据."""
        value = _load_json_object(self._existing_run_store(lab_run_id), "run.json")
        try:
            return LabRunRecord.model_validate(value)
        except ValueError as exc:
            raise NodeLabError(
                "artifact_integrity_failed",
                "Node Lab run.json 不符合当前契约。",
                stage="store_read",
                lab_run_id=lab_run_id,
            ) from exc

    def load_root_state(self, lab_run_id: str) -> dict[str, Any]:
        """读取创建时冻结的 root State."""
        run_store = self._existing_run_store(lab_run_id)
        return _load_json_object(run_store, "root-state.json")

    def _step_ids(self, lab_run_id: str) -> list[str]:
        """读取已原子提交的步骤 id 顺序."""
        run_store = self._existing_run_store(lab_run_id)
        index = _load_json_object(run_store, "indexes/steps.json")
        values = index.get("step_ids")
        if index.get("schema_version") != _STEPS_INDEX_SCHEMA or not isinstance(
            values, list
        ):
            raise NodeLabError(
                "artifact_integrity_failed",
                "Node Lab 步骤索引已损坏。",
                stage="store_read",
                lab_run_id=lab_run_id,
            )
        if not all(isinstance(value, str) for value in values):
            raise NodeLabError(
                "artifact_integrity_failed",
                "Node Lab 步骤索引包含非法 id。",
                stage="store_read",
                lab_run_id=lab_run_id,
            )
        return list(values)

    def list_step_ids(self, lab_run_id: str) -> tuple[str, ...]:
        """返回所有已提交步骤，不暴露磁盘目录扫描结果."""
        return tuple(self._step_ids(lab_run_id))

    def commit_step(
        self,
        *,
        request: StepExecutionRequest,
        response: StepExecutionResponse,
        state_before: dict[str, Any],
        state_after: dict[str, Any],
    ) -> None:
        """写完步骤证据后最后提交索引，使父快照保持不可变."""
        if request.lab_run_id != response.lab_run_id:
            raise ValueError("步骤 request/response 的 lab_run_id 不一致。")
        run_store = self._existing_run_store(request.lab_run_id)
        step_ids = self._step_ids(request.lab_run_id)
        if response.step_id in step_ids:
            raise NodeLabError(
                "step_conflict",
                "step_id 已存在，不能覆盖不可变步骤。",
                stage="store_write",
                lab_run_id=request.lab_run_id,
                step_id=response.step_id,
                node_id=request.node_id,
            )
        prefix = f"steps/{_safe_identifier(response.step_id, 'step_id')}"
        if run_store.path_for(prefix).exists():
            raise NodeLabError(
                "step_conflict",
                "步骤目录已存在但尚未登记，拒绝覆盖证据。",
                stage="store_write",
                lab_run_id=request.lab_run_id,
                step_id=response.step_id,
                node_id=request.node_id,
            )
        run_store.write_json(f"{prefix}/request.json", request.to_dict())
        run_store.write_json(
            f"{prefix}/state-before.json",
            ensure_json_object(state_before),
        )
        run_store.write_json(
            f"{prefix}/state-after.json",
            ensure_json_object(state_after),
        )
        run_store.write_json(f"{prefix}/response.json", response.to_dict())
        run_store.write_json(
            "indexes/steps.json",
            {
                "schema_version": _STEPS_INDEX_SCHEMA,
                "step_ids": [*step_ids, response.step_id],
            },
        )

    def load_step_response(
        self,
        lab_run_id: str,
        step_id: str,
    ) -> StepExecutionResponse:
        """只读取索引中已提交的步骤响应."""
        if step_id not in self._step_ids(lab_run_id):
            raise NodeLabError(
                "step_not_found",
                "未找到同一 LabRun 内已提交的步骤。",
                stage="store_read",
                lab_run_id=lab_run_id,
                step_id=step_id,
            )
        run_store = self._existing_run_store(lab_run_id)
        value = _load_json_object(run_store, f"steps/{step_id}/response.json")
        try:
            return StepExecutionResponse.model_validate(value)
        except ValueError as exc:
            raise NodeLabError(
                "artifact_integrity_failed",
                "步骤响应不符合当前契约。",
                stage="store_read",
                lab_run_id=lab_run_id,
                step_id=step_id,
            ) from exc

    def load_state_after(self, lab_run_id: str, step_id: str) -> dict[str, Any]:
        """读取已提交步骤的 State 快照，供分支作为 base 使用."""
        self.load_step_response(lab_run_id, step_id)
        run_store = self._existing_run_store(lab_run_id)
        return _load_json_object(run_store, f"steps/{step_id}/state-after.json")

    def _artifact_index(self, lab_run_id: str) -> dict[str, Any]:
        """读取 Artifact descriptor 映射."""
        run_store = self._existing_run_store(lab_run_id)
        index = _load_json_object(run_store, "indexes/artifacts.json")
        artifacts = index.get("artifacts")
        if index.get("schema_version") != _ARTIFACTS_INDEX_SCHEMA or not isinstance(
            artifacts, dict
        ):
            raise NodeLabError(
                "artifact_integrity_failed",
                "Node Lab Artifact 索引已损坏。",
                stage="store_read",
                lab_run_id=lab_run_id,
            )
        return index

    def put_artifact(
        self,
        *,
        descriptor: ArtifactDescriptor,
        data: bytes,
    ) -> ArtifactDescriptor:
        """写入 bytes，最后以不透明 id 原子提交 descriptor."""
        if not isinstance(data, bytes):
            raise TypeError("Node Lab Artifact data 必须是 bytes。")
        if len(data) > MAX_LAB_ARTIFACT_BYTES:
            raise NodeLabError(
                "artifact_too_large",
                "Node Lab Artifact 超过 8MB 上限。",
                stage="artifact_write",
                lab_run_id=descriptor.lab_run_id,
                details={
                    "size_bytes": len(data),
                    "max_size_bytes": MAX_LAB_ARTIFACT_BYTES,
                },
            )
        if descriptor.sha256 != sha256(
            data
        ).hexdigest() or descriptor.size_bytes != len(data):
            raise NodeLabError(
                "artifact_integrity_failed",
                "Artifact descriptor 与 payload 不一致。",
                stage="artifact_write",
                lab_run_id=descriptor.lab_run_id,
            )
        run_store = self._existing_run_store(descriptor.lab_run_id)
        index = self._artifact_index(descriptor.lab_run_id)
        artifacts = dict(index["artifacts"])
        if descriptor.artifact_id in artifacts:
            raise NodeLabError(
                "artifact_conflict",
                "artifact_id 已存在，不能覆盖历史 Artifact。",
                stage="artifact_write",
                lab_run_id=descriptor.lab_run_id,
            )
        artifact_id = _safe_identifier(descriptor.artifact_id, "artifact_id")
        run_store.write_bytes(
            f"uploads/{artifact_id}/payload",
            data,
            content_type=descriptor.content_type,
        )
        artifacts[artifact_id] = descriptor.to_dict()
        run_store.write_json(
            "indexes/artifacts.json",
            {"schema_version": _ARTIFACTS_INDEX_SCHEMA, "artifacts": artifacts},
        )
        return descriptor

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        """按同一 LabRun 的不透明 id 读取并复核内容 hash."""
        artifact_id = _safe_identifier(artifact_id, "artifact_id")
        index = self._artifact_index(lab_run_id)
        try:
            raw_descriptor = index["artifacts"][artifact_id]
            descriptor = ArtifactDescriptor.model_validate(raw_descriptor)
        except (KeyError, ValueError) as exc:
            raise NodeLabError(
                "artifact_not_found",
                "未找到同一 LabRun 内的 Artifact。",
                stage="artifact_read",
                lab_run_id=lab_run_id,
                details={"artifact_id": artifact_id},
            ) from exc
        if descriptor.lab_run_id != lab_run_id:
            raise NodeLabError(
                "artifact_integrity_failed",
                "Artifact descriptor 的 LabRun 绑定不一致。",
                stage="artifact_read",
                lab_run_id=lab_run_id,
            )
        run_store = self._existing_run_store(lab_run_id)
        try:
            data = run_store.read_bytes(f"uploads/{artifact_id}/payload")
        except FileNotFoundError as exc:
            raise NodeLabError(
                "artifact_integrity_failed",
                "Artifact payload 缺失。",
                stage="artifact_read",
                lab_run_id=lab_run_id,
            ) from exc
        if descriptor.sha256 != sha256(
            data
        ).hexdigest() or descriptor.size_bytes != len(data):
            raise NodeLabError(
                "artifact_integrity_failed",
                "Artifact payload 的 SHA-256 或字节数不匹配。",
                stage="artifact_read",
                lab_run_id=lab_run_id,
            )
        return descriptor, data

    def list_artifacts(self, lab_run_id: str) -> tuple[ArtifactDescriptor, ...]:
        """按提交顺序列出同一 LabRun 的 Artifact descriptor，不读取 payload."""
        index = self._artifact_index(lab_run_id)
        result: list[ArtifactDescriptor] = []
        try:
            for value in index["artifacts"].values():
                descriptor = ArtifactDescriptor.model_validate(value)
                if descriptor.lab_run_id != lab_run_id:
                    raise ValueError("Artifact descriptor 的 LabRun 绑定不一致。")
                result.append(descriptor)
        except (AttributeError, TypeError, ValueError) as exc:
            raise NodeLabError(
                "artifact_integrity_failed",
                "Node Lab Artifact 索引包含非法 descriptor。",
                stage="artifact_read",
                lab_run_id=lab_run_id,
            ) from exc
        return tuple(result)
