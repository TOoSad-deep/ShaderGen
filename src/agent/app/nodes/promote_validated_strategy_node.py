"""晋升经过 Renderer、Oracle 和 Selector 验证的 PNG-to-Shader 策略."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langgraph.runtime import Runtime

from agent.app.memory.store import upsert_validated_strategy_memory
from shaderforge.evaluation import CandidateRecord
from shaderforge.store import LocalArtifactStore

PromoteValidatedStrategyNode = Callable[
    [Mapping[str, Any], Runtime], Awaitable[dict[str, Any]]
]


def make_promote_validated_strategy_node(
    artifact_store: LocalArtifactStore,
) -> PromoteValidatedStrategyNode:
    """创建只允许 current_best 策略进入长期 Memory 的节点."""

    async def promote(
        state: Mapping[str, Any],
        runtime: Runtime,
    ) -> dict[str, Any]:
        events = tuple(state.get("events", ()))
        best_raw = state.get("current_best_record")
        final = dict(state.get("final_result", {}))
        if best_raw is None or not final.get("success"):
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
        best = (
            best_raw
            if isinstance(best_raw, CandidateRecord)
            else CandidateRecord.from_dict(dict(best_raw))
        )
        if (
            not best.hard_constraints_passed
            or best.score_summary is None
            or best.render_ref is None
            or best.metrics_ref is None
        ):
            raise RuntimeError("禁止晋升未通过确定性门禁的策略。")
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

        run_store = artifact_store.start_run(
            str(state["project_id"]),
            str(state["run_id"]),
        )
        author_value = json.loads(run_store.read_bytes(best.author_ref))
        if not isinstance(author_value, dict):
            raise ValueError("Author Artifact 根节点必须是 object。")
        try:
            item = await upsert_validated_strategy_memory(
                runtime.store,
                project_id=str(state["project_id"]),
                source_run_id=str(state["run_id"]),
                glsl_sha256=best.glsl_sha256,
                iteration=best.iteration,
                strategy_summary=str(author_value["strategy_summary"]),
                changed_problem_domain=best.changed_problem_domain,
                metric_version=best.score_summary.metric_version,
                total_loss=best.score_summary.total_loss,
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
                        "candidate_id": best.candidate_id,
                        "glsl_sha256": best.glsl_sha256,
                    },
                },
            ),
        }

    return promote
