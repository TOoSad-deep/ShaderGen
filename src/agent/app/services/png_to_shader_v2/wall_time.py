"""Service 外层 wall-time reservation 的本地持久化账本。"""
# ruff: noqa: D101, D102, D107, D415

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

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex

_ENVELOPE_FIELDS = frozenset({"sha256", "payload"})


class ServiceWallTimeLedgerV1(FrozenModel):
    schema_version: Literal["png_to_shader_v2_service_wall_time_ledger_v1"] = (
        "png_to_shader_v2_service_wall_time_ledger_v1"
    )
    run_id: NonEmptyString
    policy_hash: Sha256Hex
    revision: int = Field(ge=0)
    limit_ms: int = Field(gt=0)
    used_ms: int = Field(ge=0)
    reserved_ms: int = Field(ge=0)
    reservation_started_monotonic_ms: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_accounting(self) -> ServiceWallTimeLedgerV1:
        if self.used_ms + self.reserved_ms > self.limit_ms:
            raise ValueError("Service wall-time used + reserved 超过 limit。")
        if (self.reserved_ms > 0) != (
            self.reservation_started_monotonic_ms is not None
        ):
            raise ValueError("wall-time reservation 必须绑定 monotonic 起点。")
        return self


class ServiceWallTimeLedgerError(RuntimeError):
    pass


class ServiceWallTimeLedgerNotFound(ServiceWallTimeLedgerError):
    pass


class LocalServiceWallTimeLedgerStore:
    """与 Graph Budget CAS 分离的原子 wall-time reservation store。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)
        if not self._root.is_dir():
            raise ServiceWallTimeLedgerError("wall-time ledger root 必须是目录。")

    def initialize(
        self, *, run_id: str, policy_hash: str, limit_ms: int
    ) -> ServiceWallTimeLedgerV1:
        initial = ServiceWallTimeLedgerV1(
            run_id=run_id,
            policy_hash=policy_hash,
            revision=0,
            limit_ms=limit_ms,
            used_ms=0,
            reserved_ms=0,
            reservation_started_monotonic_ms=None,
        )
        path, lock = self._paths(run_id)
        with self._lock(lock, exclusive=True):
            if path.exists():
                raise ServiceWallTimeLedgerError("wall-time ledger 已存在。")
            return self._persist(path, initial)

    def load(self, run_id: str) -> ServiceWallTimeLedgerV1:
        path, lock = self._paths(run_id)
        with self._lock(lock, exclusive=False):
            return self._read(path, run_id)

    def reserve_remaining(
        self,
        run_id: str,
        *,
        expected_revision: int,
        started_monotonic_ms: int = 0,
    ) -> ServiceWallTimeLedgerV1:
        path, lock = self._paths(run_id)
        with self._lock(lock, exclusive=True):
            current = self._read(path, run_id)
            if current.revision != expected_revision:
                raise ServiceWallTimeLedgerError("wall-time ledger revision 冲突。")
            if current.reserved_ms:
                raise ServiceWallTimeLedgerError("wall-time 已有未结 reservation。")
            remaining = current.limit_ms - current.used_ms
            if remaining <= 0:
                raise ServiceWallTimeLedgerError("wall-time budget 已耗尽。")
            return self._persist(
                path,
                current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "reserved_ms": remaining,
                        "reservation_started_monotonic_ms": started_monotonic_ms,
                    }
                ),
            )

    def commit(
        self,
        run_id: str,
        *,
        reservation_ms: int,
        used_ms: int,
        expected_revision: int,
    ) -> ServiceWallTimeLedgerV1:
        path, lock = self._paths(run_id)
        with self._lock(lock, exclusive=True):
            current = self._read(path, run_id)
            if current.revision != expected_revision:
                raise ServiceWallTimeLedgerError("wall-time ledger revision 冲突。")
            if reservation_ms != current.reserved_ms:
                raise ServiceWallTimeLedgerError("wall-time reservation 不一致。")
            if used_ms < 0 or used_ms > reservation_ms:
                raise ServiceWallTimeLedgerError("wall-time actual 超过 reservation。")
            return self._persist(
                path,
                current.model_copy(
                    update={
                        "revision": current.revision + 1,
                        "used_ms": current.used_ms + used_ms,
                        "reserved_ms": 0,
                        "reservation_started_monotonic_ms": None,
                    }
                ),
            )

    def recover_orphan(
        self, run_id: str, *, now_monotonic_ms: int | None = None
    ) -> ServiceWallTimeLedgerV1:
        current = self.load(run_id)
        if not current.reserved_ms:
            return current
        started = current.reservation_started_monotonic_ms
        if now_monotonic_ms is None or started is None or now_monotonic_ms < started:
            charge = current.reserved_ms
        else:
            charge = min(current.reserved_ms, max(1, now_monotonic_ms - started))
        return self.commit(
            run_id,
            reservation_ms=current.reserved_ms,
            used_ms=charge,
            expected_revision=current.revision,
        )

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        if not run_id or ":" in run_id:
            raise ServiceWallTimeLedgerError("wall-time run_id 无效。")
        identity = sha256(run_id.encode("utf-8")).hexdigest()
        return self._root / f"{identity}.json", self._root / f"{identity}.lock"

    @contextmanager
    def _lock(self, path: Path, *, exclusive: bool) -> Iterator[None]:
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _persist(
        self, path: Path, value: ServiceWallTimeLedgerV1
    ) -> ServiceWallTimeLedgerV1:
        payload = value.model_dump_json().encode("utf-8")
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
    def _read(path: Path, run_id: str) -> ServiceWallTimeLedgerV1:
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
            raw_payload = envelope.get("payload")
            digest = envelope.get("sha256")
            if not isinstance(raw_payload, str) or not isinstance(digest, str):
                raise ValueError("envelope types")
            payload = raw_payload.encode()
            if sha256(payload).hexdigest() != digest:
                raise ValueError("SHA mismatch")
            json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
            value = ServiceWallTimeLedgerV1.model_validate_json(payload, strict=True)
        except FileNotFoundError as exc:
            raise ServiceWallTimeLedgerNotFound("wall-time ledger 不存在。") from exc
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ServiceWallTimeLedgerError("wall-time ledger 完整性校验失败。") from exc
        if value.run_id != run_id:
            raise ServiceWallTimeLedgerError("wall-time ledger run_id 不一致。")
        return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"wall-time ledger 包含重复 JSON key：{key}。")
        value[key] = item
    return value


def _reject_non_finite_constant(value: str) -> NoReturn:
    raise ValueError(f"wall-time ledger 包含非法 JSON 常量：{value}。")


__all__ = [
    "LocalServiceWallTimeLedgerStore",
    "ServiceWallTimeLedgerError",
    "ServiceWallTimeLedgerNotFound",
    "ServiceWallTimeLedgerV1",
]
