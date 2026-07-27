"""LayerPlan + direct GLSL 的单 engine attempt 执行内核.

本模块只负责一次隔离的 direct attempt：生成 advisory ``LayerPlanV1``，
执行 direct Initial/Refine，并复用 shadow Arm B 已验证的 canonical safety、
真实 Renderer receipt、metric、严格 incumbent 选择与预算语义。它不运行
Arm A，不写 Artifact，也不接 Graph、Backend/API 或产品 ``current_best``。
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

import numpy as np
from PIL import Image

from agent.app.contracts.layerplan_glsl_shadow import LayerPlanV1
from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.services.layerplan_glsl_shadow import (
    ARM_B,
    INCONCLUSIVE_CODES,
    REQUESTED_SAMPLING_PARAMS,
    ArmLedger,
    ArmResult,
    LayerPlanGlslShadowRunner,
    PlanLedger,
    ShadowABConfig,
    ShadowCandidate,
    ShadowRenderer,
    border_background,
    decode_reference,
    derive_canvas,
)
from shaderforge.evaluation import MIN_SCENE_METRIC_VERSION
from shaderforge.program_spec import (
    TrustedReceiptVerifier,
    canonical_json,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer

DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION = "direct_glsl_attempt_result_v1"
DIRECT_ENGINE_ID = "direct_glsl_layerplan_v1"
DIRECT_REPRESENTATION = "shader_program_spec_v1"


@dataclass(frozen=True)
class LayerPlanGlslDirectConfig:
    """冻结单 engine direct attempt 的独立预算与实现身份."""

    implementation_identity_sha256: str
    direct_author_llm_budget: int = 8
    compile_budget: int = 8
    draw_budget: int = 8
    refine_budget: int = 2
    plan_llm_budget: int = 2
    canvas_width: int | None = None
    canvas_height: int | None = None

    def __post_init__(self) -> None:
        """复用 shadow 配置校验，确保预算与画布语义完全一致."""
        self.to_shadow_config()

    def to_shadow_config(self) -> ShadowABConfig:
        """构造共享执行内核所需的冻结配置，不改变 shadow 指纹契约."""
        return ShadowABConfig(
            direct_author_llm_budget=self.direct_author_llm_budget,
            compile_budget_per_arm=self.compile_budget,
            draw_budget_per_arm=self.draw_budget,
            refine_budget_per_arm=self.refine_budget,
            plan_llm_budget=self.plan_llm_budget,
            canvas_width=self.canvas_width,
            canvas_height=self.canvas_height,
            implementation_identity_sha256=self.implementation_identity_sha256,
        )

    def to_dict(self) -> dict[str, Any]:
        """返回 direct 专用、JSON-safe 的冻结配置."""
        return {
            "implementation_identity_sha256": self.implementation_identity_sha256,
            "direct_author_llm_budget": self.direct_author_llm_budget,
            "compile_budget": self.compile_budget,
            "draw_budget": self.draw_budget,
            "refine_budget": self.refine_budget,
            "plan_llm_budget": self.plan_llm_budget,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "requested_sampling_params": dict(REQUESTED_SAMPLING_PARAMS),
        }

    def fingerprint(self) -> str:
        """返回 direct attempt 配置的内容寻址指纹."""
        return sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DirectPlanLedger:
    """不可变的 LayerPlan LLM/repair 记账快照."""

    llm_call_count: int
    total_tokens: int | None
    repair_count: int
    wall_clock_ms: float

    @classmethod
    def from_mutable(cls, ledger: PlanLedger) -> DirectPlanLedger:
        """从 attempt-local 可变 ledger 冻结快照."""
        return cls(
            llm_call_count=ledger.llm_call_count,
            total_tokens=ledger.total_tokens,
            repair_count=ledger.repair_count,
            wall_clock_ms=round(ledger.wall_clock_ms, 2),
        )

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON-safe 摘要."""
        return {
            "llm_call_count": self.llm_call_count,
            "total_tokens": self.total_tokens,
            "repair_count": self.repair_count,
            "wall_clock_ms": self.wall_clock_ms,
        }


@dataclass(frozen=True)
class DirectLedger:
    """不可变的 direct Initial/Refine/Renderer 独立记账快照."""

    llm_call_count: int
    total_tokens: int | None
    repair_count: int
    compile_count: int
    draw_count: int
    cache_hits: int
    wall_clock_ms: float
    rejected_candidates: int
    accepted_candidates: int

    @classmethod
    def from_mutable(cls, ledger: ArmLedger) -> DirectLedger:
        """从 attempt-local 可变 ledger 冻结快照."""
        return cls(
            llm_call_count=ledger.llm_call_count,
            total_tokens=ledger.total_tokens,
            repair_count=ledger.repair_count,
            compile_count=ledger.compile_count,
            draw_count=ledger.draw_count,
            cache_hits=ledger.cache_hits,
            wall_clock_ms=round(ledger.wall_clock_ms, 2),
            rejected_candidates=ledger.rejected_candidates,
            accepted_candidates=ledger.accepted_candidates,
        )

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON-safe 摘要."""
        return {
            "llm_call_count": self.llm_call_count,
            "total_tokens": self.total_tokens,
            "repair_count": self.repair_count,
            "compile_count": self.compile_count,
            "draw_count": self.draw_count,
            "cache_hits": self.cache_hits,
            "wall_clock_ms": self.wall_clock_ms,
            "rejected_candidates": self.rejected_candidates,
            "accepted_candidates": self.accepted_candidates,
        }


@dataclass(frozen=True)
class DirectEngineIdentity:
    """direct attempt 的冻结 engine/representation/实现与评估身份."""

    implementation_identity_sha256: str
    engine_id: str = DIRECT_ENGINE_ID
    representation: str = DIRECT_REPRESENTATION
    metric_version: str = MIN_SCENE_METRIC_VERSION
    renderer_contract_id: str = "webgl1_static_no_texture_v1"

    def to_dict(self) -> dict[str, str]:
        """返回 JSON-safe 身份."""
        return {
            "engine_id": self.engine_id,
            "representation": self.representation,
            "implementation_identity_sha256": self.implementation_identity_sha256,
            "metric_version": self.metric_version,
            "renderer_contract_id": self.renderer_contract_id,
        }


@dataclass(frozen=True)
class DirectAttemptResult:
    """一次 direct attempt 的不可变内存结果.

    ``layer_plan`` 与 ``current_best`` 保留 canonical plan/spec、render bytes、
    receipt attestation 和完整 metric，供后续私有 Artifact/finalize 使用。
    对外日志或父协调器只可使用 ``to_safe_summary()``；该摘要不含 GLSL、
    LayerPlan 正文、render bytes、Prompt 或原始错误文本。
    """

    status: Literal["ok", "inconclusive"]
    failure_code: str | None
    safety_failure_codes: tuple[str, ...]
    identity: DirectEngineIdentity
    config: LayerPlanGlslDirectConfig
    config_fingerprint: str
    reference_sha256: str
    reference_content_type: str
    instruction_sha256: str
    canvas_width: int
    canvas_height: int
    layer_plan: LayerPlanV1 | None
    current_best: ShadowCandidate | None
    candidates: tuple[ShadowCandidate, ...]
    plan_ledger: DirectPlanLedger
    direct_ledger: DirectLedger

    def to_safe_summary(self) -> dict[str, Any]:
        """返回可直接 ``json.dumps`` 的安全摘要，不暴露私有执行内容."""
        best = self.current_best
        plan = self.layer_plan
        return {
            "schema_version": DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION,
            "status": self.status,
            "failure_code": self.failure_code,
            "safety_failure_codes": list(self.safety_failure_codes),
            "identity": self.identity.to_dict(),
            "config_fingerprint": self.config_fingerprint,
            "reference_sha256": self.reference_sha256,
            "reference_content_type": self.reference_content_type,
            "instruction_sha256": self.instruction_sha256,
            "canvas": {
                "width": self.canvas_width,
                "height": self.canvas_height,
            },
            "plan": (
                {
                    "plan_sha256": plan.plan_sha256,
                    "author_identity_sha256": sha256(
                        canonical_json(plan.author_identity.to_dict()).encode("utf-8")
                    ).hexdigest(),
                }
                if plan is not None
                else None
            ),
            "current_best": (
                {
                    "spec_sha256": best.spec.spec_sha256,
                    "source_sha256": best.spec.source_sha256,
                    "binding_sha256": best.spec.binding_sha256,
                    "render_rgb_sha256": sha256(best.rgb_bytes).hexdigest(),
                    "render_png_sha256": sha256(best.png_bytes).hexdigest(),
                    "metric_sha256": sha256(
                        canonical_json(best.metrics).encode("utf-8")
                    ).hexdigest(),
                    "residual_sha256": sha256(
                        canonical_json(best.residual_summary).encode("utf-8")
                    ).hexdigest(),
                    "author_identity_sha256": sha256(
                        canonical_json(best.spec.author_identity.to_dict()).encode(
                            "utf-8"
                        )
                    ).hexdigest(),
                    "loss": best.loss,
                    "mae": best.mae,
                    "role": best.role,
                    "sequence": best.sequence,
                }
                if best is not None
                else None
            ),
            "candidate_count": len(self.candidates),
            "plan_ledger": self.plan_ledger.to_dict(),
            "direct_ledger": self.direct_ledger.to_dict(),
        }


def _safe_failure_codes(arm: ArmResult) -> tuple[str, ...]:
    """把内部错误收敛为预声明安全码，绝不泄露 provider/validator 原文."""
    codes: list[str] = []
    for event in arm.events:
        if event.get("ok") is not False:
            continue
        raw = event.get("error_code")
        if raw in INCONCLUSIVE_CODES:
            code = str(raw)
        elif isinstance(raw, str) and raw.startswith("llm_"):
            code = "llm_budget_exhausted"
        else:
            code = "author_output_invalid"
        if code not in codes:
            codes.append(code)
    if arm.inconclusive_code is not None and arm.inconclusive_code not in codes:
        codes.insert(0, arm.inconclusive_code)
    return tuple(codes)


class LayerPlanGlslDirectRunner:
    """只运行 LayerPlan + direct Initial/Refine 的隔离单 engine runner."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        renderer: ShadowRenderer,
        config: LayerPlanGlslDirectConfig,
        clock: Callable[[], float] = time.perf_counter,
        receipt_issuer: TrustedReceiptVerifier | None = None,
    ) -> None:
        """注入 attempt-local Gateway、Renderer、预算与 receipt 信任根."""
        self._config = config
        self._engine = LayerPlanGlslShadowRunner(
            gateway=gateway,
            renderer=renderer,
            config=config.to_shadow_config(),
            clock=clock,
            receipt_issuer=receipt_issuer,
        )

    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
    ) -> DirectAttemptResult:
        """执行一次 direct attempt；不运行 Arm A，不产生任何文件副作用."""
        image = decode_reference(reference_image)
        config = self._config
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
        background = border_background(target_rgb)
        sequence_counter = itertools.count(1)
        arm = ArmResult(
            arm_id=ARM_B,
            status="inconclusive",
            inconclusive_code=None,
            ledger=ArmLedger(ARM_B),
            layer_plan_sha256=None,
        )
        layer_plan = await self._engine.execute_layerplan_direct_arm(
            arm,
            next_sequence=lambda: next(sequence_counter),
            reference_image=reference_image,
            content_type=content_type,
            instruction=instruction,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            target_rgb=target_rgb,
            background=background,
        )
        plan_ledger = arm.plan_ledger
        assert plan_ledger is not None
        failure_code = arm.inconclusive_code if arm.status != "ok" else None
        return DirectAttemptResult(
            status=arm.status,
            failure_code=failure_code,
            safety_failure_codes=_safe_failure_codes(arm),
            identity=DirectEngineIdentity(
                implementation_identity_sha256=(config.implementation_identity_sha256)
            ),
            config=config,
            config_fingerprint=config.fingerprint(),
            reference_sha256=sha256(reference_image).hexdigest(),
            reference_content_type=content_type,
            instruction_sha256=sha256(instruction.encode("utf-8")).hexdigest(),
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            layer_plan=layer_plan,
            current_best=arm.current_best,
            candidates=tuple(arm.candidates),
            plan_ledger=DirectPlanLedger.from_mutable(plan_ledger),
            direct_ledger=DirectLedger.from_mutable(arm.ledger),
        )


class OwnedLayerPlanGlslDirectRunner:
    """为单个 attempt 持有全新默认 Gateway/Playwright Renderer 的 runner."""

    def __init__(self, config: LayerPlanGlslDirectConfig) -> None:
        """构造 attempt-local 资源；Renderer 仍惰性启动浏览器."""
        self._renderer = PlaywrightWebGL1Renderer()
        self._runner = LayerPlanGlslDirectRunner(
            gateway=LangChainLLMGateway(),
            renderer=self._renderer,
            config=config,
        )

    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
    ) -> DirectAttemptResult:
        """代理一次 direct attempt."""
        return await self._runner.run(
            reference_image,
            content_type=content_type,
            instruction=instruction,
        )

    async def close(self) -> None:
        """释放 attempt-local Playwright Renderer."""
        await self._renderer.close()


def create_owned_layerplan_glsl_direct_runner(
    config: LayerPlanGlslDirectConfig,
) -> OwnedLayerPlanGlslDirectRunner:
    """创建由调用方负责关闭的全新默认 direct attempt runner."""
    return OwnedLayerPlanGlslDirectRunner(config)


__all__ = [
    "DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION",
    "DIRECT_ENGINE_ID",
    "DIRECT_REPRESENTATION",
    "DirectAttemptResult",
    "DirectEngineIdentity",
    "DirectLedger",
    "DirectPlanLedger",
    "LayerPlanGlslDirectConfig",
    "LayerPlanGlslDirectRunner",
    "OwnedLayerPlanGlslDirectRunner",
    "create_owned_layerplan_glsl_direct_runner",
]
