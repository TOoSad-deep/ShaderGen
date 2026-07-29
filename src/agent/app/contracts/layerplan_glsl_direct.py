"""Graph-independent contracts and helpers for one direct GLSL attempt."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from io import BytesIO
from math import isfinite
from typing import Any, Literal, Protocol

import numpy as np
from PIL import Image, UnidentifiedImageError

from agent.app.contracts.layer_plan import LayerPlanV1
from shaderforge.evaluation import MIN_SCENE_METRIC_VERSION
from shaderforge.layered_spec import LayeredShaderSpecV1
from shaderforge.program_spec import ShaderProgramSpecV1, canonical_json
from shaderforge.rendering import CompileResult, PreparedRenderResult
from shaderforge.uniform_optimization import UniformOptimizationSummaryV2

DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION = "direct_glsl_attempt_result_v2"
DIRECT_ENGINE_ID = "direct_glsl_layerplan_v1"
DIRECT_REPRESENTATION = "shader_program_spec_v1"
LAYERED_AUTHORING_REPRESENTATION = "layered_shader_spec_v1"
LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION = "direct_layered_glsl_implementation_v2"
LAYERED_PARSER_POLICY_VERSION = "direct_layered_author_parser_v2"
RENDERER_DEFERRED_SAFETY_CODES = frozenset(
    {
        "too_many_uniforms",
        "too_many_uniform_components",
    }
)
MAX_WORK_SIDE = 256
MAX_CANVAS_SIDE = 4096
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
TERMINAL_REFINEMENT_FAILURE_CODES = frozenset(
    {
        "compile_budget_exhausted",
        "draw_budget_exhausted",
        "llm_budget_exhausted",
        "renderer_unavailable",
    }
)
QUALITY_TARGETS: Mapping[str, tuple[float, float]] = {
    "fast": (0.08, 0.10),
    "balanced": (0.06, 0.08),
    "high": (0.04, 0.06),
    "manual": (0.03, 0.05),
}
DIRECT_OPTIMIZATION_POLICY_SCHEMA_VERSION = "direct_optimization_policy_v2"
REFINE_FEEDBACK_METRICS = frozenset(
    {
        "global_mae",
        "foreground_mae",
        "background_mae",
        "geometry_mask_loss",
        "edge_loss",
        "worst_tile_mae",
    }
)
MAX_REFINE_STATIC_VIOLATIONS = 16
RefinementStopReason = Literal[
    "target_reached",
    "refine_budget_exhausted",
    "patience_exhausted",
    "duplicate_patch",
    "hard_resource_block",
    "no_valid_candidate",
]
RefineFeedbackOutcome = Literal[
    "minor_improvement",
    "not_improved",
    "author_failed",
    "patch_invalid",
    "static_failed",
    "compile_failed",
    "draw_failed",
    "receipt_failed",
    "attestation_failed",
]


class DirectPreparedRenderer(Protocol):
    """Prepared renderer used by the direct graph."""

    async def render_uniforms(
        self,
        uniform_values: Mapping[str, Any],
        *,
        capture_png: bool = False,
        receipt_spec_sha256: str | None = None,
    ) -> PreparedRenderResult:
        """Draw one uniform binding and optionally capture a PNG."""
        ...

    async def close(self) -> None:
        """Release the prepared renderer."""
        ...


class DirectRenderer(Protocol):
    """Renderer factory used by the direct graph."""

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> DirectPreparedRenderer:
        """Compile and link one fragment program."""
        ...


@dataclass
class PlanLedger:
    """Mutable, attempt-local accounting for visual analysis."""

    llm_call_count: int = 0
    total_tokens: int | None = 0
    repair_count: int = 0
    wall_clock_ms: float = 0.0


@dataclass
class AttemptLedger:
    """Mutable, attempt-local accounting for authoring and rendering."""

    llm_call_count: int = 0
    total_tokens: int | None = 0
    repair_count: int = 0
    compile_count: int = 0
    draw_count: int = 0
    cache_hits: int = 0
    wall_clock_ms: float = 0.0
    rejected_candidates: int = 0
    accepted_candidates: int = 0
    uniform_tuning_draw_count: int = 0
    uniform_tuning_evaluated_count: int = 0
    uniform_tuning_accepted_count: int = 0
    uniform_tuning_duplicate_count: int = 0
    uniform_tuning_session_count: int = 0
    uniform_tuning_active_component_count: int = 0


@dataclass(frozen=True)
class DirectOptimizationPolicy:
    """Per-run quality targets and bounded Refine convergence controls."""

    quality_preset: Literal["fast", "balanced", "high", "manual"] = "balanced"
    target_mae: float = 0.06
    target_loss: float = 0.08
    min_delta_loss: float = 0.001
    min_delta_mae: float = 0.001
    refinement_patience: int = 1
    detect_duplicate_patch: bool = True
    schema_version: str = DIRECT_OPTIMIZATION_POLICY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Fail closed on incoherent or non-finite optimization controls."""
        if self.schema_version != DIRECT_OPTIMIZATION_POLICY_SCHEMA_VERSION:
            raise ValueError("optimization policy schema_version is unsupported")
        if self.quality_preset not in QUALITY_TARGETS:
            raise ValueError("quality_preset is unsupported")
        expected_targets = QUALITY_TARGETS[self.quality_preset]
        if (self.target_mae, self.target_loss) != expected_targets:
            raise ValueError("quality targets must match the selected preset")
        for name in (
            "target_mae",
            "target_loss",
            "min_delta_loss",
            "min_delta_mae",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(f"{name} must be finite and within [0, 1]")
        patience = self.refinement_patience
        if isinstance(patience, bool) or not isinstance(patience, int) or patience < 0:
            raise ValueError("refinement_patience must be a non-negative integer")
        if not isinstance(self.detect_duplicate_patch, bool):
            raise ValueError("detect_duplicate_patch must be a boolean")

    @classmethod
    def for_quality_preset(
        cls,
        quality_preset: Literal["fast", "balanced", "high", "manual"] | str,
    ) -> DirectOptimizationPolicy:
        """Resolve the single canonical preset-to-target mapping."""
        try:
            target_mae, target_loss = QUALITY_TARGETS[quality_preset]
        except KeyError as exc:
            raise ValueError("quality_preset is unsupported") from exc
        return cls(
            quality_preset=quality_preset,  # type: ignore[arg-type]
            target_mae=target_mae,
            target_loss=target_loss,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-safe optimization policy."""
        return {
            "schema_version": self.schema_version,
            "quality_preset": self.quality_preset,
            "target_mae": self.target_mae,
            "target_loss": self.target_loss,
            "min_delta_loss": self.min_delta_loss,
            "min_delta_mae": self.min_delta_mae,
            "refinement_patience": self.refinement_patience,
            "detect_duplicate_patch": self.detect_duplicate_patch,
        }

    def fingerprint(self) -> str:
        """Return the stable content-addressed policy identity."""
        return sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


def candidate_excess_dominates(
    *,
    candidate_mae: float,
    candidate_loss: float,
    incumbent_mae: float,
    incumbent_loss: float,
    target_mae: float,
    target_loss: float,
) -> bool:
    """Return whether a candidate strictly dominates target-relative excesses."""
    candidate_mae_excess = max(0.0, candidate_mae - target_mae)
    candidate_loss_excess = max(0.0, candidate_loss - target_loss)
    incumbent_mae_excess = max(0.0, incumbent_mae - target_mae)
    incumbent_loss_excess = max(0.0, incumbent_loss - target_loss)
    return (
        candidate_mae_excess <= incumbent_mae_excess
        and candidate_loss_excess <= incumbent_loss_excess
        and (
            candidate_mae_excess < incumbent_mae_excess
            or candidate_loss_excess < incumbent_loss_excess
        )
    )


@dataclass(frozen=True)
class RefineStaticViolation:
    """One bounded, provider-independent source location safe for the next Refine."""

    code: str
    line: int | None = None

    def __post_init__(self) -> None:
        """Reject messages, invalid identifiers, and unusable source locations."""
        if not re.fullmatch(r"[a-z0-9_]{1,128}", self.code):
            raise ValueError("static violation code must be a stable safe identifier")
        if self.line is not None and (
            isinstance(self.line, bool)
            or not isinstance(self.line, int)
            or self.line <= 0
        ):
            raise ValueError("static violation line must be a positive integer")

    def to_dict(self) -> dict[str, str | int | None]:
        """Return the only prompt-safe serialization shape."""
        return {"code": self.code, "line": self.line}


@dataclass(frozen=True)
class RefineFeedback:
    """Safe, bounded feedback from one rejected or non-material Refine trial."""

    outcome: RefineFeedbackOutcome
    target_layer_id: str | None = None
    candidate_loss: float | None = None
    candidate_mae: float | None = None
    loss_delta: float | None = None
    mae_delta: float | None = None
    metric_deltas: Mapping[str, float] = field(default_factory=dict)
    failure_codes: tuple[str, ...] = ()
    static_violations: tuple[RefineStaticViolation, ...] = ()
    schema_version: str = "direct_refine_feedback_v1"

    def __post_init__(self) -> None:
        """Reject unsafe fields and normalize the optional metric mapping."""
        for name in (
            "candidate_loss",
            "candidate_mae",
            "loss_delta",
            "mae_delta",
        ):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be finite when present")
        for name, value in self.metric_deltas.items():
            if name not in REFINE_FEEDBACK_METRICS:
                raise ValueError("metric_deltas contains an unsupported metric")
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
            ):
                raise ValueError("metric delta must be finite")
        for code in self.failure_codes:
            if not re.fullmatch(r"[a-z0-9_]{1,128}", code):
                raise ValueError("failure code must be a stable safe identifier")
        if len(self.static_violations) > MAX_REFINE_STATIC_VIOLATIONS:
            raise ValueError("too many static violations")
        if any(
            not isinstance(violation, RefineStaticViolation)
            for violation in self.static_violations
        ):
            raise ValueError("static violations must use the safe value object")
        if self.target_layer_id is not None and not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", self.target_layer_id
        ):
            raise ValueError("target_layer_id is invalid")

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-safe feedback payload."""
        return {
            "schema_version": self.schema_version,
            "outcome": self.outcome,
            "target_layer_id": self.target_layer_id,
            "candidate_loss": self.candidate_loss,
            "candidate_mae": self.candidate_mae,
            "loss_delta": self.loss_delta,
            "mae_delta": self.mae_delta,
            "metric_deltas": dict(sorted(self.metric_deltas.items())),
            "failure_codes": list(self.failure_codes),
            "static_violations": [
                violation.to_dict() for violation in self.static_violations
            ],
        }


@dataclass(frozen=True)
class LayerPlanGlslDirectConfig:
    """Frozen budgets and implementation identity for one direct attempt."""

    implementation_identity_sha256: str
    direct_author_llm_budget: int = 8
    compile_budget: int = 8
    draw_budget: int = 8
    refine_budget: int = 2
    plan_llm_budget: int = 2
    uniform_tuning_draw_budget: int = 4
    uniform_tuning_active_component_cap: int = 8
    uniform_tuning_max_passes: int = 1
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
            "uniform_tuning_draw_budget",
            "uniform_tuning_active_component_cap",
            "uniform_tuning_max_passes",
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
                or value > MAX_CANVAS_SIDE
            ):
                raise ValueError(f"{name} must be within renderer limits")
        identity = self.implementation_identity_sha256
        if len(identity) != 64 or any(
            char not in "0123456789abcdef" for char in identity
        ):
            raise ValueError("implementation_identity_sha256 must be lowercase sha256")

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe direct-attempt configuration."""
        return {
            "implementation_identity_sha256": self.implementation_identity_sha256,
            "direct_author_llm_budget": self.direct_author_llm_budget,
            "compile_budget": self.compile_budget,
            "draw_budget": self.draw_budget,
            "refine_budget": self.refine_budget,
            "plan_llm_budget": self.plan_llm_budget,
            "uniform_tuning_draw_budget": self.uniform_tuning_draw_budget,
            "uniform_tuning_active_component_cap": (
                self.uniform_tuning_active_component_cap
            ),
            "uniform_tuning_max_passes": self.uniform_tuning_max_passes,
            "canvas_width": self.canvas_width,
            "canvas_height": self.canvas_height,
            "requested_sampling_params": dict(REQUESTED_SAMPLING_PARAMS),
        }

    def fingerprint(self) -> str:
        """Return the content-addressed configuration fingerprint."""
        return sha256(canonical_json(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DirectPlanLedger:
    """Immutable accounting snapshot for LayerPlan authoring."""

    llm_call_count: int
    total_tokens: int | None
    repair_count: int
    wall_clock_ms: float

    @classmethod
    def from_mutable(cls, ledger: PlanLedger) -> DirectPlanLedger:
        """Freeze an attempt-local plan ledger."""
        return cls(
            llm_call_count=ledger.llm_call_count,
            total_tokens=ledger.total_tokens,
            repair_count=ledger.repair_count,
            wall_clock_ms=round(ledger.wall_clock_ms, 2),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe plan accounting summary."""
        return {
            "llm_call_count": self.llm_call_count,
            "total_tokens": self.total_tokens,
            "repair_count": self.repair_count,
            "wall_clock_ms": self.wall_clock_ms,
        }


@dataclass(frozen=True)
class DirectLedger:
    """Immutable accounting snapshot for authoring and rendering."""

    llm_call_count: int
    total_tokens: int | None
    repair_count: int
    compile_count: int
    draw_count: int
    cache_hits: int
    wall_clock_ms: float
    rejected_candidates: int
    accepted_candidates: int
    uniform_tuning_draw_count: int
    uniform_tuning_evaluated_count: int
    uniform_tuning_accepted_count: int
    uniform_tuning_duplicate_count: int
    uniform_tuning_session_count: int
    uniform_tuning_active_component_count: int

    @classmethod
    def from_mutable(cls, ledger: AttemptLedger) -> DirectLedger:
        """Freeze an attempt-local direct ledger."""
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
            uniform_tuning_draw_count=ledger.uniform_tuning_draw_count,
            uniform_tuning_evaluated_count=ledger.uniform_tuning_evaluated_count,
            uniform_tuning_accepted_count=ledger.uniform_tuning_accepted_count,
            uniform_tuning_duplicate_count=ledger.uniform_tuning_duplicate_count,
            uniform_tuning_session_count=ledger.uniform_tuning_session_count,
            uniform_tuning_active_component_count=(
                ledger.uniform_tuning_active_component_count
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe direct accounting summary."""
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
            "uniform_tuning_draw_count": self.uniform_tuning_draw_count,
            "uniform_tuning_evaluated_count": self.uniform_tuning_evaluated_count,
            "uniform_tuning_accepted_count": self.uniform_tuning_accepted_count,
            "uniform_tuning_duplicate_count": self.uniform_tuning_duplicate_count,
            "uniform_tuning_session_count": self.uniform_tuning_session_count,
            "uniform_tuning_active_component_count": (
                self.uniform_tuning_active_component_count
            ),
        }


@dataclass(frozen=True)
class DirectEngineIdentity:
    """Frozen identity for the direct engine, representation and evaluator."""

    implementation_identity_sha256: str
    engine_id: str = DIRECT_ENGINE_ID
    representation: str = DIRECT_REPRESENTATION
    metric_version: str = MIN_SCENE_METRIC_VERSION
    renderer_contract_id: str = "webgl1_static_no_texture_v1"

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-safe engine identity."""
        return {
            "engine_id": self.engine_id,
            "representation": self.representation,
            "implementation_identity_sha256": self.implementation_identity_sha256,
            "metric_version": self.metric_version,
            "renderer_contract_id": self.renderer_contract_id,
        }


@dataclass(frozen=True)
class DirectCandidate:
    """One successful Layered source and compiled ProgramSpec snapshot."""

    layered_spec: LayeredShaderSpecV1
    spec: ShaderProgramSpecV1
    role: Literal["initial", "refine", "uniform_optimize"]
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
    """Immutable in-memory result for one direct attempt."""

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
    optimization_policy: DirectOptimizationPolicy
    optimization_policy_fingerprint: str
    refinement_stop_reason: RefinementStopReason
    non_improving_count: int
    duplicate_patch_count: int
    uniform_optimization_summary: UniformOptimizationSummaryV2 | None = None
    uniform_optimization_trace: tuple[dict[str, Any], ...] = ()
    private_diagnostics: tuple[dict[str, Any], ...] = ()

    def to_safe_summary(self) -> dict[str, Any]:
        """Return a JSON-safe summary without private workflow content."""
        best = self.current_best
        plan = self.layer_plan
        return {
            "schema_version": DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION,
            "status": self.status,
            "failure_code": self.failure_code,
            "safety_failure_codes": list(self.safety_failure_codes),
            "identity": self.identity.to_dict(),
            "config_fingerprint": self.config_fingerprint,
            "optimization_policy": self.optimization_policy.to_dict(),
            "optimization_policy_fingerprint": self.optimization_policy_fingerprint,
            "refinement_stop_reason": self.refinement_stop_reason,
            "non_improving_count": self.non_improving_count,
            "duplicate_patch_count": self.duplicate_patch_count,
            "uniform_optimization": (
                self.uniform_optimization_summary.to_dict()
                if self.uniform_optimization_summary is not None
                else None
            ),
            "reference_sha256": self.reference_sha256,
            "reference_content_type": self.reference_content_type,
            "instruction_sha256": self.instruction_sha256,
            "canvas": {"width": self.canvas_width, "height": self.canvas_height},
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
                    "layered_spec_sha256": best.layered_spec.layered_spec_sha256,
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
        """Return redacted diagnostics for private attempt storage."""
        return [dict(item) for item in self.private_diagnostics]

    def to_private_uniform_optimization_trace(self) -> list[dict[str, Any]]:
        """Return the hash-addressed optimizer trace for private child storage."""
        return [dict(item) for item in self.uniform_optimization_trace]


def derive_canvas(image: Image.Image) -> tuple[int, int]:
    """Derive a bounded working canvas while preserving aspect ratio."""
    width, height = image.size
    scale = min(1.0, MAX_WORK_SIDE / max(width, height))
    return max(16, round(width * scale)), max(16, round(height * scale))


def border_background(rgb: np.ndarray) -> tuple[float, float, float]:
    """Estimate the background color from border pixels."""
    border = np.concatenate((rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]), axis=0)
    median = np.median(border, axis=0)
    return (float(median[0]), float(median[1]), float(median[2]))


def decode_reference(image_bytes: bytes) -> Image.Image:
    """Decode the reference image into RGB or raise a stable error."""
    try:
        return Image.open(BytesIO(image_bytes)).convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("unable to decode reference image") from exc


def safe_compile_diagnostics(compile_result: CompileResult) -> dict[str, object]:
    """Redact renderer diagnostics to hashes and stable violation categories."""

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


def accumulate_token_usage(
    current_total: int | None,
    observed_total: int | None,
    *,
    call_count: int,
) -> int | None:
    """Accumulate known token totals and preserve unknown accounting."""
    if call_count == 0:
        return current_total
    if current_total is None or observed_total is None:
        return None
    return current_total + observed_total


def program_cache_key(spec: ShaderProgramSpecV1) -> tuple[object, ...]:
    """Return the attempt-local prepared-program cache key."""
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


def safe_failure_codes(
    events: list[dict[str, Any]],
    failure_code: str | None,
) -> tuple[str, ...]:
    """Map internal failures to predeclared public codes."""
    codes: list[str] = []
    for event in events:
        if event.get("ok") is not False:
            continue
        raw = event.get("error_code")
        if raw in {"duplicate_patch", "no_op_patch"}:
            continue
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


def private_diagnostic_events(
    events: list[dict[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Keep stable error metadata without source or provider text."""
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
                    {"code": category.get("code"), "line": category.get("line")}
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


def normalize_author_failure(error_code: str | None) -> str:
    """Normalize provider/parser failures to a stable public code."""
    if error_code in INCONCLUSIVE_CODES:
        return str(error_code)
    if isinstance(error_code, str) and error_code.startswith("llm_"):
        return "llm_invocation_failed"
    return "author_output_invalid"


__all__ = [
    "AttemptLedger",
    "DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION",
    "DIRECT_ENGINE_ID",
    "DIRECT_OPTIMIZATION_POLICY_SCHEMA_VERSION",
    "DIRECT_REPRESENTATION",
    "DirectAttemptResult",
    "DirectCandidate",
    "DirectEngineIdentity",
    "DirectLedger",
    "DirectPlanLedger",
    "DirectOptimizationPolicy",
    "DirectPreparedRenderer",
    "DirectRenderer",
    "INCONCLUSIVE_CODES",
    "LAYERED_AUTHORING_REPRESENTATION",
    "LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION",
    "LAYERED_PARSER_POLICY_VERSION",
    "MAX_REFINE_STATIC_VIOLATIONS",
    "LayerPlanGlslDirectConfig",
    "PlanLedger",
    "RENDERER_DEFERRED_SAFETY_CODES",
    "REFINE_FEEDBACK_METRICS",
    "RefineFeedback",
    "RefineFeedbackOutcome",
    "RefineStaticViolation",
    "RefinementStopReason",
    "QUALITY_TARGETS",
    "TERMINAL_REFINEMENT_FAILURE_CODES",
    "accumulate_token_usage",
    "border_background",
    "candidate_excess_dominates",
    "decode_reference",
    "derive_canvas",
    "normalize_author_failure",
    "private_diagnostic_events",
    "program_cache_key",
    "safe_compile_diagnostics",
    "safe_failure_codes",
]
