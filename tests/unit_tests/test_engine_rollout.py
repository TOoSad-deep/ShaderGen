from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from langsmith import get_tracing_context

from agent.app.services.engine_rollout_artifacts import (
    EngineRolloutArtifactService,
    SelectedEngineArtifacts,
)
from backend.app.services.engine_rollout import (
    EngineAttemptFailure,
    EngineAttemptSuccess,
    EngineParentRunCoordinator,
    ParentRunFailure,
    ParentRunRequest,
    resolve_parent_run_plan,
)
from backend.app.services.engine_rollout_graph import build_engine_parent_graph
from shaderforge.store import LocalArtifactStore

PNG = b"\x89PNG\r\n\x1a\nfake"
PARENT_GRAPH_NODES = {
    "initialize_parent",
    "execute_attempt",
    "record_attempt_outcome",
    "prepare_retry",
    "publish_parent",
    "finalize_parent",
}


class _Executor:
    def __init__(self, context, *, succeed: bool) -> None:
        self.context = context
        self.succeed = succeed
        self.closed = False

    async def execute(self, request, context):
        tracing = get_tracing_context()
        assert tracing["enabled"] is False
        assert tracing["parent"] is None
        if not self.succeed:
            raise EngineAttemptFailure("direct_attempt_failed")
        return EngineAttemptSuccess(
            attempt_id=context.attempt_id,
            engine=context.engine,
            representation=context.representation,
            response_payload={
                "glsl": "void main(){}",
                "quality_preset": "balanced",
                "stop_reason": "direct_attempt_completed",
                "render_width": 1,
                "render_height": 1,
                "pipeline": {},
            },
            artifacts=SelectedEngineArtifacts(
                final_render=PNG,
                metrics_json=b"{}",
                engine_manifest_json=b"{}",
            ),
        )

    async def close(self) -> None:
        self.closed = True


class _HangingExecutor(_Executor):
    async def execute(self, request, context):
        del request, context
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


def _artifacts(tmp_path: Path) -> EngineRolloutArtifactService:
    return EngineRolloutArtifactService(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_store=LocalArtifactStore(
            tmp_path / "private",
            restrictive_permissions=True,
        ),
        date_provider=lambda: date(2026, 8, 7),
    )


def test_parent_rollout_graph_exposes_each_core_step() -> None:
    topology = build_engine_parent_graph().get_graph()
    assert set(topology.nodes) == PARENT_GRAPH_NODES | {"__start__", "__end__"}
    assert {(edge.source, edge.target) for edge in topology.edges} == {
        ("__start__", "initialize_parent"),
        ("initialize_parent", "execute_attempt"),
        ("execute_attempt", "record_attempt_outcome"),
        ("record_attempt_outcome", "prepare_retry"),
        ("record_attempt_outcome", "publish_parent"),
        ("record_attempt_outcome", "finalize_parent"),
        ("prepare_retry", "execute_attempt"),
        ("publish_parent", "finalize_parent"),
        ("finalize_parent", "__end__"),
    }


@pytest.mark.anyio
async def test_direct_coordinator_uses_fresh_attempts_and_publishes_winner(
    tmp_path: Path,
) -> None:
    created = []
    progress_events = []

    def factory(context):
        executor = _Executor(context, succeed=context.attempt_index == 2)
        created.append(executor)
        return executor

    parent_id = uuid4()
    coordinator = EngineParentRunCoordinator(
        direct_factory=factory,
        artifacts=_artifacts(tmp_path),
        direct_attempt_limit=3,
    )
    result = await coordinator.execute(
        request=ParentRunRequest(
            parent_run_id=parent_id,
            project_id="project",
            image=b"image",
            content_type="image/png",
            instruction="",
            quality_preset="balanced",
            publication_date="2026-08-07",
            filename="../玻璃 图标.png",
            progress_callback=lambda event, render: progress_events.append(
                (event, render)
            ),
        ),
        plan=resolve_parent_run_plan(
            parent_run_id=parent_id,
            project_id="project",
        ),
    )
    assert len(created) == 3
    assert len({item.context.attempt_id for item in created}) == 3
    assert all(item.closed for item in created)
    assert [event["attempt_index"] for event, _render in progress_events] == [1, 2]
    assert [item["status"] for item in result.engine_run["attempt_refs"]] == [
        "failed",
        "failed",
        "succeeded",
    ]
    manifest = json.loads(
        (
            tmp_path
            / "public"
            / "玻璃-图标"
            / "2026-08-07"
            / str(parent_id)
            / "final"
            / "manifest.json"
        ).read_text()
    )
    assert manifest["engine"] == "direct_glsl_layerplan_v1"


@pytest.mark.anyio
async def test_three_direct_failures_return_safe_attempt_refs(tmp_path: Path) -> None:
    parent_id = uuid4()
    coordinator = EngineParentRunCoordinator(
        direct_factory=lambda context: _Executor(context, succeed=False),
        artifacts=_artifacts(tmp_path),
    )
    with pytest.raises(ParentRunFailure) as exc_info:
        await coordinator.execute(
            request=ParentRunRequest(
                parent_run_id=parent_id,
                project_id="project",
                image=b"image",
                content_type="image/png",
                instruction="",
                quality_preset="balanced",
                publication_date="2026-08-07",
            ),
            plan=resolve_parent_run_plan(
                parent_run_id=parent_id,
                project_id="project",
            ),
        )
    assert exc_info.value.code == "direct_attempts_failed"
    assert len(exc_info.value.attempt_refs) == 3
    assert not (tmp_path / "public" / ".run-index" / f"{parent_id}.json").exists()


@pytest.mark.anyio
async def test_unexpected_attempt_failure_logs_safe_location_without_message(
    tmp_path: Path,
    caplog,
) -> None:
    class ExplodingExecutor:
        async def execute(self, request, context):
            raise RuntimeError("secret-provider-or-shader-content")

        async def close(self) -> None:
            return None

    parent_id = uuid4()
    coordinator = EngineParentRunCoordinator(
        direct_factory=lambda context: ExplodingExecutor(),
        artifacts=_artifacts(tmp_path),
        direct_attempt_limit=1,
    )
    caplog.set_level(logging.ERROR, logger="backend.engine_rollout")

    with pytest.raises(ParentRunFailure) as exc_info:
        await coordinator.execute(
            request=ParentRunRequest(
                parent_run_id=parent_id,
                project_id="project",
                image=b"image",
                content_type="image/png",
                instruction="",
                quality_preset="balanced",
                publication_date="2026-08-07",
            ),
            plan=resolve_parent_run_plan(
                parent_run_id=parent_id,
                project_id="project",
            ),
        )

    assert [item.failure_code for item in exc_info.value.attempt_refs] == [
        "engine_attempt_failed"
    ]
    assert "event=engine.attempt.failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "test_engine_rollout.py" in caplog.text
    assert "secret-provider-or-shader-content" not in caplog.text


@pytest.mark.anyio
async def test_attempt_timeout_is_recorded_and_fresh_executor_is_closed(
    tmp_path: Path,
) -> None:
    created = []

    def factory(context):
        executor = _HangingExecutor(context, succeed=False)
        created.append(executor)
        return executor

    parent_id = uuid4()
    coordinator = EngineParentRunCoordinator(
        direct_factory=factory,
        artifacts=_artifacts(tmp_path),
        attempt_timeout_seconds=0.01,
        direct_attempt_limit=1,
    )
    with pytest.raises(ParentRunFailure) as exc_info:
        await coordinator.execute(
            request=ParentRunRequest(
                parent_run_id=parent_id,
                project_id="project",
                image=b"image",
                content_type="image/png",
                instruction="",
                quality_preset="balanced",
                publication_date="2026-08-07",
            ),
            plan=resolve_parent_run_plan(
                parent_run_id=parent_id,
                project_id="project",
            ),
        )

    assert [item.failure_code for item in exc_info.value.attempt_refs] == [
        "engine_attempt_timeout"
    ]
    assert len(created) == 1
    assert created[0].closed is True
