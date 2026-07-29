"""Direct-only parent run coordinator with three fresh attempts."""

from __future__ import annotations

import asyncio
import inspect
import logging
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
from backend.app.core.log_context import scoped_log_context
from backend.app.core.logging import safe_exception_diagnostics
from shaderforge.config import RUNTIME_TIMEOUTS

logger = logging.getLogger("backend.engine_rollout")

AttemptStatus = Literal["succeeded", "failed"]
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")
DIRECT_ENGINE: EngineId = "direct_glsl_layerplan_v1"
DIRECT_REPRESENTATION: Representation = "shader_program_spec_v1"


class EngineRolloutError(RuntimeError):
    def __init__(self, code: str) -> None:
        if not _SAFE_CODE.fullmatch(code):
            raise ValueError("rollout error code must be a safe identifier")
        self.code = code
        super().__init__(code)


class EngineAttemptFailure(EngineRolloutError):
    """One isolated Direct attempt failed safely."""


class EngineResponseContractFailure(EngineRolloutError):
    def __init__(self, field: str) -> None:
        self.field = (
            field if re.fullmatch(r"[A-Za-z0-9_.]{1,160}", field) else "unknown"
        )
        super().__init__("engine_response_contract_failed")


class ParentRunFailure(EngineRolloutError):
    def __init__(self, code: str, *, attempt_refs: tuple[AttemptRef, ...]) -> None:
        self.attempt_refs = attempt_refs
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class AttemptRef:
    attempt_id: str
    engine: EngineId
    representation: Representation
    status: AttemptStatus
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "engine": self.engine,
            "representation": self.representation,
            "status": self.status,
            "failure_code": self.failure_code,
        }


@dataclass(frozen=True, slots=True)
class ParentRunPlan:
    parent_run_id: UUID
    project_id: str


@dataclass(frozen=True, slots=True)
class EngineAttemptContext:
    parent_run_id: UUID
    attempt_id: UUID
    attempt_index: int
    engine: EngineId = DIRECT_ENGINE
    representation: Representation = DIRECT_REPRESENTATION
    artifact_scope: Literal["private_attempt"] = "private_attempt"


@dataclass(frozen=True, slots=True)
class ParentRunRequest:
    parent_run_id: UUID
    project_id: str
    image: bytes
    content_type: str
    instruction: str
    quality_preset: str
    progress_callback: Callable[[dict[str, Any], bytes | None], None] | None = None


@dataclass(frozen=True, slots=True)
class EngineAttemptSuccess:
    attempt_id: UUID
    engine: EngineId
    representation: Representation
    response_payload: dict[str, Any]
    artifacts: SelectedEngineArtifacts
    artifact_scope: Literal["private_attempt"] = "private_attempt"


class EngineAttemptExecutor(Protocol):
    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess: ...

    async def close(self) -> None: ...


AttemptExecutorFactory = Callable[[EngineAttemptContext], EngineAttemptExecutor]


@dataclass(frozen=True, slots=True)
class ParentRunResult:
    response_payload: dict[str, Any]
    engine: EngineId
    representation: Representation
    engine_run: dict[str, Any]
    published_artifacts: PublishedParentArtifacts


def child_attempt_id(parent_run_id: UUID, attempt_index: int) -> UUID:
    if isinstance(attempt_index, bool) or attempt_index < 0:
        raise ValueError("attempt_index must be a non-negative integer")
    return uuid5(parent_run_id, f"{DIRECT_ENGINE}:{attempt_index}")


def resolve_parent_run_plan(*, parent_run_id: UUID, project_id: str) -> ParentRunPlan:
    """Freeze the only supported execution plan."""
    return ParentRunPlan(parent_run_id=parent_run_id, project_id=project_id)


class EngineParentRunCoordinator:
    """Run at most three fresh Direct attempts, then publish the winner."""

    def __init__(
        self,
        *,
        direct_factory: AttemptExecutorFactory,
        artifacts: EngineRolloutArtifactService,
        attempt_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.attempt_seconds,
        close_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.close_seconds,
        direct_attempt_limit: int = 3,
    ) -> None:
        for name, value in (
            ("attempt_timeout_seconds", attempt_timeout_seconds),
            ("close_timeout_seconds", close_timeout_seconds),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")
        if (
            isinstance(direct_attempt_limit, bool)
            or not isinstance(direct_attempt_limit, int)
            or not 1 <= direct_attempt_limit <= 3
        ):
            raise ValueError("direct_attempt_limit must be 1, 2, or 3")
        self._direct_factory = direct_factory
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
        if (
            request.parent_run_id != plan.parent_run_id
            or request.project_id != plan.project_id
        ):
            logger.warning(
                "event=engine.parent_run.rejected run_id=%s project_id=%s "
                "attempt_id=- attempt_index=- stage=parent_run_plan "
                "error_code=parent_run_identity_mismatch "
                "error_type=ParentRunFailure retryable=false suppressed=false",
                request.parent_run_id,
                request.project_id,
            )
            raise ParentRunFailure("parent_run_identity_mismatch", attempt_refs=())
        refs: list[AttemptRef] = []
        selected: EngineAttemptSuccess | None = None
        for attempt_index in range(self._direct_attempt_limit):
            context = EngineAttemptContext(
                parent_run_id=plan.parent_run_id,
                attempt_id=child_attempt_id(plan.parent_run_id, attempt_index),
                attempt_index=attempt_index,
            )
            try:
                with scoped_log_context(
                    attempt_id=str(context.attempt_id),
                    stage="engine_attempt",
                ):
                    selected = await self._execute_attempt(request, context)
            except EngineAttemptFailure as exc:
                refs.append(self._failure_ref(context, exc.code))
                if attempt_index + 1 >= self._direct_attempt_limit:
                    failure = ParentRunFailure(
                        "direct_attempts_failed",
                        attempt_refs=tuple(refs),
                    )
                    logger.error(
                        "event=engine.parent_run.failed run_id=%s project_id=%s "
                        "attempt_id=- attempt_index=- stage=engine_rollout "
                        "error_code=%s error_type=%s retryable=true suppressed=false "
                        "attempt_refs=%s",
                        request.parent_run_id,
                        request.project_id,
                        failure.code,
                        type(failure).__name__,
                        [item.to_dict() for item in failure.attempt_refs],
                    )
                    raise failure from exc
                logger.warning(
                    "event=engine.attempt.failed run_id=%s project_id=%s "
                    "attempt_id=%s attempt_index=%s stage=engine_attempt "
                    "error_code=%s error_type=%s retryable=true suppressed=false",
                    request.parent_run_id,
                    request.project_id,
                    context.attempt_id,
                    context.attempt_index,
                    exc.code,
                    type(exc).__name__,
                )
                self._publish_retry(
                    request,
                    attempt_index=attempt_index + 1,
                    failure_code=exc.code,
                )
                continue
            refs.append(self._success_ref(context))
            break
        assert selected is not None
        engine_run = {
            "selected_engine": DIRECT_ENGINE,
            "selected_representation": DIRECT_REPRESENTATION,
            "selected_attempt_id": str(selected.attempt_id),
            "attempt_refs": [item.to_dict() for item in refs],
        }
        try:
            published = await asyncio.to_thread(
                self._artifacts.publish_parent,
                project_id=request.project_id,
                parent_run_id=str(request.parent_run_id),
                engine=DIRECT_ENGINE,
                representation=DIRECT_REPRESENTATION,
                engine_run=engine_run,
                selected=selected.artifacts,
            )
        except EngineRolloutArtifactError as exc:
            failure = ParentRunFailure(
                "parent_artifact_publish_failed",
                attempt_refs=tuple(refs),
            )
            logger.error(
                "event=engine.parent_run.failed run_id=%s project_id=%s "
                "attempt_id=- attempt_index=- stage=parent_artifact_publish "
                "error_code=%s error_type=%s retryable=false suppressed=false "
                "attempt_refs=%s",
                request.parent_run_id,
                request.project_id,
                failure.code,
                type(exc).__name__,
                [item.to_dict() for item in failure.attempt_refs],
            )
            raise failure from exc
        base = f"/api/shader/runs/{request.parent_run_id}/artifacts"
        payload = {
            **selected.response_payload,
            "project_id": request.project_id,
            "run_id": str(request.parent_run_id),
            "engine": DIRECT_ENGINE,
            "representation": DIRECT_REPRESENTATION,
            "engine_run": engine_run,
            "final_render_url": f"{base}/final-render",
            "metrics_url": f"{base}/metrics",
            "manifest_url": f"{base}/manifest",
        }
        return ParentRunResult(
            response_payload=payload,
            engine=DIRECT_ENGINE,
            representation=DIRECT_REPRESENTATION,
            engine_run=engine_run,
            published_artifacts=published,
        )

    @staticmethod
    def _publish_retry(
        request: ParentRunRequest,
        *,
        attempt_index: int,
        failure_code: str,
    ) -> None:
        if request.progress_callback is None:
            return
        try:
            request.progress_callback(
                {
                    "node": "engine_rollout",
                    "phase": "engine_retry",
                    "status": "running",
                    "engine": DIRECT_ENGINE,
                    "attempt_index": attempt_index,
                    "failure_code": failure_code,
                },
                None,
            )
        except Exception as exc:
            cause_types, stack_frames = safe_exception_diagnostics(exc)
            logger.warning(
                "event=engine.progress_callback.failed run_id=%s project_id=%s "
                "attempt_id=%s attempt_index=%s stage=progress_callback "
                "error_code=progress_callback_failed error_type=%s "
                "cause_type_chain=%s stack_frames=%s retryable=true suppressed=true",
                request.parent_run_id,
                request.project_id,
                child_attempt_id(request.parent_run_id, attempt_index),
                attempt_index,
                type(exc).__name__,
                cause_types,
                stack_frames,
            )

    async def _execute_attempt(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        executor: EngineAttemptExecutor | None = None
        try:
            executor = self._direct_factory(context)
            result = await asyncio.wait_for(
                executor.execute(request, context),
                timeout=self._attempt_timeout_seconds,
            )
            self._validate_success(result, context)
            return result
        except EngineAttemptFailure:
            raise
        except (TimeoutError, asyncio.TimeoutError) as exc:
            cause_types, stack_frames = safe_exception_diagnostics(exc)
            logger.error(
                "event=engine.attempt.failed run_id=%s project_id=%s "
                "attempt_id=%s attempt_index=%s stage=engine_attempt "
                "error_code=engine_attempt_timeout error_type=%s "
                "cause_type_chain=%s stack_frames=%s retryable=true suppressed=false",
                request.parent_run_id,
                request.project_id,
                context.attempt_id,
                context.attempt_index,
                type(exc).__name__,
                cause_types,
                stack_frames,
            )
            raise EngineAttemptFailure("engine_attempt_timeout") from exc
        except Exception as exc:
            cause_types, stack_frames = safe_exception_diagnostics(exc)
            logger.error(
                "event=engine.attempt.failed run_id=%s project_id=%s "
                "attempt_id=%s attempt_index=%s stage=engine_attempt "
                "error_code=engine_attempt_failed error_type=%s "
                "cause_type_chain=%s stack_frames=%s retryable=true suppressed=false",
                request.parent_run_id,
                request.project_id,
                context.attempt_id,
                context.attempt_index,
                type(exc).__name__,
                cause_types,
                stack_frames,
            )
            raise EngineAttemptFailure("engine_attempt_failed") from exc
        finally:
            if executor is not None:
                try:
                    closed = executor.close()
                    if inspect.isawaitable(closed):
                        await asyncio.wait_for(
                            closed,
                            timeout=self._close_timeout_seconds,
                        )
                except Exception as exc:
                    cause_types, stack_frames = safe_exception_diagnostics(exc)
                    logger.warning(
                        "event=engine.attempt.close_failed run_id=%s project_id=%s "
                        "attempt_id=%s attempt_index=%s stage=executor_close "
                        "error_code=engine_attempt_close_failed error_type=%s "
                        "cause_type_chain=%s stack_frames=%s retryable=true "
                        "suppressed=true",
                        request.parent_run_id,
                        request.project_id,
                        context.attempt_id,
                        context.attempt_index,
                        type(exc).__name__,
                        cause_types,
                        stack_frames,
                    )

    @staticmethod
    def _validate_success(
        result: EngineAttemptSuccess,
        context: EngineAttemptContext,
    ) -> None:
        if (
            result.attempt_id != context.attempt_id
            or result.engine != DIRECT_ENGINE
            or result.representation != DIRECT_REPRESENTATION
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
            str(context.attempt_id),
            DIRECT_ENGINE,
            DIRECT_REPRESENTATION,
            "succeeded",
        )

    @staticmethod
    def _failure_ref(
        context: EngineAttemptContext,
        failure_code: str,
    ) -> AttemptRef:
        return AttemptRef(
            str(context.attempt_id),
            DIRECT_ENGINE,
            DIRECT_REPRESENTATION,
            "failed",
            failure_code,
        )


__all__ = [
    "AttemptExecutorFactory",
    "AttemptRef",
    "DIRECT_ENGINE",
    "DIRECT_REPRESENTATION",
    "EngineAttemptContext",
    "EngineAttemptExecutor",
    "EngineAttemptFailure",
    "EngineAttemptSuccess",
    "EngineParentRunCoordinator",
    "EngineResponseContractFailure",
    "ParentRunFailure",
    "ParentRunPlan",
    "ParentRunRequest",
    "ParentRunResult",
    "child_attempt_id",
    "resolve_parent_run_plan",
]
