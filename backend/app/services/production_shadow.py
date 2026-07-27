"""D091 production shadow 的有界、非权威 Backend 协调器.

本模块只允许 ``production_shadow`` 阶段运行 direct child attempt。它不会注册
产品 Artifact、修改 ``current_best``、写产品成功账本或参与 HTTP 响应。
"""

from __future__ import annotations

import asyncio
import inspect
import itertools
import json
import logging
import os
import shutil
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, Protocol
from uuid import UUID, uuid4, uuid5

from agent.app.services.layerplan_glsl_direct import (
    DIRECT_ENGINE_ID,
    DirectAttemptResult,
    LayerPlanGlslDirectConfig,
    create_owned_layerplan_glsl_direct_runner,
)
from agent.app.services.layerplan_glsl_shadow_suite import (
    current_direct_glsl_implementation_identity,
)
from backend.app.core.engine_policy import (
    EnginePolicyResolution,
    ShaderEnginePolicyV1,
    bucket_matches_percent,
    shader_engine_policy_sha256,
    stable_project_bucket,
)

logger = logging.getLogger("backend.shader.shadow")

SHADOW_ARTIFACT_SCHEMA_VERSION = "production_shadow_artifact_v1"
SHADOW_SUMMARY_SCHEMA_VERSION = "production_shadow_summary_v1"
SHADOW_ATTEMPT_NAME = f"{DIRECT_ENGINE_ID}:0"


class ProductionShadowArtifactError(ValueError):
    """production shadow 私有 Artifact 不满足递归完整性约束."""


class _AttemptRunner(Protocol):
    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
    ) -> DirectAttemptResult:
        """执行一次 attempt."""

    async def close(self) -> None:
        """释放 attempt-local 资源."""


AttemptRunnerFactory = Callable[[LayerPlanGlslDirectConfig], _AttemptRunner]


@dataclass(frozen=True, slots=True)
class ProductionShadowConfig:
    """Backend 启动时冻结的 shadow 并发、超时和私有目录配置."""

    output_root: Path
    queue_capacity: int = 4
    worker_count: int = 1
    attempt_timeout_seconds: float = 180.0
    close_timeout_seconds: float = 5.0
    resource_close_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        """拒绝无界或非正的并发与超时配置."""
        for name in ("queue_capacity", "worker_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数。")
        for name in (
            "attempt_timeout_seconds",
            "close_timeout_seconds",
            "resource_close_timeout_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是正数。")


@dataclass(frozen=True, slots=True)
class _ShadowWorkItem:
    project_id: str
    parent_run_id: UUID
    attempt_id: UUID
    bucket: int
    image: bytes
    content_type: str
    instruction: str
    accepted_at: float


def _default_runner_factory(
    config: LayerPlanGlslDirectConfig,
) -> _AttemptRunner:
    return create_owned_layerplan_glsl_direct_runner(config)


def direct_shadow_attempt_id(parent_run_id: UUID | str) -> UUID:
    """返回 D091 冻结的 direct child attempt UUID5."""
    parent = (
        parent_run_id if isinstance(parent_run_id, UUID) else UUID(str(parent_run_id))
    )
    return uuid5(parent, SHADOW_ATTEMPT_NAME)


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (_canonical_json(payload) + "\n").encode("utf-8")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    """生成 Backend 私有 manifest 使用的稳定 JSON."""
    return json.dumps(
        dict(payload),
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _write_private_file(root: Path, relative: str, data: bytes) -> str:
    path = root.joinpath(*PurePosixPath(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return sha256(data).hexdigest()


def _layer_plan_payload(result: DirectAttemptResult) -> dict[str, Any] | None:
    plan = result.layer_plan
    if plan is None:
        return None
    return {
        "schema_version": plan.schema_version,
        "layers": [layer.to_dict() for layer in plan.layers],
        "reference_sha256": plan.reference_sha256,
        "author_identity": plan.author_identity.to_dict(),
        "observations_ref": plan.observations_ref,
        "plan_sha256": plan.plan_sha256,
    }


def _spec_payload(result: DirectAttemptResult) -> dict[str, Any] | None:
    best = result.current_best
    if best is None:
        return None
    spec = best.spec
    return {
        "schema_version": spec.schema_version,
        "renderer_contract_id": spec.renderer_contract_id,
        "fragment_source": spec.fragment_source,
        "uniform_schema": [item.to_dict() for item in spec.uniform_schema],
        "uniform_values": {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in spec.uniform_values.items()
        },
        "tunable_manifest": [item.to_dict() for item in spec.tunable_manifest],
        "canvas": spec.canvas.to_dict(),
        "source_sha256": spec.source_sha256,
        "binding_sha256": spec.binding_sha256,
        "spec_sha256": spec.spec_sha256,
        "author_identity": spec.author_identity.to_dict(),
        "validation_attestation": (
            spec.validation_attestation.to_dict()
            if spec.validation_attestation is not None
            else None
        ),
    }


def _metric_payload(result: DirectAttemptResult) -> dict[str, Any] | None:
    best = result.current_best
    if best is None:
        return None
    return {
        "mae": best.mae,
        "loss": best.loss,
        "metrics": best.metrics,
        "residual_summary": best.residual_summary,
        "metric_version": result.identity.metric_version,
        "reference_sha256": result.reference_sha256,
        "spec_sha256": best.spec.spec_sha256,
        "render_rgb_sha256": sha256(best.rgb_bytes).hexdigest(),
        "render_png_sha256": sha256(best.png_bytes).hexdigest(),
        "parent_spec_sha256": best.parent_spec_sha256,
        "provenance": best.provenance,
    }


def _write_shadow_attempt(
    *,
    output_root: Path,
    item: _ShadowWorkItem,
    summary: Mapping[str, Any],
    implementation_identity: Mapping[str, Any],
    result: DirectAttemptResult | None,
) -> Path:
    """以同根 staging + rename 原子提交 write-once 私有 attempt."""
    if output_root.is_symlink():
        raise ProductionShadowArtifactError("shadow output_root 不得是 symlink。")
    output_root.mkdir(parents=True, exist_ok=True)
    os.chmod(output_root, 0o700)
    parent_dir = output_root / str(item.parent_run_id)
    if parent_dir.is_symlink():
        raise ProductionShadowArtifactError("shadow parent 目录不得是 symlink。")
    parent_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(parent_dir, 0o700)
    attempt_dir = parent_dir / str(item.attempt_id)
    staging = parent_dir / (
        f".{item.attempt_id}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    )
    staging.mkdir(mode=0o700)
    try:
        files: dict[str, str] = {}
        files["safe-summary.json"] = _write_private_file(
            staging, "safe-summary.json", _json_bytes(summary)
        )
        files["private/implementation-identity.json"] = _write_private_file(
            staging,
            "private/implementation-identity.json",
            _json_bytes(implementation_identity),
        )
        if result is not None:
            files["private/config.json"] = _write_private_file(
                staging, "private/config.json", _json_bytes(result.config.to_dict())
            )
            plan = _layer_plan_payload(result)
            if plan is not None:
                files["private/layer-plan.json"] = _write_private_file(
                    staging, "private/layer-plan.json", _json_bytes(plan)
                )
            spec = _spec_payload(result)
            metric = _metric_payload(result)
            best = result.current_best
            if spec is not None and metric is not None and best is not None:
                files["private/current-best/spec.json"] = _write_private_file(
                    staging, "private/current-best/spec.json", _json_bytes(spec)
                )
                files["private/current-best/render.png"] = _write_private_file(
                    staging, "private/current-best/render.png", best.png_bytes
                )
                files["private/current-best/metric.json"] = _write_private_file(
                    staging, "private/current-best/metric.json", _json_bytes(metric)
                )
        manifest_body: dict[str, Any] = {
            "schema_version": SHADOW_ARTIFACT_SCHEMA_VERSION,
            "parent_run_id": str(item.parent_run_id),
            "attempt_id": str(item.attempt_id),
            "attempt_name": SHADOW_ATTEMPT_NAME,
            "files": dict(sorted(files.items())),
        }
        manifest = dict(manifest_body)
        manifest["manifest_sha256"] = sha256(
            _canonical_json(manifest_body).encode("utf-8")
        ).hexdigest()
        _write_private_file(staging, "manifest.json", _json_bytes(manifest))
        for path in itertools.chain([staging], staging.rglob("*")):
            if path.is_dir():
                os.chmod(path, 0o700)
        if attempt_dir.exists() or attempt_dir.is_symlink():
            raise FileExistsError(f"shadow attempt 已存在，拒绝覆盖：{attempt_dir}")
        os.rename(staging, attempt_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    verify_production_shadow_attempt(attempt_dir)
    return attempt_dir


def _strict_relative(value: Any) -> str:
    if not isinstance(value, str):
        raise ProductionShadowArtifactError("Artifact 路径必须是字符串。")
    pure = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ProductionShadowArtifactError(f"非法 Artifact 相对路径：{value!r}")
    return value


def verify_production_shadow_attempt(attempt_dir: Path) -> dict[str, Any]:
    """递归校验 shadow attempt，拒绝 symlink、额外文件、改名与篡改."""
    if attempt_dir.is_symlink() or not attempt_dir.is_dir():
        raise ProductionShadowArtifactError("shadow attempt 目录无效。")
    try:
        attempt_id = UUID(attempt_dir.name)
        parent_id = UUID(attempt_dir.parent.name)
    except ValueError as exc:
        raise ProductionShadowArtifactError(
            "shadow parent/attempt 目录名必须是 UUID。"
        ) from exc
    if attempt_id != direct_shadow_attempt_id(parent_id):
        raise ProductionShadowArtifactError("shadow child attempt UUID5 身份不匹配。")
    for boundary in (attempt_dir.parent.parent, attempt_dir.parent):
        if boundary.is_symlink() or not boundary.is_dir():
            raise ProductionShadowArtifactError("shadow 私有根/parent 目录无效。")
        if stat.S_IMODE(boundary.stat().st_mode) != 0o700:
            raise ProductionShadowArtifactError(
                f"shadow 私有根/parent 权限非法：{boundary}"
            )
    for path in [attempt_dir, *attempt_dir.rglob("*")]:
        if path.is_symlink():
            raise ProductionShadowArtifactError(f"shadow Artifact 禁止 symlink：{path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        expected_mode = 0o700 if path.is_dir() else 0o600
        if mode != expected_mode:
            raise ProductionShadowArtifactError(
                f"shadow Artifact 权限非法：{path} mode={oct(mode)}"
            )
    manifest_path = attempt_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ProductionShadowArtifactError("shadow attempt 缺少 manifest.json。")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProductionShadowArtifactError("shadow manifest 不是合法 JSON。") from exc
    if not isinstance(manifest, dict):
        raise ProductionShadowArtifactError("shadow manifest 必须是 object。")
    manifest_sha256 = manifest.pop("manifest_sha256", None)
    if (
        not isinstance(manifest_sha256, str)
        or manifest_sha256
        != sha256(_canonical_json(manifest).encode("utf-8")).hexdigest()
    ):
        raise ProductionShadowArtifactError("shadow manifest hash 不匹配。")
    if (
        manifest.get("schema_version") != SHADOW_ARTIFACT_SCHEMA_VERSION
        or manifest.get("parent_run_id") != str(parent_id)
        or manifest.get("attempt_id") != str(attempt_id)
        or manifest.get("attempt_name") != SHADOW_ATTEMPT_NAME
    ):
        raise ProductionShadowArtifactError("shadow manifest attempt 身份不匹配。")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, dict):
        raise ProductionShadowArtifactError("shadow manifest files 必须是 object。")
    expected_files = {"manifest.json"}
    expected_dirs = {PurePosixPath(".")}
    root = attempt_dir.resolve(strict=True)
    for raw_relative, digest in raw_files.items():
        relative = _strict_relative(raw_relative)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ProductionShadowArtifactError("shadow file digest 非法。")
        target = attempt_dir.joinpath(*PurePosixPath(relative).parts)
        if target.is_symlink() or not target.is_file():
            raise ProductionShadowArtifactError(f"shadow 文件缺失或改名：{relative}")
        if not target.resolve(strict=True).is_relative_to(root):
            raise ProductionShadowArtifactError("shadow 文件越出 attempt 目录。")
        if sha256(target.read_bytes()).hexdigest() != digest:
            raise ProductionShadowArtifactError(f"shadow 文件被篡改：{relative}")
        expected_files.add(relative)
        parent = PurePosixPath(relative).parent
        while parent.as_posix() != ".":
            expected_dirs.add(parent)
            parent = parent.parent
    actual_files = {
        path.relative_to(attempt_dir).as_posix()
        for path in attempt_dir.rglob("*")
        if path.is_file()
    }
    actual_dirs = {
        path.relative_to(attempt_dir)
        for path in attempt_dir.rglob("*")
        if path.is_dir()
    }
    if actual_files != expected_files:
        raise ProductionShadowArtifactError("shadow 文件集合漂移（额外、缺失或改名）。")
    expected_dir_paths = {
        Path(*pure.parts) for pure in expected_dirs if pure.as_posix() != "."
    }
    if actual_dirs != expected_dir_paths:
        raise ProductionShadowArtifactError("shadow 目录集合漂移。")
    summary_path = attempt_dir / "safe-summary.json"
    if not summary_path.is_file():
        raise ProductionShadowArtifactError("shadow attempt 缺少 safe summary。")
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProductionShadowArtifactError("shadow safe summary 非法。") from exc
    if not isinstance(summary, dict):
        raise ProductionShadowArtifactError("shadow safe summary 必须是 object。")
    if (
        summary.get("schema_version") != SHADOW_SUMMARY_SCHEMA_VERSION
        or summary.get("parent_run_id") != str(parent_id)
        or summary.get("attempt_id") != str(attempt_id)
        or summary.get("engine_id") != DIRECT_ENGINE_ID
        or summary.get("representation") != "shader_program_spec_v1"
    ):
        raise ProductionShadowArtifactError("shadow safe summary 身份不匹配。")
    forbidden_summary_keys = {
        "fragment_source",
        "instruction",
        "layer_plan",
        "png_bytes",
        "prompt",
        "rgb_bytes",
    }
    pending: list[Any] = [summary]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if forbidden_summary_keys.intersection(value):
                raise ProductionShadowArtifactError(
                    "shadow safe summary 包含私有内容字段。"
                )
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    identity_path = attempt_dir / "private/implementation-identity.json"
    try:
        identity = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise ProductionShadowArtifactError(
            "shadow implementation identity 非法。"
        ) from exc
    if not isinstance(identity, dict):
        raise ProductionShadowArtifactError(
            "shadow implementation identity 必须是 object。"
        )
    identity_sha256 = identity.pop("identity_sha256", None)
    if (
        not isinstance(identity_sha256, str)
        or identity_sha256
        != sha256(_canonical_json(identity).encode("utf-8")).hexdigest()
        or summary.get("implementation_identity_sha256") != identity_sha256
    ):
        raise ProductionShadowArtifactError(
            "shadow implementation identity hash 不匹配。"
        )
    return summary


class ProductionShadowCoordinator:
    """有界队列协调 production shadow；所有公开方法均返回 JSON-safe 摘要."""

    def __init__(
        self,
        *,
        policy: ShaderEnginePolicyV1,
        resolution: EnginePolicyResolution,
        config: ProductionShadowConfig,
        runner_factory: AttemptRunnerFactory = _default_runner_factory,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        """冻结 policy/identity/config；此时不启动 worker 或创建 Artifact."""
        self._policy = policy
        self._resolution = resolution
        self._config = config
        self._runner_factory = runner_factory
        self._clock = clock
        identity = current_direct_glsl_implementation_identity()
        self._implementation_identity = dict(identity)
        self._implementation_identity_sha256 = str(identity["identity_sha256"])
        self._direct_config = LayerPlanGlslDirectConfig(
            implementation_identity_sha256=self._implementation_identity_sha256
        )
        self._queue: asyncio.Queue[_ShadowWorkItem] = asyncio.Queue(
            maxsize=config.queue_capacity
        )
        self._workers: list[asyncio.Task[None]] = []
        # 只保存排队中/执行中的 attempt；已落盘 attempt 由 write-once 目录判重，
        # 避免长生命周期 Backend 随历史 run 数量无限增长。
        self._submitted_attempts: set[UUID] = set()
        self._started = False
        self._closing = False

    @property
    def enabled(self) -> bool:
        """只有有效 production_shadow 且比例大于零才启动 worker."""
        return (
            self._resolution.effective_stage == "production_shadow"
            and self._policy.shadow_percent > 0
        )

    async def start(self) -> None:
        """仅在有效且非零的 production_shadow 阶段启动固定 worker."""
        if self._started or self._closing:
            return
        self._started = True
        if not self.enabled:
            return
        for index in range(self._config.worker_count):
            self._workers.append(
                asyncio.create_task(
                    self._worker(index),
                    name=f"production-shadow-{index}",
                )
            )

    def submit(
        self,
        *,
        project_id: str,
        parent_run_id: UUID | str,
        image: bytes,
        content_type: str,
        instruction: str,
    ) -> dict[str, Any]:
        """非阻塞提交；policy、kill switch 和分桶只取服务端冻结值."""
        base = {
            "schema_version": SHADOW_SUMMARY_SCHEMA_VERSION,
            "policy_id": self._policy.policy_id,
            "policy_sha256": shader_engine_policy_sha256(self._policy),
            "configured_stage": self._resolution.configured_stage,
            "effective_stage": self._resolution.effective_stage,
            "kill_switch_active": self._resolution.kill_switch_active,
        }
        if self._resolution.kill_switch_active:
            return {**base, "status": "skipped", "reason": "shadow_skipped_kill_switch"}
        if self._resolution.effective_stage != "production_shadow":
            return {**base, "status": "skipped", "reason": "shadow_skipped_disabled"}
        if self._policy.shadow_percent <= 0:
            return {
                **base,
                "status": "skipped",
                "reason": "shadow_skipped_zero_percent",
            }
        bucket = stable_project_bucket(
            policy_id=self._policy.policy_id,
            project_id=project_id,
        )
        base["bucket"] = bucket
        base["shadow_percent"] = self._policy.shadow_percent
        if not bucket_matches_percent(bucket, self._policy.shadow_percent):
            return {**base, "status": "skipped", "reason": "shadow_skipped_bucket"}
        if not self._started or self._closing:
            return {
                **base,
                "status": "skipped",
                "reason": "shadow_skipped_unavailable",
            }
        try:
            parent = (
                parent_run_id
                if isinstance(parent_run_id, UUID)
                else UUID(str(parent_run_id))
            )
        except (TypeError, ValueError, AttributeError):
            return {
                **base,
                "status": "skipped",
                "reason": "shadow_skipped_invalid_parent_run_id",
            }
        attempt_id = direct_shadow_attempt_id(parent)
        base["parent_run_id"] = str(parent)
        base["attempt_id"] = str(attempt_id)
        attempt_dir = (
            self._config.output_root / str(parent) / str(attempt_id)
        )
        if (
            attempt_id in self._submitted_attempts
            or attempt_dir.exists()
            or attempt_dir.is_symlink()
        ):
            return {**base, "status": "skipped", "reason": "shadow_skipped_duplicate"}
        item = _ShadowWorkItem(
            project_id=project_id,
            parent_run_id=parent,
            attempt_id=attempt_id,
            bucket=bucket,
            image=bytes(image),
            content_type=str(content_type),
            instruction=str(instruction),
            accepted_at=self._clock(),
        )
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            return {
                **base,
                "status": "skipped",
                "reason": "shadow_skipped_capacity",
            }
        self._submitted_attempts.add(attempt_id)
        return {**base, "status": "accepted", "reason": "shadow_queued"}

    async def _worker(self, worker_index: int) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._execute(item, worker_index=worker_index)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "shader.shadow.worker_failed parent_run_id=%s attempt_id=%s "
                    "error_type=%s",
                    item.parent_run_id,
                    item.attempt_id,
                    type(exc).__name__,
                )
            finally:
                self._submitted_attempts.discard(item.attempt_id)
                self._queue.task_done()

    async def _execute(self, item: _ShadowWorkItem, *, worker_index: int) -> None:
        started = self._clock()
        runner: _AttemptRunner | None = None
        result: DirectAttemptResult | None = None
        status = "failed"
        failure_code: str | None = "shadow_internal_error"
        try:
            runner = self._runner_factory(self._direct_config)
            attempt_result = await asyncio.wait_for(
                runner.run(
                    item.image,
                    content_type=item.content_type,
                    instruction=item.instruction,
                ),
                timeout=self._config.attempt_timeout_seconds,
            )
            result = attempt_result
            status = attempt_result.status
            failure_code = attempt_result.failure_code
        except asyncio.TimeoutError:
            status = "timeout"
            failure_code = "shadow_attempt_timeout"
        except asyncio.CancelledError:
            status = "cancelled"
            failure_code = "shadow_shutdown_cancelled"
            raise
        except Exception as exc:
            failure_code = "shadow_attempt_failed"
            logger.warning(
                "shader.shadow.attempt_failed parent_run_id=%s attempt_id=%s "
                "error_type=%s",
                item.parent_run_id,
                item.attempt_id,
                type(exc).__name__,
            )
        finally:
            if runner is not None:
                await self._close_runner(runner)
            duration_ms = round((self._clock() - started) * 1000, 2)
            summary: dict[str, Any] = {
                "schema_version": SHADOW_SUMMARY_SCHEMA_VERSION,
                "status": status,
                "failure_code": failure_code,
                "parent_run_id": str(item.parent_run_id),
                "attempt_id": str(item.attempt_id),
                "engine_id": DIRECT_ENGINE_ID,
                "representation": "shader_program_spec_v1",
                "policy_id": self._policy.policy_id,
                "policy_sha256": shader_engine_policy_sha256(self._policy),
                "stage": self._resolution.effective_stage,
                "bucket": item.bucket,
                "shadow_percent": self._policy.shadow_percent,
                "implementation_identity_sha256": (
                    self._implementation_identity_sha256
                ),
                "config_fingerprint": self._direct_config.fingerprint(),
                "queue_delay_ms": round((started - item.accepted_at) * 1000, 2),
                "duration_ms": duration_ms,
                "worker_index": worker_index,
            }
            if result is not None:
                summary["result"] = result.to_safe_summary()
            try:
                # 私有 Spec/render 的写入、chmod 与全量 hash 复验不得阻塞
                # FastAPI 主事件循环；每个 attempt 仍在独立线程内原子提交。
                await asyncio.to_thread(
                    _write_shadow_attempt,
                    output_root=self._config.output_root,
                    item=item,
                    summary=summary,
                    implementation_identity=self._implementation_identity,
                    result=result,
                )
            except Exception as exc:
                logger.error(
                    "shader.shadow.artifact_failed parent_run_id=%s attempt_id=%s "
                    "status=%s error_type=%s",
                    item.parent_run_id,
                    item.attempt_id,
                    status,
                    type(exc).__name__,
                )
            logger.info(
                "shader.shadow.finished parent_run_id=%s attempt_id=%s status=%s "
                "failure_code=%s duration_ms=%.2f",
                item.parent_run_id,
                item.attempt_id,
                status,
                failure_code,
                duration_ms,
            )

    async def _close_runner(self, runner: _AttemptRunner) -> None:
        try:
            result = runner.close()
            if inspect.isawaitable(result):
                await asyncio.wait_for(
                    result,
                    timeout=self._config.resource_close_timeout_seconds,
                )
        except Exception as exc:
            logger.warning(
                "shader.shadow.runner_close_failed error_type=%s",
                type(exc).__name__,
            )

    async def close(self) -> None:
        """在总 timeout 内 drain，随后取消 worker/余项，绝不阻塞 shutdown."""
        if self._closing:
            return
        self._closing = True
        if not self._workers:
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._config.close_timeout_seconds
        try:
            await asyncio.wait_for(
                self._queue.join(),
                timeout=self._config.close_timeout_seconds,
            )
        except asyncio.TimeoutError:
            pass
        for task in self._workers:
            task.cancel()
        while True:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                self._submitted_attempts.discard(item.attempt_id)
                self._queue.task_done()
        done, pending = await asyncio.wait(
            self._workers,
            timeout=max(0.0, deadline - loop.time()),
        )
        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning(
                    "shader.shadow.worker_close_failed error_type=%s",
                    type(exc).__name__,
                )
        self._workers.clear()


__all__ = [
    "AttemptRunnerFactory",
    "ProductionShadowArtifactError",
    "ProductionShadowConfig",
    "ProductionShadowCoordinator",
    "SHADOW_ARTIFACT_SCHEMA_VERSION",
    "SHADOW_ATTEMPT_NAME",
    "SHADOW_SUMMARY_SCHEMA_VERSION",
    "direct_shadow_attempt_id",
    "verify_production_shadow_attempt",
]
