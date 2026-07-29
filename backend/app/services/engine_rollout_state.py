"""Contracts, runtime context, and private state for the parent rollout graph."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict
from uuid import UUID, uuid5

from agent.app.services.engine_rollout_artifacts import (
    EngineId,
    EngineRolloutArtifactService,
    PublishedParentArtifacts,
    Representation,
    SelectedEngineArtifacts,
)

AttemptStatus = Literal["succeeded", "failed"]
DIRECT_ENGINE: EngineId = "direct_glsl_layerplan_v1"
DIRECT_REPRESENTATION: Representation = "shader_program_spec_v1"
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class EngineRolloutError(RuntimeError):
    """Base error carrying one safe parent-rollout code."""

    def __init__(self, code: str) -> None:
        """Validate and retain one stable safe code."""
        if not _SAFE_CODE.fullmatch(code):
            raise ValueError("rollout error code must be a safe identifier")
        self.code = code
        super().__init__(code)


class EngineAttemptFailure(EngineRolloutError):
    """One isolated Direct attempt failed safely."""


class EngineResponseContractFailure(EngineRolloutError):
    """An attempt response failed the public response contract."""

    def __init__(self, field: str) -> None:
        """Retain a sanitized field name and stable failure code."""
        self.field = (
            field if re.fullmatch(r"[A-Za-z0-9_.]{1,160}", field) else "unknown"
        )
        super().__init__("engine_response_contract_failed")


@dataclass(frozen=True, slots=True)
class AttemptRef:
    """Safe parent-visible reference to one private child attempt."""

    attempt_id: str
    engine: EngineId
    representation: Representation
    status: AttemptStatus
    failure_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the stable JSON-safe attempt summary."""
        return {
            "attempt_id": self.attempt_id,
            "engine": self.engine,
            "representation": self.representation,
            "status": self.status,
            "failure_code": self.failure_code,
        }


class ParentRunFailure(EngineRolloutError):
    """The parent run cannot publish a successful result."""

    def __init__(self, code: str, *, attempt_refs: tuple[AttemptRef, ...]) -> None:
        """Retain the safe child references accumulated before failure."""
        self.attempt_refs = attempt_refs
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ParentRunPlan:
    """Frozen identity of the only supported parent execution plan."""

    parent_run_id: UUID
    project_id: str


@dataclass(frozen=True, slots=True)
class EngineAttemptContext:
    """Identity and isolation boundary for one fresh child attempt."""

    parent_run_id: UUID
    attempt_id: UUID
    attempt_index: int
    engine: EngineId = DIRECT_ENGINE
    representation: Representation = DIRECT_REPRESENTATION
    artifact_scope: Literal["private_attempt"] = "private_attempt"


@dataclass(frozen=True, slots=True)
class ParentRunRequest:
    """Immutable input shared by all fresh attempts in one parent run."""

    parent_run_id: UUID
    project_id: str
    image: bytes
    content_type: str
    instruction: str
    quality_preset: str
    progress_callback: Callable[[dict[str, Any], bytes | None], None] | None = None


@dataclass(frozen=True, slots=True)
class EngineAttemptSuccess:
    """Validated private result returned by one attempt executor."""

    attempt_id: UUID
    engine: EngineId
    representation: Representation
    response_payload: dict[str, Any]
    artifacts: SelectedEngineArtifacts
    artifact_scope: Literal["private_attempt"] = "private_attempt"


class EngineAttemptExecutor(Protocol):
    """Fresh executor owned by exactly one child attempt."""

    async def execute(
        self,
        request: ParentRunRequest,
        context: EngineAttemptContext,
    ) -> EngineAttemptSuccess:
        """Execute the isolated child attempt."""
        ...

    async def close(self) -> None:
        """Release attempt-local resources."""
        ...


AttemptExecutorFactory = Callable[[EngineAttemptContext], EngineAttemptExecutor]


@dataclass(frozen=True, slots=True)
class ParentRunResult:
    """Stable successful parent result consumed by the Backend runtime."""

    response_payload: dict[str, Any]
    engine: EngineId
    representation: Representation
    engine_run: dict[str, Any]
    published_artifacts: PublishedParentArtifacts


@dataclass(frozen=True, slots=True)
class EngineParentGraphContext:
    """Non-serializable dependencies and fixed rollout limits."""

    direct_factory: AttemptExecutorFactory
    artifacts: EngineRolloutArtifactService
    attempt_timeout_seconds: float
    close_timeout_seconds: float
    direct_attempt_limit: int


class EngineParentGraphInput(TypedDict):
    """Invocation input for one parent rollout."""

    request: ParentRunRequest
    plan: ParentRunPlan


class EngineParentGraphOutput(TypedDict):
    """Successful output of the parent rollout graph."""

    result: ParentRunResult
    completed_nodes: tuple[str, ...]


class EngineParentState(TypedDict, total=False):
    """Private state for the bounded parent rollout."""

    request: ParentRunRequest
    plan: ParentRunPlan
    attempt_index: int
    attempt_context: EngineAttemptContext
    attempt_success: EngineAttemptSuccess | None
    attempt_failure_code: str | None
    attempt_refs: tuple[AttemptRef, ...]
    selected: EngineAttemptSuccess | None
    engine_run: dict[str, Any]
    published_artifacts: PublishedParentArtifacts
    completed_nodes: tuple[str, ...]
    result: ParentRunResult


def child_attempt_id(parent_run_id: UUID, attempt_index: int) -> UUID:
    """Derive the stable private child identity for one attempt index."""
    if isinstance(attempt_index, bool) or attempt_index < 0:
        raise ValueError("attempt_index must be a non-negative integer")
    return uuid5(parent_run_id, f"{DIRECT_ENGINE}:{attempt_index}")


def resolve_parent_run_plan(*, parent_run_id: UUID, project_id: str) -> ParentRunPlan:
    """Freeze the only supported execution plan."""
    return ParentRunPlan(parent_run_id=parent_run_id, project_id=project_id)


__all__ = [
    "AttemptExecutorFactory",
    "AttemptRef",
    "AttemptStatus",
    "DIRECT_ENGINE",
    "DIRECT_REPRESENTATION",
    "EngineAttemptContext",
    "EngineAttemptExecutor",
    "EngineAttemptFailure",
    "EngineAttemptSuccess",
    "EngineParentGraphContext",
    "EngineParentGraphInput",
    "EngineParentGraphOutput",
    "EngineParentState",
    "EngineResponseContractFailure",
    "EngineRolloutError",
    "ParentRunFailure",
    "ParentRunPlan",
    "ParentRunRequest",
    "ParentRunResult",
    "child_attempt_id",
    "resolve_parent_run_plan",
]
