"""PngToShader V2 State 的单机文件持久化与双 revision CAS。."""

from __future__ import annotations

import fcntl
import json
import os
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    PngToShaderV2State,
    build_checkpoint_namespace_v2,
    commit_budget_v2,
    evolve_state_v2,
    reserve_budget_v2,
    restore_state_v2,
    serialize_state_v2,
)

_ENVELOPE_SCHEMA_VERSION = "local_state_checkpoint_v4"
_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "checkpoint_sha256", "checkpoint_json"}
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class V2StateStoreError(RuntimeError):
    """V2 本地 State Store 失败。."""


class V2StateCheckpointNotFoundError(V2StateStoreError):
    """指定 run 没有已确认 checkpoint。."""


class V2StateCheckpointExistsError(V2StateStoreError):
    """指定 run 已有 checkpoint，禁止覆盖初始化。."""


class V2StateCheckpointIntegrityError(V2StateStoreError):
    """checkpoint JSON、摘要或 State 契约不可信。."""


class V2StateRevisionConflictError(V2StateStoreError):
    """run 或 budget revision 已陈旧。."""


class LocalPngToShaderV2StateStore:
    """使用 flock、fsync 和原子替换实现单机 checkpoint CAS。.

    文件锁只覆盖同一文件系统上的本地进程，不提供数据库事务、跨机器或
    分布式 compare-and-swap。
    """

    def __init__(self, root: Path) -> None:
        """创建或打开一个本地 State Store 根目录。."""
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise V2StateStoreError("V2 State Store root 必须是目录。")

    def initialize(self, state: PngToShaderV2State) -> PngToShaderV2State:
        """仅在 run 尚不存在时写入 revision 起点并确认回读。."""
        checkpoint_path, lock_path = self._paths(state.run_id)
        with self._lock(lock_path, exclusive=True):
            if checkpoint_path.exists():
                raise V2StateCheckpointExistsError(
                    f"run {state.run_id} 已存在 V2 checkpoint。"
                )
            return self._persist_and_confirm(checkpoint_path, state)

    def load_last_confirmed(self, run_id: str) -> PngToShaderV2State:
        """恢复原子替换后最后确认的 checkpoint；孤立临时文件不参与恢复。."""
        checkpoint_path, lock_path = self._paths(run_id)
        with self._lock(lock_path, exclusive=False):
            return self._read_checkpoint(checkpoint_path, expected_run_id=run_id)

    def compare_and_swap_run(
        self,
        run_id: str,
        *,
        expected_run_revision: int,
        changes: Mapping[str, Any],
    ) -> PngToShaderV2State:
        """在锁内校验并推进 run revision，保持 budget revision 不变。."""
        checkpoint_path, lock_path = self._paths(run_id)
        with self._lock(lock_path, exclusive=True):
            current = self._read_checkpoint(
                checkpoint_path,
                expected_run_id=run_id,
            )
            try:
                updated = evolve_state_v2(
                    current,
                    expected_run_revision=expected_run_revision,
                    **dict(changes),
                )
            except RuntimeError as exc:
                raise V2StateRevisionConflictError(
                    "V2 State run_revision CAS 冲突。"
                ) from exc
            if updated.budget_state != current.budget_state:
                raise V2StateCheckpointIntegrityError(
                    "run CAS 不得修改 BudgetStateV2。"
                )
            return self._persist_and_confirm(checkpoint_path, updated)

    def reserve_budget(
        self,
        run_id: str,
        delta: BudgetVectorV2,
        *,
        expected_budget_revision: int,
    ) -> PngToShaderV2State:
        """在锁内持久化 reservation，保持 run revision 不变。."""
        checkpoint_path, lock_path = self._paths(run_id)
        with self._lock(lock_path, exclusive=True):
            current = self._read_checkpoint(
                checkpoint_path,
                expected_run_id=run_id,
            )
            try:
                budget_state = reserve_budget_v2(
                    current.budget_state,
                    delta,
                    expected_revision=expected_budget_revision,
                )
            except RuntimeError as exc:
                raise V2StateRevisionConflictError(
                    "V2 State budget revision CAS 冲突。"
                ) from exc
            updated = _replace_budget_state(current, budget_state)
            return self._persist_and_confirm(checkpoint_path, updated)

    def commit_budget(
        self,
        run_id: str,
        *,
        reservation: BudgetVectorV2,
        used: BudgetVectorV2,
        expected_budget_revision: int,
    ) -> PngToShaderV2State:
        """在锁内提交 reservation，保持 run revision 不变。."""
        checkpoint_path, lock_path = self._paths(run_id)
        with self._lock(lock_path, exclusive=True):
            current = self._read_checkpoint(
                checkpoint_path,
                expected_run_id=run_id,
            )
            try:
                budget_state = commit_budget_v2(
                    current.budget_state,
                    reservation=reservation,
                    used=used,
                    expected_revision=expected_budget_revision,
                )
            except RuntimeError as exc:
                raise V2StateRevisionConflictError(
                    "V2 State budget revision CAS 冲突。"
                ) from exc
            updated = _replace_budget_state(current, budget_state)
            return self._persist_and_confirm(checkpoint_path, updated)

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        try:
            namespace = build_checkpoint_namespace_v2(run_id)
        except ValueError as exc:
            raise V2StateStoreError("V2 State Store run_id 无效。") from exc
        identity = sha256(namespace.encode("utf-8")).hexdigest()
        return (
            self._root / f"{identity}.checkpoint.json",
            self._root / f"{identity}.lock",
        )

    @contextmanager
    def _lock(self, lock_path: Path, *, exclusive: bool) -> Iterator[None]:
        with lock_path.open("a+b") as lock_file:
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(lock_file.fileno(), operation)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _persist_and_confirm(
        self,
        checkpoint_path: Path,
        state: PngToShaderV2State,
    ) -> PngToShaderV2State:
        payload = serialize_state_v2(state)
        envelope = json.dumps(
            {
                "schema_version": _ENVELOPE_SCHEMA_VERSION,
                "checkpoint_sha256": sha256(payload).hexdigest(),
                "checkpoint_json": payload.decode("utf-8"),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        temporary_path = checkpoint_path.with_name(
            f".{checkpoint_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
        )
        try:
            with temporary_path.open("xb") as temporary_file:
                temporary_file.write(envelope)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, checkpoint_path)
            self._fsync_root()
        finally:
            temporary_path.unlink(missing_ok=True)
        confirmed = self._read_checkpoint(
            checkpoint_path,
            expected_run_id=state.run_id,
        )
        if confirmed != state:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint 原子写入后的确认回读不一致。"
            )
        return confirmed

    def _read_checkpoint(
        self,
        checkpoint_path: Path,
        *,
        expected_run_id: str,
    ) -> PngToShaderV2State:
        try:
            envelope_bytes = checkpoint_path.read_bytes()
        except FileNotFoundError as exc:
            raise V2StateCheckpointNotFoundError(
                f"run {expected_run_id} 没有已确认 V2 checkpoint。"
            ) from exc
        try:
            envelope = json.loads(
                envelope_bytes,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint envelope 不是严格 JSON。"
            ) from exc
        if not isinstance(envelope, dict) or set(envelope) != _ENVELOPE_FIELDS:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint envelope 字段不完整或包含未知字段。"
            )
        if envelope.get("schema_version") != _ENVELOPE_SCHEMA_VERSION:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint envelope schema_version 不受支持。"
            )
        checkpoint_json = envelope.get("checkpoint_json")
        checkpoint_sha256 = envelope.get("checkpoint_sha256")
        if not isinstance(checkpoint_json, str) or not isinstance(
            checkpoint_sha256, str
        ):
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint payload 或 SHA-256 类型无效。"
            )
        if not _SHA256_PATTERN.fullmatch(checkpoint_sha256):
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint SHA-256 格式无效。"
            )
        checkpoint_payload = checkpoint_json.encode("utf-8")
        if sha256(checkpoint_payload).hexdigest() != checkpoint_sha256:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint payload SHA-256 不匹配。"
            )
        try:
            state = restore_state_v2(checkpoint_payload)
        except (UnicodeDecodeError, ValueError) as exc:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint State 契约恢复失败。"
            ) from exc
        if state.run_id != expected_run_id:
            raise V2StateCheckpointIntegrityError(
                "V2 checkpoint 不属于请求的 run。"
            )
        return state

    def _fsync_root(self) -> None:
        directory_fd = os.open(self._root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _replace_budget_state(
    state: PngToShaderV2State,
    budget_state: BudgetStateV2,
) -> PngToShaderV2State:
    candidate = state.model_copy(update={"budget_state": budget_state})
    updated = PngToShaderV2State.model_validate_json(
        candidate.model_dump_json(warnings="none"),
        strict=True,
    )
    if updated.run_revision != state.run_revision:
        raise V2StateCheckpointIntegrityError(
            "Budget CAS 不得修改 run_revision。"
        )
    return updated


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise V2StateCheckpointIntegrityError(
                f"V2 checkpoint 包含重复 JSON key：{key}。"
            )
        value[key] = item
    return value


def _reject_non_finite_constant(value: str) -> NoReturn:
    raise V2StateCheckpointIntegrityError(
        f"V2 checkpoint 包含非法 JSON 常量：{value}。"
    )
