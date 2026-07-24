"""对固定 scene_mvp Patch fixture 执行 12/32 draw 单因素重放。."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image

if __package__:
    from scripts.run_scene_mvp_run_diagnostics import (
        _feature_fixture,
        _raw_rgb_array,
    )
else:
    from run_scene_mvp_run_diagnostics import (  # type: ignore[no-redef]
        _feature_fixture,
        _raw_rgb_array,
    )
from shaderforge.evaluation import evaluate_min_scene
from shaderforge.generation import materialize_min_shader
from shaderforge.optimization import (
    accepts_strict_total_loss,
    propose_min_scene_candidates,
    rebase_candidate_proposal,
)
from shaderforge.perception import perceive_min_target
from shaderforge.rendering import PlaywrightWebGL1Renderer, PreparedWebGL1Renderer
from shaderforge.scene import MinScene

SCHEMA_VERSION = "scene_mvp_maturity_budget_replay_v1"
ARM12_LOCAL_DRAW_BUDGET = 11
ARM32_LOCAL_DRAW_BUDGET = 31
ROUND_BATCH_SIZE = 16
MATERIAL_REGRESSION_TOLERANCE = 0.01
DEFAULT_SOURCE_RUN = Path(
    "output/png-to-shader/"
    "a7611e43-8bb8-4b6a-ae91-4fbebb2b0e59/"
    "79f51d8a-1aaa-4f92-b806-cd8a44ddf297"
)
DEFAULT_OUTPUT_DIR = Path(
    "output/diagnostics/scene-mvp/maturity-budget-replay/20260723-v2"
)

PatchOperation = Literal[
    "add_feature",
    "replace_feature",
    "remove_feature",
    "replace_color_field",
]
EvaluationCallback = Callable[[MinScene], Awaitable["SceneSnapshot"]]


class RendererDrawFailed(RuntimeError):
    """表示某次 fixture draw 失败，整条臂按生产语义拒绝。."""


@dataclass(frozen=True)
class SceneSnapshot:
    """保存一次已记账 draw 的 Scene、loss、指标与 PNG。."""

    scene: MinScene
    loss: float
    metrics: Mapping[str, Any]
    image_bytes: bytes | None = None


@dataclass(frozen=True)
class ArmResult:
    """一个 maturity 预算臂的完整可复查结果。."""

    name: str
    local_draw_budget: int
    best: SceneSnapshot
    draw_trace: tuple[dict[str, Any], ...]
    accepted_steps: tuple[dict[str, Any], ...]
    local_draw_count: int
    clamp_skip_count: int
    tie_reject_count: int
    renderer_failed: bool
    renderer_error: str | None
    prefix_best_loss: float | None
    prefix_best_scene_sha256: str | None

    def to_dict(self, *, anchor_loss: float) -> dict[str, Any]:
        """返回报告友好的普通字典。."""
        return {
            "name": self.name,
            "local_draw_budget": self.local_draw_budget,
            "raw_draw_count": 1,
            "local_draw_count": self.local_draw_count,
            "total_candidate_draw_count": 1 + self.local_draw_count,
            "clamp_skip_count": self.clamp_skip_count,
            "tie_reject_count": self.tie_reject_count,
            "renderer_failed": self.renderer_failed,
            "renderer_error": self.renderer_error,
            "matured_loss": self.best.loss,
            "accepted_vs_anchor": accepts_strict_total_loss(
                self.best.loss, anchor_loss
            ),
            "accepted_step_count": len(self.accepted_steps),
            "accepted_steps": list(self.accepted_steps),
            "draw_trace": list(self.draw_trace),
            "prefix_best_loss_after_11_draws": self.prefix_best_loss,
            "prefix_best_scene_sha256_after_11_draws": (self.prefix_best_scene_sha256),
            "final_metrics": dict(self.best.metrics),
            "final_scene_sha256": _scene_sha256(self.best.scene),
        }


def _scene_sha256(scene: MinScene) -> str:
    return sha256(
        json.dumps(
            scene.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def maturity_stage_for_patch(
    operation: PatchOperation | str,
) -> tuple[Literal["feature", "color_field"] | None, bool]:
    """把 typed Patch operation 映射到生产 maturity stage。."""
    if operation in {"add_feature", "replace_feature"}:
        return "feature", True
    if operation == "replace_color_field":
        return "color_field", True
    if operation == "remove_feature":
        return None, False
    raise ValueError(f"未知 Patch operation：{operation}")


async def run_maturity_arm(
    raw: SceneSnapshot,
    *,
    name: str,
    local_draw_budget: int,
    stage: Literal["feature", "color_field"],
    feature_id: str | None,
    evaluate: EvaluationCallback,
) -> ArmResult:
    """按生产顺序执行单臂；12 单批，32 才允许有界多轮 re-propose。."""
    if local_draw_budget not in {
        ARM12_LOCAL_DRAW_BUDGET,
        ARM32_LOCAL_DRAW_BUDGET,
    }:
        raise ValueError("只允许冻结的 11/31 local draw 预算。")
    best = raw
    remaining = local_draw_budget
    draw_trace: list[dict[str, Any]] = []
    accepted_steps: list[dict[str, Any]] = []
    clamp_skip_count = 0
    tie_reject_count = 0
    round_index = 0
    renderer_failed = False
    renderer_error: str | None = None
    prefix_best_loss: float | None = None
    prefix_best_scene_sha256: str | None = None

    while remaining > 0:
        round_index += 1
        batch_size = (
            ARM12_LOCAL_DRAW_BUDGET
            if local_draw_budget == ARM12_LOCAL_DRAW_BUDGET
            else ROUND_BATCH_SIZE
        )
        proposals = propose_min_scene_candidates(
            best.scene,
            stage=stage,
            feature_id=feature_id,
            remaining_draw_budget=min(remaining, batch_size),
            batch_size=batch_size,
        )
        if not proposals:
            break
        evaluated_this_round = 0
        for planned in proposals:
            if remaining <= 0:
                break
            rebased = rebase_candidate_proposal(best.scene, planned)
            if rebased is None:
                clamp_skip_count += 1
                continue
            try:
                candidate = await evaluate(rebased.scene)
            except RendererDrawFailed as exc:
                renderer_failed = True
                renderer_error = str(exc)
                break
            remaining -= 1
            evaluated_this_round += 1
            accepted = accepts_strict_total_loss(candidate.loss, best.loss)
            if candidate.loss == best.loss:
                tie_reject_count += 1
            if accepted:
                best = candidate
            record = {
                "draw_index": local_draw_budget - remaining,
                "round": round_index,
                "parameter_path": rebased.parameter.path,
                "direction": rebased.direction,
                "before": rebased.before,
                "after": rebased.after,
                "loss": candidate.loss,
                "accepted": accepted,
                "best_loss_after": best.loss,
                "best_scene_sha256_after": _scene_sha256(best.scene),
            }
            draw_trace.append(record)
            if accepted:
                accepted_steps.append(record)
            if len(draw_trace) == ARM12_LOCAL_DRAW_BUDGET:
                prefix_best_loss = best.loss
                prefix_best_scene_sha256 = _scene_sha256(best.scene)
        if renderer_failed or evaluated_this_round == 0:
            break
        # 生产 12 draw 是单批；只有实验 Arm-32 执行后续有界 re-propose。
        if local_draw_budget == ARM12_LOCAL_DRAW_BUDGET:
            break

    return ArmResult(
        name=name,
        local_draw_budget=local_draw_budget,
        best=best,
        draw_trace=tuple(draw_trace),
        accepted_steps=tuple(accepted_steps),
        local_draw_count=len(draw_trace),
        clamp_skip_count=clamp_skip_count,
        tie_reject_count=tie_reject_count,
        renderer_failed=renderer_failed,
        renderer_error=renderer_error,
        prefix_best_loss=prefix_best_loss,
        prefix_best_scene_sha256=prefix_best_scene_sha256,
    )


def _required_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def evaluate_budget_gate(cases: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """按预声明非空收益门禁决定 32、12 或 inconclusive。."""
    if not cases:
        return {
            "outcome": "inconclusive",
            "reason": "no_gate_cases",
            "gate_case_count": 0,
        }
    normalized: list[dict[str, Any]] = []
    for case in cases:
        arm12_loss = _required_number(case.get("arm12_loss"))
        arm32_loss = _required_number(case.get("arm32_loss"))
        objective_delta = _required_number(case.get("arm32_objective_delta"))
        roi_delta = _required_number(case.get("arm32_max_roi_delta"))
        if (
            arm12_loss is None
            or arm32_loss is None
            or objective_delta is None
            or roi_delta is None
            or bool(case.get("renderer_failed"))
        ):
            return {
                "outcome": "inconclusive",
                "reason": "missing_or_failed_gate_field",
                "gate_case_count": len(cases),
            }
        rescued = bool(case.get("rescued_by_32"))
        clean = (
            rescued
            and objective_delta <= MATERIAL_REGRESSION_TOLERANCE
            and roi_delta <= MATERIAL_REGRESSION_TOLERANCE
        )
        normalized.append(
            {
                "arm12_loss": arm12_loss,
                "arm32_loss": arm32_loss,
                "rescued": rescued,
                "clean": clean,
            }
        )

    losses12 = [item["arm12_loss"] for item in normalized]
    losses32 = [item["arm32_loss"] for item in normalized]
    rescue_count = sum(1 for item in normalized if item["rescued"])
    clean_rescue_count = sum(1 for item in normalized if item["clean"])
    harmful_rescue_count = rescue_count - clean_rescue_count
    aggregate_not_worse = statistics.mean(losses32) <= statistics.mean(
        losses12
    ) and statistics.median(losses32) <= statistics.median(losses12)
    no_internal_material_degradation = all(
        item["arm32_loss"] - item["arm12_loss"] <= MATERIAL_REGRESSION_TOLERANCE
        for item in normalized
    )
    budget32_supported = (
        rescue_count >= 1
        and clean_rescue_count == rescue_count
        and aggregate_not_worse
        and no_internal_material_degradation
    )
    budget12_supported = clean_rescue_count == 0
    if budget32_supported and not budget12_supported:
        outcome = "budget32_supported"
        reason = "nonempty_clean_rescue_without_material_regression"
    elif budget12_supported and not budget32_supported:
        outcome = "budget12_supported"
        reason = "no_clean_rescue"
    else:
        outcome = "inconclusive"
        reason = "mixed_or_conflicting_rescue_evidence"
    return {
        "outcome": outcome,
        "reason": reason,
        "gate_case_count": len(normalized),
        "rescue_count": rescue_count,
        "clean_rescue_count": clean_rescue_count,
        "harmful_rescue_count": harmful_rescue_count,
        "aggregate_not_worse": aggregate_not_worse,
        "no_internal_material_degradation": no_internal_material_degradation,
        "mean_loss": {
            "arm12": statistics.mean(losses12),
            "arm32": statistics.mean(losses32),
        },
        "median_loss": {
            "arm12": statistics.median(losses12),
            "arm32": statistics.median(losses32),
        },
        "material_regression_tolerance": MATERIAL_REGRESSION_TOLERANCE,
    }


def extra_draws_per_rescue(cases: Sequence[Mapping[str, Any]]) -> float | None:
    """只对被 32 draw 救回的 case 计算平均额外 local draw。."""
    rescued = [case for case in cases if bool(case.get("rescued_by_32"))]
    if not rescued:
        return None
    values = [_required_number(case.get("extra_local_draws")) for case in rescued]
    if any(value is None for value in values):
        return None
    return sum(float(value) for value in values if value is not None) / len(rescued)


async def _render_snapshot(
    prepared: PreparedWebGL1Renderer,
    scene: MinScene,
    *,
    reference: Any,
    metric_background: tuple[float, float, float],
) -> SceneSnapshot:
    materialized = materialize_min_shader(scene)
    result = await prepared.render_uniforms(
        materialized.uniform_values,
        capture_png=True,
    )
    if not result.success or result.rgb_bytes is None or result.image_bytes is None:
        raise RendererDrawFailed(result.draw_error or "maturity_replay_renderer_failed")
    rgb = _raw_rgb_array(result.rgb_bytes, scene.canvas.width, scene.canvas.height)
    metric = evaluate_min_scene(reference, rgb, metric_background)
    return SceneSnapshot(
        scene=scene,
        loss=metric.total_loss,
        metrics=metric.to_dict(),
        image_bytes=result.image_bytes,
    )


def _max_roi_delta(
    anchor: Mapping[str, float], candidate: Mapping[str, float]
) -> float:
    shared = anchor.keys() & candidate.keys()
    if not shared:
        return math.nan
    return max(candidate[key] - anchor[key] for key in shared)


def _rgb_png(rgb: Any) -> bytes:
    """把 perception 的目标 RGB 转成与 Renderer 同尺寸的 PNG。."""
    array = np.asarray(rgb, dtype=np.float32)
    if array.ndim != 3 or array.shape[2] != 3:
        raise ValueError("目标 RGB 必须是 HxWx3。")
    image = Image.fromarray(
        np.clip(np.rint(array * 255.0), 0, 255).astype(np.uint8),
        mode="RGB",
    )
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _write_results(
    output_dir: Path,
    report: Mapping[str, Any],
    renders: Sequence[tuple[str, bytes]],
) -> None:
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_dir}")
    render_dir = output_dir / "renders"
    render_dir.mkdir(parents=True)
    for filename, data in renders:
        (render_dir / filename).write_bytes(data)
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


async def run_replay(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    """运行两个冻结 fixture，并写入 local/partial 诊断证据。."""
    try:
        from shaderforge.evaluation import evaluate_render
    except ImportError as exc:
        raise RuntimeError(
            "D076 已退役旧 MinScene external Oracle；"
            "当前只保留 maturity 纯函数、历史源码与既有报告审计。"
        ) from exc
    manifest_path = run_dir / "final/manifest.json"
    metrics_path = run_dir / "final/metrics.json"
    reference_path = run_dir / "input/reference.png"
    required = (manifest_path, metrics_path, reference_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"run 产物不完整：{missing}")
    if output_dir.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖：{output_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    recorded_metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    scene = MinScene.model_validate(manifest["scene"])
    reference_bytes = reference_path.read_bytes()
    perception = perceive_min_target(reference_bytes)
    external_reference_bytes = _rgb_png(perception.target_rgb)
    metric_background = perception.fallback_scene.canvas.background
    materialized = materialize_min_shader(scene)
    renders: list[tuple[str, bytes]] = []
    cases: list[dict[str, Any]] = []

    async with PlaywrightWebGL1Renderer() as renderer:
        prepared = await renderer.prepare(
            materialized.webgl1_source,
            scene.canvas.width,
            scene.canvas.height,
            materialized.uniform_schema,
        )

        async def evaluate(scene_to_render: MinScene) -> SceneSnapshot:
            return await _render_snapshot(
                prepared,
                scene_to_render,
                reference=perception.target_rgb,
                metric_background=metric_background,
            )

        anchor = await evaluate(scene)
        if not math.isclose(
            anchor.loss,
            float(recorded_metrics["total_loss"]),
            abs_tol=1.0e-9,
        ):
            raise RuntimeError(
                "anchor loss 复算不一致："
                f"{anchor.loss}!={recorded_metrics['total_loss']}"
            )
        if anchor.image_bytes is None:
            raise RuntimeError("anchor PNG 缺失。")
        renders.append(("anchor.png", anchor.image_bytes))
        anchor_external = evaluate_render(external_reference_bytes, anchor.image_bytes)
        fixtures = (
            ("underfit_top_left", (0.20, 0.05), 0.60),
            ("overfit_top_left", (0.35, 0.15), 1.20),
        )
        for fixture_name, axes, intensity in fixtures:
            raw_scene = _feature_fixture(
                scene,
                fixture_name=fixture_name,
                axes=axes,
                intensity=intensity,
            )
            feature_id = f"diagnostic_{fixture_name}"
            raw = await evaluate(raw_scene)
            arm12 = await run_maturity_arm(
                raw,
                name="arm12",
                local_draw_budget=ARM12_LOCAL_DRAW_BUDGET,
                stage="feature",
                feature_id=feature_id,
                evaluate=evaluate,
            )
            arm32 = await run_maturity_arm(
                raw,
                name="arm32",
                local_draw_budget=ARM32_LOCAL_DRAW_BUDGET,
                stage="feature",
                feature_id=feature_id,
                evaluate=evaluate,
            )
            prefix12 = [
                (
                    item["parameter_path"],
                    item["direction"],
                    item["before"],
                    item["after"],
                    item["loss"],
                )
                for item in arm12.draw_trace
            ]
            prefix32 = [
                (
                    item["parameter_path"],
                    item["direction"],
                    item["before"],
                    item["after"],
                    item["loss"],
                )
                for item in arm32.draw_trace[: len(prefix12)]
            ]
            prefix_matches = (
                len(prefix12) == ARM12_LOCAL_DRAW_BUDGET
                and prefix12 == prefix32
                and arm12.best.loss == arm32.prefix_best_loss
                and _scene_sha256(arm12.best.scene) == arm32.prefix_best_scene_sha256
            )
            if not prefix_matches:
                raise RuntimeError(f"{fixture_name} 前 11 draw 前缀不一致。")
            if (
                raw.image_bytes is None
                or arm12.best.image_bytes is None
                or arm32.best.image_bytes is None
            ):
                raise RuntimeError(f"{fixture_name} 已记账 PNG 缺失。")
            renders.extend(
                (
                    (f"{fixture_name}-raw.png", raw.image_bytes),
                    (f"{fixture_name}-arm12.png", arm12.best.image_bytes),
                    (f"{fixture_name}-arm32.png", arm32.best.image_bytes),
                )
            )
            external12 = evaluate_render(
                external_reference_bytes, arm12.best.image_bytes
            )
            external32 = evaluate_render(
                external_reference_bytes, arm32.best.image_bytes
            )
            objective_delta32 = external32.total_loss - anchor_external.total_loss
            max_roi_delta32 = _max_roi_delta(
                anchor_external.roi_loss_map,
                external32.roi_loss_map,
            )
            rescued = not accepts_strict_total_loss(
                arm12.best.loss, anchor.loss
            ) and accepts_strict_total_loss(arm32.best.loss, anchor.loss)
            case = {
                "fixture": fixture_name,
                "patch_operation": "add_feature",
                "feature_id": feature_id,
                "anchor_loss": anchor.loss,
                "raw_loss": raw.loss,
                "prefix_matches_first_11_draws": prefix_matches,
                "arm12": arm12.to_dict(anchor_loss=anchor.loss),
                "arm32": arm32.to_dict(anchor_loss=anchor.loss),
                "matured_delta_32_minus_12": arm32.best.loss - arm12.best.loss,
                "extra_local_draws": arm32.local_draw_count - arm12.local_draw_count,
                "rescued_by_32": rescued,
                "external": {
                    "metric_version": external32.metric_version,
                    "anchor": anchor_external.to_dict(),
                    "arm12": external12.to_dict(),
                    "arm32": external32.to_dict(),
                    "arm12_objective_delta_vs_anchor": (
                        external12.total_loss - anchor_external.total_loss
                    ),
                    "arm32_objective_delta_vs_anchor": objective_delta32,
                    "arm12_max_roi_delta_vs_anchor": _max_roi_delta(
                        anchor_external.roi_loss_map,
                        external12.roi_loss_map,
                    ),
                    "arm32_max_roi_delta_vs_anchor": max_roi_delta32,
                    "roi_source": "reference_auto_roi_candidates",
                },
                "gate": {
                    "arm12_loss": arm12.best.loss,
                    "arm32_loss": arm32.best.loss,
                    "arm32_objective_delta": objective_delta32,
                    "arm32_max_roi_delta": max_roi_delta32,
                    "rescued_by_32": rescued,
                    "renderer_failed": (arm12.renderer_failed or arm32.renderer_failed),
                },
            }
            cases.append(case)
        physical_renderer_draw_count = prepared.render_count
        renderer_metadata = (
            prepared.metadata.to_dict() if prepared.metadata is not None else None
        )
        await prepared.close()

    gate = evaluate_budget_gate([case["gate"] for case in cases])
    expected_physical_draws = 1 + sum(
        1
        + int(case["arm12"]["local_draw_count"])
        + int(case["arm32"]["local_draw_count"])
        for case in cases
    )
    if physical_renderer_draw_count != expected_physical_draws:
        raise RuntimeError(
            "Renderer draw 与候选账本不一致："
            f"{physical_renderer_draw_count}!={expected_physical_draws}"
        )
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "experiment_type": "offline_no_model_maturity_budget_replay",
        "durability_status": "local_ignored",
        "run_identity": {
            key: manifest.get(key)
            for key in (
                "project_id",
                "run_id",
                "run_classification",
                "experiment_id",
                "config_fingerprint",
                "report_schema_version",
            )
        },
        "inputs": {
            "run_dir": str(run_dir.resolve()),
            "manifest_sha256": _file_sha256(manifest_path),
            "metrics_sha256": _file_sha256(metrics_path),
            "reference_sha256": _file_sha256(reference_path),
            "external_resized_reference_sha256": sha256(
                external_reference_bytes
            ).hexdigest(),
        },
        "fixed_contract": {
            "shared_raw_snapshot": True,
            "arm12_draw_budget": {
                "raw": 1,
                "local": ARM12_LOCAL_DRAW_BUDGET,
                "total": 12,
                "single_batch": True,
            },
            "arm32_draw_budget": {
                "raw": 1,
                "local": ARM32_LOCAL_DRAW_BUDGET,
                "total": 32,
                "round_batch_size": ROUND_BATCH_SIZE,
            },
            "proposal_order": "production_decrease_all_then_increase_all",
            "prefix_draw_count": ARM12_LOCAL_DRAW_BUDGET,
            "scorer": recorded_metrics["metric_version"],
            "selection": "accepts_strict_total_loss",
            "template_version": manifest["template_version"],
            "metric_background": [float(value) for value in metric_background],
            "external_reference_source": "perception_target_rgb_resized_png",
            "model_call_count": 0,
            "material_regression_tolerance": MATERIAL_REGRESSION_TOLERANCE,
            "gate_revision": "nonempty_clean_rescue_v1",
        },
        "anchor": {
            "loss": anchor.loss,
            "metrics": dict(anchor.metrics),
            "scene_sha256": _scene_sha256(anchor.scene),
            "external": anchor_external.to_dict(),
        },
        "cases": cases,
        "summary": {
            "gate": gate,
            "physical_renderer_draw_count": physical_renderer_draw_count,
            "expected_physical_renderer_draw_count": expected_physical_draws,
            "extra_draws_per_rescue": extra_draws_per_rescue(cases),
            "extra_draw_cost_threshold": {
                "value": 20,
                "informational_only": True,
            },
        },
        "environment": {"renderer": renderer_metadata},
        "notes": [
            "本实验是独立的 local/partial 无模型 fixture 重放，不是冻结 benchmark。",
            "run 79f reference 不属于固定 benchmark；external ROI 来自 reference 自动测量，不是 benchmark key_rois。",
            "Arm-32 复用 Arm-12 前 11 draw，并只接受 strict loss 改善，因此内部不劣近乎由构造保证；机器区分度来自非空 clean rescue 与额外 draw 成本。",
            "两个合成 feature fixture 不能代表真实模型 Patch 分布，结果不能直接修改生产 12 draw 或使 F09 passing。",
            "所有 PNG 都来自已记账 draw，没有 final/contact-sheet 隐藏渲染，也没有模型调用。",
            "阶段 2 应使用 D074 私有 replay bundle 的真实 Patch、固定 7 例和人工盲评。",
        ],
    }
    _write_results(output_dir, report, renders)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="执行 scene_mvp maturity 12/32 draw 单因素重放。"
    )
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_SOURCE_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    """解析 CLI 参数并运行重放实验。."""
    args = _parse_args()
    report = asyncio.run(run_replay(args.run_dir, args.output_dir))
    print(  # noqa: T201
        json.dumps(
            {
                "output_dir": str(args.output_dir.resolve()),
                "gate": report["summary"]["gate"]["outcome"],
                "physical_renderer_draw_count": report["summary"][
                    "physical_renderer_draw_count"
                ],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
