"""LayerPlan/direct GLSL shadow A/B 独立离线 harness（D084 第二阶段）.

本模块只服务 shadow 实验，不接入生产 ``png_to_shader_min`` Graph/runtime、
Backend/API、公开 Artifact 白名单或 durable evidence registry。

冻结语义（对齐 D083/D084 与设计文档第 6 节）：

- Arm A 与 Arm B 尽量使用同一模型、同一 Prompt 主体、请求采样参数与预算；
  预期控制差异只有 LayerPlan——Arm A 不注入，Arm B 注入同一份 LayerPlanV1。
  无 seed 的模型采样、执行顺序和服务端漂移仍是混杂因素，单 run 只作探索。
- LayerPlanV1 由 VisualAnalysisAuthor 直接读取参考图只为 Arm B 生成一次，
  使用独立的 plan 预算与 plan ledger，不消耗任一臂的 direct GLSL Author
  预算；LayerPlan 永久 advisory，候选接受谓词只读取真实 Render + metric
  的 strict total-loss。
- Author 返回的 canonical ``ShaderProgramSpecV1`` 必须再经静态安全校验与
  真实（或注入协议的）WebGL1 prepare+draw；只有成功 draw 后才签发 matching
  attestation，并以真实 metric 严格更新 arm-local ``current_best``。
- 两臂的 LLM/token/repair/compile/draw/wall-clock/program cache 与
  ``current_best`` 完全隔离，执行顺序在查看结果前冻结并写入报告。
- 预算耗尽与所有失败都收敛为预声明的 ``INCONCLUSIVE_CODES``。
- 详细证据只写显式指定的本地私有 run 目录；不调用
  ``LocalArtifactStore.register_run``，不接产品 API/manifest。
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from uuid import uuid4

import numpy as np
from PIL import Image, UnidentifiedImageError

from agent.app.contracts.layerplan_glsl_shadow import (
    SHADOW_METRIC_PREPROCESS,
    LayerPlanV1,
    ShaderProgramSpecV1,
    ValidatedIncumbent,
)
from agent.app.contracts.llm import LLMGateway
from agent.app.nodes.layerplan_glsl_shadow.authors import (
    run_initial_glsl_author,
    run_refine_glsl_author,
    run_visual_analysis_author,
)
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.evaluation import (
    MIN_SCENE_METRIC_VERSION,
    dominant_metric_component,
    evaluate_min_scene,
    summarize_spatial_residual,
)
from shaderforge.program_spec import (
    TRUSTED_VALIDATOR_VERSION,
    AttestationError,
    TrustedReceiptVerifier,
    canonical_json,
    is_executable,
    issue_attestation,
    process_receipt_verifier,
)
from shaderforge.rendering import (
    PreparedRenderResult,
    RendererUnavailableError,
    ShaderPreparationError,
)
from shaderforge.validation import (
    validate_program_spec_safety,
)

SHADOW_EXPERIMENT_ID = "layerplan_glsl_shadow_ab_v1"
REPORT_SCHEMA_VERSION = "layerplan_glsl_shadow_ab_report_v1"
ArmId = Literal["A", "B"]
AuthorRole = Literal["initial", "refine"]
ARM_A: ArmId = "A"
ARM_B: ArmId = "B"
ARM_IDS: tuple[ArmId, ArmId] = (ARM_A, ARM_B)


def _safe_compile_diagnostics(compile_result: Any) -> dict[str, object]:
    """把 Renderer 诊断收敛为不含原始日志/message/GLSL 的摘要."""
    def log_hash(value: str) -> str | None:
        return sha256(value.encode("utf-8")).hexdigest() if value else None

    return {
        "success": bool(compile_result.success),
        "vertex_log_present": bool(compile_result.vertex_log),
        "fragment_log_present": bool(compile_result.fragment_log),
        "link_log_present": bool(compile_result.link_log),
        "vertex_log_sha256": log_hash(compile_result.vertex_log),
        "fragment_log_sha256": log_hash(compile_result.fragment_log),
        "link_log_sha256": log_hash(compile_result.link_log),
        "static_violation_categories": [
            {"code": item.code, "line": item.line}
            for item in compile_result.static_validation.violations
            if item.severity == "error"
        ][:12],
    }
MAX_WORK_SIDE = 256
MAX_CANVAS_SIDE = WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side

# 预算耗尽与失败归类的预声明 inconclusive code，查看结果前冻结，不得事后新增。
INCONCLUSIVE_CODES = frozenset(
    {
        "layer_plan_generation_failed",
        "llm_budget_exhausted",
        "author_output_invalid",
        "author_identity_unavailable",
        "static_validation_failed",
        "compile_or_link_failed",
        "draw_failed",
        "compile_budget_exhausted",
        "draw_budget_exhausted",
        "renderer_unavailable",
        "no_valid_candidate",
    }
)

# 两臂一致的请求采样语义（invoke_min_author 内部按此下发请求）。
# 注意：这只是请求值，不是事实——例如 kimi 端点强制 temperature=1 并以
# reasoning_effort 承载 thinking；每次调用实际生效的身份以 Gateway 记录、
# 绑定进 author_identity.sampling_params 的 effective 值为准。
REQUESTED_SAMPLING_PARAMS: Mapping[str, Any] = {
    "temperature": 0,
    "thinking": "off",
    "response_format": "json_object",
}


class ShadowABConfigError(ValueError):
    """shadow A/B 配置违反冻结语义的 fail-closed 错误."""


class ShadowPreparedRenderer(Protocol):
    """harness 依赖的最小 prepared program 协议（真实或注入实现）."""

    async def render_uniforms(
        self,
        uniform_values: Mapping[str, Any],
        *,
        capture_png: bool = False,
        receipt_spec_sha256: str | None = None,
    ) -> PreparedRenderResult:
        """上传完整 typed uniform 值集并绘制；成功 draw 必须就地签发 receipt."""
        ...

    async def close(self) -> None:
        """释放 program/context."""
        ...


class ShadowRenderer(Protocol):
    """harness 依赖的最小 Renderer 协议（真实或注入实现）."""

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> ShadowPreparedRenderer:
        """静态校验并一次性编译/链接固定 program."""
        ...


@dataclass(frozen=True)
class ShadowABConfig:
    """两臂共享的冻结实验配置；预期控制差异 LayerPlan 不在本配置中.

    ``plan_llm_budget`` 是 VisualAnalysis/LayerPlan 的独立预算，与任一臂的
    direct GLSL Author 预算完全分离：LayerPlan 生成不得消耗 Arm B 的
    Author 预算，否则会额外引入预算混杂因素。
    """

    direct_author_llm_budget: int = 8
    compile_budget_per_arm: int = 8
    draw_budget_per_arm: int = 8
    refine_budget_per_arm: int = 2
    plan_llm_budget: int = 2
    arm_order: tuple[ArmId, ArmId] = (ARM_A, ARM_B)
    canvas_width: int | None = None
    canvas_height: int | None = None
    implementation_identity_sha256: str | None = None

    def __post_init__(self) -> None:
        """校验预算与臂顺序，非法配置 fail-closed."""
        for name in (
            "direct_author_llm_budget",
            "compile_budget_per_arm",
            "draw_budget_per_arm",
            "refine_budget_per_arm",
            "plan_llm_budget",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ShadowABConfigError(f"{name} 必须是非负整数。")
        if sorted(self.arm_order) != [ARM_A, ARM_B]:
            raise ShadowABConfigError("arm_order 必须是 A/B 的一次排列。")
        for name in ("canvas_width", "canvas_height"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
            ):
                raise ShadowABConfigError(f"{name} 必须是正整数或 None。")
        if (self.canvas_width is None) != (self.canvas_height is None):
            raise ShadowABConfigError("canvas_width 与 canvas_height 必须同时给出。")
        if self.implementation_identity_sha256 is not None and (
            not isinstance(self.implementation_identity_sha256, str)
            or len(self.implementation_identity_sha256) != 64
            or any(
                char not in "0123456789abcdef"
                for char in self.implementation_identity_sha256
            )
        ):
            raise ShadowABConfigError(
                "implementation_identity_sha256 必须是小写 SHA-256 或 None。"
            )
        if (
            self.canvas_width is not None
            and self.canvas_height is not None
            and max(self.canvas_width, self.canvas_height) > MAX_CANVAS_SIDE
        ):
            raise ShadowABConfigError(
                f"显式画布长边不得超过 Renderer 契约上限 {MAX_CANVAS_SIDE}。"
            )

    def fingerprint(self) -> str:
        """返回冻结配置的内容寻址指纹."""
        return sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化的冻结配置."""
        payload = {
            "direct_author_llm_budget": self.direct_author_llm_budget,
            "compile_budget_per_arm": self.compile_budget_per_arm,
            "draw_budget_per_arm": self.draw_budget_per_arm,
            "refine_budget_per_arm": self.refine_budget_per_arm,
            "plan_llm_budget": self.plan_llm_budget,
            "arm_order": list(self.arm_order),
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "requested_sampling_params": dict(REQUESTED_SAMPLING_PARAMS),
            "sampling_identity_note": (
                "requested 只是请求值；每次调用实际生效的 provider/model/"
                "temperature/reasoning_effort 以各候选 author_identity."
                "sampling_params 的 effective 记录为准。"
            ),
        }
        if self.implementation_identity_sha256 is not None:
            payload["implementation_identity_sha256"] = (
                self.implementation_identity_sha256
            )
        return payload


@dataclass
class ArmLedger:
    """单臂独立记账：LLM/token/repair/compile/draw/wall-clock 与缓存."""

    arm_id: ArmId
    llm_call_count: int = 0
    total_tokens: int | None = 0
    repair_count: int = 0
    compile_count: int = 0
    draw_count: int = 0
    cache_hits: int = 0
    wall_clock_ms: float = 0.0
    rejected_candidates: int = 0
    accepted_candidates: int = 0

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化 ledger 摘要."""
        return {
            "arm_id": self.arm_id,
            "llm_call_count": self.llm_call_count,
            "total_tokens": self.total_tokens,
            "repair_count": self.repair_count,
            "compile_count": self.compile_count,
            "draw_count": self.draw_count,
            "cache_hits": self.cache_hits,
            "wall_clock_ms": round(self.wall_clock_ms, 2),
            "rejected_candidates": self.rejected_candidates,
            "accepted_candidates": self.accepted_candidates,
        }


@dataclass
class PlanLedger:
    """VisualAnalysis/LayerPlan 的独立记账，不属于任一臂的 Author ledger."""

    llm_call_count: int = 0
    total_tokens: int | None = 0
    repair_count: int = 0
    wall_clock_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化 LayerPlan 生成记账摘要."""
        return {
            "llm_call_count": self.llm_call_count,
            "total_tokens": self.total_tokens,
            "repair_count": self.repair_count,
            "wall_clock_ms": round(self.wall_clock_ms, 2),
        }


@dataclass(frozen=True)
class ShadowCandidate:
    """一次成功 draw 并经 matching attestation 的候选快照（V2 语义，不可变）."""

    spec: ShaderProgramSpecV1
    role: AuthorRole
    sequence: int
    rgb_bytes: bytes
    png_bytes: bytes
    mae: float
    loss: float
    metrics: dict[str, Any]
    residual_summary: dict[str, Any]
    parent_spec_sha256: str | None
    provenance: str = "model_generated_direct_glsl"


@dataclass
class ArmResult:
    """单臂执行结果：状态、ledger、候选演进与 arm-local current_best.

    ``plan_ledger`` 只在 Arm B 上存在：LayerPlan 生成有独立预算与记账，
    不消耗该臂 direct GLSL Author 的 LLM 预算。
    """

    arm_id: ArmId
    status: Literal["ok", "inconclusive"]
    inconclusive_code: str | None
    ledger: ArmLedger
    layer_plan_sha256: str | None
    plan_ledger: PlanLedger | None = None
    candidates: list[ShadowCandidate] = field(default_factory=list)
    current_best: ShadowCandidate | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        """返回臂级摘要（内容寻址，不内嵌大图/源码）."""
        return {
            "arm_id": self.arm_id,
            "status": self.status,
            "inconclusive_code": self.inconclusive_code,
            "layer_plan_sha256": self.layer_plan_sha256,
            "ledger": self.ledger.to_dict(),
            "plan_ledger": (
                self.plan_ledger.to_dict() if self.plan_ledger is not None else None
            ),
            "current_best": (
                {
                    "spec_sha256": self.current_best.spec.spec_sha256,
                    "loss": self.current_best.loss,
                    "mae": self.current_best.mae,
                    "role": self.current_best.role,
                    "sequence": self.current_best.sequence,
                }
                if self.current_best is not None
                else None
            ),
            "candidates": [
                {
                    "spec_sha256": item.spec.spec_sha256,
                    "role": item.role,
                    "sequence": item.sequence,
                    "loss": item.loss,
                    "mae": item.mae,
                    "parent_spec_sha256": item.parent_spec_sha256,
                    "provenance": item.provenance,
                    "metric_sha256": sha256(
                        canonical_json(item.metrics).encode("utf-8")
                    ).hexdigest(),
                    "residual_sha256": sha256(
                        canonical_json(item.residual_summary).encode("utf-8")
                    ).hexdigest(),
                    "is_current_best": item is self.current_best,
                }
                for item in self.candidates
            ],
            "events": list(self.events),
        }


@dataclass(frozen=True)
class ShadowABRunResult:
    """shadow A/B 一次完整执行的内存结果，等待写入私有 run 目录."""

    reference_image: bytes
    reference_content_type: str
    instruction: str
    canvas_width: int
    canvas_height: int
    config: ShadowABConfig
    config_fingerprint: str
    reference_sha256: str
    instruction_sha256: str
    layer_plan: LayerPlanV1 | None
    arms: tuple[ArmResult, ArmResult]
    execution_order: tuple[ArmId, ArmId]
    status: Literal["ok", "inconclusive"]
    background: tuple[float, float, float]


def derive_canvas(image: Image.Image) -> tuple[int, int]:
    """按 scene_mvp 同一缩放规则推导工作画布（长边 ≤ MAX_WORK_SIDE，短边 ≥ 16）."""
    width, height = image.size
    scale = min(1.0, MAX_WORK_SIDE / max(width, height))
    return max(16, round(width * scale)), max(16, round(height * scale))


def border_background(rgb: np.ndarray) -> tuple[float, float, float]:
    """按 scene_mvp 同一边界中位数规则推导 metric 背景色."""
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    median = np.median(border, axis=0)
    return (float(median[0]), float(median[1]), float(median[2]))


def decode_reference(image_bytes: bytes) -> Image.Image:
    """解码参考图为 RGB PIL 图像；失败 fail-closed."""
    try:
        return Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ShadowABConfigError("无法解码参考图。") from exc


def is_strict_improvement(candidate_loss: float, incumbent_loss: float) -> bool:
    """Strict total-loss 严格改善；只读取真实 metric，绝不读取 LayerPlan."""
    return bool(np.isfinite(candidate_loss)) and candidate_loss < incumbent_loss


def _accumulate_token_usage(
    current_total: int | None,
    observed_total: int | None,
    *,
    call_count: int,
) -> int | None:
    """聚合实际调用的完整 usage；零调用不改变既有总量."""
    if call_count == 0:
        return current_total
    if current_total is None or observed_total is None:
        return None
    return current_total + observed_total


def _program_cache_key(spec: ShaderProgramSpecV1) -> tuple[Any, ...]:
    """返回可编译 program 的缓存 key：source + uniform schema 类型 + canvas/contract.

    只绑定编译产物，不绑定 uniform_values 或 author 身份；同一 program 的
    新 Spec 复用 prepared handle 跳过 compile，但仍各自 draw、签发自己的
    attestation、形成自己的候选。
    """
    schema_signature = tuple(
        sorted((item.name, item.type) for item in spec.uniform_schema)
    )
    return (
        spec.source_sha256,
        schema_signature,
        spec.canvas.width,
        spec.canvas.height,
        spec.renderer_contract_id,
    )


class LayerPlanGlslShadowRunner:
    """执行隔离的 LayerPlan/direct GLSL shadow A/B（离线，私有证据）."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        renderer: ShadowRenderer,
        config: ShadowABConfig | None = None,
        clock: Callable[[], float] = time.perf_counter,
        receipt_issuer: TrustedReceiptVerifier | None = None,
    ) -> None:
        """注入统一 Gateway、真实或协议注入的 Renderer、冻结配置与 receipt 信任根.

        ``receipt_issuer`` 缺省使用进程级实例；测试必须显式注入自己的
        test-only 实例，绝不与生产 CLI 共享信任根。
        """
        self._gateway = gateway
        self._renderer = renderer
        self._config = config or ShadowABConfig()
        self._clock = clock
        self._receipt_issuer = receipt_issuer or process_receipt_verifier()

    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
    ) -> ShadowABRunResult:
        """按冻结顺序执行两臂并返回内存结果；失败收敛为预声明 code."""
        config = self._config
        image = decode_reference(reference_image)
        canvas_width, canvas_height = (
            (config.canvas_width, config.canvas_height)
            if config.canvas_width is not None and config.canvas_height is not None
            else derive_canvas(image)
        )
        assert canvas_width is not None and canvas_height is not None
        if (canvas_width, canvas_height) != image.size:
            image = image.resize(
                (canvas_width, canvas_height), Image.Resampling.LANCZOS
            )
        target_rgb = np.asarray(image, dtype=np.float32) / 255.0
        reference_sha256 = sha256(reference_image).hexdigest()
        instruction_sha256 = sha256(instruction.encode("utf-8")).hexdigest()
        background = border_background(target_rgb)

        arms: dict[ArmId, ArmResult] = {
            arm_id: ArmResult(
                arm_id=arm_id,
                status="inconclusive",
                inconclusive_code=None,
                ledger=ArmLedger(arm_id),
                layer_plan_sha256=None,
            )
            for arm_id in ARM_IDS
        }
        sequence_counter = itertools.count(1)
        layer_plan: LayerPlanV1 | None = None

        for arm_id in config.arm_order:
            arm = arms[arm_id]
            if arm_id == ARM_B:
                layer_plan = await self._ensure_layer_plan(
                    arm,
                    sequence=next(sequence_counter),
                    reference_image=reference_image,
                    content_type=content_type,
                    instruction=instruction,
                )
                if layer_plan is None:
                    arm.inconclusive_code = "layer_plan_generation_failed"
                    continue
                arm.layer_plan_sha256 = layer_plan.plan_sha256
            await self._run_arm(
                arm,
                next_sequence=lambda: next(sequence_counter),
                layer_plan=layer_plan if arm_id == ARM_B else None,
                reference_image=reference_image,
                content_type=content_type,
                instruction=instruction,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                target_rgb=target_rgb,
                background=background,
            )

        by_id = {arm.arm_id: arm for arm in arms.values()}
        status: Literal["ok", "inconclusive"] = (
            "ok"
            if all(by_id[arm_id].status == "ok" for arm_id in ARM_IDS)
            else "inconclusive"
        )
        return ShadowABRunResult(
            reference_image=reference_image,
            reference_content_type=content_type,
            instruction=instruction,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            config=config,
            config_fingerprint=config.fingerprint(),
            reference_sha256=reference_sha256,
            instruction_sha256=instruction_sha256,
            layer_plan=layer_plan,
            arms=(by_id[ARM_A], by_id[ARM_B]),
            execution_order=config.arm_order,
            status=status,
            background=background,
        )

    async def _ensure_layer_plan(
        self,
        arm: ArmResult,
        *,
        sequence: int,
        reference_image: bytes,
        content_type: str,
        instruction: str,
    ) -> LayerPlanV1 | None:
        """只为 Arm B 生成一次 LayerPlan；使用独立 plan 预算与 plan ledger.

        LayerPlan 生成不消耗 Arm B 的 direct GLSL Author 预算与记账，
        保证两臂 Author 输入除 LayerPlan 外的预算语义完全一致。
        """
        config = self._config
        plan_ledger = arm.plan_ledger
        if plan_ledger is None:
            plan_ledger = PlanLedger()
            arm.plan_ledger = plan_ledger
        remaining = config.plan_llm_budget - plan_ledger.llm_call_count
        started = self._clock()
        result = await run_visual_analysis_author(
            gateway=self._gateway,
            reference_image=reference_image,
            content_type=content_type,
            user_instruction=instruction,
            remaining_calls=remaining,
        )
        plan_ledger.wall_clock_ms += (self._clock() - started) * 1000.0
        plan_ledger.llm_call_count += result.call_count
        plan_ledger.total_tokens = _accumulate_token_usage(
            plan_ledger.total_tokens,
            result.total_tokens,
            call_count=result.call_count,
        )
        plan_ledger.repair_count += 1 if result.repaired else 0
        arm.events.append(
            {
                "sequence": sequence,
                "kind": "visual_analysis",
                "ok": result.plan is not None,
                "error_code": result.error_code,
                "repaired": result.repaired,
                "call_count": result.call_count,
            }
        )
        return result.plan

    async def _run_arm(
        self,
        arm: ArmResult,
        *,
        next_sequence: Callable[[], int],
        layer_plan: LayerPlanV1 | None,
        reference_image: bytes,
        content_type: str,
        instruction: str,
        canvas_width: int,
        canvas_height: int,
        target_rgb: np.ndarray,
        background: tuple[float, float, float],
    ) -> None:
        """执行单臂 Initial + 有界 Refine，严格更新 arm-local current_best.

        program cache 只绑定可编译 program（source_sha256 + uniform schema
        类型 + canvas/contract）：命中时复用 prepared handle 跳过 compile，
        但每个新 Spec 仍各自 draw、签发自己的 attestation、形成自己的候选；
        所有 prepared handle 在臂结束时统一关闭。
        """
        config = self._config
        program_cache: dict[tuple[Any, ...], ShadowPreparedRenderer] = {}

        async def propose(role: AuthorRole) -> None:
            sequence = next_sequence()
            remaining = config.direct_author_llm_budget - arm.ledger.llm_call_count
            started = self._clock()
            if role == "initial":
                result = await run_initial_glsl_author(
                    gateway=self._gateway,
                    reference_image=reference_image,
                    content_type=content_type,
                    user_instruction=instruction,
                    canvas_width=canvas_width,
                    canvas_height=canvas_height,
                    layer_plan=layer_plan,
                    remaining_calls=remaining,
                )
                spec = result.spec
                parent_spec_sha256: str | None = None
                error_code = result.error_code
                call_count = result.call_count
                total_tokens = result.total_tokens
                repaired = result.repaired
            else:
                incumbent = arm.current_best
                if incumbent is None:
                    return
                refine_result = await run_refine_glsl_author(
                    gateway=self._gateway,
                    reference_image=reference_image,
                    content_type=content_type,
                    current_render=incumbent.png_bytes,
                    user_instruction=instruction,
                    incumbent=ValidatedIncumbent(
                        program_spec=incumbent.spec,
                        mae=incumbent.mae,
                        loss=incumbent.loss,
                        metrics=dict(incumbent.metrics),
                        residual_summary=dict(incumbent.residual_summary),
                    ),
                    layer_plan=layer_plan,
                    remaining_calls=remaining,
                )
                spec = refine_result.spec
                parent_spec_sha256 = refine_result.parent_spec_sha256
                error_code = refine_result.error_code
                call_count = refine_result.call_count
                total_tokens = refine_result.total_tokens
                repaired = refine_result.repaired
            arm.ledger.wall_clock_ms += (self._clock() - started) * 1000.0
            arm.ledger.llm_call_count += call_count
            arm.ledger.total_tokens = _accumulate_token_usage(
                arm.ledger.total_tokens,
                total_tokens,
                call_count=call_count,
            )
            arm.ledger.repair_count += 1 if repaired else 0
            if spec is None:
                arm.ledger.rejected_candidates += 1
                arm.events.append(
                    {
                        "sequence": sequence,
                        "kind": role,
                        "ok": False,
                        "error_code": error_code or "author_output_invalid",
                        "repaired": repaired,
                        "call_count": call_count,
                    }
                )
                self._fail(arm, role, error_code or "author_output_invalid")
                return

            candidate = await self._validate_render_attest(
                arm,
                spec=spec,
                role=role,
                sequence=sequence,
                parent_spec_sha256=parent_spec_sha256,
                target_rgb=target_rgb,
                background=background,
                program_cache=program_cache,
            )
            if candidate is None:
                return
            arm.candidates.append(candidate)
            self._consider(arm, candidate)

        try:
            await propose("initial")
            if arm.current_best is None:
                if arm.inconclusive_code is None:
                    arm.inconclusive_code = "no_valid_candidate"
                return
            arm.status = "ok"
            for _ in range(config.refine_budget_per_arm):
                await propose("refine")
        finally:
            for prepared in program_cache.values():
                try:
                    await prepared.close()
                except Exception:  # noqa: BLE001 - 释放失败不掩盖臂结论
                    pass

    @staticmethod
    def _fail(arm: ArmResult, role: AuthorRole, error_code: str) -> None:
        """把 Initial 失败收敛为预声明 inconclusive code；Refine 失败只丢弃候选."""
        if role != "initial":
            return
        if error_code in INCONCLUSIVE_CODES:
            arm.inconclusive_code = error_code
        elif error_code.startswith("llm_"):
            arm.inconclusive_code = "llm_budget_exhausted"
        else:
            arm.inconclusive_code = "author_output_invalid"

    def _consider(self, arm: ArmResult, candidate: ShadowCandidate) -> None:
        """按 strict total-loss 更新 arm-local current_best；不读取 LayerPlan."""
        incumbent = arm.current_best
        if incumbent is None or is_strict_improvement(candidate.loss, incumbent.loss):
            arm.current_best = candidate
            arm.ledger.accepted_candidates += 1
        else:
            arm.ledger.rejected_candidates += 1

    async def _validate_render_attest(
        self,
        arm: ArmResult,
        *,
        spec: ShaderProgramSpecV1,
        role: AuthorRole,
        sequence: int,
        parent_spec_sha256: str | None,
        target_rgb: np.ndarray,
        background: tuple[float, float, float],
        program_cache: dict[tuple[Any, ...], ShadowPreparedRenderer],
    ) -> ShadowCandidate | None:
        """静态校验 → prepare（或 program cache 复用）→ draw → attestation → metric.

        任何一步失败都不得渲染为候选；attestation 只在成功 draw 后签发。
        program cache 命中只跳过 compile；新 Spec 仍真实 draw 并签发自己的
        attestation，绝不把旧候选冒充新 Spec。
        """
        config = self._config
        ledger = arm.ledger

        def reject(error_code: str, **extra: Any) -> None:
            ledger.rejected_candidates += 1
            arm.events.append(
                {
                    "sequence": sequence,
                    "kind": role,
                    "ok": False,
                    "error_code": error_code,
                    "spec_sha256": spec.spec_sha256,
                    **extra,
                }
            )
            if role == "initial":
                arm.inconclusive_code = error_code

        started = self._clock()
        static_result = validate_program_spec_safety(spec)
        if not static_result.valid:
            reject(
                "static_validation_failed",
                violations=[item.code for item in static_result.violations],
            )
            return None

        cache_key = _program_cache_key(spec)
        cached = program_cache.get(cache_key)
        cache_hit = cached is not None
        if cached is not None:
            ledger.cache_hits += 1
            prepared = cached
        else:
            if ledger.compile_count >= config.compile_budget_per_arm:
                reject("compile_budget_exhausted")
                return None
            uniform_schema = {item.name: item.type for item in spec.uniform_schema}
            ledger.compile_count += 1
            try:
                prepared = await self._renderer.prepare(
                    spec.fragment_source,
                    spec.canvas.width,
                    spec.canvas.height,
                    uniform_schema,
                )
            except ShaderPreparationError as exc:
                ledger.wall_clock_ms += (self._clock() - started) * 1000.0
                reject(
                    "compile_or_link_failed",
                    diagnostics=_safe_compile_diagnostics(exc.compile_result),
                )
                return None
            except (RendererUnavailableError, ValueError, OSError) as exc:
                ledger.wall_clock_ms += (self._clock() - started) * 1000.0
                reject("renderer_unavailable", detail=type(exc).__name__)
                return None
            program_cache[cache_key] = prepared

        try:
            if ledger.draw_count >= config.draw_budget_per_arm:
                reject("draw_budget_exhausted")
                return None
            ledger.draw_count += 1
            draw = await prepared.render_uniforms(
                dict(spec.uniform_values),
                capture_png=True,
                receipt_spec_sha256=spec.spec_sha256,
            )
            ledger.wall_clock_ms += (self._clock() - started) * 1000.0
            if not draw.success or draw.rgb_bytes is None or draw.image_bytes is None:
                reject("draw_failed", draw_error=draw.draw_error)
                return None
        except (RendererUnavailableError, ValueError, OSError) as exc:
            ledger.wall_clock_ms += (self._clock() - started) * 1000.0
            reject("renderer_unavailable", detail=type(exc).__name__)
            return None

        # receipt 只能来自 renderer 的成功 prepare+draw 路径（真实 renderer
        # 就地签发；fake 测试用显式 test-only issuer 签发）；runner 绝不自行
        # 签发或填写执行结论。缺失、伪造、篡改或像素绑定不符一律 fail-closed。
        receipt = draw.execution_receipt
        if receipt is None:
            reject("static_validation_failed", detail="receipt_missing")
            return None
        required_runtime = ("browser_version", "gl_version", "glsl_version")
        if (
            sha256(draw.rgb_bytes).hexdigest() != receipt.rgb_sha256
            or receipt.png_sha256 is None
            or sha256(draw.image_bytes).hexdigest() != receipt.png_sha256
            or any(not receipt.runtime_metadata.get(key) for key in required_runtime)
        ):
            reject("static_validation_failed", detail="receipt_pixel_mismatch")
            return None
        attested = None
        try:
            attested = spec.with_attestation(
                issue_attestation(
                    spec,
                    receipt=receipt,
                    static_ok=True,
                    issuer=self._receipt_issuer,
                )
            )
        except AttestationError as exc:
            reject("static_validation_failed", detail=exc.code)
            return None
        if not is_executable(attested, issuer=self._receipt_issuer):
            reject("static_validation_failed", detail="attestation_mismatch")
            return None

        rendered = (
            np.frombuffer(draw.rgb_bytes, dtype=np.uint8)
            .reshape(spec.canvas.height, spec.canvas.width, 3)
            .astype(np.float32)
            / 255.0
        )
        metric = evaluate_min_scene(target_rgb, rendered, background)
        residual_summary = summarize_spatial_residual(target_rgb, rendered)
        residual_summary["dominant_metric_component"] = dominant_metric_component(
            metric
        )
        arm.events.append(
            {
                "sequence": sequence,
                "kind": role,
                "ok": True,
                "spec_sha256": attested.spec_sha256,
                "loss": metric.total_loss,
                "mae": metric.global_mae,
                "validator_version": TRUSTED_VALIDATOR_VERSION,
                "cache_hit": cache_hit,
            }
        )
        return ShadowCandidate(
            spec=attested,
            role=role,
            sequence=sequence,
            rgb_bytes=draw.rgb_bytes,
            png_bytes=draw.image_bytes,
            mae=metric.global_mae,
            loss=metric.total_loss,
            metrics=metric.to_dict(),
            residual_summary=residual_summary,
            parent_spec_sha256=parent_spec_sha256,
        )


def _spec_to_dict(spec: ShaderProgramSpecV1) -> dict[str, Any]:
    """返回 canonical Spec 的规范化字典（含 attestation），仅写私有 run 目录."""
    return {
        "schema_version": spec.schema_version,
        "renderer_contract_id": spec.renderer_contract_id,
        "fragment_source": spec.fragment_source,
        "uniform_schema": [item.to_dict() for item in spec.uniform_schema],
        "uniform_values": {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in spec.uniform_values.items()
        },
        "tunable_manifest": [item.to_dict() for item in spec.tunable_manifest],
        "canvas": spec.canvas.to_dict(),
        "source_sha256": spec.source_sha256,
        "binding_sha256": spec.binding_sha256,
        "spec_sha256": spec.spec_sha256,
        "author_identity": spec.author_identity.to_dict(),
        "validation_attestation": (
            spec.validation_attestation.to_dict()
            if spec.validation_attestation is not None
            else None
        ),
    }


def _layer_plan_to_dict(plan: LayerPlanV1) -> dict[str, Any]:
    """返回 canonical LayerPlan 的规范化字典，只写私有 run 目录."""
    return {
        "schema_version": plan.schema_version,
        "layers": [layer.to_dict() for layer in plan.layers],
        "reference_sha256": plan.reference_sha256,
        "author_identity": plan.author_identity.to_dict(),
        "observations_ref": plan.observations_ref,
        "plan_sha256": plan.plan_sha256,
    }


def build_report_payload(result: ShadowABRunResult) -> dict[str, Any]:
    """组装内容寻址报告主体（不含文件哈希与 report_sha256）."""
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "experiment_id": SHADOW_EXPERIMENT_ID,
        "run_classification": "independent_experiment",
        "status": result.status,
        "config": result.config.to_dict(),
        "config_fingerprint": result.config_fingerprint,
        "reference_sha256": result.reference_sha256,
        "reference_content_type": result.reference_content_type,
        "instruction_sha256": result.instruction_sha256,
        "canvas": {"width": result.canvas_width, "height": result.canvas_height},
        "evaluation": {
            "metric_version": MIN_SCENE_METRIC_VERSION,
            "preprocess": dict(SHADOW_METRIC_PREPROCESS),
            "background": [float(value) for value in result.background],
        },
        "layer_plan_sha256": (
            result.layer_plan.plan_sha256 if result.layer_plan is not None else None
        ),
        "execution_order": list(result.execution_order),
        "arms": [arm.to_summary_dict() for arm in result.arms],
        "inconclusive_codes": sorted(INCONCLUSIVE_CODES),
        "validity_notes": [
            "无 seed 且 kimi 端点强制 temperature=1：单次 A/B 只具有探索性，"
            "不得声称 LayerPlan 是唯一因果变量。",
            "任何结论必须基于多轮重复与 AB/BA 交叉平衡，单 run 报告不构成因果证据。",
            "每次调用实际生效的模型/采样身份以 author_identity.sampling_params "
            "的 effective 记录为准，requested_sampling_params 不是事实。",
        ],
        "durability_status": "local_private_not_registered",
    }


def shadow_run_id(result: ShadowABRunResult) -> str:
    """由完整报告主体推导内容寻址 run id，允许随机模型的重复实验.

    只绑定输入/配置会让同配置的多轮 stochastic A/B 永远撞同一目录，和
    “多轮重复 + AB/BA”门禁冲突。报告主体已包含 plan/spec/author/effective
    identity/metric/执行顺序摘要；用其 canonical hash 后，不同实际结果会
    得到不同 run_id，完全相同的重复结果仍 write-once 拒绝覆盖。
    """
    return _shadow_run_id_from_report_body(build_report_payload(result))


def _shadow_run_id_from_report_body(report_body: Mapping[str, Any]) -> str:
    """由不含文件清单/报告哈希的 canonical 报告主体推导 run id."""
    digest = sha256(canonical_json(dict(report_body)).encode("utf-8")).hexdigest()
    return f"shadow-{digest[:12]}"


def _write_text(run_dir: Path, relative: str, text: str) -> str:
    """写入 UTF-8 文本（0600）并返回内容 SHA-256."""
    path = run_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return sha256(text.encode("utf-8")).hexdigest()


def _write_bytes(run_dir: Path, relative: str, data: bytes) -> str:
    """写入二进制内容（0600）并返回内容 SHA-256."""
    path = run_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return sha256(data).hexdigest()


def write_shadow_run(result: ShadowABRunResult, output_root: Path) -> Path:
    """把详细证据写入显式私有 run 目录并生成内容寻址报告.

    只写 ``output_root/shadow-<id>/``；不调用
    ``LocalArtifactStore.register_run``，不接产品 API/manifest，不登记
    durable evidence。写入采用同根 staging 目录 + 原子 rename：崩溃只会在
    ``output_root`` 下留下 ``.shadow-<id>.staging-*`` 半成品，绝不占用最终
    run_id；目标已存在或是 symlink 时 fail-closed，绝不覆盖历史证据。
    私有权限：目录 0700、文件 0600。
    """
    if output_root.is_symlink():
        raise ShadowEvidenceError(f"output_root 不得是 symlink：{output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    run_id = shadow_run_id(result)
    run_dir = output_root / run_id
    staging = output_root / f".{run_id}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    staging.mkdir(mode=0o700)
    try:
        files = _write_run_files(result, staging)
        payload = build_report_payload(result)
        payload["files"] = files
        payload["report_sha256"] = sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        _write_text(staging, "report.json", canonical_json(payload) + "\n")
        # 统一收紧 staging 内全部目录权限（mkdir 的中间目录受 umask 影响）。
        for path in itertools.chain([staging], staging.rglob("*")):
            if path.is_dir():
                os.chmod(path, 0o700)
        if run_dir.exists() or run_dir.is_symlink():
            raise FileExistsError(f"shadow 私有 run 目录已存在：{run_dir}")
        # 同文件系统 rename 是原子提交：最终 run_id 要么完整出现，要么不出现。
        os.rename(staging, run_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return run_dir


def _write_run_files(result: ShadowABRunResult, run_dir: Path) -> dict[str, str]:
    """把除报告外的全部证据文件写入 run 目录，返回相对路径到内容哈希的映射."""
    files: dict[str, str] = {}
    files["input/reference"] = _write_bytes(
        run_dir, "input/reference", result.reference_image
    )
    files["input/instruction.txt"] = _write_text(
        run_dir, "input/instruction.txt", result.instruction
    )
    files["config.json"] = _write_text(
        run_dir, "config.json", canonical_json(result.config.to_dict()) + "\n"
    )
    if result.layer_plan is not None:
        files["layer_plan.json"] = _write_text(
            run_dir,
            "layer_plan.json",
            canonical_json(_layer_plan_to_dict(result.layer_plan)) + "\n",
        )
    for arm in result.arms:
        prefix = f"arms/{arm.arm_id}"
        files[f"{prefix}/ledger.json"] = _write_text(
            run_dir,
            f"{prefix}/ledger.json",
            canonical_json(arm.to_summary_dict()) + "\n",
        )
        for index, candidate in enumerate(arm.candidates, start=1):
            candidate_dir = (
                f"{prefix}/candidates/"
                f"{index:03d}-{candidate.role}-{candidate.spec.spec_sha256[:8]}"
            )
            files[f"{candidate_dir}/spec.json"] = _write_text(
                run_dir,
                f"{candidate_dir}/spec.json",
                canonical_json(_spec_to_dict(candidate.spec)) + "\n",
            )
            files[f"{candidate_dir}/render.png"] = _write_bytes(
                run_dir, f"{candidate_dir}/render.png", candidate.png_bytes
            )
            metric_sha256 = sha256(
                canonical_json(candidate.metrics).encode("utf-8")
            ).hexdigest()
            residual_sha256 = sha256(
                canonical_json(candidate.residual_summary).encode("utf-8")
            ).hexdigest()
            metrics_payload = {
                "mae": candidate.mae,
                "loss": candidate.loss,
                "metrics": candidate.metrics,
                "residual_summary": candidate.residual_summary,
                "parent_spec_sha256": candidate.parent_spec_sha256,
                "provenance": candidate.provenance,
                "reference_sha256": result.reference_sha256,
                "reference_content_type": result.reference_content_type,
                "metric_version": MIN_SCENE_METRIC_VERSION,
                "background": [float(value) for value in result.background],
                "metric_sha256": metric_sha256,
                "residual_sha256": residual_sha256,
            }
            files[f"{candidate_dir}/metrics.json"] = _write_text(
                run_dir,
                f"{candidate_dir}/metrics.json",
                canonical_json(metrics_payload) + "\n",
            )
        if arm.current_best is not None:
            files[f"{prefix}/current_best.json"] = _write_text(
                run_dir,
                f"{prefix}/current_best.json",
                canonical_json(
                    {
                        "spec_sha256": arm.current_best.spec.spec_sha256,
                        "role": arm.current_best.role,
                        "loss": arm.current_best.loss,
                        "mae": arm.current_best.mae,
                    }
                )
                + "\n",
            )
    return files


class ShadowEvidenceError(ValueError):
    """shadow 私有证据目录违反完整性/权限/边界约束的 fail-closed 错误."""


def _verify_permissions(path: Path, *, is_dir: bool) -> None:
    """私有证据不得对 group/other 开放任何权限."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ShadowEvidenceError(
            f"私有证据权限过宽：{path} mode={oct(mode)}，要求 {'0700' if is_dir else '0600'}。"
        )


def verify_shadow_run(run_dir: Path) -> dict[str, Any]:
    """校验私有 run 目录的全部文件哈希与报告哈希，返回报告 payload.

    fail-closed：run 目录不得是 symlink 或 staging 半成品；``files`` 映射中
    每个文件必须存在、非 symlink、内容哈希匹配；report_sha256 必须对去除
    自身后的 canonical payload 重算匹配；目录中不得出现映射外的额外文件；
    文件/目录权限不得对 group/other 开放。
    """
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise ShadowEvidenceError(f"shadow run 目录无效：{run_dir}")
    if ".staging-" in run_dir.name or not run_dir.name.startswith("shadow-"):
        raise ShadowEvidenceError(f"拒绝校验 staging 半成品或未知目录：{run_dir}")
    root_resolved = run_dir.resolve(strict=True)
    for path in run_dir.rglob("*"):
        if path.is_symlink():
            raise ShadowEvidenceError(f"私有证据树不得包含 symlink：{path}")
    _verify_permissions(run_dir, is_dir=True)
    report_path = run_dir / "report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ShadowEvidenceError(f"缺少 report.json：{run_dir}")
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ShadowEvidenceError(f"report.json 不是合法 JSON：{run_dir}") from exc
    if not isinstance(payload, dict):
        raise ShadowEvidenceError(f"report.json 必须是 JSON object：{run_dir}")
    files = payload.get("files")
    if not isinstance(files, dict):
        raise ShadowEvidenceError("report.json 缺少 files 映射。")

    seen: set[str] = {"report.json"}
    for relative, digest in files.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise ShadowEvidenceError("files 映射必须是 相对路径: sha256。")
        pure = PurePosixPath(relative)
        if (
            not relative
            or "\\" in relative
            or pure.is_absolute()
            or pure.as_posix() != relative
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise ShadowEvidenceError(
                f"证据文件路径不是规范 POSIX 相对路径：{relative}"
            )
        target = run_dir.joinpath(*pure.parts)
        if target.is_symlink() or not target.is_file():
            raise ShadowEvidenceError(f"证据文件缺失或是 symlink：{relative}")
        target_resolved = target.resolve(strict=True)
        if not target_resolved.is_relative_to(root_resolved):
            raise ShadowEvidenceError(f"证据文件路径越出 run 目录：{relative}")
        _verify_permissions(target, is_dir=False)
        actual = sha256(target.read_bytes()).hexdigest()
        if actual != digest:
            raise ShadowEvidenceError(
                f"证据文件内容哈希不匹配：{relative}（可能被篡改）。"
            )
        seen.add(relative)

    unexpected = sorted(
        str(path.relative_to(run_dir))
        for path in run_dir.rglob("*")
        if path.is_file() and str(path.relative_to(run_dir)) not in seen
    )
    if unexpected:
        raise ShadowEvidenceError(f"发现映射外的额外文件：{unexpected}")

    for path in run_dir.rglob("*"):
        if path.is_dir():
            _verify_permissions(path, is_dir=True)

    report_sha256 = payload.pop("report_sha256", None)
    if not isinstance(report_sha256, str):
        raise ShadowEvidenceError("report.json 缺少 report_sha256。")
    actual_report = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if actual_report != report_sha256:
        raise ShadowEvidenceError("report_sha256 不匹配（报告可能被篡改）。")
    payload["report_sha256"] = report_sha256
    report_body = {
        key: value
        for key, value in payload.items()
        if key not in {"files", "report_sha256"}
    }
    expected_run_id = _shadow_run_id_from_report_body(report_body)
    if run_dir.name != expected_run_id:
        raise ShadowEvidenceError(
            "shadow run 目录名与报告内容寻址身份不匹配："
            f"expected={expected_run_id} actual={run_dir.name}"
        )
    return payload


__all__ = [
    "ARM_A",
    "ARM_B",
    "ARM_IDS",
    "REQUESTED_SAMPLING_PARAMS",
    "INCONCLUSIVE_CODES",
    "MAX_WORK_SIDE",
    "REPORT_SCHEMA_VERSION",
    "SHADOW_EXPERIMENT_ID",
    "ArmLedger",
    "ArmResult",
    "LayerPlanGlslShadowRunner",
    "PlanLedger",
    "ShadowABConfig",
    "ShadowABConfigError",
    "ShadowABRunResult",
    "ShadowCandidate",
    "ShadowEvidenceError",
    "ShadowPreparedRenderer",
    "ShadowRenderer",
    "border_background",
    "build_report_payload",
    "decode_reference",
    "derive_canvas",
    "is_strict_improvement",
    "shadow_run_id",
    "verify_shadow_run",
    "write_shadow_run",
]
