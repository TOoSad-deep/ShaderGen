"""Service facade for one LayerPlan + direct GLSL LangGraph attempt."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict
from hashlib import sha256
from typing import Any

from agent.app.contracts.layer_plan import layer_plan_json_schema
from agent.app.contracts.layered_direct_glsl import (
    layer_patch_json_schema,
    layered_shader_spec_json_schema,
)
from agent.app.contracts.layerplan_glsl_direct import (
    DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION,
    DIRECT_ENGINE_ID,
    DIRECT_REPRESENTATION,
    LAYERED_AUTHORING_REPRESENTATION,
    LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION,
    LAYERED_PARSER_POLICY_VERSION,
    RENDERER_DEFERRED_SAFETY_CODES,
    DirectAttemptResult,
    DirectCandidate,
    DirectEngineIdentity,
    DirectLedger,
    DirectPlanLedger,
    DirectRenderer,
    LayerPlanGlslDirectConfig,
)
from agent.app.contracts.llm import LLMGateway
from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.nodes.layered_direct.authors import (
    DEFAULT_LAYER_PATCH_MAX_OUTPUT_TOKENS,
    DEFAULT_LAYERED_INITIAL_MAX_OUTPUT_TOKENS,
    DIRECT_LAYERED_INITIAL_PROMPT,
    DIRECT_LAYERED_REFINE_PROMPT,
    DIRECT_LAYERED_REPAIR_PROMPT,
)
from agent.app.nodes.layered_direct.layer_plan_author import (
    DEFAULT_PLAN_MAX_OUTPUT_TOKENS,
    VISUAL_ANALYSIS_PROMPT,
)
from agent.app.nodes.layered_direct.structured_author import MAX_STRUCTURED_ATTEMPTS
from agent.app.states.layerplan_glsl_direct import (
    DIRECT_GRAPH_NODE_NAMES,
    NodeProgressCallback,
)
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.layered_spec import (
    LAYER_PATCH_V1_SCHEMA_VERSION,
    LAYERED_COMPILER_VERSION,
    LAYERED_SHADER_SPEC_V1_SCHEMA_VERSION,
)
from shaderforge.program_spec import (
    TRUSTED_VALIDATOR_VERSION,
    TrustedReceiptVerifier,
    canonical_json,
    process_receipt_verifier,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer
from shaderforge.validation import ProgramSpecSafetyLimits


def current_layered_direct_glsl_implementation_identity() -> dict[str, Any]:
    """Return the stable identity of the current layered direct workflow."""
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
        "renderer_deferred_safety_codes": sorted(RENDERER_DEFERRED_SAFETY_CODES),
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


class LayerPlanGlslDirectRunner:
    """Inject attempt-local dependencies and invoke the direct graph."""

    def __init__(
        self,
        *,
        gateway: LLMGateway,
        renderer: DirectRenderer,
        config: LayerPlanGlslDirectConfig,
        clock: Callable[[], float] = time.perf_counter,
        receipt_issuer: TrustedReceiptVerifier | None = None,
    ) -> None:
        """Inject attempt-local gateway, renderer, budgets and trust root."""
        self._config = config
        self._gateway = gateway
        self._renderer = renderer
        self._clock = clock
        self._receipt_issuer = receipt_issuer or process_receipt_verifier()

    async def run(
        self,
        reference_image: bytes,
        *,
        content_type: str = "image/png",
        instruction: str = "",
        node_progress_callback: NodeProgressCallback | None = None,
    ) -> DirectAttemptResult:
        """Execute one attempt through the guarded LangGraph entry point."""
        from agent.app.graphs.layerplan_glsl_direct import (
            DirectGraphContext,
            run_layerplan_glsl_direct_graph,
        )

        output = await run_layerplan_glsl_direct_graph(
            reference_image=reference_image,
            content_type=content_type,
            instruction=instruction,
            context=DirectGraphContext(
                gateway=self._gateway,
                renderer=self._renderer,
                config=self._config,
                clock=self._clock,
                receipt_issuer=self._receipt_issuer,
                node_progress_callback=node_progress_callback,
            ),
        )
        return output["result"]


class OwnedLayerPlanGlslDirectRunner:
    """Own a fresh default gateway and Playwright renderer for one attempt."""

    def __init__(self, config: LayerPlanGlslDirectConfig) -> None:
        """Construct owned default resources for one attempt."""
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
        node_progress_callback: NodeProgressCallback | None = None,
    ) -> DirectAttemptResult:
        """Delegate one attempt to the injected runner."""
        return await self._runner.run(
            reference_image,
            content_type=content_type,
            instruction=instruction,
            node_progress_callback=node_progress_callback,
        )

    async def close(self) -> None:
        """Release the owned Playwright renderer."""
        await self._renderer.close()


def create_owned_layerplan_glsl_direct_runner(
    config: LayerPlanGlslDirectConfig,
) -> OwnedLayerPlanGlslDirectRunner:
    """Create a default runner whose owner must close it."""
    return OwnedLayerPlanGlslDirectRunner(config)


__all__ = [
    "DIRECT_GRAPH_NODE_NAMES",
    "DIRECT_ATTEMPT_RESULT_SCHEMA_VERSION",
    "DIRECT_ENGINE_ID",
    "DIRECT_REPRESENTATION",
    "LAYERED_AUTHORING_REPRESENTATION",
    "LAYERED_IMPLEMENTATION_IDENTITY_SCHEMA_VERSION",
    "DirectAttemptResult",
    "DirectCandidate",
    "DirectEngineIdentity",
    "DirectLedger",
    "DirectPlanLedger",
    "LayerPlanGlslDirectConfig",
    "LayerPlanGlslDirectRunner",
    "NodeProgressCallback",
    "OwnedLayerPlanGlslDirectRunner",
    "create_owned_layerplan_glsl_direct_runner",
    "current_layered_direct_glsl_implementation_identity",
]
