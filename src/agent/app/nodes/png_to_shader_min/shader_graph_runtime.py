"""ShaderGraph 产品候选的编译、实渲染与不可回退快照边界."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, cast

from langchain_core.messages import SystemMessage

from agent.app.config.png_to_shader_min import (
    MAX_MIN_OPTIMIZATION_ITEMS,
    required_shader_graph_program_compiles,
)
from agent.app.contracts.llm import LLMGateway
from agent.app.contracts.shader_graph_author import (
    ShaderGraphAuthorPatch,
    apply_shader_graph_author_patch,
    summarize_shader_graph_author_patch,
)
from agent.app.messages.structured_multimodal import (
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.nodes.png_to_shader_min.model_author import (
    invoke_min_author,
    remaining_llm_calls,
)
from agent.app.nodes.png_to_shader_min.runtime import (
    MinRendererRegistry,
    _bounded_append,
    _encode_rgb_png,
    _metric_deltas,
    _raw_rgb_array,
    _trace,
    make_min_nodes,
)
from agent.app.nodes.png_to_shader_min.shader_graph_author import (
    SHADER_GRAPH_AUTHOR_INITIAL_PROMPT,
    SHADER_GRAPH_AUTHOR_REFINE_PROMPT,
    shader_graph_author_patch_json_schema,
    shader_graph_document_json_schema,
)
from agent.app.parsers.shader_graph_author import (
    parse_shader_graph_author_patch,
    parse_shader_graph_document,
)
from shaderforge.dsl import (
    CANVAS_BLOCK,
    CompiledDslShader,
    ShaderDocument,
    compile_dsl_shader,
)
from shaderforge.evaluation import (
    MIN_SCENE_METRIC_VERSION,
    dominant_metric_component,
    evaluate_min_scene,
    summarize_spatial_residual,
)
from shaderforge.optimization import dsl_parameter_specs, replace_dsl_parameter
from shaderforge.rendering import GraphProgramBudgetError, GraphProgramKey
from shaderforge.store import LocalArtifactStore

SHADER_GRAPH_RENDERER_PATH = "compiled_graph_program_cache_v1"
SHADER_GRAPH_SELECTION_POLICY = "strict_total_loss_v1"


@dataclass(frozen=True)
class ShaderGraphCandidateSnapshot:
    """绑定文档、Compiler 产物、真实像素与评分的不可变候选."""

    document: ShaderDocument
    compiled: CompiledDslShader
    program_key: GraphProgramKey
    mae: float
    loss: float
    metrics: dict[str, Any]
    residual_summary: dict[str, Any]
    render: bytes
    parent_document_sha256: str | None
    provenance: str

    def public_document(self) -> dict[str, Any]:
        """返回可进入 State/API/Artifact 的严格 ShaderGraph JSON."""
        return self.document.model_dump(mode="json", by_alias=True)


def _program_key(
    document: ShaderDocument,
    compiled: CompiledDslShader,
) -> GraphProgramKey:
    """构造绑定实际 specialized source 与 active manifest 的 program key."""
    return GraphProgramKey(
        compiler_version=compiled.compiler_version,
        topology_sha256=compiled.topology_sha256,
        active_parameter_manifest_sha256=compiled.parameter_manifest_sha256,
        baked_parameter_sha256=compiled.glsl_sha256,
        width=document.canvas.width,
        height=document.canvas.height,
    )


def _active_layer_summary(document: ShaderDocument) -> list[dict[str, Any]]:
    """返回不含参数值的 Layer 身份摘要."""
    return [
        {
            "layer_id": layer.id,
            "shape_type": layer.shape.kind,
            "fill_type": layer.fill.kind,
            "effect_types": [effect.kind for effect in layer.effects],
        }
        for layer in document.layers
        if layer.visible
    ]


async def evaluate_shader_graph(
    state: dict[str, Any],
    document: ShaderDocument,
    registry: MinRendererRegistry,
    *,
    active_block: str | None = None,
    capture_png: bool = False,
) -> dict[str, Any]:
    """在 compile/render 预算内真实执行一个 ShaderGraph 候选."""
    render_count = int(state.get("render_count", 0))
    if render_count >= int(state.get("render_budget", 0)):
        raise RuntimeError("render_budget_exhausted")
    compiled = compile_dsl_shader(document, active_block=active_block)
    key = _program_key(document, compiled)
    max_compiles = required_shader_graph_program_compiles(
        int(state.get("llm_budget", 0)),
        int(state.get("refine_budget", 0)),
    )
    try:
        prepared = await registry.prepare_graph(
            str(state["project_id"]),
            str(state["run_id"]),
            key,
            compiled.fragment_source,
            compiled.uniform_schema,
            max_compiles=max_compiles,
        )
    except GraphProgramBudgetError:
        return {
            "success": False,
            "render_count": render_count,
            "compiled": compiled,
            "program_key": key,
            "error": "graph_program_budget_exhausted",
        }
    result = await prepared.render_uniforms(
        compiled.uniform_values,
        capture_png=capture_png,
    )
    render_count += 1
    if not result.success or result.rgb_bytes is None:
        return {
            "success": False,
            "render_count": render_count,
            "compiled": compiled,
            "program_key": key,
            "error": result.draw_error or "render_failed",
        }
    rendered = _raw_rgb_array(
        result.rgb_bytes,
        document.canvas.width,
        document.canvas.height,
    )
    background = document.canvas.background[:3]
    metric = evaluate_min_scene(state["target_rgb"], rendered, background)
    residual = summarize_spatial_residual(state["target_rgb"], rendered)
    residual["dominant_metric_component"] = dominant_metric_component(metric)
    residual["active_layer_summary"] = _active_layer_summary(document)
    return {
        "success": True,
        "render_count": render_count,
        "compiled": compiled,
        "program_key": key,
        "rgb": result.rgb_bytes,
        "image": result.image_bytes,
        "mae": metric.global_mae,
        "loss": metric.total_loss,
        "metrics": metric.to_dict(),
        "residual_summary": residual,
    }


def candidate_from_graph_outcome(
    document: ShaderDocument,
    outcome: dict[str, Any],
    *,
    parent_document_sha256: str | None,
    provenance: str,
) -> ShaderGraphCandidateSnapshot:
    """把成功 draw 收敛为不可变 CandidateSnapshot."""
    if not outcome.get("success"):
        raise ValueError("失败的 ShaderGraph draw 不能生成 CandidateSnapshot。")
    return ShaderGraphCandidateSnapshot(
        document=document,
        compiled=outcome["compiled"],
        program_key=outcome["program_key"],
        mae=float(outcome["mae"]),
        loss=float(outcome["loss"]),
        metrics=dict(outcome["metrics"]),
        residual_summary=dict(outcome["residual_summary"]),
        render=_encode_rgb_png(
            outcome["rgb"],
            document.canvas.width,
            document.canvas.height,
        ),
        parent_document_sha256=parent_document_sha256,
        provenance=provenance,
    )


def snapshot_summary(snapshot: ShaderGraphCandidateSnapshot) -> dict[str, Any]:
    """返回不含像素和完整 GLSL 的 CandidateSnapshot 公开摘要."""
    return {
        "document_sha256": snapshot.compiled.document_sha256,
        "topology_sha256": snapshot.compiled.topology_sha256,
        "compiler_version": snapshot.compiled.compiler_version,
        "glsl_sha256": snapshot.compiled.glsl_sha256,
        "parameter_manifest_sha256": snapshot.compiled.parameter_manifest_sha256,
        "parent_document_sha256": snapshot.parent_document_sha256,
        "provenance": snapshot.provenance,
        "selection_policy": SHADER_GRAPH_SELECTION_POLICY,
        "mae": snapshot.mae,
        "loss": snapshot.loss,
        "resource_summary": snapshot.compiled.resource_summary.to_dict(),
    }


def patch_fingerprint(payload: dict[str, Any]) -> str:
    """为 typed patch 摘要提供稳定短路去重指纹."""
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _parameter_blocks(document: ShaderDocument) -> tuple[str, ...]:
    """按 Layer 顺序返回稳定优化 block，canvas 由 base 节点单独处理."""
    blocks: list[str] = []
    for spec in dsl_parameter_specs(document):
        if spec.block == CANVAS_BLOCK or spec.block in blocks:
            continue
        blocks.append(spec.block)
    return tuple(blocks[:MAX_MIN_OPTIMIZATION_ITEMS])


async def _optimize_block(
    state: dict[str, Any],
    registry: MinRendererRegistry,
    *,
    block: str,
    max_draws: int,
) -> tuple[ShaderGraphCandidateSnapshot, int, str | None]:
    """对一个稳定 block 做 current±step 的小邻域严格改善."""
    best = cast(ShaderGraphCandidateSnapshot, state["current_best"])
    render_count = int(state["render_count"])
    accepted_path: str | None = None
    draws = 0
    specs = tuple(
        spec for spec in dsl_parameter_specs(best.document) if spec.block == block
    )
    for original in specs:
        if draws >= max_draws or render_count >= int(state["render_budget"]):
            break
        if ".transform.rotation." in original.path:
            # rotation=(cos,sin) 是成对单位向量；V1 参数热路径暂不拆成两个标量。
            continue
        current_specs = {item.path: item for item in dsl_parameter_specs(best.document)}
        spec = current_specs[original.path]
        for value in (spec.value - spec.step, spec.value + spec.step):
            if draws >= max_draws or render_count >= int(state["render_budget"]):
                break
            if value < spec.minimum or value > spec.maximum:
                continue
            try:
                candidate_document = replace_dsl_parameter(
                    best.document,
                    spec.path,
                    value,
                )
            except ValueError:
                continue
            outcome = await evaluate_shader_graph(
                {**state, "render_count": render_count},
                candidate_document,
                registry,
                active_block=block,
            )
            render_count = int(outcome["render_count"])
            draws += 1
            if outcome.get("success") and float(outcome["loss"]) < best.loss:
                best = candidate_from_graph_outcome(
                    candidate_document,
                    outcome,
                    parent_document_sha256=best.compiled.document_sha256,
                    provenance=f"parameter:{spec.path}",
                )
                accepted_path = spec.path
    return best, render_count, accepted_path


def make_shader_graph_nodes(
    artifacts: LocalArtifactStore,
    registry: MinRendererRegistry,
    gateway: LLMGateway,
) -> dict[str, Callable[..., Any]]:
    """沿用现有 12 节点拓扑，替换为 ShaderGraph 领域文档与候选边界."""
    nodes = make_min_nodes(artifacts, registry, gateway, None)

    async def author_initial(state: dict[str, Any]) -> dict[str, Any]:
        # 产品热路径直接消费感知阶段产出的 ShaderDocument fallback，
        # 不再经过 MinScene 中间表示；legacy Builder 仍使用 runtime.py。
        fallback = ShaderDocument.model_validate(state["fallback_shader_graph"])
        remaining = remaining_llm_calls(state)
        if remaining <= 0:
            return {
                "phase": "author_initial",
                "scene": fallback.model_dump(mode="json", by_alias=True),
                "fallback_shader_graph": fallback.model_dump(
                    mode="json", by_alias=True
                ),
                "trace": _trace(
                    state,
                    "author_initial",
                    "模型预算为 0，使用感知直接产出的 ShaderGraph fallback。",
                    author_source="perception_fallback",
                ),
            }
        schema = shader_graph_document_json_schema()
        content = [
            text_part("perception", state.get("perception", {})),
            text_part("fallback_shader_graph", fallback),
            text_part("user_instruction", state.get("instruction", "")),
            text_part("expected_json_schema", schema),
            *labeled_image_parts(
                "reference_image",
                state["image"],
                state.get("content_type", "image/png"),
            ),
        ]
        result = await invoke_min_author(
            gateway=gateway,
            messages=[
                SystemMessage(content=SHADER_GRAPH_AUTHOR_INITIAL_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            prompt=SHADER_GRAPH_AUTHOR_INITIAL_PROMPT,
            schema=schema,
            parser=lambda text: parse_shader_graph_document(
                text,
                expected_width=fallback.canvas.width,
                expected_height=fallback.canvas.height,
            ),
            remaining_calls=remaining,
            max_output_tokens=4000,
        )
        call_count = int(state.get("llm_call_count", 0)) + result.call_count
        document = (
            result.value if isinstance(result.value, ShaderDocument) else fallback
        )
        source = (
            "model"
            if isinstance(result.value, ShaderDocument)
            else "perception_fallback"
        )
        return {
            "phase": "author_initial",
            "scene": document.model_dump(mode="json", by_alias=True),
            "fallback_shader_graph": fallback.model_dump(mode="json", by_alias=True),
            "llm_call_count": call_count,
            "author_model": result.model_ref,
            "author_error": None if source == "model" else result.error_code,
            "trace": _trace(
                state,
                "author_initial",
                "完整 ShaderDocument 已通过严格模型契约。"
                if source == "model"
                else "模型调用或解析失败，安全回退到感知 ShaderGraph。",
                author_source=source,
                model_calls=result.call_count,
                repaired=result.repaired,
                error_code=result.error_code,
                author_latency_ms=result.latency_ms,
                author_tokens=result.total_tokens,
            ),
        }

    async def materialize_shader(state: dict[str, Any]) -> dict[str, Any]:
        document = ShaderDocument.model_validate(state["scene"])
        compiled = compile_dsl_shader(document)
        return {
            "phase": "materialize",
            "materialized": compiled,
            "current_glsl": compiled.fragment_source,
            "trace": _trace(
                state,
                "materialize_shader",
                f"compiler={compiled.compiler_version}，layers={len(document.layers)}",
                document_sha256=compiled.document_sha256[:12],
                topology_sha256=compiled.topology_sha256[:12],
            ),
        }

    async def render_and_evaluate(state: dict[str, Any]) -> dict[str, Any]:
        document = ShaderDocument.model_validate(state["scene"])
        pending = state.get("pending_patch_summary")
        previous = state.get("current_best")
        if (
            isinstance(previous, ShaderGraphCandidateSnapshot)
            and pending is None
            and bool(state.get("refine_branch_resolved"))
        ):
            return {
                "phase": "render",
                "scene": previous.public_document(),
                "current_best": previous,
                "current_glsl": previous.compiled.fragment_source,
                "current_render": previous.render,
                "current_mae": previous.mae,
                "current_best_mae": previous.mae,
                "current_best_loss": previous.loss,
                "residual_summary": previous.residual_summary,
                "render_count": int(state.get("render_count", 0)),
                "feature_queue": (),
                "error": None,
                "trace": _trace(
                    state,
                    "render_and_evaluate",
                    "Refine 未产生可渲染候选，保留 current_best 且不重建参数队列。",
                    candidate_status="no_op",
                ),
            }
        if isinstance(previous, ShaderGraphCandidateSnapshot) and isinstance(
            pending, dict
        ):
            outcome = await evaluate_shader_graph(
                state,
                document,
                registry,
                capture_png=True,
            )
            if not outcome.get("success"):
                return {
                    "phase": "render",
                    "error": str(outcome.get("error", "render_failed")),
                    "render_count": int(outcome["render_count"]),
                    "trace": _trace(
                        state,
                        "render_and_evaluate",
                        "ShaderGraph typed patch 渲染失败。",
                        status="failed",
                    ),
                }
            candidate = candidate_from_graph_outcome(
                document,
                outcome,
                parent_document_sha256=previous.compiled.document_sha256,
                provenance=f"patch:{pending.get('patch_operation', 'unknown')}",
            )
            accepted = candidate.loss < previous.loss
            best = candidate if accepted else previous
            if not accepted and candidate.program_key != previous.program_key:
                await registry.discard_graph(
                    str(state["project_id"]),
                    str(state["run_id"]),
                    candidate.program_key,
                )
            evidence = {
                **pending,
                "status": "accepted" if accepted else "rejected",
                "candidate_document_sha256": candidate.compiled.document_sha256,
                "result_document_sha256": best.compiled.document_sha256,
                "loss_delta": candidate.loss - previous.loss,
                "metric_deltas": _metric_deltas(
                    {"metrics": candidate.metrics},
                    {"metrics": previous.metrics},
                ),
            }
            return {
                "phase": "render",
                "scene": best.public_document(),
                "current_best": best,
                "current_glsl": best.compiled.fragment_source,
                "current_render": best.render,
                "current_mae": candidate.mae,
                "current_best_mae": best.mae,
                "current_best_loss": best.loss,
                "residual_summary": best.residual_summary,
                "render_count": int(outcome["render_count"]),
                "feature_queue": (),
                "refine_branch_resolved": True,
                "pending_patch_summary": None,
                "patch_evidence": _bounded_append(
                    tuple(state.get("patch_evidence", ())),
                    evidence,
                ),
                "recent_rejected_patch_summaries": (
                    tuple(state.get("recent_rejected_patch_summaries", ()))
                    if accepted
                    else _bounded_append(
                        tuple(state.get("recent_rejected_patch_summaries", ())),
                        evidence,
                        limit=3,
                    )
                ),
                "trace": _trace(
                    state,
                    "render_and_evaluate",
                    f"typed layer patch {'accepted' if accepted else 'rolled_back'}，loss={best.loss:.6f}",
                    candidate_loss=candidate.loss,
                    anchor_loss=previous.loss,
                    document_sha256=best.compiled.document_sha256[:12],
                ),
            }

        candidates: list[tuple[str, ShaderDocument]] = [("model_or_fallback", document)]
        fallback_value = state.get("fallback_shader_graph")
        if isinstance(fallback_value, dict) and fallback_value != state["scene"]:
            candidates.append(
                ("perception_fallback", ShaderDocument.model_validate(fallback_value))
            )
        evaluated: list[tuple[str, ShaderGraphCandidateSnapshot]] = []
        render_count = int(state.get("render_count", 0))
        last_error: str | None = None
        for source, candidate_document in candidates:
            if render_count >= int(state["render_budget"]):
                break
            outcome = await evaluate_shader_graph(
                {**state, "render_count": render_count},
                candidate_document,
                registry,
                capture_png=True,
            )
            render_count = int(outcome["render_count"])
            if not outcome.get("success"):
                last_error = str(outcome.get("error", "render_failed"))
                continue
            evaluated.append(
                (
                    source,
                    candidate_from_graph_outcome(
                        candidate_document,
                        outcome,
                        parent_document_sha256=None,
                        provenance=f"initial:{source}",
                    ),
                )
            )
        if not evaluated:
            return {
                "phase": "render",
                "error": last_error or "no_valid_shader_graph_render",
                "render_count": render_count,
                "trace": _trace(
                    state,
                    "render_and_evaluate",
                    "没有有效 ShaderGraph 首帧。",
                    status="failed",
                ),
            }
        selected_source, best = min(evaluated, key=lambda item: item[1].loss)
        return {
            "phase": "render",
            "scene": best.public_document(),
            "current_best": best,
            "current_glsl": best.compiled.fragment_source,
            "current_render": best.render,
            "current_mae": best.mae,
            "current_best_mae": best.mae,
            "current_best_loss": best.loss,
            "residual_summary": best.residual_summary,
            "render_count": render_count,
            "feature_queue": _parameter_blocks(best.document),
            "refine_branch_resolved": False,
            "error": None,
            "trace": _trace(
                state,
                "render_and_evaluate",
                f"selected={selected_source}，loss={best.loss:.6f}",
                selected_source=selected_source,
                document_sha256=best.compiled.document_sha256[:12],
            ),
        }

    async def optimize_base(state: dict[str, Any]) -> dict[str, Any]:
        if bool(state.get("refine_branch_resolved")):
            best = cast(ShaderGraphCandidateSnapshot, state["current_best"])
            return {
                "phase": "base",
                "scene": best.public_document(),
                "current_best": best,
                "current_best_mae": best.mae,
                "current_best_loss": best.loss,
                "current_glsl": best.compiled.fragment_source,
                "current_render": best.render,
                "residual_summary": best.residual_summary,
                "feature_queue": (),
                "refine_branch_resolved": False,
                "trace": _trace(
                    state,
                    "optimize_base",
                    "Refine 分支已完成选择，跳过 base sweep。",
                    candidates_evaluated=0,
                ),
            }
        baseline = cast(ShaderGraphCandidateSnapshot, state["current_best"])
        best, count, accepted_path = await _optimize_block(
            state,
            registry,
            block=CANVAS_BLOCK,
            max_draws=4,
        )
        return {
            "phase": "base",
            "scene": best.public_document(),
            "current_best": best,
            "current_best_mae": best.mae,
            "current_best_loss": best.loss,
            "current_glsl": best.compiled.fragment_source,
            "current_render": best.render,
            "residual_summary": best.residual_summary,
            "render_count": count,
            "feature_queue": _parameter_blocks(best.document),
            "trace": _trace(
                state,
                "optimize_base",
                f"{'accepted' if best.loss < baseline.loss else 'rolled_back'}，loss={best.loss:.6f}",
                candidates_evaluated=count - int(state["render_count"]),
                accepted_parameter=accepted_path,
            ),
        }

    async def optimize_feature(state: dict[str, Any]) -> dict[str, Any]:
        queue = list(state.get("feature_queue", ()))
        block = queue.pop(0) if queue else ""
        baseline = cast(ShaderGraphCandidateSnapshot, state["current_best"])
        if block:
            best, count, accepted_path = await _optimize_block(
                state,
                registry,
                block=block,
                max_draws=6,
            )
        else:
            best, count, accepted_path = baseline, int(state["render_count"]), None
        return {
            "phase": "feature",
            "scene": best.public_document(),
            "current_best": best,
            "current_best_mae": best.mae,
            "current_best_loss": best.loss,
            "current_glsl": best.compiled.fragment_source,
            "current_render": best.render,
            "residual_summary": best.residual_summary,
            "render_count": count,
            "feature_queue": tuple(queue),
            "trace": _trace(
                state,
                "optimize_feature",
                f"{block or 'none'} {'accepted' if best.loss < baseline.loss else 'rolled_back'}，loss={best.loss:.6f}",
                optimization_block=block or None,
                candidates_evaluated=count - int(state["render_count"]),
                accepted_parameter=accepted_path,
            ),
        }

    async def author_refine(state: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        best = state.get("current_best")
        refine_count = int(state.get("refine_count", 0)) + 1
        if not isinstance(best, ShaderGraphCandidateSnapshot):
            return {
                "phase": "refine",
                "refine_count": refine_count,
                "trace": _trace(
                    state,
                    "author_refine",
                    "缺少 ShaderGraph current_best，拒绝生成 patch。",
                    status="failed",
                ),
            }
        remaining = remaining_llm_calls(state)
        if remaining <= 0:
            return {
                "phase": "refine",
                "scene": best.public_document(),
                "refine_count": refine_count,
                "refine_branch_resolved": True,
                "trace": _trace(
                    state,
                    "author_refine",
                    "模型预算已耗尽，保留 current_best。",
                    author_source="current_best",
                ),
            }
        schema = shader_graph_author_patch_json_schema()
        content = [
            text_part("current_best_shader_graph", best.document),
            text_part(
                "base_document_sha256",
                best.compiled.document_sha256,
            ),
            text_part("current_best_loss", best.loss),
            text_part("current_best_metrics", best.metrics),
            text_part("spatial_residual_summary", best.residual_summary),
            text_part(
                "recent_rejected_patch_summaries",
                state.get("recent_rejected_patch_summaries", ()),
            ),
            text_part("user_instruction", state.get("instruction", "")),
            text_part("expected_json_schema", schema),
            *labeled_image_parts(
                "reference_image",
                state["image"],
                state.get("content_type", "image/png"),
            ),
            *labeled_image_parts("current_best_render", best.render, "image/png"),
        ]
        result = await invoke_min_author(
            gateway=gateway,
            messages=[
                SystemMessage(content=SHADER_GRAPH_AUTHOR_REFINE_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            prompt=SHADER_GRAPH_AUTHOR_REFINE_PROMPT,
            schema=schema,
            parser=parse_shader_graph_author_patch,
            remaining_calls=remaining,
            max_output_tokens=1800,
        )
        call_count = int(state.get("llm_call_count", 0)) + result.call_count
        patch = (
            cast(ShaderGraphAuthorPatch, result.value)
            if result.value is not None
            else None
        )
        summary = (
            summarize_shader_graph_author_patch(patch) if patch is not None else None
        )
        rejected_fingerprints = {
            str(item.get("patch_fingerprint"))
            for item in state.get("recent_rejected_patch_summaries", ())
            if isinstance(item, dict)
        }
        if (
            summary is not None
            and summary["patch_fingerprint"] in rejected_fingerprints
        ):
            return {
                "phase": "refine",
                "scene": best.public_document(),
                "llm_call_count": call_count,
                "refine_count": refine_count,
                "author_model": result.model_ref,
                "author_error": "duplicate_recent_patch",
                "pending_patch_summary": None,
                "refine_branch_resolved": True,
                "trace": _trace(
                    state,
                    "author_refine",
                    "Patch 与近期拒绝项重复，保留 current_best。",
                    author_source="current_best",
                ),
            }
        candidate: ShaderDocument | None = None
        error_code = result.error_code
        if patch is not None:
            try:
                candidate = apply_shader_graph_author_patch(best.document, patch)
            except ValueError as exc:
                error_code = getattr(exc, "code", "patch_apply_failed")
        if candidate is None:
            return {
                "phase": "refine",
                "scene": best.public_document(),
                "llm_call_count": call_count,
                "refine_count": refine_count,
                "author_model": result.model_ref,
                "author_error": error_code,
                "pending_patch_summary": None,
                "refine_branch_resolved": True,
                "trace": _trace(
                    state,
                    "author_refine",
                    "typed layer patch 无效，保留 current_best。",
                    author_source="current_best",
                    error_code=error_code,
                ),
            }
        return {
            "phase": "refine",
            "scene": candidate.model_dump(mode="json", by_alias=True),
            "llm_call_count": call_count,
            "refine_count": refine_count,
            "author_model": result.model_ref,
            "author_error": None,
            "pending_patch_summary": {**(summary or {}), "status": "pending"},
            "refine_branch_resolved": False,
            "trace": _trace(
                state,
                "author_refine",
                "已派生一个 typed layer patch，等待真实渲染严格选择。",
                author_source="model_patch",
                model_calls=result.call_count,
                duration_ms=round((time.perf_counter() - started_at) * 1000.0, 3),
            ),
        }

    async def finalize(state: dict[str, Any]) -> dict[str, Any]:
        project_id, run_id = str(state["project_id"]), str(state["run_id"])
        best = state.get("current_best")
        if not isinstance(best, ShaderGraphCandidateSnapshot):
            await registry.close(project_id, run_id)
            return {
                "status": "failed",
                "stop_reason": state.get("error") or "no_valid_render",
                "final_result": {},
                "trace": _trace(
                    state,
                    "finalize",
                    "没有有效 ShaderGraph 渲染结果。",
                    status="failed",
                ),
            }
        final_compiled = compile_dsl_shader(best.document)
        run = artifacts.start_run(project_id, run_id)
        run.write_text("final/webgl1.glsl", final_compiled.fragment_source)
        run.write_bytes("final/render.png", best.render, content_type="image/png")
        run.write_json("final/shader-graph.json", best.public_document())
        graph_metrics = registry.graph_metrics(project_id, run_id)
        target_mae = float(state["target_mae"])
        target_loss = float(state["target_loss"])
        target_reached = best.loss <= target_loss
        patch_evidence = tuple(state.get("patch_evidence", ()))
        run_identity = {
            "run_classification": str(state["run_classification"]),
            "experiment_id": state.get("experiment_id"),
            "config_fingerprint": str(state["config_fingerprint"]),
            "report_schema_version": str(state["report_schema_version"]),
        }
        metrics = {
            **best.metrics,
            "metric_version": str(
                best.metrics.get("metric_version", MIN_SCENE_METRIC_VERSION)
            ),
            "template_version": final_compiled.compiler_version,
            "compiler_version": final_compiled.compiler_version,
            "dsl_schema_version": final_compiled.dsl_schema_version,
            "document_sha256": final_compiled.document_sha256,
            "topology_sha256": final_compiled.topology_sha256,
            "mae": best.mae,
            "objective_loss": best.loss,
            "quality_preset": str(state.get("quality_preset", "balanced")),
            "render_count": int(state.get("render_count", 0)),
            "render_budget": int(state.get("render_budget", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
            "llm_budget": int(state.get("llm_budget", 0)),
            "refine_budget": int(state.get("refine_budget", 0)),
            "patch_candidate_draw_budget": 1,
            "patch_candidate_count": len(patch_evidence),
            **run_identity,
            "renderer_path": SHADER_GRAPH_RENDERER_PATH,
            **graph_metrics,
            "target_mae": target_mae,
            "target_loss": target_loss,
            "target_reached": target_reached,
        }
        run.write_json("final/metrics.json", metrics)
        trace = _trace(
            state,
            "finalize",
            f"已固化 ShaderGraph final，loss={best.loss:.6f}，MAE={best.mae:.6f}",
            renderer_path=SHADER_GRAPH_RENDERER_PATH,
            document_sha256=final_compiled.document_sha256[:12],
            compile_count=graph_metrics["compile_count"],
            cache_hit_count=graph_metrics["cache_hit_count"],
            target_reached=target_reached,
        )
        manifest = {
            "schema_version": "png_to_shader_graph_manifest_v1",
            **run_identity,
            "project_id": project_id,
            "run_id": run_id,
            "status": "completed",
            "stop_reason": state.get("stop_reason", "bounded_mvp_complete"),
            "dsl_schema_version": final_compiled.dsl_schema_version,
            "compiler_version": final_compiled.compiler_version,
            "renderer_path": SHADER_GRAPH_RENDERER_PATH,
            "shader_graph": best.public_document(),
            "candidate_snapshot": snapshot_summary(best),
            "metrics": metrics,
            "patch_evidence": patch_evidence,
            "trace": trace,
        }
        manifest_ref = run.write_json("final/manifest.json", manifest)
        await registry.close(project_id, run_id)
        return {
            "status": "completed",
            "stop_reason": str(state.get("stop_reason", "bounded_mvp_complete")),
            "trace": trace,
            "final_manifest_ref": manifest_ref.relative_path,
            "final_result": {
                "project_id": project_id,
                "run_id": run_id,
                "glsl": final_compiled.fragment_source,
                "render_width": best.document.canvas.width,
                "render_height": best.document.canvas.height,
                "status": "completed",
                "stop_reason": str(state.get("stop_reason", "bounded_mvp_complete")),
                "template_version": final_compiled.compiler_version,
                "quality_preset": str(state.get("quality_preset", "balanced")),
                "current_best_mae": best.mae,
                "current_best_loss": best.loss,
                "metric_breakdown": best.metrics,
                "render_count": int(state.get("render_count", 0)),
                "render_budget": int(state.get("render_budget", 0)),
                "llm_call_count": int(state.get("llm_call_count", 0)),
                "llm_budget": int(state.get("llm_budget", 0)),
                "refine_budget": int(state.get("refine_budget", 0)),
                "patch_candidate_draw_budget": 1,
                "patch_evidence": patch_evidence,
                **run_identity,
                "renderer_path": SHADER_GRAPH_RENDERER_PATH,
                "target_mae": target_mae,
                "target_loss": target_loss,
                "target_reached": target_reached,
                "prepare_duration_ms": float(graph_metrics["prepare_duration_ms"]),
                "uniform_render_count": int(graph_metrics["uniform_render_count"]),
                "uniform_render_p95_ms": float(graph_metrics["uniform_render_p95_ms"]),
                "scene": best.public_document(),
                "shader_graph_shadow": None,
                "trace": trace,
            },
        }

    nodes.update(
        {
            "author_initial": author_initial,
            "materialize_shader": materialize_shader,
            "render_and_evaluate": render_and_evaluate,
            "optimize_base": optimize_base,
            "optimize_feature": optimize_feature,
            "author_refine": author_refine,
            "finalize": finalize,
        }
    )
    return nodes


__all__ = [
    "SHADER_GRAPH_RENDERER_PATH",
    "SHADER_GRAPH_SELECTION_POLICY",
    "ShaderGraphCandidateSnapshot",
    "candidate_from_graph_outcome",
    "evaluate_shader_graph",
    "make_shader_graph_nodes",
    "patch_fingerprint",
    "snapshot_summary",
]
