"""最小 scene Graph 的确定性工作节点。."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from io import BytesIO
from typing import Any, cast

import numpy as np
from langchain_core.messages import SystemMessage
from PIL import Image

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.config.png_to_shader_min import MIN_PIPELINE_CONFIG
from agent.app.contracts.llm import LLMGateway
from agent.app.contracts.png_to_shader_min import (
    MinAuthorPatch,
    apply_min_author_patch,
    summarize_min_author_patch,
)
from agent.app.contracts.png_to_shader_min_replay import (
    REPLAY_BUNDLE_PATH,
    REPLAY_BUNDLE_SCHEMA_VERSION,
    REPLAY_PATCH_SCHEMA_VERSION,
    REPLAY_RENDERS_DIR,
    REPLAY_STEP_SCHEMA_VERSION,
    REPLAY_STEPS_DIR,
    build_bundle_summary,
    bytes_sha256,
    canonical_json_sha256,
    decode_verified_replay_json,
    replay_step_dir_name,
)
from agent.app.messages.structured_multimodal import (
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.nodes.png_to_shader_min.model_author import (
    MIN_AUTHOR_INITIAL_PROMPT,
    MIN_AUTHOR_REFINE_PROMPT,
    MIN_AUTHOR_REPAIR_PROMPT,
    effective_llm_budget,
    invoke_min_author,
    remaining_llm_calls,
)
from agent.app.nodes.png_to_shader_min.shader_graph_shadow import (
    ShaderGraphShadowRunner,
)
from agent.app.parsers.png_to_shader_min import (
    min_author_patch_json_schema,
    parse_min_author_patch,
    parse_min_scene,
)
from shaderforge.evaluation import (
    MIN_SCENE_METRIC_VERSION,
    dominant_metric_component,
    evaluate_min_scene,
    summarize_spatial_residual,
)
from shaderforge.generation import (
    bake_min_uniforms,
    materialize_min_shader,
)
from shaderforge.optimization import (
    MAX_PATCH_CANDIDATE_DRAWS,
    accepts_strict_total_loss,
    propose_min_scene_candidates,
    rebase_candidate_proposal,
)
from shaderforge.public import MIN_SCENE_VERSION, MinScene, perceive_min_target
from shaderforge.rendering import (
    PREPARED_RENDERER_PATH,
    GraphProgramKey,
    GraphProgramRegistry,
    PlaywrightWebGL1Renderer,
    PreparedWebGL1Renderer,
)
from shaderforge.store import LocalArtifactStore

RendererFactory = Callable[[], PlaywrightWebGL1Renderer]
_DEFAULT_MIN_POLICY = MIN_PIPELINE_CONFIG.quality_presets["balanced"]
_RECENT_REJECTED_PATCH_LIMIT = 3
_METRIC_DELTA_KEYS = (
    "total_loss",
    "global_mae",
    "foreground_mae",
    "background_mae",
    "geometry_mask_loss",
    "edge_loss",
    "worst_tile_mae",
)


def _trace(
    state: dict[str, Any],
    phase: str,
    message: str,
    *,
    status: str = "completed",
    **details: Any,
) -> tuple[dict[str, Any], ...]:
    return (
        *tuple(state.get("trace", ())),
        {"phase": phase, "status": status, "message": message, **details},
    )


@dataclass(frozen=True)
class _PreparedEntry:
    """一个 run 内固定的 prepared program 及其不变签名."""

    signature: tuple[Any, ...]
    prepared: PreparedWebGL1Renderer


class MinRendererRegistry:
    """按 project/run 隔离并复用 Renderer。."""

    def __init__(self, factory: RendererFactory = PlaywrightWebGL1Renderer) -> None:
        """保存惰性 Renderer 工厂。."""
        self._factory = factory
        self._renderers: dict[tuple[str, str], PlaywrightWebGL1Renderer] = {}
        self._prepared: dict[tuple[str, str], _PreparedEntry] = {}
        self._graph_programs: dict[tuple[str, str], GraphProgramRegistry] = {}
        self._graph_prepared: dict[
            tuple[str, str], dict[GraphProgramKey, PreparedWebGL1Renderer]
        ] = {}

    def get(self, project_id: str, run_id: str) -> PlaywrightWebGL1Renderer:
        """获取或创建指定 run 的 Renderer。."""
        key = (project_id, run_id)
        renderer = self._renderers.get(key)
        if renderer is None:
            renderer = self._factory()
            self._renderers[key] = renderer
        return renderer

    async def prepare(
        self,
        project_id: str,
        run_id: str,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: dict[str, Any],
    ) -> PreparedWebGL1Renderer:
        """为 run 惰性准备唯一 program，后续候选只复用它."""
        key = (project_id, run_id)
        signature = (
            fragment_source,
            width,
            height,
            tuple(sorted((name, spec.type) for name, spec in uniform_schema.items())),
        )
        entry = self._prepared.get(key)
        if entry is not None:
            if entry.signature != signature:
                raise RuntimeError("scene_mvp run 内 prepared program 签名发生了变化。")
            return entry.prepared
        renderer = self.get(project_id, run_id)
        prepared = await renderer.prepare(
            fragment_source,
            width,
            height,
            uniform_schema,
        )
        self._prepared[key] = _PreparedEntry(signature, prepared)
        return prepared

    async def prepare_graph(
        self,
        project_id: str,
        run_id: str,
        key: GraphProgramKey,
        fragment_source: str,
        uniform_schema: dict[str, Any],
    ) -> PreparedWebGL1Renderer:
        """在同一 run 内按 program key 编译或复用 ShaderGraph program."""
        run_key = (project_id, run_id)
        programs = self._graph_programs.get(run_key)
        if programs is None:
            programs = GraphProgramRegistry(
                self.get(project_id, run_id),
                max_programs=4,
                max_compiles=16,
            )
            self._graph_programs[run_key] = programs
        prepared = cast(
            PreparedWebGL1Renderer,
            await programs.get_or_prepare(
                key,
                fragment_source,
                uniform_schema,
            ),
        )
        self._graph_prepared.setdefault(run_key, {})[key] = prepared
        return prepared

    async def discard_graph(
        self,
        project_id: str,
        run_id: str,
        key: GraphProgramKey,
    ) -> bool:
        """丢弃被拒结构分支的 program，不影响当前 best 的 program."""
        programs = self._graph_programs.get((project_id, run_id))
        if programs is None:
            return False
        return await programs.discard(key)

    def graph_metrics(self, project_id: str, run_id: str) -> dict[str, float | int]:
        """返回 ShaderGraph program cache 的公开安全计数."""
        run_key = (project_id, run_id)
        programs = self._graph_programs.get(run_key)
        if programs is None:
            return {
                "compile_count": 0,
                "cache_hit_count": 0,
                "cache_size": 0,
                "max_programs": 4,
                "max_compiles": 16,
                "prepare_duration_ms": 0.0,
                "uniform_render_count": 0,
                "uniform_render_p95_ms": 0.0,
            }
        prepared = tuple(self._graph_prepared.get(run_key, {}).values())
        durations = sorted(
            duration for item in prepared for duration in item.render_durations_ms
        )
        p95 = (
            durations[max(0, math.ceil(len(durations) * 0.95) - 1)]
            if durations
            else 0.0
        )
        return {
            **programs.summary(),
            "prepare_duration_ms": sum(item.prepare_duration_ms for item in prepared),
            "uniform_render_count": len(durations),
            "uniform_render_p95_ms": p95,
        }

    def metrics(self, project_id: str, run_id: str) -> dict[str, float | int | str]:
        """返回 run 内 prepared 路径的公开可观测摘要."""
        entry = self._prepared.get((project_id, run_id))
        if entry is None:
            return {
                "renderer_path": PREPARED_RENDERER_PATH,
                "prepare_duration_ms": 0.0,
                "uniform_render_count": 0,
                "uniform_render_p95_ms": 0.0,
            }
        durations = sorted(entry.prepared.render_durations_ms)
        p95 = (
            durations[max(0, math.ceil(len(durations) * 0.95) - 1)]
            if durations
            else 0.0
        )
        return {
            "renderer_path": PREPARED_RENDERER_PATH,
            "prepare_duration_ms": entry.prepared.prepare_duration_ms,
            "uniform_render_count": entry.prepared.render_count,
            "uniform_render_p95_ms": p95,
        }

    async def close(self, project_id: str, run_id: str) -> None:
        """幂等关闭指定 run 的 Renderer。."""
        graph_programs = self._graph_programs.pop((project_id, run_id), None)
        self._graph_prepared.pop((project_id, run_id), None)
        prepared_entry = self._prepared.pop((project_id, run_id), None)
        renderer = self._renderers.pop((project_id, run_id), None)
        first_error: Exception | None = None
        if graph_programs is not None:
            try:
                await graph_programs.close_all()
            except Exception as exc:
                first_error = exc
        if prepared_entry is not None:
            try:
                await prepared_entry.prepared.close()
            except Exception as exc:
                first_error = exc
        if renderer is not None:
            try:
                await renderer.close()
            except Exception as exc:
                first_error = first_error or exc
        if first_error is not None:
            raise first_error


def _raw_rgb_array(rgb_bytes: bytes, width: int, height: int) -> np.ndarray:
    """把 Renderer 原始 RGB bytes 视图转为 MAE 使用的 float32 数组."""
    expected = width * height * 3
    if len(rgb_bytes) != expected:
        raise ValueError(f"prepared RGB 长度应为 {expected}，实际为 {len(rgb_bytes)}。")
    return (
        np.frombuffer(rgb_bytes, dtype=np.uint8)
        .reshape(height, width, 3)
        .astype(np.float32)
        / 255.0
    )


def _encode_rgb_png(rgb_bytes: bytes, width: int, height: int) -> bytes:
    """仅在候选被接受时把已返回的 RGB 编码为 PNG."""
    _raw_rgb_array(rgb_bytes, width, height)
    image = Image.frombytes("RGB", (width, height), rgb_bytes)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _best_loss(candidate: dict[str, Any]) -> float:
    """兼容旧测试状态：缺少复合损失时退回全局 MAE。."""
    return float(candidate.get("loss", candidate["mae"]))


def _node_duration_ms(started_at: float) -> float:
    """返回节点内 wall-clock 耗时，不把模型 latency 冒充节点耗时."""
    return round((time.perf_counter() - started_at) * 1000.0, 3)


def _active_feature_summary(scene: MinScene) -> list[dict[str, str]]:
    """返回不含参数值的 active feature 身份摘要."""
    return [
        {"feature_id": feature.id, "feature_type": feature.type}
        for feature in scene.object.features
    ]


def _metric_deltas(
    candidate: dict[str, Any] | None,
    baseline: dict[str, Any],
) -> dict[str, float]:
    """返回 candidate-baseline 的稳定 metric delta；正值表示变差."""
    if not isinstance(candidate, dict):
        return {}
    candidate_metrics = candidate.get("metrics")
    baseline_metrics = baseline.get("metrics")
    if not isinstance(candidate_metrics, dict) or not isinstance(
        baseline_metrics, dict
    ):
        return {}
    deltas: dict[str, float] = {}
    for key in _METRIC_DELTA_KEYS:
        candidate_value = candidate_metrics.get(key)
        baseline_value = baseline_metrics.get(key)
        if (
            isinstance(candidate_value, bool)
            or isinstance(baseline_value, bool)
            or not isinstance(candidate_value, (int, float))
            or not isinstance(baseline_value, (int, float))
        ):
            continue
        deltas[key] = round(float(candidate_value) - float(baseline_value), 9)
    return deltas


def _bounded_append(
    items: tuple[dict[str, Any], ...] | None,
    value: dict[str, Any],
    *,
    limit: int | None = None,
) -> tuple[dict[str, Any], ...]:
    """向安全摘要历史追加一项，并按需保留最近固定窗口."""
    history = tuple(item for item in (items or ()) if isinstance(item, dict))
    appended = (*history, value)
    return appended[-limit:] if limit is not None else appended


def _write_replay_json_once(
    run: Any,
    relative_path: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """原子写私有 replay JSON；重复 step 或路径越界 fail-closed."""
    if run.path_for(relative_path).exists():
        raise RuntimeError(f"replay step 产物已存在，拒绝覆盖：{relative_path}")
    ref = run.write_json(relative_path, payload)
    return {
        "path": ref.relative_path,
        "sha256": ref.sha256,
        "size_bytes": ref.size_bytes,
    }


def _verify_replay_render_ref(run: Any, ref: Any) -> None:
    """fail-closed 复验内容寻址渲染 PNG 引用：路径推导、hash、size、类型."""
    if not isinstance(ref, dict):
        raise RuntimeError("replay 渲染引用必须是 object。")
    digest = ref.get("sha256")
    expected_path = (
        f"{REPLAY_RENDERS_DIR}/{digest}.png" if isinstance(digest, str) else None
    )
    if expected_path is None or ref.get("path") != expected_path:
        raise RuntimeError("replay 渲染引用路径与内容寻址约定不符。")
    if ref.get("content_type") != "image/png":
        raise RuntimeError("replay 渲染引用 content_type 必须是 image/png。")
    data = run.read_bytes(expected_path)
    if ref.get("size_bytes") != len(data) or bytes_sha256(data) != digest:
        raise RuntimeError("replay 渲染 PNG 与引用 hash/size 不符。")


def _write_replay_render(run: Any, png: bytes) -> dict[str, Any]:
    """内容寻址写私有渲染 PNG；已存在文件必须复验 hash 才复用."""
    digest = bytes_sha256(png)
    ref = {
        "path": f"{REPLAY_RENDERS_DIR}/{digest}.png",
        "sha256": digest,
        "size_bytes": len(png),
        "content_type": "image/png",
    }
    if run.path_for(str(ref["path"])).exists():
        _verify_replay_render_ref(run, ref)
    else:
        run.write_bytes(str(ref["path"]), png, content_type="image/png")
    return ref


def _replay_candidate_record(
    run: Any,
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """把候选快照收敛为私有 step 记录，渲染 PNG 内容寻址落盘."""
    if not isinstance(candidate, dict):
        return None
    render = candidate.get("render")
    return {
        "scene": candidate["scene"],
        "scene_sha256": canonical_json_sha256(candidate["scene"]),
        "loss": _best_loss(candidate),
        "metrics": candidate.get("metrics", {}),
        "render": (
            _write_replay_render(run, render) if isinstance(render, bytes) else None
        ),
    }


def _replay_step_ref(
    run: Any,
    refine_count: int,
    typed_patch: MinAuthorPatch | None,
    patch_summary: dict[str, Any] | None,
    author: dict[str, Any],
) -> dict[str, Any]:
    """立即原子写私有 patch draft，并返回经 Untracked State 传递的内部引用.

    完整 typed patch 只落盘到 ``private/replay/steps/``，State 与 trace
    只携带路径/hash 级引用；解析失败的 step 没有 patch draft。
    """
    step_dir = f"{REPLAY_STEPS_DIR}/{replay_step_dir_name(refine_count)}"
    patch_ref = None
    if typed_patch is not None and patch_summary is not None:
        patch_ref = _write_replay_json_once(
            run,
            f"{step_dir}/patch.json",
            {
                "schema_version": REPLAY_PATCH_SCHEMA_VERSION,
                "refine_count": refine_count,
                **patch_summary,
                "typed_patch": typed_patch.model_dump(mode="json"),
                "author": author,
            },
        )
    return {
        "refine_count": refine_count,
        "step_dir": step_dir,
        "patch": patch_ref,
        "author": author,
    }


def _candidate_from_outcome(
    scene: MinScene,
    outcome: dict[str, Any],
) -> dict[str, Any]:
    """把成功 draw 收敛为可与 current_best 比较的候选快照."""
    return {
        "scene": scene.model_dump(mode="json"),
        "mae": outcome["mae"],
        "loss": outcome["loss"],
        "metrics": outcome["metrics"],
        "residual_summary": outcome["residual_summary"],
        "glsl": outcome["glsl"],
        "render": _encode_rgb_png(
            outcome["rgb"],
            scene.canvas.width,
            scene.canvas.height,
        ),
    }


async def _evaluate_scene(
    state: dict[str, Any],
    scene: MinScene,
    registry: MinRendererRegistry,
    *,
    capture_png: bool,
) -> dict[str, Any]:
    if int(state.get("render_count", 0)) >= int(state.get("render_budget", 0)):
        raise RuntimeError("render_budget_exhausted")
    materialized = materialize_min_shader(scene)
    glsl = bake_min_uniforms(materialized)
    prepared = await registry.prepare(
        str(state["project_id"]),
        str(state["run_id"]),
        materialized.webgl1_source,
        scene.canvas.width,
        scene.canvas.height,
        materialized.uniform_schema,
    )
    result = await prepared.render_uniforms(
        materialized.uniform_values,
        capture_png=capture_png,
    )
    count = int(state.get("render_count", 0)) + 1
    if not result.success or result.rgb_bytes is None:
        return {
            "success": False,
            "render_count": count,
            "glsl": glsl,
            "materialized": materialized,
            "error": result.draw_error or "render_failed",
        }
    rendered = _raw_rgb_array(result.rgb_bytes, scene.canvas.width, scene.canvas.height)
    metric = evaluate_min_scene(
        state["target_rgb"],
        rendered,
        state.get("metric_background", scene.canvas.background),
    )
    residual_summary = summarize_spatial_residual(state["target_rgb"], rendered)
    residual_summary["dominant_metric_component"] = dominant_metric_component(metric)
    residual_summary["active_feature_summary"] = _active_feature_summary(scene)
    return {
        "success": True,
        "render_count": count,
        "glsl": glsl,
        "materialized": materialized,
        "image": result.image_bytes,
        "rgb": result.rgb_bytes,
        "mae": metric.global_mae,
        "loss": metric.total_loss,
        "metrics": metric.to_dict(),
        "residual_summary": residual_summary,
    }


def make_min_nodes(
    artifacts: LocalArtifactStore,
    registry: MinRendererRegistry,
    gateway: LLMGateway,
    shader_graph_shadow: ShaderGraphShadowRunner | None = None,
) -> dict[str, Callable[..., Any]]:
    """创建共享 Gateway/Artifact/Renderer 边界的九个工作节点和三个决定节点。."""

    async def initialize_run(state: dict[str, Any]) -> dict[str, Any]:
        project_id, run_id = str(state["project_id"]), str(state["run_id"])
        run = artifacts.register_run(project_id, run_id)
        reference = run.write_bytes(
            "input/reference.png", state["image"], content_type=state["content_type"]
        )
        return {
            "phase": "initialize",
            "status": "running",
            "quality_preset": str(state.get("quality_preset", "balanced")),
            "run_classification": str(
                state.get("run_classification", MIN_PIPELINE_CONFIG.run_classification)
            ),
            "experiment_id": state.get(
                "experiment_id", MIN_PIPELINE_CONFIG.experiment_id
            ),
            "config_fingerprint": str(
                state.get("config_fingerprint", MIN_PIPELINE_CONFIG.config_fingerprint)
            ),
            "report_schema_version": str(
                state.get(
                    "report_schema_version",
                    MIN_PIPELINE_CONFIG.report_schema_version,
                )
            ),
            "render_count": int(state.get("render_count", 0)),
            "render_budget": int(state["render_budget"]),
            "llm_call_count": min(
                effective_llm_budget(state["llm_budget"]),
                max(0, int(state.get("llm_call_count", 0))),
            ),
            "llm_budget": effective_llm_budget(state["llm_budget"]),
            "refine_count": 0,
            "refine_budget": int(state["refine_budget"]),
            "target_mae": float(
                state.get("target_mae", _DEFAULT_MIN_POLICY.target_mae)
            ),
            "target_loss": float(
                state.get("target_loss", _DEFAULT_MIN_POLICY.target_loss)
            ),
            "feature_queue": (),
            "refine_branch_resolved": False,
            "pending_patch_summary": None,
            "recent_rejected_patch_summaries": (),
            "patch_evidence": (),
            "pending_replay_step": None,
            "replay_step_refs": (),
            "trace": _trace(
                state, "initialize_run", f"输入已登记：{reference.sha256[:12]}"
            ),
        }

    async def perceive_target(state: dict[str, Any]) -> dict[str, Any]:
        perception = perceive_min_target(state["image"])
        fallback_scene = perception.fallback_scene.model_dump(mode="json")
        return {
            "phase": "perception",
            "perception": perception.summary,
            "target_rgb": perception.target_rgb,
            "metric_background": perception.fallback_scene.canvas.background,
            "fallback_scene": fallback_scene,
            "fallback_shader_graph": perception.fallback_document.model_dump(
                mode="json", by_alias=True
            ),
            "scene": fallback_scene,
            "trace": _trace(
                state,
                "perceive_target",
                f"{perception.width}x{perception.height}，scope={perception.summary['supported_scope']}",
            ),
        }

    async def author_initial(state: dict[str, Any]) -> dict[str, Any]:
        fallback = MinScene.model_validate(state.get("fallback_scene", state["scene"]))
        remaining = remaining_llm_calls(state)
        if remaining <= 0:
            return {
                "phase": "author_initial",
                "scene": fallback.model_dump(mode="json"),
                "trace": _trace(
                    state,
                    "author_initial",
                    "模型预算为 0，使用确定性感知 scene。",
                    author_source="perception_fallback",
                ),
            }
        schema = MinScene.model_json_schema(mode="validation")
        content = [
            text_part("perception", state.get("perception", {})),
            text_part("fallback_scene", fallback),
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
                SystemMessage(content=MIN_AUTHOR_INITIAL_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            prompt=MIN_AUTHOR_INITIAL_PROMPT,
            schema=schema,
            parser=lambda text: parse_min_scene(
                text,
                expected_width=fallback.canvas.width,
                expected_height=fallback.canvas.height,
            ),
            remaining_calls=remaining,
            max_output_tokens=1800,
        )
        call_count = int(state.get("llm_call_count", 0)) + result.call_count
        if not isinstance(result.value, MinScene):
            return {
                "phase": "author_initial",
                "scene": fallback.model_dump(mode="json"),
                "llm_call_count": call_count,
                "author_model": result.model_ref,
                "author_error": result.error_code,
                "trace": _trace(
                    state,
                    "author_initial",
                    "模型调用或严格解析失败，安全回退到感知 scene。",
                    author_source="perception_fallback",
                    model_calls=result.call_count,
                    error_code=result.error_code,
                    author_latency_ms=result.latency_ms,
                    author_tokens=result.total_tokens,
                ),
            }
        return {
            "phase": "author_initial",
            "scene": result.value.model_dump(mode="json"),
            "llm_call_count": call_count,
            "author_model": result.model_ref,
            "author_error": None,
            "trace": _trace(
                state,
                "author_initial",
                "完整 MinScene 已通过严格模型契约。",
                author_source="model",
                model_calls=result.call_count,
                repaired=result.repaired,
                author_latency_ms=result.latency_ms,
                author_tokens=result.total_tokens,
            ),
        }

    async def materialize_shader(state: dict[str, Any]) -> dict[str, Any]:
        scene = MinScene.model_validate(state["scene"])
        materialized = materialize_min_shader(scene)
        return {
            "phase": "materialize",
            "materialized": materialized,
            "current_glsl": bake_min_uniforms(materialized),
            "trace": _trace(
                state, "materialize_shader", f"template={materialized.template_version}"
            ),
        }

    async def _evaluate_refine_branch(
        state: dict[str, Any],
        pending: dict[str, Any],
    ) -> dict[str, Any]:
        """在独立分支内成熟 typed Patch，最后才与只读 best 锚点比较."""
        started_at = time.perf_counter()
        anchor = dict(state["current_best"])
        render_count = int(state.get("render_count", 0))
        status = str(pending.get("status", "pending"))
        raw: dict[str, Any] | None = None
        matured: dict[str, Any] | None = None
        maturity_draw_count = 0
        rejected_reason: str | None = None
        run_store = artifacts.start_run(str(state["project_id"]), str(state["run_id"]))
        replay_step = state.get("pending_replay_step")
        typed_patch_content: dict[str, Any] | None = None
        step_dir: str | None = None
        if isinstance(replay_step, dict):
            # step 目录只从 refine_count 派生，不信任 State 携带的路径。
            step_dir = (
                f"{REPLAY_STEPS_DIR}/"
                f"{replay_step_dir_name(replay_step['refine_count'])}"
            )
            if replay_step.get("step_dir") != step_dir:
                raise RuntimeError("私有 replay step 目录引用与 refine_count 不符。")
            if isinstance(replay_step.get("patch"), dict):
                draft = decode_verified_replay_json(
                    run_store.read_bytes(f"{step_dir}/patch.json"),
                    replay_step["patch"],
                    expected_path=f"{step_dir}/patch.json",
                    expected_schema_version=REPLAY_PATCH_SCHEMA_VERSION,
                    expected_refine_count=replay_step["refine_count"],
                )
                draft_patch = draft.get("typed_patch")
                typed_patch_content = (
                    draft_patch if isinstance(draft_patch, dict) else None
                )
        proposal_records: list[dict[str, Any]] = []

        if status != "pending":
            rejected_reason = str(
                pending.get(
                    "rejected_reason",
                    "duplicate_recent_patch"
                    if status == "duplicate"
                    else "invalid_patch",
                )
            )
        else:
            candidate_scene = MinScene.model_validate(state["scene"])
            raw_outcome = await _evaluate_scene(
                {**state, "render_count": render_count},
                candidate_scene,
                registry,
                capture_png=False,
            )
            render_count = int(raw_outcome["render_count"])
            if not raw_outcome["success"]:
                rejected_reason = "renderer_failed"
            else:
                raw = _candidate_from_outcome(candidate_scene, raw_outcome)
                matured = raw
                patch_operation = str(pending.get("patch_operation", ""))
                stage: str | None = None
                feature_id: str | None = None
                if patch_operation in {"add_feature", "replace_feature"}:
                    stage = "feature"
                    feature_id = (
                        str(pending["feature_id"])
                        if pending.get("feature_id") is not None
                        else None
                    )
                elif patch_operation == "replace_color_field":
                    stage = "color_field"
                elif patch_operation != "remove_feature":
                    rejected_reason = "invalid_patch"

                remaining_local = min(
                    MAX_PATCH_CANDIDATE_DRAWS - 1,
                    max(0, int(state["render_budget"]) - render_count),
                )
                proposals = (
                    propose_min_scene_candidates(
                        MinScene.model_validate(matured["scene"]),
                        stage=cast(Any, stage),
                        feature_id=feature_id,
                        remaining_draw_budget=remaining_local,
                        batch_size=MAX_PATCH_CANDIDATE_DRAWS - 1,
                    )
                    if stage is not None and rejected_reason is None
                    else ()
                )
                for proposal in proposals:
                    assert matured is not None
                    rebased = rebase_candidate_proposal(
                        MinScene.model_validate(matured["scene"]), proposal
                    )
                    if rebased is None:
                        continue
                    outcome = await _evaluate_scene(
                        {**state, "render_count": render_count},
                        rebased.scene,
                        registry,
                        capture_png=False,
                    )
                    render_count = int(outcome["render_count"])
                    maturity_draw_count += 1
                    if not outcome["success"]:
                        proposal_records.append(
                            {
                                "parameter_path": rebased.parameter.path,
                                "direction": rebased.direction,
                                "renderer_failed": True,
                                "draw_error": str(outcome["error"]),
                            }
                        )
                        rejected_reason = "renderer_failed"
                        matured = None
                        break
                    candidate = _candidate_from_outcome(rebased.scene, outcome)
                    proposal_records.append(
                        {
                            "parameter_path": rebased.parameter.path,
                            "direction": rebased.direction,
                            "before": rebased.before,
                            "after": rebased.after,
                            "loss": float(outcome["loss"]),
                            "scene_sha256": canonical_json_sha256(
                                rebased.scene.model_dump(mode="json")
                            ),
                            # 原始 RGB 字节域，与 PNG 文件 hash 严格区分。
                            "render_rgb_sha256": bytes_sha256(outcome["rgb"]),
                            "render_rgb_encoding": "rgb8_bytes",
                            "render_width": rebased.scene.canvas.width,
                            "render_height": rebased.scene.canvas.height,
                        }
                    )
                    if accepts_strict_total_loss(
                        _best_loss(candidate), _best_loss(matured)
                    ):
                        matured = candidate

        accepted = (
            rejected_reason is None
            and isinstance(matured, dict)
            and accepts_strict_total_loss(_best_loss(matured), _best_loss(anchor))
        )
        if not accepted and rejected_reason is None:
            rejected_reason = "no_strict_loss_improvement"
        best = matured if accepted and isinstance(matured, dict) else anchor
        evidence = {
            **{
                key: pending.get(key)
                for key in (
                    "patch_operation",
                    "feature_id",
                    "feature_type",
                    "patch_fingerprint",
                )
            },
            "raw_candidate_loss": _best_loss(raw) if isinstance(raw, dict) else None,
            "matured_candidate_loss": (
                _best_loss(matured) if isinstance(matured, dict) else None
            ),
            "best_loss_before": _best_loss(anchor),
            "best_loss_after": _best_loss(best),
            "raw_metric_deltas": _metric_deltas(raw, anchor),
            "matured_metric_deltas": _metric_deltas(matured, anchor),
            "maturity_draw_count": maturity_draw_count,
            "total_candidate_draw_count": render_count
            - int(state.get("render_count", 0)),
            "accepted": accepted,
            "rejected_reason": None if accepted else rejected_reason,
            "duplicate_of_recent": status == "duplicate",
            "duration_ms": _node_duration_ms(started_at),
        }
        rejected = tuple(state.get("recent_rejected_patch_summaries", ()))
        if not accepted:
            rejected = _bounded_append(
                rejected,
                evidence,
                limit=_RECENT_REJECTED_PATCH_LIMIT,
            )
        patch_evidence = _bounded_append(
            tuple(state.get("patch_evidence", ())),
            evidence,
        )
        replay_step_refs = tuple(state.get("replay_step_refs", ()))
        if isinstance(replay_step, dict):
            anchor_render = anchor.get("render")
            record = {
                "schema_version": REPLAY_STEP_SCHEMA_VERSION,
                "refine_count": replay_step["refine_count"],
                "status": status,
                "patch_operation": pending.get("patch_operation"),
                "feature_id": pending.get("feature_id"),
                "feature_type": pending.get("feature_type"),
                "patch_fingerprint": pending.get("patch_fingerprint"),
                "patch_ref": replay_step.get("patch"),
                "typed_patch": typed_patch_content,
                "author": replay_step.get("author"),
                "anchor": {
                    "scene": anchor["scene"],
                    "scene_sha256": canonical_json_sha256(anchor["scene"]),
                    "loss": _best_loss(anchor),
                    "metrics": anchor.get("metrics", {}),
                    "render_sha256": (
                        bytes_sha256(anchor_render)
                        if isinstance(anchor_render, bytes)
                        else None
                    ),
                },
                "candidate_scene": (state["scene"] if status == "pending" else None),
                "candidate_scene_sha256": (
                    canonical_json_sha256(state["scene"])
                    if status == "pending"
                    else None
                ),
                "raw": _replay_candidate_record(run_store, raw),
                "matured": _replay_candidate_record(run_store, matured),
                "maturity_proposals": proposal_records,
                "draws": {
                    "render_count_before": int(state.get("render_count", 0)),
                    "render_count_after": render_count,
                    "maturity_draw_count": maturity_draw_count,
                    "total_candidate_draw_count": render_count
                    - int(state.get("render_count", 0)),
                },
                "acceptance": {
                    "accepted": accepted,
                    "rejected_reason": evidence["rejected_reason"],
                    "duplicate_of_recent": status == "duplicate",
                    "best_loss_before": _best_loss(anchor),
                    "best_loss_after": _best_loss(best),
                },
                "duration_ms": evidence["duration_ms"],
            }
            record_ref = _write_replay_json_once(
                run_store,
                f"{step_dir}/record.json",
                record,
            )
            replay_step_refs = (
                *replay_step_refs,
                {
                    "refine_count": replay_step["refine_count"],
                    "step_dir": step_dir,
                    "patch": replay_step.get("patch"),
                    "record": record_ref,
                },
            )
        return {
            "phase": "render",
            "render_count": render_count,
            "scene": best["scene"],
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "current_mae": (
                raw["mae"] if isinstance(raw, dict) else float(best["mae"])
            ),
            "current_best_mae": best["mae"],
            "current_best_loss": _best_loss(best),
            "current_best": best,
            "residual_summary": dict(best.get("residual_summary", {})),
            "feature_queue": (),
            "refine_branch_resolved": True,
            "pending_patch_summary": None,
            "pending_replay_step": None,
            "replay_step_refs": replay_step_refs,
            "recent_rejected_patch_summaries": rejected,
            "patch_evidence": patch_evidence,
            "error": None,
            "trace": _trace(
                state,
                "render_and_evaluate",
                (
                    "matured_candidate accepted"
                    if accepted
                    else f"candidate_branch rejected：{rejected_reason}"
                ),
                selected_source="model_patch",
                patch_evidence=evidence,
                duration_ms=evidence["duration_ms"],
            ),
        }

    async def render_and_evaluate(state: dict[str, Any]) -> dict[str, Any]:
        pending = state.get("pending_patch_summary")
        if isinstance(pending, dict):
            return await _evaluate_refine_branch(state, pending)
        scene = MinScene.model_validate(state["scene"])
        outcome = await _evaluate_scene(state, scene, registry, capture_png=True)
        evaluated: list[tuple[str, MinScene, dict[str, Any]]] = [
            ("working_scene", scene, outcome)
        ]
        previous = state.get("current_best")
        fallback_value = state.get("fallback_scene")
        if (
            not isinstance(previous, dict)
            and isinstance(fallback_value, dict)
            and fallback_value != scene.model_dump(mode="json")
            and int(outcome["render_count"]) < int(state.get("render_budget", 0))
        ):
            fallback = MinScene.model_validate(fallback_value)
            fallback_outcome = await _evaluate_scene(
                {**state, "render_count": outcome["render_count"]},
                fallback,
                registry,
                capture_png=True,
            )
            evaluated.append(("perception_fallback", fallback, fallback_outcome))
        successful = [item for item in evaluated if item[2]["success"]]
        if not successful:
            last_outcome = evaluated[-1][2]
            return {
                "phase": "render",
                "error": str(last_outcome["error"]),
                "render_count": last_outcome["render_count"],
                "trace": _trace(
                    state,
                    "render_and_evaluate",
                    str(last_outcome["error"]),
                    status="failed",
                ),
            }
        selected_source, selected_scene, selected_outcome = min(
            successful,
            key=lambda item: float(item[2]["loss"]),
        )
        candidate = {
            "scene": selected_scene.model_dump(mode="json"),
            "mae": selected_outcome["mae"],
            "loss": selected_outcome["loss"],
            "metrics": selected_outcome["metrics"],
            "residual_summary": selected_outcome["residual_summary"],
            "glsl": selected_outcome["glsl"],
            "render": selected_outcome["image"],
        }
        accepted = not isinstance(previous, dict) or accepts_strict_total_loss(
            _best_loss(candidate), _best_loss(previous)
        )
        best = candidate if accepted else previous
        assert isinstance(best, dict)
        candidate_mae = next(
            (
                float(item[2]["mae"])
                for item in evaluated
                if item[0] == "working_scene" and item[2]["success"]
            ),
            None,
        )
        fallback_mae = next(
            (
                float(item[2]["mae"])
                for item in evaluated
                if item[0] == "perception_fallback" and item[2]["success"]
            ),
            None,
        )
        return {
            "phase": "render",
            "render_count": evaluated[-1][2]["render_count"],
            "scene": best["scene"],
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "current_mae": selected_outcome["mae"],
            "current_best_mae": best["mae"],
            "current_best_loss": _best_loss(best),
            "current_best": best,
            "residual_summary": dict(best.get("residual_summary", {})),
            "refine_branch_resolved": False,
            "feature_queue": tuple(
                feature.id
                for feature in MinScene.model_validate(best["scene"]).object.features
            ),
            "error": None,
            "trace": _trace(
                state,
                "render_and_evaluate",
                f"{'accepted' if accepted else 'rejected'}，候选 loss={selected_outcome['loss']:.6f}，best loss={_best_loss(best):.6f}",
                selected_source=selected_source,
                working_scene_mae=candidate_mae,
                fallback_mae=fallback_mae,
                working_scene_loss=next(
                    (
                        float(item[2]["loss"])
                        for item in evaluated
                        if item[0] == "working_scene" and item[2]["success"]
                    ),
                    None,
                ),
                fallback_loss=next(
                    (
                        float(item[2]["loss"])
                        for item in evaluated
                        if item[0] == "perception_fallback" and item[2]["success"]
                    ),
                    None,
                ),
            ),
        }

    async def optimize_base(state: dict[str, Any]) -> dict[str, Any]:
        if bool(state.get("refine_branch_resolved")):
            best = dict(state["current_best"])
            return {
                "phase": "base",
                "scene": best["scene"],
                "current_best": best,
                "current_best_mae": best["mae"],
                "current_best_loss": _best_loss(best),
                "current_glsl": best["glsl"],
                "current_render": best["render"],
                "residual_summary": dict(best.get("residual_summary", {})),
                "feature_queue": (),
                "refine_branch_resolved": False,
                "trace": _trace(
                    state,
                    "optimize_base",
                    "Refine 分支已完成有界局部成熟与选择，跳过全量 base sweep。",
                    candidates_evaluated=0,
                ),
            }
        baseline = dict(state["current_best"])
        best = baseline
        render_count = int(state["render_count"])
        baseline_scene = MinScene.model_validate(baseline["scene"])
        proposals = propose_min_scene_candidates(
            baseline_scene,
            stage="base",
            remaining_draw_budget=max(0, int(state["render_budget"]) - render_count),
            batch_size=32,
        )
        accepted_parameter: str | None = None
        for proposal in proposals:
            rebased = rebase_candidate_proposal(
                MinScene.model_validate(best["scene"]), proposal
            )
            if rebased is None:
                continue
            outcome = await _evaluate_scene(
                {**state, "render_count": render_count},
                rebased.scene,
                registry,
                capture_png=False,
            )
            render_count = outcome["render_count"]
            if outcome["success"] and accepts_strict_total_loss(
                float(outcome["loss"]), _best_loss(best)
            ):
                best = {
                    "scene": rebased.scene.model_dump(mode="json"),
                    "mae": outcome["mae"],
                    "loss": outcome["loss"],
                    "metrics": outcome["metrics"],
                    "residual_summary": outcome["residual_summary"],
                    "glsl": outcome["glsl"],
                    "render": _encode_rgb_png(
                        outcome["rgb"],
                        rebased.scene.canvas.width,
                        rebased.scene.canvas.height,
                    ),
                }
                accepted_parameter = rebased.parameter.path
        improved = accepts_strict_total_loss(_best_loss(best), _best_loss(baseline))
        return {
            "phase": "base",
            "scene": best["scene"],
            "current_best": best,
            "current_best_mae": best["mae"],
            "current_best_loss": _best_loss(best),
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "residual_summary": dict(best.get("residual_summary", {})),
            "render_count": render_count,
            "trace": _trace(
                state,
                "optimize_base",
                f"{'accepted' if improved else 'rolled_back'}，loss={_best_loss(best):.6f}",
                candidates_evaluated=render_count - int(state["render_count"]),
                accepted_parameter=accepted_parameter,
            ),
        }

    async def optimize_feature(state: dict[str, Any]) -> dict[str, Any]:
        queue = list(state.get("feature_queue", ()))
        feature_id = queue.pop(0) if queue else "none"
        baseline = dict(state["current_best"])
        best = baseline
        render_count = int(state["render_count"])
        baseline_scene = MinScene.model_validate(baseline["scene"])
        feature = next(
            (item for item in baseline_scene.object.features if item.id == feature_id),
            None,
        )
        proposals = (
            propose_min_scene_candidates(
                baseline_scene,
                stage="feature",
                feature_id=feature.id,
                remaining_draw_budget=max(
                    0, int(state["render_budget"]) - render_count
                ),
                batch_size=12,
            )
            if feature is not None
            else ()
        )
        accepted_parameter: str | None = None
        for proposal in proposals:
            rebased = rebase_candidate_proposal(
                MinScene.model_validate(best["scene"]), proposal
            )
            if rebased is None:
                continue
            outcome = await _evaluate_scene(
                {**state, "render_count": render_count},
                rebased.scene,
                registry,
                capture_png=False,
            )
            render_count = outcome["render_count"]
            if outcome["success"] and accepts_strict_total_loss(
                float(outcome["loss"]), _best_loss(best)
            ):
                best = {
                    "scene": rebased.scene.model_dump(mode="json"),
                    "mae": outcome["mae"],
                    "loss": outcome["loss"],
                    "metrics": outcome["metrics"],
                    "residual_summary": outcome["residual_summary"],
                    "glsl": outcome["glsl"],
                    "render": _encode_rgb_png(
                        outcome["rgb"],
                        rebased.scene.canvas.width,
                        rebased.scene.canvas.height,
                    ),
                }
                accepted_parameter = rebased.parameter.path
        improved = accepts_strict_total_loss(_best_loss(best), _best_loss(baseline))
        return {
            "phase": "feature",
            "scene": best["scene"],
            "current_best": best,
            "current_best_mae": best["mae"],
            "current_best_loss": _best_loss(best),
            "current_glsl": best["glsl"],
            "current_render": best["render"],
            "residual_summary": dict(best.get("residual_summary", {})),
            "render_count": render_count,
            "feature_queue": tuple(queue),
            "trace": _trace(
                state,
                "optimize_feature",
                f"{feature_id} {'accepted' if improved else 'rolled_back'}，loss={_best_loss(best):.6f}",
                feature_id=feature_id,
                feature_type=feature.type if feature is not None else None,
                candidates_evaluated=render_count - int(state["render_count"]),
                accepted_parameter=accepted_parameter,
            ),
        }

    async def author_refine(state: dict[str, Any]) -> dict[str, Any]:
        started_at = time.perf_counter()
        best = state.get("current_best")
        refine_count = int(state.get("refine_count", 0)) + 1
        if not isinstance(best, dict):
            return {
                "phase": "refine",
                "refine_count": refine_count,
                "trace": _trace(
                    state,
                    "author_refine",
                    "缺少 current_best，拒绝生成候选。",
                    status="failed",
                    duration_ms=_node_duration_ms(started_at),
                ),
            }
        best_scene = MinScene.model_validate(best["scene"])
        remaining = remaining_llm_calls(state)
        if remaining <= 0:
            return {
                "phase": "refine",
                "scene": best_scene.model_dump(mode="json"),
                "refine_count": refine_count,
                "trace": _trace(
                    state,
                    "author_refine",
                    "模型预算已耗尽，保留 current_best。",
                    author_source="current_best",
                    duration_ms=_node_duration_ms(started_at),
                ),
            }
        schema = min_author_patch_json_schema()
        content = [
            text_part("current_best_scene", best_scene),
            text_part("current_best_mae", best.get("mae")),
            text_part("current_best_loss", _best_loss(best)),
            text_part("current_best_metrics", best.get("metrics", {})),
            text_part(
                "spatial_residual_summary",
                best.get("residual_summary", state.get("residual_summary", {})),
            ),
            text_part("active_feature_summary", _active_feature_summary(best_scene)),
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
        ]
        render = best.get("render")
        if isinstance(render, bytes):
            content.extend(
                labeled_image_parts("current_best_render", render, "image/png")
            )
        result = await invoke_min_author(
            gateway=gateway,
            messages=[
                SystemMessage(content=MIN_AUTHOR_REFINE_PROMPT.prompt),
                multimodal_human_message(content),
            ],
            prompt=MIN_AUTHOR_REFINE_PROMPT,
            schema=schema,
            parser=parse_min_author_patch,
            remaining_calls=remaining,
            max_output_tokens=500,
        )
        call_count = int(state.get("llm_call_count", 0)) + result.call_count
        run_store = artifacts.start_run(str(state["project_id"]), str(state["run_id"]))
        output_prompt = (
            MIN_AUTHOR_REPAIR_PROMPT if result.repaired else MIN_AUTHOR_REFINE_PROMPT
        )
        author_record = {
            "model_ref": result.model_ref,
            "requested_model_ref": SHADER_GEN_MODEL_NAME,
            "source_prompt": {
                "name": MIN_AUTHOR_REFINE_PROMPT.name,
                "version": MIN_AUTHOR_REFINE_PROMPT.version,
            },
            "output_prompt": {
                "name": output_prompt.name,
                "version": output_prompt.version,
            },
            "call_count": result.call_count,
            "repaired": result.repaired,
            "latency_ms": result.latency_ms,
            "total_tokens": result.total_tokens,
            "error_code": result.error_code,
        }
        replay_step: dict[str, Any] | None = None
        candidate: MinScene | None = None
        error_code = result.error_code
        patch_summary: dict[str, Any] | None = None
        if result.value is not None:
            typed_patch = cast(MinAuthorPatch, result.value)
            patch_summary = summarize_min_author_patch(typed_patch)
            rejected_fingerprints = {
                str(item.get("patch_fingerprint"))
                for item in state.get("recent_rejected_patch_summaries", ())
                if isinstance(item, dict) and item.get("patch_fingerprint")
            }
            if patch_summary["patch_fingerprint"] in rejected_fingerprints:
                replay_step = _replay_step_ref(
                    run_store, refine_count, typed_patch, patch_summary, author_record
                )
                return {
                    "phase": "refine",
                    "scene": best_scene.model_dump(mode="json"),
                    "llm_call_count": call_count,
                    "refine_count": refine_count,
                    "author_model": result.model_ref,
                    "author_error": "duplicate_recent_patch",
                    "pending_patch_summary": {
                        **patch_summary,
                        "status": "duplicate",
                        "rejected_reason": "duplicate_recent_patch",
                    },
                    "pending_replay_step": replay_step,
                    "trace": _trace(
                        state,
                        "author_refine",
                        "Patch 与近期已拒候选重复，保留 current_best 且不分配 draw。",
                        author_source="current_best",
                        patch_summary=patch_summary,
                        model_calls=result.call_count,
                        author_latency_ms=result.latency_ms,
                        author_tokens=result.total_tokens,
                        duration_ms=_node_duration_ms(started_at),
                    ),
                }
            try:
                candidate = apply_min_author_patch(
                    best_scene,
                    typed_patch,
                )
            except (TypeError, ValueError) as exc:
                error_code = f"patch_apply_failed:{type(exc).__name__}"
                # draft 在 apply 之后写盘，author 携带最终阶段错误码，
                # 保证 patch.json 与 record.json 的 author 一致。
                author_record = {**author_record, "error_code": error_code}
            replay_step = _replay_step_ref(
                run_store, refine_count, typed_patch, patch_summary, author_record
            )
        if candidate is None:
            rejected_reason = (
                "patch_apply_failed"
                if error_code and str(error_code).startswith("patch_apply_failed:")
                else "invalid_patch"
            )
            if replay_step is None:
                replay_step = _replay_step_ref(
                    run_store, refine_count, None, None, author_record
                )
            return {
                "phase": "refine",
                "scene": best_scene.model_dump(mode="json"),
                "llm_call_count": call_count,
                "refine_count": refine_count,
                "author_model": result.model_ref,
                "author_error": error_code,
                "pending_patch_summary": {
                    **(patch_summary or {}),
                    "status": "invalid",
                    "rejected_reason": rejected_reason,
                },
                "pending_replay_step": replay_step,
                "trace": _trace(
                    state,
                    "author_refine",
                    "Patch 无效或调用失败，保留 current_best。",
                    author_source="current_best",
                    model_calls=result.call_count,
                    error_code=error_code,
                    author_latency_ms=result.latency_ms,
                    author_tokens=result.total_tokens,
                    duration_ms=_node_duration_ms(started_at),
                ),
            }
        return {
            "phase": "refine",
            "scene": candidate.model_dump(mode="json"),
            "llm_call_count": call_count,
            "refine_count": refine_count,
            "author_model": result.model_ref,
            "author_error": None,
            "pending_patch_summary": {
                **(patch_summary or {}),
                "status": "pending",
            },
            "pending_replay_step": replay_step,
            "trace": _trace(
                state,
                "author_refine",
                "已从 current_best 派生一个 typed patch 候选，等待真实渲染选择。",
                author_source="model_patch",
                model_calls=result.call_count,
                repaired=result.repaired,
                author_latency_ms=result.latency_ms,
                author_tokens=result.total_tokens,
                patch_summary=patch_summary,
                duration_ms=_node_duration_ms(started_at),
            ),
        }

    async def finalize(state: dict[str, Any]) -> dict[str, Any]:
        project_id, run_id = str(state["project_id"]), str(state["run_id"])
        best = state.get("current_best")
        if not isinstance(best, dict):
            await registry.close(project_id, run_id)
            return {
                "status": "failed",
                "stop_reason": state.get("error") or "no_valid_render",
                "final_result": {},
                "trace": _trace(
                    state, "finalize", "没有有效渲染结果。", status="failed"
                ),
            }
        scene = MinScene.model_validate(best["scene"])
        materialized = materialize_min_shader(scene)
        run = artifacts.start_run(project_id, run_id)
        run.write_text("final/webgl1.glsl", str(best["glsl"]))
        run.write_text("final/shadertoy.glsl", materialized.shadertoy_source)
        run.write_bytes("final/render.png", best["render"], content_type="image/png")
        shader_graph_shadow_summary: dict[str, Any] | None = None
        if shader_graph_shadow is not None:
            try:
                shadow_result = await shader_graph_shadow.run(scene)
                shader_graph_shadow_summary = dict(shadow_result.summary)
                if shadow_result.fragment_source is not None:
                    run.write_text(
                        "final/shader-graph-shadow.glsl",
                        shadow_result.fragment_source,
                    )
                if shadow_result.image_bytes is not None:
                    run.write_bytes(
                        "final/shader-graph-shadow.png",
                        shadow_result.image_bytes,
                        content_type="image/png",
                    )
                graph_document = shader_graph_shadow_summary.get("shader_graph")
                if isinstance(graph_document, dict):
                    run.write_json(
                        "final/shader-graph.json",
                        graph_document,
                    )
            except Exception:
                shader_graph_shadow_summary = {
                    "status": "failed",
                    "layer_count": 0,
                    "primitive_count": 0,
                    "compile_count": 0,
                    "cache_hit_count": 0,
                    "cache_size": 0,
                    "unsupported_features": [],
                    "error_code": "shadow_internal_error",
                }
        renderer_metrics = registry.metrics(project_id, run_id)
        target_mae = float(state["target_mae"])
        target_loss = float(state["target_loss"])
        best_loss = _best_loss(best)
        target_reached = best_loss <= target_loss
        score_metrics = dict(best.get("metrics", {}))
        patch_evidence = tuple(state.get("patch_evidence", ()))
        run_identity = {
            "run_classification": str(state["run_classification"]),
            "experiment_id": state.get("experiment_id"),
            "config_fingerprint": str(state["config_fingerprint"]),
            "report_schema_version": str(state["report_schema_version"]),
        }
        metrics = {
            **score_metrics,
            "metric_version": str(
                score_metrics.get("metric_version", MIN_SCENE_METRIC_VERSION)
            ),
            "template_version": materialized.template_version,
            "mae": float(best["mae"]),
            "objective_loss": best_loss,
            "quality_preset": str(state.get("quality_preset", "balanced")),
            "render_count": int(state.get("render_count", 0)),
            "render_budget": int(state.get("render_budget", 0)),
            "llm_call_count": int(state.get("llm_call_count", 0)),
            "llm_budget": int(state.get("llm_budget", 0)),
            "refine_budget": int(state.get("refine_budget", 0)),
            "patch_candidate_draw_budget": MAX_PATCH_CANDIDATE_DRAWS,
            "patch_candidate_count": len(patch_evidence),
            **run_identity,
            **renderer_metrics,
            "target_mae": target_mae,
            "target_loss": target_loss,
            "target_reached": target_reached,
        }
        run.write_json("final/metrics.json", metrics)
        step_records: list[dict[str, Any]] = []
        for step_ref in state.get("replay_step_refs", ()):
            if not isinstance(step_ref, dict):
                raise RuntimeError("私有 replay step 引用损坏。")
            # step 目录只从 refine_count 派生，不信任 State 携带的路径。
            step_dir = (
                f"{REPLAY_STEPS_DIR}/{replay_step_dir_name(step_ref['refine_count'])}"
            )
            if step_ref.get("step_dir") != step_dir:
                raise RuntimeError("私有 replay step 目录引用与 refine_count 不符。")
            record = decode_verified_replay_json(
                run.read_bytes(f"{step_dir}/record.json"),
                step_ref.get("record"),
                expected_path=f"{step_dir}/record.json",
                expected_schema_version=REPLAY_STEP_SCHEMA_VERSION,
                expected_refine_count=step_ref["refine_count"],
            )
            for candidate_key in ("raw", "matured"):
                candidate = record.get(candidate_key)
                if isinstance(candidate, dict) and candidate.get("render") is not None:
                    _verify_replay_render_ref(run, candidate["render"])
            step_records.append(record)
        try:
            reference_sha256: str | None = bytes_sha256(
                run.read_bytes("input/reference.png")
            )
        except FileNotFoundError:
            reference_sha256 = None
        requested_model_refs = sorted(
            {
                str(step["author"]["requested_model_ref"])
                for step in step_records
                if isinstance(step.get("author"), dict)
                and step["author"].get("requested_model_ref")
            }
        )
        actual_model_refs = sorted(
            {
                str(step["author"]["model_ref"])
                for step in step_records
                if isinstance(step.get("author"), dict)
                and step["author"].get("model_ref")
            }
        )
        metric_background = state.get("metric_background", scene.canvas.background)
        bundle = {
            "schema_version": REPLAY_BUNDLE_SCHEMA_VERSION,
            "run": {
                "project_id": project_id,
                "run_id": run_id,
                **run_identity,
            },
            "config": {
                "quality_preset": str(state.get("quality_preset", "balanced")),
                "target_mae": target_mae,
                "target_loss": target_loss,
                "render_budget": int(state.get("render_budget", 0)),
                "llm_budget": int(state.get("llm_budget", 0)),
                "refine_budget": int(state.get("refine_budget", 0)),
                "patch_candidate_draw_budget": MAX_PATCH_CANDIDATE_DRAWS,
            },
            "reference": {
                "artifact": "input/reference.png",
                "sha256": reference_sha256,
            },
            "identity": {
                "model_refs": {
                    "requested": requested_model_refs,
                    "actual": actual_model_refs,
                    "scope": "refine_steps_only",
                    "identity_source": (
                        "aggregated_from_replay_step_author_records"
                        if step_records
                        else "unavailable_no_refine_step"
                    ),
                },
                "prompts": {
                    "initial": {
                        "name": MIN_AUTHOR_INITIAL_PROMPT.name,
                        "version": MIN_AUTHOR_INITIAL_PROMPT.version,
                    },
                    "refine": {
                        "name": MIN_AUTHOR_REFINE_PROMPT.name,
                        "version": MIN_AUTHOR_REFINE_PROMPT.version,
                    },
                    "repair": {
                        "name": MIN_AUTHOR_REPAIR_PROMPT.name,
                        "version": MIN_AUTHOR_REPAIR_PROMPT.version,
                    },
                },
                "scene_version": MIN_SCENE_VERSION,
                "template_version": materialized.template_version,
                "metric_version": metrics["metric_version"],
                "optimization": {
                    "module": "shaderforge.optimization.min_optimize",
                    "version": "unavailable",
                    "identity_source": "module_defines_no_version_constant",
                },
                "selection": {
                    "predicate": "accepts_strict_total_loss",
                    "identity_source": (
                        "shaderforge.optimization.min_optimize."
                        "accepts_strict_total_loss"
                    ),
                },
                "source_revision": {
                    "status": "unavailable",
                    "identity_source": "no_reliable_injection_point",
                },
            },
            "metric_background": [float(value) for value in metric_background],
            "steps": step_records,
            "step_count": len(step_records),
        }
        bundle_ref = _write_replay_json_once(run, REPLAY_BUNDLE_PATH, bundle)
        replay_summary = build_bundle_summary(
            bundle_sha256=bundle_ref["sha256"],
            size_bytes=bundle_ref["size_bytes"],
            step_count=len(step_records),
        )
        trace = _trace(
            state,
            "finalize",
            f"已固化 final，loss={best_loss:.6f}，MAE={best['mae']:.6f}",
            renderer_path=renderer_metrics["renderer_path"],
            target_mae=target_mae,
            target_loss=target_loss,
            target_reached=target_reached,
            prepare_duration_ms=renderer_metrics["prepare_duration_ms"],
            uniform_render_count=renderer_metrics["uniform_render_count"],
            uniform_render_p95_ms=renderer_metrics["uniform_render_p95_ms"],
            shader_graph_shadow_status=(
                shader_graph_shadow_summary.get("status")
                if shader_graph_shadow_summary is not None
                else "disabled"
            ),
            shader_graph_topology_sha256=(
                shader_graph_shadow_summary.get("topology_sha256")
                if shader_graph_shadow_summary is not None
                else None
            ),
        )
        manifest = {
            "schema_version": "png_to_shader_min_manifest_v1",
            **run_identity,
            "project_id": project_id,
            "run_id": run_id,
            "status": "completed",
            "stop_reason": state.get("stop_reason", "bounded_mvp_complete"),
            "template_version": materialized.template_version,
            "scene": best["scene"],
            "metrics": metrics,
            "patch_evidence": patch_evidence,
            "shader_graph_shadow": shader_graph_shadow_summary,
            "private_replay_bundle": replay_summary,
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
                "glsl": best["glsl"],
                "render_width": scene.canvas.width,
                "render_height": scene.canvas.height,
                "status": "completed",
                "stop_reason": str(state.get("stop_reason", "bounded_mvp_complete")),
                "template_version": materialized.template_version,
                "quality_preset": str(state.get("quality_preset", "balanced")),
                "current_best_mae": float(best["mae"]),
                "current_best_loss": best_loss,
                "metric_breakdown": score_metrics,
                "render_count": int(state.get("render_count", 0)),
                "render_budget": int(state.get("render_budget", 0)),
                "llm_call_count": int(state.get("llm_call_count", 0)),
                "llm_budget": int(state.get("llm_budget", 0)),
                "refine_budget": int(state.get("refine_budget", 0)),
                "patch_candidate_draw_budget": MAX_PATCH_CANDIDATE_DRAWS,
                "patch_evidence": patch_evidence,
                **run_identity,
                "renderer_path": renderer_metrics["renderer_path"],
                "target_mae": target_mae,
                "target_loss": target_loss,
                "target_reached": target_reached,
                "prepare_duration_ms": renderer_metrics["prepare_duration_ms"],
                "uniform_render_count": renderer_metrics["uniform_render_count"],
                "uniform_render_p95_ms": renderer_metrics["uniform_render_p95_ms"],
                "scene": best["scene"],
                "shader_graph_shadow": shader_graph_shadow_summary,
                "trace": trace,
            },
        }

    def decide_after_render(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("error"):
            action, reason = "finalize", "render_failed"
        elif float(state["current_best_loss"]) <= float(state["target_loss"]):
            action, reason = "finalize", "target_loss_reached"
        elif int(state["render_count"]) >= int(state["render_budget"]):
            action, reason = "finalize", "render_budget_exhausted"
        else:
            action, reason = "optimize_base", "continue"
        return {"next_action": action, "stop_reason": reason}

    def _after_optimization(state: dict[str, Any]) -> dict[str, Any]:
        if float(state["current_best_loss"]) <= float(state["target_loss"]):
            return {"next_action": "finalize", "stop_reason": "target_loss_reached"}
        if int(state["render_count"]) >= int(state["render_budget"]):
            return {"next_action": "finalize", "stop_reason": "render_budget_exhausted"}
        if state.get("feature_queue"):
            return {"next_action": "optimize_feature", "stop_reason": "continue"}
        if int(state.get("llm_call_count", 0)) < int(
            state.get("llm_budget", 0)
        ) and int(state.get("refine_count", 0)) < int(state.get("refine_budget", 0)):
            return {"next_action": "author_refine", "stop_reason": "continue"}
        return {"next_action": "finalize", "stop_reason": "bounded_mvp_complete"}

    return {
        "initialize_run": initialize_run,
        "perceive_target": perceive_target,
        "author_initial": author_initial,
        "materialize_shader": materialize_shader,
        "render_and_evaluate": render_and_evaluate,
        "optimize_base": optimize_base,
        "optimize_feature": optimize_feature,
        "author_refine": author_refine,
        "finalize": finalize,
        "decide_after_render": decide_after_render,
        "decide_after_base": _after_optimization,
        "decide_after_feature": _after_optimization,
    }


__all__ = ["MinRendererRegistry", "make_min_nodes"]
