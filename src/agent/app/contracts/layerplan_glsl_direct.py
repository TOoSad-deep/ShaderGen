"""Graph-independent contracts and helpers for one direct GLSL attempt."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
from typing import Any, Literal, Protocol

import numpy as np
from PIL import Image, UnidentifiedImageError

from agent.app.contracts.layer_plan import LayerPlanV1
from shaderforge.evaluation import MIN_SCENE_METRIC_VERSION
from shaderforge.layered_spec import LayeredShaderSpecV1
from shaderforge.program_spec import ShaderProgramSpecV1, canonical_json
from shaderforge.rendering import CompileResult, PreparedRenderResult

DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION = "direct_glsl_attempt_result_v1"
DIRECT_ENGINE_ID = "direct_glsl_layerplan_v1"
DIRECT_REPRESENTATION = "shader_program_spec_v1"
LAYERED_AUTHORING_REPRESENTATION = "layered_shader_spec_v1"
LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION = "direct_layered_glsl_implementation_v1"
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


@dataclass(frozen=True)
class LayerPlanGlslDirectConfig:
    """Frozen budgets and implementation identity for one direct attempt."""

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
    "DIRECT_REPRESENTATION",
    "DirectAttemptResult",
    "DirectCandidate",
    "DirectEngineIdentity",
    "DirectLedger",
    "DirectPlanLedger",
    "DirectPreparedRenderer",
    "DirectRenderer",
    "INCONCLUSIVE_CODES",
    "LAYERED_AUTHORING_REPRESENTATION",
    "LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION",
    "LAYERED_PARSER_POLICY_VERSION",
    "LayerPlanGlslDirectConfig",
    "PlanLedger",
    "RENDERER_DEFERRED_SAFETY_CODES",
    "TERMINAL_REFINEMENT_FAILURE_CODES",
    "accumulate_token_usage",
    "border_background",
    "decode_reference",
    "derive_canvas",
    "normalize_author_failure",
    "private_diagnostic_events",
    "program_cache_key",
    "safe_compile_diagnostics",
    "safe_failure_codes",
]
