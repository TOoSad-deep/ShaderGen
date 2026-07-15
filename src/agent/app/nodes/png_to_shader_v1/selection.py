"""PNG 转 Shader V1 的候选选择、回载与复核持久化节点."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from typing import Any

from agent.app.contracts.png_to_shader_v1 import (
    CandidateRecordInput,
)
from shaderforge.evaluation import (
    ScoreBreakdownV1,
    select_current_best,
)
from shaderforge.store import LocalArtifactStore

from .runtime import (
    RunNode,
    _acceptance,
    _read_json,
    _record,
    _replace_record,
    _run_store,
    _write_candidate_manifest,
)


def make_select_current_best_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建 current_best 单调接受节点."""

    async def select(state: Mapping[str, Any]) -> dict[str, Any]:
        candidate = _record(state["candidate_record"])
        current_raw = state.get("current_best_record")
        current = None if current_raw is None else _record(current_raw)
        decision = select_current_best(current, candidate, _acceptance(state))
        store = _run_store(artifact_store, state)
        decision_ref = store.write_json(
            f"candidates/{candidate.candidate_id}/selection.json",
            decision.to_dict(),
        )
        update: dict[str, Any] = {
            "phase": "candidate_selected",
            "selection_decision": decision.to_dict(),
            "selection_ref": decision_ref.relative_path,
            "events": (
                *state.get("events", ()),
                {
                    "stage": "selection",
                    "event_type": (
                        "current_best_updated"
                        if decision.accepted
                        else "candidate_rejected"
                    ),
                    "payload": {
                        "candidate_id": candidate.candidate_id,
                        **decision.to_dict(),
                    },
                },
            ),
        }
        if decision.accepted:
            if candidate.score_summary is None:
                raise RuntimeError("被接受候选缺少 score_summary。")
            update.update(
                {
                    "current_best_record": candidate,
                    "current_best_id": candidate.candidate_id,
                    "current_best_glsl_sha256": candidate.glsl_sha256,
                    "current_best_total_loss": candidate.score_summary.total_loss,
                    "current_best_score_summary": candidate.score_summary.to_dict(),
                    "no_improvement_count": 0,
                    "iteration": candidate.iteration,
                }
            )
        elif current is not None:
            if current.score_summary is None:
                raise RuntimeError("既有 current_best 缺少 score_summary。")
            update.update(
                {
                    "current_best_record": current,
                    "current_best_id": current.candidate_id,
                    "current_best_glsl_sha256": current.glsl_sha256,
                    "current_best_total_loss": current.score_summary.total_loss,
                    "current_best_score_summary": current.score_summary.to_dict(),
                    "iteration": current.iteration,
                }
            )
            if candidate.origin != "deterministic":
                update["no_improvement_count"] = (
                    int(state.get("no_improvement_count", 0)) + 1
                )
        return update

    return select


def _residual_summary(score: ScoreBreakdownV1) -> dict[str, Any]:
    worst_rois = sorted(score.roi_losses, key=lambda item: (-item[1], item[0]))[:3]
    return {
        "total_loss": score.total_loss,
        "global_rmse": score.global_rmse,
        "edge_loss": score.edge_loss,
        "geometry_loss": score.geometry_loss,
        "worst_rois": [
            {"region_id": region_id, "loss": loss} for region_id, loss in worst_rois
        ],
        "diagnostics": list(score.diagnostics),
    }


def make_load_current_best_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建只从 current_best Artifact 恢复 Critic/Refine 输入的节点."""

    async def load(state: Mapping[str, Any]) -> dict[str, Any]:
        best = _record(state["current_best_record"])
        if (
            not best.hard_constraints_passed
            or best.score_summary is None
            or best.render_ref is None
        ):
            raise RuntimeError("current_best 不是可运行候选。")
        store = _run_store(artifact_store, state)
        glsl = store.read_bytes(best.glsl_ref).decode("utf-8")
        rendered = store.read_bytes(best.render_ref)
        if sha256(glsl.encode("utf-8")).hexdigest() != best.glsl_sha256:
            raise RuntimeError("current_best GLSL Artifact hash 不一致。")
        if sha256(rendered).hexdigest() != best.render_sha256:
            raise RuntimeError("current_best Render Artifact hash 不一致。")
        author = _read_json(store, best.author_ref)
        candidate_input = CandidateRecordInput(
            candidate_id=best.candidate_id,
            parent_candidate_id=best.parent_candidate_id,
            glsl_sha256=best.glsl_sha256,
            render_sha256=best.render_sha256,
            prompt_version=best.prompt_version,
            model_ref=best.model_ref,
            iteration=best.iteration,
            origin=best.origin,
            generator_version=best.generator_version,
        ).to_dict()
        binding = {
            "candidate_id": best.candidate_id,
            "glsl_sha256": best.glsl_sha256,
            "image_sha256": best.render_sha256,
        }
        return {
            "phase": "current_best_loaded",
            "candidate_record": best,
            "author_result": author,
            "glsl": glsl,
            "rendered_image": rendered,
            "rendered_content_type": "image/png",
            "score_breakdown": best.score_summary.to_dict(),
            "residual_summary": _residual_summary(best.score_summary),
            "current_candidate": candidate_input,
            "current_best_candidate": candidate_input,
            "render_evidence_binding": binding,
        }

    return load


def make_persist_visual_review_node(artifact_store: LocalArtifactStore) -> RunNode:
    """创建把 Critic 结果绑定并写回 current_best manifest 的节点."""

    async def persist(state: Mapping[str, Any]) -> dict[str, Any]:
        best = _record(state["current_best_record"])
        review = dict(state["visual_review"])
        if str(review["candidate_id"]) != best.candidate_id:
            raise ValueError("VisualReview 未绑定 current_best。")
        store = _run_store(artifact_store, state)
        review_sequence = int(state.get("visual_refinement_count", 0)) + 1
        review_ref = store.write_json(
            f"candidates/{best.candidate_id}/reviews/review-{review_sequence:04d}.json",
            review,
        )
        updated = replace(best, review_ref=review_ref.relative_path)
        _write_candidate_manifest(store, updated)
        return {
            "phase": "review_persisted",
            "current_best_record": updated,
            "candidate_record": updated,
            "candidate_records": _replace_record(
                tuple(state.get("candidate_records", ())), updated
            ),
            "events": (
                *state.get("events", ()),
                {
                    "stage": "visual_critic",
                    "event_type": "review_persisted",
                    "payload": {
                        "candidate_id": best.candidate_id,
                        "artifact_ref": review_ref.relative_path,
                        "review_sequence": review_sequence,
                    },
                },
            ),
        }

    return persist
