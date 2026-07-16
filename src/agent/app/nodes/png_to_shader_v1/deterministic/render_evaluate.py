"""PNG 转 Shader V1 确定性渲染评估 Node 的薄编排入口."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shaderforge.store import LocalArtifactStore

from .render_evaluate_rendering import SuccessfulRender, render_candidate
from .render_evaluate_scoring import evaluate_rendered_candidate
from .render_evaluate_validation import validate_candidate
from .runtime import (
    Clock,
    RenderEvaluator,
    RunNode,
    RunRendererRegistry,
    _record,
    _run_store,
)


def make_render_and_evaluate_node(
    artifact_store: LocalArtifactStore,
    renderer_registry: RunRendererRegistry,
    evaluator: RenderEvaluator,
    *,
    clock: Clock,
) -> RunNode:
    """按校验、渲染、评分三个确定性阶段创建 Node."""

    async def render_and_evaluate(state: Mapping[str, Any]) -> dict[str, Any]:
        record = _record(state["candidate_record"])
        store = _run_store(artifact_store, state)
        run_id = str(state["run_id"])
        project_id = str(state["project_id"])

        validation = validate_candidate(
            store,
            state,
            record,
            str(state["glsl"]),
            clock=clock,
            run_id=run_id,
            project_id=project_id,
        )
        if validation.failure_update is not None:
            return validation.failure_update

        rendered = await render_candidate(
            store,
            state,
            validation.record,
            validation.glsl,
            validation.events,
            validation.repair_update,
            renderer_registry=renderer_registry,
            clock=clock,
            run_id=run_id,
            project_id=project_id,
        )
        if not isinstance(rendered, SuccessfulRender):
            return rendered

        return await evaluate_rendered_candidate(
            store,
            state,
            rendered,
            validation.events,
            validation.repair_update,
            evaluator=evaluator,
            clock=clock,
            run_id=run_id,
            project_id=project_id,
        )

    return render_and_evaluate
