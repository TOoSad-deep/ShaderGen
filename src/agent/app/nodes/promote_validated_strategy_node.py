"""晋升经过 Renderer、Oracle 和 Selector 验证的 PNG-to-Shader 策略."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.runtime import Runtime

from agent.app.memory.models import (
    STRATEGY_IMPORTANCE,
    build_validated_strategy_summary,
    strategy_memory_id,
)
from agent.app.memory.store import upsert_validated_strategy_memory
from shaderforge.evaluation import CandidateRecord
from shaderforge.store import LocalArtifactStore

PromoteValidatedStrategyNode = Callable[
    [Mapping[str, Any], Runtime], Awaitable[dict[str, Any]]
]
PreviewValidatedStrategyNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class _ValidatedStrategyPlan:
    """晋升预览与真实写入共享的已校验证据."""

    preview: dict[str, Any]
    strategy_summary: str
    changed_problem_domain: str
    metric_version: str
    total_loss: float


def _validated_strategy_plan(
    state: Mapping[str, Any],
    artifact_store: LocalArtifactStore,
) -> _ValidatedStrategyPlan | None:
    """从 current_best 证据构造预览和真实写入共享的计划."""
    best_raw = state.get("current_best_record")
    final = dict(state.get("final_result", {}))
    if best_raw is None or not final.get("success"):
        return None
    best = (
        best_raw
        if isinstance(best_raw, CandidateRecord)
        else CandidateRecord.from_dict(dict(best_raw))
    )
    final_score = final.get("score_breakdown")
    if (
        final.get("project_id") != str(state["project_id"])
        or final.get("run_id") != str(state["run_id"])
        or final.get("candidate_id") != best.candidate_id
        or final.get("glsl_sha256") != best.glsl_sha256
        or final.get("render_sha256") != best.render_sha256
        or not isinstance(final_score, Mapping)
        or best.score_summary is None
        or float(final_score.get("total_loss", float("nan")))
        != best.score_summary.total_loss
    ):
        raise RuntimeError("final_result 未与 current_best 证据完整绑定。")
    if (
        not best.hard_constraints_passed
        or best.render_ref is None
        or best.metrics_ref is None
    ):
        raise RuntimeError("禁止晋升未通过确定性门禁的策略。")
    run_store = artifact_store.start_run(
        str(state["project_id"]),
        str(state["run_id"]),
    )
    author_value = json.loads(run_store.read_bytes(best.author_ref))
    if not isinstance(author_value, dict):
        raise ValueError("Author Artifact 根节点必须是 object。")
    summary = build_validated_strategy_summary(
        str(author_value["strategy_summary"]),
        changed_problem_domain=best.changed_problem_domain,
        metric_version=best.score_summary.metric_version,
        total_loss=best.score_summary.total_loss,
    )
    return _ValidatedStrategyPlan(
        preview={
            "schema_version": 1,
            "memory_id": strategy_memory_id(best.glsl_sha256),
            "kind": "strategy",
            "project_id": str(state["project_id"]),
            "summary": summary,
            "importance": STRATEGY_IMPORTANCE,
            "source_run_id": str(state["run_id"]),
            "candidate_id": best.candidate_id,
            "glsl_sha256": best.glsl_sha256,
            "iteration": best.iteration,
            "effect_mode": "preview",
        },
        strategy_summary=str(author_value["strategy_summary"]),
        changed_problem_domain=best.changed_problem_domain,
        metric_version=best.score_summary.metric_version,
        total_loss=best.score_summary.total_loss,
    )


def build_validated_strategy_preview(
    state: Mapping[str, Any],
    artifact_store: LocalArtifactStore,
) -> dict[str, Any] | None:
    """从生产 current_best 证据构造无副作用的策略晋升计划."""
    plan = _validated_strategy_plan(state, artifact_store)
    return None if plan is None else plan.preview


def make_preview_validated_strategy_node(
    artifact_store: LocalArtifactStore,
) -> PreviewValidatedStrategyNode:
    """创建可由 Node Lab 直接调用的生产晋升预览 Node."""

    async def preview(state: Mapping[str, Any]) -> dict[str, Any]:
        events = tuple(state.get("events", ()))
        value = build_validated_strategy_preview(state, artifact_store)
        if value is None:
            return {
                "memory_preview": None,
                "events": (
                    *events,
                    {
                        "stage": "memory",
                        "event_type": "strategy_promotion_skipped",
                        "payload": {"reason": "no_validated_current_best"},
                    },
                ),
            }
        return {
            "memory_preview": value,
            "memory_status": str(state.get("memory_status", "ephemeral")),
            "events": (
                *events,
                {
                    "stage": "memory",
                    "event_type": "validated_strategy_previewed",
                    "payload": {
                        "memory_id": value["memory_id"],
                        "candidate_id": value["candidate_id"],
                        "glsl_sha256": value["glsl_sha256"],
                    },
                },
            ),
        }

    return preview


def make_promote_validated_strategy_node(
    artifact_store: LocalArtifactStore,
) -> PromoteValidatedStrategyNode:
    """创建只允许 current_best 策略进入长期 Memory 的节点."""

    async def promote(
        state: Mapping[str, Any],
        runtime: Runtime,
    ) -> dict[str, Any]:
        events = tuple(state.get("events", ()))
        plan = _validated_strategy_plan(state, artifact_store)
        if plan is None:
            return {
                "events": (
                    *events,
                    {
                        "stage": "memory",
                        "event_type": "strategy_promotion_skipped",
                        "payload": {"reason": "no_validated_current_best"},
                    },
                )
            }
        if runtime.store is None:
            return {
                "memory_status": "degraded",
                "events": (
                    *events,
                    {
                        "stage": "memory",
                        "event_type": "memory_degraded",
                        "payload": {
                            "operation": "write",
                            "error_type": "StoreMissing",
                        },
                    },
                ),
            }

        try:
            item = await upsert_validated_strategy_memory(
                runtime.store,
                project_id=str(state["project_id"]),
                source_run_id=str(state["run_id"]),
                glsl_sha256=str(plan.preview["glsl_sha256"]),
                iteration=int(plan.preview["iteration"]),
                strategy_summary=plan.strategy_summary,
                changed_problem_domain=plan.changed_problem_domain,
                metric_version=plan.metric_version,
                total_loss=plan.total_loss,
            )
        except Exception as exc:
            return {
                "memory_status": "degraded",
                "events": (
                    *events,
                    {
                        "stage": "memory",
                        "event_type": "memory_degraded",
                        "payload": {
                            "operation": "write",
                            "error_type": type(exc).__name__,
                        },
                    },
                ),
            }
        return {
            # Store 可写只代表本次晋升成功；持久性等级由服务生命周期决定。
            # 例如测试/本地进程使用 InMemoryStore 时仍应保持 ephemeral。
            "memory_status": str(state.get("memory_status", "ephemeral")),
            "events": (
                *events,
                {
                    "stage": "memory",
                    "event_type": "validated_strategy_promoted",
                    "payload": {
                        "memory_id": item.memory_id,
                        "candidate_id": plan.preview["candidate_id"],
                        "glsl_sha256": plan.preview["glsl_sha256"],
                    },
                },
            ),
        }

    return promote
