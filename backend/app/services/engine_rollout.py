"""Stable service facade for the Direct-only parent rollout graph."""

from __future__ import annotations

from agent.app.services.engine_rollout_artifacts import EngineRolloutArtifactService
from backend.app.services.engine_rollout_state import (
    DIRECT_ENGINE,
    DIRECT_REPRESENTATION,
    AttemptExecutorFactory,
    AttemptRef,
    EngineAttemptContext,
    EngineAttemptExecutor,
    EngineAttemptFailure,
    EngineAttemptSuccess,
    EngineParentGraphContext,
    EngineResponseContractFailure,
    EngineRolloutError,
    ParentRunFailure,
    ParentRunPlan,
    ParentRunRequest,
    ParentRunResult,
    child_attempt_id,
    resolve_parent_run_plan,
)
from shaderforge.config import RUNTIME_TIMEOUTS


class EngineParentRunCoordinator:
    """Invoke the bounded parent LangGraph with stable injected dependencies."""

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
        """Run the explicit initialize/attempt/retry/publish/finalize graph."""
        from backend.app.services.engine_rollout_graph import (
            run_engine_parent_graph,
        )

        output = await run_engine_parent_graph(
            request=request,
            plan=plan,
            context=EngineParentGraphContext(
                direct_factory=self._direct_factory,
                artifacts=self._artifacts,
                attempt_timeout_seconds=self._attempt_timeout_seconds,
                close_timeout_seconds=self._close_timeout_seconds,
                direct_attempt_limit=self._direct_attempt_limit,
            ),
        )
        return output["result"]


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
    "EngineRolloutError",
    "ParentRunFailure",
    "ParentRunPlan",
    "ParentRunRequest",
    "ParentRunResult",
    "child_attempt_id",
    "resolve_parent_run_plan",
]
