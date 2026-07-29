"""LayerPlan + direct GLSL 的单 engine attempt 执行内核.

本模块只负责一次隔离的 direct attempt：生成 advisory ``LayerPlanV1``，
执行 Layered Initial/单 Layer Refine、确定性编译为 ``ShaderProgramSpecV1``，
再走 canonical safety、真实 Renderer receipt、metric 与严格 incumbent 选择。
它不运行 shadow A/B、不写 Artifact，也不接 Graph 或 Backend/API。
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any, Literal, Protocol

import numpy as np
from PIL import Image, UnidentifiedImageError

from agent.app.contracts.layer_plan import LayerPlanV1, layer_plan_json_schema
from agent.app.contracts.layered_direct_glsl import (
    layer_patch_json_schema,
    layered_shader_spec_json_schema,
)
from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.layered_direct.authors import (
    DEFAULT_LAYER_PATCH_MAX_OUTPUT_TOKENS,
    DEFAULT_LAYERED_INITIAL_MAX_OUTPUT_TOKENS,
    DIRECT_LAYERED_INITIAL_PROMPT,
    DIRECT_LAYERED_REFINE_PROMPT,
    DIRECT_LAYERED_REPAIR_PROMPT,
    ValidatedLayeredIncumbent,
    run_initial_layered_glsl_author,
    run_refine_layered_glsl_author,
)
from agent.app.nodes.layered_direct.layer_plan_author import (
    DEFAULT_PLAN_MAX_OUTPUT_TOKENS,
    VISUAL_ANALYSIS_PROMPT,
    run_visual_analysis_author,
)
from agent.app.nodes.layered_direct.structured_author import (
    MAX_STRUCTURED_ATTEMPTS,
)
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.evaluation import (
    MIN_SCENE_METRIC_VERSION,
    dominant_metric_component,
    evaluate_min_scene,
    summarize_spatial_residual,
)
from shaderforge.layered_spec import (
    LAYER_PATCH_V1_SCHEMA_VERSION,
    LAYERED_COMPILER_VERSION,
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
    LayeredShaderSpecV1,
    LayeredSpecError,
    apply_layer_patch,
    compile_layered_shader,
)
from shaderforge.program_spec import (
    TRUSTED_VALIDATOR_VERSION,
    AttestationError,
    ShaderProgramSpecV1,
    TrustedReceiptVerifier,
    canonical_json,
    is_executable,
    issue_attestation,
    process_receipt_verifier,
)
from shaderforge.rendering import (
    PlaywrightWebGL1Renderer,
    RendererUnavailableError,
    ShaderPreparationError,
)
from shaderforge.validation import ProgramSpecSafetyLimits, validate_program_spec_safety

logger = logging.getLogger("agent.direct")

DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION = "direct_glsl_attempt_result_v1"
DIRECT_ENGINE_ID = "direct_glsl_layerplan_v1"
DIRECT_REPRESENTATION = "shader_program_spec_v1"
LAYERED_AUTHORING_REPRESENTATION = LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION
LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION = "direct_layered_glsl_implementation_v1"
LAYERED_PARSER_POLICY_VERSION = "direct_layered_author_parser_v2"
_RENDERER_DEFERRED_SAFETY_CODES = frozenset(
    {
        "too_many_uniforms",
        "too_many_uniform_components",
    }
)
_MAX_WORK_SIDE = 256
_MAX_CANVAS_SIDE = 4096
REQUESTED_SAMPLING_PARAMS: Mapping[str, Any] = {
    "temperature": 0,
    "thinking": "off",
    "response_format": "json_object",
}
INCONCLUSIVE_CODES = frozenset(
    {
        "layer_plan_generation_failed",
        "llm_budget_exhausted",
        "llm_invocation_failed",
        "llm_transient_failure",
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


class DirectPreparedRenderer(Protocol):
    async def render_uniforms(
        self,
        uniform_values: Mapping[str, Any],
        *,
        capture_png: bool = False,
        receipt_spec_sha256: str | None = None,
    ) -> Any: ...

    async def close(self) -> None: ...


class DirectRenderer(Protocol):
    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> DirectPreparedRenderer: ...


@dataclass
class _PlanLedger:
    llm_call_count: int = 0
    total_tokens: int | None = 0
    repair_count: int = 0
    wall_clock_ms: float = 0.0


@dataclass
class _AttemptLedger:
    llm_call_count: int = 0
    total_tokens: int | None = 0
    repair_count: int = 0
    compile_count: int = 0
    draw_count: int = 0
    cache_hits: int = 0
    wall_clock_ms: float = 0.0
    rejected_candidates: int = 0
    accepted_candidates: int = 0


def _derive_canvas(image: Image.Image) -> tuple[int, int]:
    width, height = image.size
    scale = min(1.0, _MAX_WORK_SIDE / max(width, height))
    return max(16, round(width * scale)), max(16, round(height * scale))


def _border_background(rgb: np.ndarray) -> tuple[float, float, float]:
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    median = np.median(border, axis=0)
    return (float(median[0]), float(median[1]), float(median[2]))


def _decode_reference(image_bytes: bytes) -> Image.Image:
    try:
        return Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("unable to decode reference image") from exc


def current_layered_direct_glsl_implementation_identity() -> dict[str, Any]:
    """返回当前产品 Layered direct GLSL 的稳定运行契约身份."""
    prompts = {}
    for role, prompt in (
        ("visual_analysis", VISUAL_ANALYSIS_PROMPT),
        ("layered_initial", DIRECT_LAYERED_INITIAL_PROMPT),
        ("layered_refine", DIRECT_LAYERED_REFINE_PROMPT),
        ("layered_repair", DIRECT_LAYERED_REPAIR_PROMPT),
    ):
        prompts[role] = {
            "name": prompt.name,
            "version": prompt.version,
            "prompt_sha256": sha256(prompt.prompt.encode("utf-8")).hexdigest(),
        }
    body: dict[str, Any] = {
        "schema_version": LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION,
        "parser_policy_version": LAYERED_PARSER_POLICY_VERSION,
        "layered_compiler_version": LAYERED_COMPILER_VERSION,
        "authoring_representation": LAYERED_AUTHORING_REPRESENTATION,
        "execution_representation": DIRECT_REPRESENTATION,
        "layered_spec_schema_version": LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
        "layer_patch_schema_version": LAYER_PATCH_V1_SCHEMA_VERSION,
        "trusted_validator_version": TRUSTED_VALIDATOR_VERSION,
        "layer_plan_json_schema_sha256": sha256(
            canonical_json(layer_plan_json_schema()).encode("utf-8")
        ).hexdigest(),
        "layered_spec_json_schema_sha256": sha256(
            canonical_json(layered_shader_spec_json_schema()).encode("utf-8")
        ).hexdigest(),
        "layer_patch_json_schema_sha256": sha256(
            canonical_json(layer_patch_json_schema()).encode("utf-8")
        ).hexdigest(),
        "author_limits": {
            "plan_max_output_tokens": DEFAULT_PLAN_MAX_OUTPUT_TOKENS,
            "layered_initial_max_output_tokens": (
                DEFAULT_LAYERED_INITIAL_MAX_OUTPUT_TOKENS
            ),
            "layer_patch_max_output_tokens": DEFAULT_LAYER_PATCH_MAX_OUTPUT_TOKENS,
            "max_structured_attempts": MAX_STRUCTURED_ATTEMPTS,
        },
        "prompts": prompts,
        "renderer_contract_id": WEBGL1_STATIC_NO_TEXTURE_V1.contract_id,
        "renderer_contract_sha256": sha256(
            canonical_json(WEBGL1_STATIC_NO_TEXTURE_V1.to_dict()).encode("utf-8")
        ).hexdigest(),
        "program_spec_safety_limits": {
            name: value
            for name, value in asdict(ProgramSpecSafetyLimits()).items()
            if name not in {"max_uniforms", "max_uniform_components"}
        },
        "renderer_deferred_safety_codes": sorted(_RENDERER_DEFERRED_SAFETY_CODES),
    }
    normalized = json.loads(canonical_json(body))
    if not isinstance(
        normalized, dict
    ):  # pragma: no cover - canonical object invariant
        raise TypeError("layered implementation identity must be an object")
    normalized["identity_sha256"] = sha256(
        canonical_json(normalized).encode("utf-8")
    ).hexdigest()
    return normalized


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
        """Fail closed on invalid attempt budgets, canvas or identity."""
        for name in (
            "direct_author_llm_budget",
            "compile_budget",
            "draw_budget",
            "refine_budget",
            "plan_llm_budget",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if (self.canvas_width is None) != (self.canvas_height is None):
            raise ValueError("canvas_width and canvas_height must be set together")
        for name in ("canvas_width", "canvas_height"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                or value > _MAX_CANVAS_SIDE
            ):
                raise ValueError(f"{name} must be within renderer limits")
        identity = self.implementation_identity_sha256
        if len(identity) != 64 or any(
            char not in "0123456789abcdef" for char in identity
        ):
            raise ValueError("implementation_identity_sha256 must be lowercase sha256")

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
    def from_mutable(cls, ledger: _PlanLedger) -> DirectPlanLedger:
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
    def from_mutable(cls, ledger: _AttemptLedger) -> DirectLedger:
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
class DirectCandidate:
    """一次成功 draw 的 Layered 源表示与编译后 ProgramSpec 不可变快照."""

    layered_spec: LayeredShaderSpecV1
    spec: ShaderProgramSpecV1
    role: Literal["initial", "refine"]
    sequence: int
    rgb_bytes: bytes
    png_bytes: bytes
    mae: float
    loss: float
    metrics: dict[str, Any]
    residual_summary: dict[str, Any]
    parent_layered_spec_sha256: str | None
    patched_layer_id: str | None
    provenance: str = "model_generated_layered_direct_glsl"


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
    current_best: DirectCandidate | None
    candidates: tuple[DirectCandidate, ...]
    plan_ledger: DirectPlanLedger
    direct_ledger: DirectLedger
    private_diagnostics: tuple[dict[str, Any], ...] = ()

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
                    "layered_spec_sha256": (best.layered_spec.layered_spec_sha256),
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
                    "patched_layer_id": best.patched_layer_id,
                }
                if best is not None
                else None
            ),
            "candidate_count": len(self.candidates),
            "plan_ledger": self.plan_ledger.to_dict(),
            "direct_ledger": self.direct_ledger.to_dict(),
        }

    def to_private_diagnostics(self) -> list[dict[str, Any]]:
        """返回仅供私有 attempt 使用的脱敏阶段诊断."""
        return [dict(item) for item in self.private_diagnostics]


def _safe_compile_diagnostics(compile_result: Any) -> dict[str, object]:
    """把 Renderer 编译诊断收敛为不含源码或原始日志的摘要."""

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


def _accumulate_token_usage(
    current_total: int | None,
    observed_total: int | None,
    *,
    call_count: int,
) -> int | None:
    if call_count == 0:
        return current_total
    if current_total is None or observed_total is None:
        return None
    return current_total + observed_total


def _program_cache_key(spec: ShaderProgramSpecV1) -> tuple[Any, ...]:
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


def _safe_failure_codes(
    events: list[dict[str, Any]],
    failure_code: str | None,
) -> tuple[str, ...]:
    """把内部错误收敛为预声明安全码，绝不泄露 provider/validator 原文."""
    codes: list[str] = []
    for event in events:
        if event.get("ok") is not False:
            continue
        raw = event.get("error_code")
        if raw in INCONCLUSIVE_CODES:
            code = str(raw)
        elif isinstance(raw, str) and raw.startswith("llm_"):
            code = "llm_invocation_failed"
        else:
            code = "author_output_invalid"
        if code not in codes:
            codes.append(code)
    if failure_code is not None and failure_code not in codes:
        codes.insert(0, failure_code)
    return tuple(codes)


def _private_diagnostic_events(
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """只保留稳定错误码、规则类别和行号，不泄露源码或 provider 原文."""
    diagnostics: list[dict[str, Any]] = []
    for event in events:
        if event.get("ok") is not False:
            continue
        item: dict[str, Any] = {
            "sequence": event.get("sequence"),
            "kind": event.get("kind"),
            "error_code": event.get("error_code"),
        }
        violations = event.get("violations")
        if isinstance(violations, list):
            item["violation_codes"] = [
                str(code) for code in violations if isinstance(code, str)
            ][:16]
        compile_diagnostics = event.get("diagnostics")
        if isinstance(compile_diagnostics, dict):
            categories = compile_diagnostics.get("static_violation_categories")
            if isinstance(categories, list):
                item["static_violation_categories"] = [
                    {
                        "code": category.get("code"),
                        "line": category.get("line"),
                    }
                    for category in categories
                    if isinstance(category, dict)
                ][:16]
            for key in (
                "vertex_log_sha256",
                "fragment_log_sha256",
                "link_log_sha256",
            ):
                value = compile_diagnostics.get(key)
                if isinstance(value, str):
                    item[key] = value
        detail = event.get("detail")
        if isinstance(detail, str) and re.fullmatch(r"[a-z0-9_]{1,128}", detail):
            item["detail"] = detail
        diagnostics.append(item)
    return tuple(diagnostics)


def _normalize_author_failure(error_code: str | None) -> str:
    if error_code in INCONCLUSIVE_CODES:
        return str(error_code)
    if isinstance(error_code, str) and error_code.startswith("llm_"):
        return "llm_invocation_failed"
    return "author_output_invalid"


class LayerPlanGlslDirectRunner:
    """运行 LayerPlan、Layered Initial/Refine 与完整 ProgramSpec 渲染."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        renderer: DirectRenderer,
        config: LayerPlanGlslDirectConfig,
        clock: Callable[[], float] = time.perf_counter,
        receipt_issuer: TrustedReceiptVerifier | None = None,
    ) -> None:
        """注入 attempt-local Gateway、Renderer、预算与 receipt 信任根."""
        self._config = config
        self._gateway = gateway
        self._renderer = renderer
        self._clock = clock
        self._receipt_issuer = receipt_issuer or process_receipt_verifier()

    async def _render_candidate(
        self,
        *,
        layered_spec: LayeredShaderSpecV1,
        compiled_spec: ShaderProgramSpecV1,
        role: Literal["initial", "refine"],
        sequence: int,
        parent_layered_spec_sha256: str | None,
        patched_layer_id: str | None,
        target_rgb: np.ndarray,
        background: tuple[float, float, float],
        ledger: _AttemptLedger,
        events: list[dict[str, Any]],
        program_cache: dict[tuple[Any, ...], Any],
    ) -> DirectCandidate | None:
        """校验、真实 draw、签发 attestation 并计算整图指标."""
        started = self._clock()

        def reject(error_code: str, **extra: Any) -> None:
            ledger.rejected_candidates += 1
            events.append(
                {
                    "sequence": sequence,
                    "kind": role,
                    "ok": False,
                    "error_code": error_code,
                    "layered_spec_sha256": layered_spec.layered_spec_sha256,
                    "spec_sha256": compiled_spec.spec_sha256,
                    **extra,
                }
            )

        static_result = validate_program_spec_safety(compiled_spec)
        blocking_violations = tuple(
            item
            for item in static_result.violations
            if item.code not in _RENDERER_DEFERRED_SAFETY_CODES
        )
        if any(item.severity == "error" for item in blocking_violations):
            reject(
                "static_validation_failed",
                violations=[item.code for item in blocking_violations],
            )
            return None

        cache_key = _program_cache_key(compiled_spec)
        prepared = program_cache.get(cache_key)
        cache_hit = prepared is not None
        if prepared is not None:
            ledger.cache_hits += 1
        else:
            if ledger.compile_count >= self._config.compile_budget:
                reject("compile_budget_exhausted")
                return None
            ledger.compile_count += 1
            uniform_schema = {
                item.name: item.type for item in compiled_spec.uniform_schema
            }
            try:
                prepared = await self._renderer.prepare(
                    compiled_spec.fragment_source,
                    compiled_spec.canvas.width,
                    compiled_spec.canvas.height,
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
            if ledger.draw_count >= self._config.draw_budget:
                reject("draw_budget_exhausted")
                return None
            ledger.draw_count += 1
            draw = await prepared.render_uniforms(
                dict(compiled_spec.uniform_values),
                capture_png=True,
                receipt_spec_sha256=compiled_spec.spec_sha256,
            )
            ledger.wall_clock_ms += (self._clock() - started) * 1000.0
            if not draw.success or draw.rgb_bytes is None or draw.image_bytes is None:
                reject("draw_failed", draw_error=draw.draw_error)
                return None
        except (RendererUnavailableError, ValueError, OSError) as exc:
            ledger.wall_clock_ms += (self._clock() - started) * 1000.0
            reject("renderer_unavailable", detail=type(exc).__name__)
            return None

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
        try:
            attested = compiled_spec.with_attestation(
                issue_attestation(
                    compiled_spec,
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
            .reshape(compiled_spec.canvas.height, compiled_spec.canvas.width, 3)
            .astype(np.float32)
            / 255.0
        )
        metric = evaluate_min_scene(target_rgb, rendered, background)
        residual_summary = summarize_spatial_residual(target_rgb, rendered)
        residual_summary["dominant_metric_component"] = dominant_metric_component(
            metric
        )
        events.append(
            {
                "sequence": sequence,
                "kind": role,
                "ok": True,
                "layered_spec_sha256": layered_spec.layered_spec_sha256,
                "spec_sha256": attested.spec_sha256,
                "patched_layer_id": patched_layer_id,
                "loss": metric.total_loss,
                "mae": metric.global_mae,
                "validator_version": TRUSTED_VALIDATOR_VERSION,
                "cache_hit": cache_hit,
            }
        )
        return DirectCandidate(
            layered_spec=layered_spec,
            spec=attested,
            role=role,
            sequence=sequence,
            rgb_bytes=draw.rgb_bytes,
            png_bytes=draw.image_bytes,
            mae=metric.global_mae,
            loss=metric.total_loss,
            metrics=metric.to_dict(),
            residual_summary=residual_summary,
            parent_layered_spec_sha256=parent_layered_spec_sha256,
            patched_layer_id=patched_layer_id,
        )

    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
    ) -> DirectAttemptResult:
        """执行一次 direct attempt；不运行 Arm A，不产生任何文件副作用."""
        image = _decode_reference(reference_image)
        config = self._config
        canvas_width, canvas_height = (
            (config.canvas_width, config.canvas_height)
            if config.canvas_width is not None and config.canvas_height is not None
            else _derive_canvas(image)
        )
        assert canvas_width is not None and canvas_height is not None
        if (canvas_width, canvas_height) != image.size:
            image = image.resize(
                (canvas_width, canvas_height), Image.Resampling.LANCZOS
            )
        target_rgb = np.asarray(image, dtype=np.float32) / 255.0
        background = _border_background(target_rgb)
        sequence_counter = itertools.count(1)
        plan_ledger = _PlanLedger()
        ledger = _AttemptLedger()
        events: list[dict[str, Any]] = []
        candidates: list[DirectCandidate] = []
        current_best: DirectCandidate | None = None
        failure_code: str | None = None

        plan_sequence = next(sequence_counter)
        remaining_plan_calls = config.plan_llm_budget - plan_ledger.llm_call_count
        plan_started = self._clock()
        plan_result = await run_visual_analysis_author(
            gateway=self._gateway,
            reference_image=reference_image,
            content_type=content_type,
            user_instruction=instruction,
            remaining_calls=remaining_plan_calls,
        )
        plan_ledger.wall_clock_ms += (self._clock() - plan_started) * 1000.0
        plan_ledger.llm_call_count += plan_result.call_count
        plan_ledger.total_tokens = _accumulate_token_usage(
            plan_ledger.total_tokens,
            plan_result.total_tokens,
            call_count=plan_result.call_count,
        )
        plan_ledger.repair_count += 1 if plan_result.repaired else 0
        layer_plan = plan_result.plan
        events.append(
            {
                "sequence": plan_sequence,
                "kind": "visual_analysis",
                "ok": layer_plan is not None,
                "error_code": plan_result.error_code,
                "repaired": plan_result.repaired,
                "call_count": plan_result.call_count,
            }
        )
        if layer_plan is None:
            failure_code = "layer_plan_generation_failed"
        else:
            program_cache: dict[tuple[Any, ...], Any] = {}
            try:
                initial_sequence = next(sequence_counter)
                remaining = config.direct_author_llm_budget - ledger.llm_call_count
                started = self._clock()
                initial = await run_initial_layered_glsl_author(
                    gateway=self._gateway,
                    reference_image=reference_image,
                    content_type=content_type,
                    user_instruction=instruction,
                    layer_plan=layer_plan,
                    canvas_width=canvas_width,
                    canvas_height=canvas_height,
                    remaining_calls=remaining,
                )
                ledger.wall_clock_ms += (self._clock() - started) * 1000.0
                ledger.llm_call_count += initial.call_count
                ledger.total_tokens = _accumulate_token_usage(
                    ledger.total_tokens,
                    initial.total_tokens,
                    call_count=initial.call_count,
                )
                ledger.repair_count += 1 if initial.repaired else 0
                if initial.layered_spec is None:
                    ledger.rejected_candidates += 1
                    failure_code = _normalize_author_failure(initial.error_code)
                    events.append(
                        {
                            "sequence": initial_sequence,
                            "kind": "initial",
                            "ok": False,
                            "error_code": failure_code,
                            "detail": initial.error_code,
                            "repaired": initial.repaired,
                            "call_count": initial.call_count,
                        }
                    )
                else:
                    try:
                        compiled = compile_layered_shader(initial.layered_spec)
                    except LayeredSpecError as exc:
                        ledger.rejected_candidates += 1
                        failure_code = "static_validation_failed"
                        events.append(
                            {
                                "sequence": initial_sequence,
                                "kind": "initial",
                                "ok": False,
                                "error_code": failure_code,
                                "detail": exc.code,
                            }
                        )
                    else:
                        candidate = await self._render_candidate(
                            layered_spec=initial.layered_spec,
                            compiled_spec=compiled,
                            role="initial",
                            sequence=initial_sequence,
                            parent_layered_spec_sha256=None,
                            patched_layer_id=None,
                            target_rgb=target_rgb,
                            background=background,
                            ledger=ledger,
                            events=events,
                            program_cache=program_cache,
                        )
                        if candidate is None:
                            failure_code = str(
                                events[-1].get("error_code", "no_valid_candidate")
                            )
                        else:
                            candidates.append(candidate)
                            current_best = candidate
                            ledger.accepted_candidates += 1

                if current_best is not None:
                    failure_code = None
                    for _ in range(config.refine_budget):
                        refine_sequence = next(sequence_counter)
                        remaining = (
                            config.direct_author_llm_budget - ledger.llm_call_count
                        )
                        started = self._clock()
                        refine = await run_refine_layered_glsl_author(
                            gateway=self._gateway,
                            reference_image=reference_image,
                            current_render=current_best.png_bytes,
                            content_type=content_type,
                            user_instruction=instruction,
                            incumbent=ValidatedLayeredIncumbent(
                                layered_spec=current_best.layered_spec,
                                compiled_program_spec=current_best.spec,
                                mae=current_best.mae,
                                loss=current_best.loss,
                                metrics=dict(current_best.metrics),
                                residual_summary=dict(current_best.residual_summary),
                            ),
                            layer_plan=layer_plan,
                            remaining_calls=remaining,
                        )
                        ledger.wall_clock_ms += (self._clock() - started) * 1000.0
                        ledger.llm_call_count += refine.call_count
                        ledger.total_tokens = _accumulate_token_usage(
                            ledger.total_tokens,
                            refine.total_tokens,
                            call_count=refine.call_count,
                        )
                        ledger.repair_count += 1 if refine.repaired else 0
                        if refine.patch is None or refine.author_identity is None:
                            ledger.rejected_candidates += 1
                            refine_failure = _normalize_author_failure(
                                refine.error_code
                            )
                            events.append(
                                {
                                    "sequence": refine_sequence,
                                    "kind": "refine",
                                    "ok": False,
                                    "error_code": refine_failure,
                                    "detail": refine.error_code,
                                    "repaired": refine.repaired,
                                    "call_count": refine.call_count,
                                }
                            )
                            continue
                        parent_layered_spec_sha256 = (
                            current_best.layered_spec.layered_spec_sha256
                        )
                        try:
                            refined_layered = apply_layer_patch(
                                current_best.layered_spec,
                                refine.patch,
                                refine.author_identity,
                            )
                            refined_compiled = compile_layered_shader(refined_layered)
                        except LayeredSpecError as exc:
                            ledger.rejected_candidates += 1
                            events.append(
                                {
                                    "sequence": refine_sequence,
                                    "kind": "refine",
                                    "ok": False,
                                    "error_code": "author_output_invalid",
                                    "detail": exc.code,
                                }
                            )
                            continue
                        candidate = await self._render_candidate(
                            layered_spec=refined_layered,
                            compiled_spec=refined_compiled,
                            role="refine",
                            sequence=refine_sequence,
                            parent_layered_spec_sha256=(parent_layered_spec_sha256),
                            patched_layer_id=refine.patch.target_layer_id,
                            target_rgb=target_rgb,
                            background=background,
                            ledger=ledger,
                            events=events,
                            program_cache=program_cache,
                        )
                        if candidate is None:
                            continue
                        candidates.append(candidate)
                        if candidate.loss < current_best.loss:
                            current_best = candidate
                            ledger.accepted_candidates += 1
                        else:
                            ledger.rejected_candidates += 1
            finally:
                for prepared in program_cache.values():
                    try:
                        await prepared.close()
                    except Exception as exc:  # noqa: BLE001 - 释放失败不掩盖结论
                        # Renderer teardown errors are non-fatal, but hiding them
                        # makes browser/process leaks impossible to diagnose. Do not
                        # include exc_info because a renderer exception can contain
                        # browser or provider output.
                        logger.warning(
                            "direct.cleanup_failed event=%s error_type=%s "
                            "stage=%s suppressed=%s",
                            "direct.prepared_renderer_close_failed",
                            type(exc).__name__,
                            "prepared_renderer_close",
                            True,
                        )

        status: Literal["ok", "inconclusive"] = (
            "ok" if current_best is not None else "inconclusive"
        )
        if status == "inconclusive" and failure_code is None:
            failure_code = "no_valid_candidate"
        return DirectAttemptResult(
            status=status,
            failure_code=failure_code,
            safety_failure_codes=_safe_failure_codes(events, failure_code),
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
            current_best=current_best,
            candidates=tuple(candidates),
            plan_ledger=DirectPlanLedger.from_mutable(plan_ledger),
            direct_ledger=DirectLedger.from_mutable(ledger),
            private_diagnostics=_private_diagnostic_events(events),
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
    "LAYERED_AUTHORING_REPRESENTATION",
    "LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION",
    "DirectCandidate",
    "DirectAttemptResult",
    "DirectEngineIdentity",
    "DirectLedger",
    "DirectPlanLedger",
    "LayerPlanGlslDirectConfig",
    "LayerPlanGlslDirectRunner",
    "OwnedLayerPlanGlslDirectRunner",
    "create_owned_layerplan_glsl_direct_runner",
    "current_layered_direct_glsl_implementation_identity",
]
