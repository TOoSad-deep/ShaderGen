from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest

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
from shaderforge.store import LocalArtifactStore

PNG = b"\x89PNG\r\n\x1a\nfake"


class _Executor:
    def __init__(self, context, *, succeed: bool) -> None:
        self.context = context
        self.succeed = succeed
        self.closed = False

    async def execute(self, request, context):
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


def _artifacts(tmp_path: Path) -> EngineRolloutArtifactService:
    return EngineRolloutArtifactService(
        public_store=LocalArtifactStore(tmp_path / "public"),
        private_attempt_store=LocalArtifactStore(
            tmp_path / "private",
            restrictive_permissions=True,
        ),
    )


@pytest.mark.anyio
async def test_direct_coordinator_uses_fresh_attempts_and_publishes_winner(
    tmp_path: Path,
) -> None:
    created = []

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
        ),
        plan=resolve_parent_run_plan(
            parent_run_id=parent_id,
            project_id="project",
        ),
    )
    assert len(created) == 3
    assert len({item.context.attempt_id for item in created}) == 3
    assert all(item.closed for item in created)
    assert [item["status"] for item in result.engine_run["attempt_refs"]] == [
        "failed",
        "failed",
        "succeeded",
    ]
    manifest = json.loads(
        (
            tmp_path / "public" / "project" / str(parent_id) / "final" / "manifest.json"
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
            ),
            plan=resolve_parent_run_plan(
                parent_run_id=parent_id,
                project_id="project",
            ),
        )
    assert exc_info.value.code == "direct_attempts_failed"
    assert len(exc_info.value.attempt_refs) == 3
    assert not (tmp_path / "public" / "project" / str(parent_id)).exists()


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

    with pytest.raises(ParentRunFailure):
        await coordinator.execute(
            request=ParentRunRequest(
                parent_run_id=parent_id,
                project_id="project",
                image=b"image",
                content_type="image/png",
                instruction="",
                quality_preset="balanced",
            ),
            plan=resolve_parent_run_plan(
                parent_run_id=parent_id,
                project_id="project",
            ),
        )

    assert "event=engine.attempt.failed" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "test_engine_rollout.py" in caplog.text
    assert "secret-provider-or-shader-content" not in caplog.text
