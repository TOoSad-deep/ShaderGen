"""V2 development Service 的原子 bootstrap/result journal."""
# ruff: noqa: D102, D107, D415

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Iterator, Literal, NoReturn
from uuid import uuid4

from pydantic import Field, model_validator

from agent.app.states.png_to_shader_v2_state import BudgetVectorV2
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.store import ArtifactRefV2

_ENVELOPE_FIELDS = frozenset({"sha256", "payload"})


class DurableArtifactPutSlotV1(FrozenModel):
    """State 建立后单个内容寻址 Artifact 的 reserve/put/commit 意图。"""

    schema_version: Literal["png_to_shader_v2_durable_artifact_put_slot_v1"] = (
        "png_to_shader_v2_durable_artifact_put_slot_v1"
    )
    slot: Literal["real_resume_context", "real_final_constraint"]
    phase: Literal["prepared", "reserved", "put", "committed"]
    kind: NonEmptyString
    artifact_schema_version: NonEmptyString
    content_type: NonEmptyString
    payload_sha256: Sha256Hex
    payload_size_bytes: int = Field(ge=0)
    pre_artifact_used: int = Field(ge=0)
    pre_catalog_bytes: int = Field(ge=0)
    reservation_budget_revision: int | None = Field(default=None, ge=0)
    actual_artifact_bytes: int | None = Field(default=None, ge=0)
    artifact_ref: ArtifactRefV2 | None = None

    @model_validator(mode="after")
    def _validate_phase(self) -> DurableArtifactPutSlotV1:
        if self.phase == "prepared":
            if any(
                value is not None
                for value in (
                    self.reservation_budget_revision,
                    self.actual_artifact_bytes,
                    self.artifact_ref,
                )
            ):
                raise ValueError("prepared put slot 不得提前绑定 reserve/put 结果。")
        elif self.phase == "reserved":
            if self.reservation_budget_revision is None:
                raise ValueError("reserved put slot 必须绑定 budget revision。")
            if self.actual_artifact_bytes is not None or self.artifact_ref is not None:
                raise ValueError("reserved put slot 不得提前绑定 put 结果。")
        else:
            if any(
                value is None
                for value in (
                    self.reservation_budget_revision,
                    self.actual_artifact_bytes,
                    self.artifact_ref,
                )
            ):
                raise ValueError("put/committed slot 必须绑定完整结算信息。")
            assert self.artifact_ref is not None
            if (
                self.artifact_ref.kind != self.kind
                or self.artifact_ref.schema_version != self.artifact_schema_version
                or self.artifact_ref.content_type != self.content_type
                or self.artifact_ref.sha256 != self.payload_sha256
                or self.artifact_ref.size_bytes != self.payload_size_bytes
            ):
                raise ValueError("put slot ArtifactRef 与冻结 payload identity 不一致。")
            assert self.actual_artifact_bytes is not None
            if self.actual_artifact_bytes > self.payload_size_bytes:
                raise ValueError("put slot 实际去重 delta 不得超过 payload bytes。")
        return self


class ServiceRunJournalV2(FrozenModel):
    """独立于 Graph State 的 bootstrap checkpoint 与 terminal result index。"""

    schema_version: Literal["png_to_shader_v2_service_run_journal_v2"] = (
        "png_to_shader_v2_service_run_journal_v2"
    )
    project_id: NonEmptyString
    run_id: NonEmptyString
    revision: int = Field(ge=0)
    phase: Literal[
        "bootstrap",
        "source_put",
        "config_put",
        "metadata_put",
        "measurements_put",
        "intent_context_put",
        "preliminary_constraint_put",
        "state_initialized",
        "model_committed",
        "resume_context_put",
        "final_constraint_put",
        "real_closure_committed",
        "graph_finalized",
        "manifest_put",
        "terminal",
        "terminal_failure",
    ]
    policy_hash: Sha256Hex
    source_sha256: Sha256Hex
    config_json: NonEmptyString
    request_metadata_json: NonEmptyString
    catalog_artifact_bytes: int = Field(ge=0)
    source_ref: ArtifactRefV2 | None = None
    config_ref: ArtifactRefV2 | None = None
    request_metadata_ref: ArtifactRefV2 | None = None
    measurement_bundle_ref: ArtifactRefV2 | None = None
    intent_context_ref: ArtifactRefV2 | None = None
    preliminary_constraint_ref: ArtifactRefV2 | None = None
    model_audit_ref: ArtifactRefV2 | None = None
    model_interpretation_ref: ArtifactRefV2 | None = None
    resume_context_ref: ArtifactRefV2 | None = None
    final_constraint_ref: ArtifactRefV2 | None = None
    active_artifact_put: DurableArtifactPutSlotV1 | None = None
    terminal_manifest_ref: ArtifactRefV2 | None = None
    terminal_pre_budget_revision: int | None = Field(default=None, ge=0)
    terminal_pre_artifact_bytes: int | None = Field(default=None, ge=0)
    terminal_budget_snapshot: BudgetVectorV2 | None = None
    terminal_failure_status: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_terminal(self) -> ServiceRunJournalV2:
        for field_name, payload_json in (
            ("config_json", self.config_json),
            ("request_metadata_json", self.request_metadata_json),
        ):
            try:
                decoded = json.loads(
                    payload_json,
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_non_finite_constant,
                )
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Service journal {field_name} 不是严格 JSON。"
                ) from exc
            if not isinstance(decoded, dict):
                raise ValueError(f"Service journal {field_name} 必须是 JSON object。")
        expected_refs = (
            (
                self.source_ref,
                "png_to_shader_v2_source_input",
                "png_to_shader_v2_source_input_v1",
                "application/octet-stream",
            ),
            (
                self.config_ref,
                "png_to_shader_v2_service_config",
                "png_to_shader_v2_service_config_v1",
                "application/json",
            ),
            (
                self.request_metadata_ref,
                "png_to_shader_v2_request_metadata",
                "png_to_shader_v2_request_metadata_v1",
                "application/json",
            ),
            (
                self.measurement_bundle_ref,
                "target_measurements_bundle",
                "target_measurements_v2_artifact_bundle_v2",
                "application/json",
            ),
            (
                self.intent_context_ref,
                "intent_build_context",
                "intent_build_context_v1",
                "application/json",
            ),
            (
                self.preliminary_constraint_ref,
                "request_constraint_set",
                "request_constraint_set_v1",
                "application/json",
            ),
            (
                self.model_audit_ref,
                "visual_interpretation_call_audit",
                "visual_interpretation_call_audit_v2",
                "application/json",
            ),
            (
                self.model_interpretation_ref,
                "visual_interpretation",
                "visual_interpretation_v2_1",
                "application/json",
            ),
            (
                self.resume_context_ref,
                "png_to_shader_v2_resume_context",
                "png_to_shader_v2_resume_context_v1",
                "application/json",
            ),
            (
                self.final_constraint_ref,
                "request_constraint_set",
                "request_constraint_set_v1",
                "application/json",
            ),
        )
        for ref, kind, schema_version, content_type in expected_refs:
            if ref is not None and (
                ref.kind != kind
                or ref.schema_version != schema_version
                or ref.content_type != content_type
            ):
                raise ValueError("Service journal ArtifactRef 元数据错绑。")
        if self.source_ref is not None and self.source_ref.sha256 != self.source_sha256:
            raise ValueError("Service journal source ref 与 source_sha256 错绑。")
        fields = (
            self.terminal_manifest_ref,
            self.terminal_pre_budget_revision,
            self.terminal_pre_artifact_bytes,
        )
        if self.phase in {"manifest_put", "terminal"} and any(
            item is None for item in fields
        ):
            raise ValueError("manifest/terminal journal 必须完整绑定 manifest 结算起点。")
        if self.phase in {"terminal", "terminal_failure"} and (
            self.terminal_budget_snapshot is None
        ):
            raise ValueError("terminal journal 必须冻结七维 budget snapshot。")
        if (self.phase == "terminal_failure") != (
            self.terminal_failure_status is not None
        ):
            raise ValueError("terminal failure phase/status 必须同时存在。")
        if self.phase not in {"manifest_put", "terminal"} and any(
            item is not None for item in fields
        ):
            raise ValueError("非 manifest phase 不得提前记录 terminal 字段。")
        if self.active_artifact_put is not None and self.active_artifact_put.phase == "committed":
            raise ValueError("已 committed put slot 必须折叠进 journal 固定 ref。")
        if self.phase in {
            "model_committed",
            "resume_context_put",
            "final_constraint_put",
            "real_closure_committed",
            "graph_finalized",
            "manifest_put",
            "terminal",
        } and self.model_interpretation_ref is None:
            # fixture/no-model 不走 model_committed/real_closure_committed 两个 phase；
            # graph/terminal 则允许 fixture 路径没有 model 专属 journal ref。
            if self.phase in {
                "model_committed",
                "resume_context_put",
                "final_constraint_put",
                "real_closure_committed",
            }:
                raise ValueError("real closure phase 必须绑定模型 Interpretation ref。")
        return self


class ServiceRunJournalError(RuntimeError):
    """journal identity、revision 或完整性失败。"""


class ServiceRunJournalNotFound(ServiceRunJournalError):
    """run 尚无 Service journal。"""


class LocalServiceRunJournalStore:
    """以 flock、fsync、原子替换保存单机 Service journal。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def initialize(self, value: ServiceRunJournalV2) -> ServiceRunJournalV2:
        path, lock = self._paths(value.run_id)
        with self._lock(lock, exclusive=True):
            if path.exists():
                raise ServiceRunJournalError("Service journal 已存在。")
            return self._persist(path, value)

    def load(self, run_id: str) -> ServiceRunJournalV2:
        path, lock = self._paths(run_id)
        with self._lock(lock, exclusive=False):
            return self._read(path, run_id)

    def replace(
        self,
        run_id: str,
        *,
        expected_revision: int,
        value: ServiceRunJournalV2,
    ) -> ServiceRunJournalV2:
        path, lock = self._paths(run_id)
        with self._lock(lock, exclusive=True):
            current = self._read(path, run_id)
            if current.revision != expected_revision:
                raise ServiceRunJournalError("Service journal revision 冲突。")
            if value.run_id != run_id or value.revision != current.revision + 1:
                raise ServiceRunJournalError("Service journal replacement 身份无效。")
            return self._persist(path, value)

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        if not run_id or ":" in run_id:
            raise ServiceRunJournalError("Service journal run_id 无效。")
        identity = sha256(run_id.encode()).hexdigest()
        return self._root / f"{identity}.json", self._root / f"{identity}.lock"

    @contextmanager
    def _lock(self, path: Path, *, exclusive: bool) -> Iterator[None]:
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _persist(self, path: Path, value: ServiceRunJournalV2) -> ServiceRunJournalV2:
        payload = value.model_dump_json().encode()
        envelope = json.dumps(
            {"sha256": sha256(payload).hexdigest(), "payload": payload.decode()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)
        return self._read(path, value.run_id)

    @staticmethod
    def _read(path: Path, run_id: str) -> ServiceRunJournalV2:
        try:
            envelope = json.loads(
                path.read_bytes(),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"非法 JSON 常量：{value}")
                ),
            )
            if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
                raise ValueError("envelope fields")
            raw = envelope["payload"]
            digest = envelope["sha256"]
            if not isinstance(raw, str) or not isinstance(digest, str):
                raise ValueError("envelope types")
            payload = raw.encode()
            if sha256(payload).hexdigest() != digest:
                raise ValueError("SHA mismatch")
            json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
            value = ServiceRunJournalV2.model_validate_json(payload, strict=True)
        except FileNotFoundError as exc:
            raise ServiceRunJournalNotFound("Service journal 不存在。") from exc
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ServiceRunJournalError("Service journal 完整性校验失败。") from exc
        if value.run_id != run_id:
            raise ServiceRunJournalError("Service journal run_id 不一致。")
        return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Service journal 包含重复 JSON key：{key}。")
        value[key] = item
    return value


def _reject_non_finite_constant(value: str) -> NoReturn:
    raise ValueError(f"Service journal 包含非法 JSON 常量：{value}。")


__all__ = [
    "DurableArtifactPutSlotV1",
    "LocalServiceRunJournalStore",
    "ServiceRunJournalError",
    "ServiceRunJournalNotFound",
    "ServiceRunJournalV2",
]
