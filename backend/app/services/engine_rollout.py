"""D095 canary/direct-default 的父 run/child attempt 协调核心."""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from uuid import UUID, uuid5

from agent.app.services.engine_rollout_artifacts import (
    EngineId,
    EngineRolloutArtifactError,
    EngineRolloutArtifactService,
    PublishedParentArtifacts,
    Representation,
    SelectedEngineArtifacts,
)
from agent.app.services.layerplan_glsl_direct import (
    current_layered_direct_glsl_implementation_identity as current_direct_glsl_implementation_identity,
)
from backend.app.core.engine_policy import (
    EnginePolicyResolution,
    PolicyStage,
    PromotionAuthorizationV1,
    ShaderEnginePolicyV1,
    bucket_matches_percent,
    promotion_authorization_sha256,
    shader_engine_policy_sha256,
    stable_project_bucket,
)
from shaderforge.config import RUNTIME_TIMEOUTS

AttemptStatus = Literal["succeeded", "failed"]
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
_REPRESENTATION_BY_ENGINE: dict[EngineId, Representation] = {
    "shader_graph_v1": "shader_document_v1",
    "direct_glsl_layerplan_v1": "shader_program_spec_v1",
}


class EngineRolloutError(RuntimeError):
    """engine rollout 不能安全规划、执行或发布."""

    def __init__(self, code: str) -> None:
        """保存预声明安全码，不携带底层错误原文."""
        if not _SAFE_CODE.fullmatch(code):
            raise ValueError("rollout error code 必须是安全标识符。")
        self.code = code
        super().__init__(code)


class PromotionAuthorityUnavailable(EngineRolloutError):
    """缺少可验证 durable promotion authority."""


class EngineAttemptFailure(EngineRolloutError):
    """单个 child attempt 的安全失败."""


class EngineResponseContractFailure(EngineRolloutError):
    """选中引擎的父响应不能还原为公开 API 契约."""

    def __init__(self, field: str) -> None:
        """记录固定 schema 的安全字段路径，不暴露实际值或底层错误."""
        if not re.fullmatch(r"[A-Za-z0-9_.]{1,160}", field):
            field = "unknown"
        self.field = field
        super().__init__("engine_response_contract_failed")


class ParentRunFailure(EngineRolloutError):
    """父 run 在全部 attempt 或公开发布后仍失败."""

    def __init__(
        self,
        code: str,
        *,
        attempt_refs: tuple[AttemptRef, ...],
    ) -> None:
        """保存已执行 attempt 的安全引用."""
        self.attempt_refs = attempt_refs
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VerifiedPromotionEvidence:
    """外部 verifier 返回的内容寻址 durable capability."""

    authorization_sha256: str
    target_stage: Literal["canary", "direct_default"]
    durable_registry_entry_id: str
    durable_evidence_sha256: str
    direct_implementation_identity: str


class PromotionEvidenceVerifier(Protocol):
    """未来部署控制面提供的只读 durable evidence verifier."""

    def verify(
        self,
        authorization: PromotionAuthorizationV1,
    ) -> VerifiedPromotionEvidence:
        """递归复验授权引用的跨环境 durable evidence."""


@dataclass(frozen=True, slots=True)
class AttemptRef:
    """父 run 可公开的 child attempt 安全引用."""

    attempt_id: str
    engine: EngineId
    representation: Representation
    status: AttemptStatus
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回 API/manifest 共用的 JSON-safe 引用."""
        return {
            "attempt_id": self.attempt_id,
            "engine": self.engine,
            "representation": self.representation,
            "status": self.status,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class ParentRunPlan:
    """在任何 engine 执行前冻结的父 run policy 选择."""

    parent_run_id: UUID
    project_id: str
    policy_id: str
    policy_sha256: str
    configured_stage: PolicyStage
    effective_stage: PolicyStage
    bucket: int
    primary_engine: EngineId
    promotion_authorization_sha256: str | None


@dataclass(frozen=True, slots=True)
class EngineAttemptContext:
    """单个 child attempt 的 engine/representation/索引冻结身份."""

    parent_run_id: UUID
    attempt_id: UUID
    attempt_index: int
    engine: EngineId
    representation: Representation
    artifact_scope: Literal["private_attempt"] = "private_attempt"


@dataclass(frozen=True, slots=True)
class ParentRunRequest:
    """父协调器执行所需的可信请求输入."""

    parent_run_id: UUID
    project_id: str
    image: bytes
    content_type: str
    instruction: str
    quality_preset: str
    progress_callback: Callable[[dict[str, Any], bytes | None], None] | None = None


@dataclass(frozen=True, slots=True)
class EngineAttemptSuccess:
    """一个已完成响应契约和私有 Artifact 的 child engine 成功."""

    attempt_id: UUID
    engine: EngineId
    representation: Representation
    response_payload: dict[str, Any]
    artifacts: SelectedEngineArtifacts
    artifact_scope: Literal["private_attempt"] = "private_attempt"


class EngineAttemptExecutor(Protocol):
    """一种 engine 的全新 attempt-local 执行器."""

    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        """执行并返回经过自身 response contract 的私有 child 结果."""

    async def close(self) -> None:
        """释放 attempt-local Renderer/cache/service."""


AttemptExecutorFactory = Callable[[EngineAttemptContext], EngineAttemptExecutor]


@dataclass(frozen=True, slots=True)
class ParentRunResult:
    """父 run 已原子发布后的选中结果."""

    response_payload: dict[str, Any]
    engine: EngineId
    representation: Representation
    engine_run: dict[str, Any]
    published_artifacts: PublishedParentArtifacts


def child_attempt_id(
    parent_run_id: UUID,
    engine: EngineId,
    attempt_index: int,
) -> UUID:
    """返回 D095 冻结的 ``uuid5(parent, '<engine>:<index>')``."""
    if isinstance(attempt_index, bool) or attempt_index < 0:
        raise ValueError("attempt_index 必须是非负整数。")
    return uuid5(parent_run_id, f"{engine}:{attempt_index}")


def _verify_promotion_authority(
    *,
    policy: ShaderEnginePolicyV1,
    verifier: PromotionEvidenceVerifier | None,
    direct_implementation_identity: str,
) -> str:
    authorization = policy.promotion_authorization
    if authorization is None or verifier is None:
        raise PromotionAuthorityUnavailable("promotion_authority_unavailable")
    if authorization.direct_implementation_identity != direct_implementation_identity:
        raise PromotionAuthorityUnavailable("direct_implementation_identity_drift")
    authorization_sha = promotion_authorization_sha256(authorization)
    assert authorization_sha is not None
    try:
        verified = verifier.verify(authorization)
    except Exception as exc:
        raise PromotionAuthorityUnavailable(
            "promotion_evidence_verification_failed"
        ) from exc
    if (
        verified.authorization_sha256 != authorization_sha
        or verified.target_stage != authorization.target_stage
        or verified.durable_registry_entry_id != authorization.durable_registry_entry_id
        or verified.durable_evidence_sha256 != authorization.durable_evidence_sha256
        or verified.direct_implementation_identity != direct_implementation_identity
    ):
        raise PromotionAuthorityUnavailable("promotion_evidence_identity_drift")
    return authorization_sha


def resolve_parent_run_plan(
    *,
    policy: ShaderEnginePolicyV1,
    resolution: EnginePolicyResolution,
    parent_run_id: UUID,
    project_id: str,
    promotion_verifier: PromotionEvidenceVerifier | None = None,
    direct_implementation_identity: str | None = None,
) -> ParentRunPlan:
    """冻结新父 run 的 engine；direct 选择缺 durable verifier 时 fail closed."""
    bucket = stable_project_bucket(
        policy_id=policy.policy_id,
        project_id=project_id,
    )
    stage = resolution.effective_stage
    primary_engine: EngineId = "shader_graph_v1"
    authorization_sha: str | None = None
    direct_selected = stage == "direct_default" or (
        stage == "canary" and bucket_matches_percent(bucket, policy.canary_percent)
    )
    if direct_selected:
        current_identity = (
            direct_implementation_identity
            if direct_implementation_identity is not None
            else str(current_direct_glsl_implementation_identity()["identity_sha256"])
        )
        if policy.promotion_authorization is not None:
            authorization_sha = _verify_promotion_authority(
                policy=policy,
                verifier=promotion_verifier,
                direct_implementation_identity=current_identity,
            )
        elif stage != "direct_default":
            raise PromotionAuthorityUnavailable("promotion_authority_unavailable")
        primary_engine = "direct_glsl_layerplan_v1"
    elif policy.promotion_authorization is not None:
        authorization_sha = promotion_authorization_sha256(
            policy.promotion_authorization
        )
    return ParentRunPlan(
        parent_run_id=parent_run_id,
        project_id=project_id,
        policy_id=policy.policy_id,
        policy_sha256=shader_engine_policy_sha256(policy),
        configured_stage=resolution.configured_stage,
        effective_stage=stage,
        bucket=bucket,
        primary_engine=primary_engine,
        promotion_authorization_sha256=authorization_sha,
    )


class EngineParentRunCoordinator:
    """显式 direct-first/fresh-direct-retry/parent-publish 协调器."""

    def __init__(
        self,
        *,
        direct_factory: AttemptExecutorFactory,
        shader_graph_factory: AttemptExecutorFactory,
        artifacts: EngineRolloutArtifactService,
        attempt_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.attempt_seconds,
        close_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.close_seconds,
        direct_attempt_limit: int = 2,
    ) -> None:
        """注入私有 attempt factory；本类自身不持有共享 Renderer/cache."""
        for name, value in (
            ("attempt_timeout_seconds", attempt_timeout_seconds),
            ("close_timeout_seconds", close_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是正数。")
        if (
            isinstance(direct_attempt_limit, bool)
            or not isinstance(direct_attempt_limit, int)
            or not 1 <= direct_attempt_limit <= 2
        ):
            raise ValueError("direct_attempt_limit 必须是 1 或 2。")
        self._factories: dict[EngineId, AttemptExecutorFactory] = {
            "direct_glsl_layerplan_v1": direct_factory,
            "shader_graph_v1": shader_graph_factory,
        }
        self._artifacts = artifacts
        self._attempt_timeout_seconds = float(attempt_timeout_seconds)
        self._close_timeout_seconds = float(close_timeout_seconds)
        self._direct_attempt_limit = direct_attempt_limit

    async def execute(
        self,
        *,
        request: ParentRunRequest,
        plan: ParentRunPlan,
    ) -> ParentRunResult:
        """执行冻结计划；direct 失败只可创建 fresh direct child 重试."""
        if (
            request.parent_run_id != plan.parent_run_id
            or request.project_id != plan.project_id
        ):
            raise ParentRunFailure(
                "parent_run_identity_mismatch",
                attempt_refs=(),
            )
        attempt_refs: list[AttemptRef] = []
        selected: EngineAttemptSuccess
        if plan.primary_engine == "direct_glsl_layerplan_v1":
            for attempt_index in range(self._direct_attempt_limit):
                context = self._context(
                    plan,
                    engine="direct_glsl_layerplan_v1",
                    attempt_index=attempt_index,
                )
                try:
                    selected = await self._execute_attempt(request, context)
                except EngineAttemptFailure as exc:
                    attempt_refs.append(self._failure_ref(context, exc.code))
                    if attempt_index + 1 >= self._direct_attempt_limit:
                        raise ParentRunFailure(
                            "direct_attempts_failed",
                            attempt_refs=tuple(attempt_refs),
                        ) from exc
                    self._publish_direct_retry(
                        request,
                        attempt_index=attempt_index + 1,
                        failure_code=exc.code,
                    )
                    continue
                attempt_refs.append(self._success_ref(context))
                break
            else:  # pragma: no cover - range 至少执行一次且仅以上分支可继续
                raise AssertionError("direct attempt loop did not terminate")
        else:
            primary_context = self._context(
                plan,
                engine=plan.primary_engine,
                attempt_index=0,
            )
            try:
                selected = await self._execute_attempt(request, primary_context)
                attempt_refs.append(self._success_ref(primary_context))
            except EngineAttemptFailure as exc:
                attempt_refs.append(self._failure_ref(primary_context, exc.code))
                raise ParentRunFailure(
                    "shader_graph_attempt_failed",
                    attempt_refs=tuple(attempt_refs),
                ) from exc
        selected_engine = selected.engine
        selected_representation = selected.representation
        engine_run = {
            "policy_id": plan.policy_id,
            "policy_sha256": plan.policy_sha256,
            "configured_stage": plan.configured_stage,
            "stage": plan.effective_stage,
            "bucket": plan.bucket,
            "selected_engine": selected_engine,
            "selected_representation": selected_representation,
            "selected_attempt_id": str(selected.attempt_id),
            "attempt_refs": [item.to_dict() for item in attempt_refs],
            "fallback_from": None,
            "fallback_reason": None,
            "promotion_authorization_sha256": (plan.promotion_authorization_sha256),
        }
        try:
            published = await asyncio.to_thread(
                self._artifacts.publish_parent,
                project_id=request.project_id,
                parent_run_id=str(request.parent_run_id),
                engine=selected_engine,
                representation=selected_representation,
                engine_run=engine_run,
                selected=selected.artifacts,
            )
        except EngineRolloutArtifactError as exc:
            raise ParentRunFailure(
                "parent_artifact_publish_failed",
                attempt_refs=tuple(attempt_refs),
            ) from exc
        parent_artifact_base = f"/api/shader/runs/{request.parent_run_id}/artifacts"
        response_payload = {
            **selected.response_payload,
            "project_id": request.project_id,
            "run_id": str(request.parent_run_id),
            "engine": selected_engine,
            "representation": selected_representation,
            "engine_run": engine_run,
            "final_render_url": f"{parent_artifact_base}/final-render",
            "metrics_url": f"{parent_artifact_base}/metrics",
            "manifest_url": f"{parent_artifact_base}/manifest",
        }
        return ParentRunResult(
            response_payload=response_payload,
            engine=selected_engine,
            representation=selected_representation,
            engine_run=engine_run,
            published_artifacts=published,
        )

    @staticmethod
    def _publish_direct_retry(
        request: ParentRunRequest,
        *,
        attempt_index: int,
        failure_code: str,
    ) -> None:
        callback = request.progress_callback
        if callback is None:
            return
        try:
            callback(
                {
                    "node": "engine_rollout",
                    "phase": "engine_retry",
                    "status": "running",
                    "engine": "direct_glsl_layerplan_v1",
                    "attempt_index": attempt_index,
                    "failure_code": failure_code,
                },
                None,
            )
        except Exception:
            return

    @staticmethod
    def _context(
        plan: ParentRunPlan,
        *,
        engine: EngineId,
        attempt_index: int,
    ) -> EngineAttemptContext:
        return EngineAttemptContext(
            parent_run_id=plan.parent_run_id,
            attempt_id=child_attempt_id(
                plan.parent_run_id,
                engine,
                attempt_index,
            ),
            attempt_index=attempt_index,
            engine=engine,
            representation=_REPRESENTATION_BY_ENGINE[engine],
        )

    async def _execute_attempt(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        executor: EngineAttemptExecutor | None = None
        try:
            executor = self._factories[context.engine](context)
            result = await asyncio.wait_for(
                executor.execute(request, context),
                timeout=self._attempt_timeout_seconds,
            )
            self._validate_success(result, context)
            return result
        except EngineAttemptFailure:
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise EngineAttemptFailure("engine_attempt_timeout") from exc
        except Exception as exc:
            raise EngineAttemptFailure("engine_attempt_failed") from exc
        finally:
            if executor is not None:
                await self._close_executor(executor)

    async def _close_executor(self, executor: EngineAttemptExecutor) -> None:
        try:
            value = executor.close()
            if inspect.isawaitable(value):
                await asyncio.wait_for(
                    value,
                    timeout=self._close_timeout_seconds,
                )
        except Exception:
            return

    @staticmethod
    def _validate_success(
        result: EngineAttemptSuccess,
        context: EngineAttemptContext,
    ) -> None:
        if (
            result.attempt_id != context.attempt_id
            or result.engine != context.engine
            or result.representation != context.representation
            or result.artifact_scope != "private_attempt"
        ):
            raise EngineAttemptFailure("engine_attempt_identity_mismatch")
        if not isinstance(result.response_payload, dict):
            raise EngineAttemptFailure("engine_response_contract_failed")
        forbidden = {
            "engine_manifest",
            "fragment_source",
            "layer_plan",
            "program_spec",
            "prompt",
            "repair_context",
        }
        pending: list[Any] = [result.response_payload]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                if forbidden.intersection(value):
                    raise EngineAttemptFailure("engine_response_private_data")
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    @staticmethod
    def _success_ref(context: EngineAttemptContext) -> AttemptRef:
        return AttemptRef(
            attempt_id=str(context.attempt_id),
            engine=context.engine,
            representation=context.representation,
            status="succeeded",
        )

    @staticmethod
    def _failure_ref(
        context: EngineAttemptContext,
        failure_code: str,
    ) -> AttemptRef:
        return AttemptRef(
            attempt_id=str(context.attempt_id),
            engine=context.engine,
            representation=context.representation,
            status="failed",
            failure_code=failure_code,
        )


__all__ = [
    "AttemptExecutorFactory",
    "AttemptRef",
    "EngineAttemptContext",
    "EngineAttemptExecutor",
    "EngineAttemptFailure",
    "EngineAttemptSuccess",
    "EngineParentRunCoordinator",
    "EngineRolloutError",
    "EngineResponseContractFailure",
    "ParentRunFailure",
    "ParentRunPlan",
    "ParentRunRequest",
    "ParentRunResult",
    "PromotionAuthorityUnavailable",
    "PromotionEvidenceVerifier",
    "VerifiedPromotionEvidence",
    "child_attempt_id",
    "resolve_parent_run_plan",
]
