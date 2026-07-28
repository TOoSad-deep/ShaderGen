"""Current Layered Direct LayerPlan author contract.

The model may describe layer semantics only. Canonical identity, validation and
hashing remain owned by :mod:`shaderforge.program_spec`.
"""

from __future__ import annotations

import json
from typing import Any, cast

from shaderforge.program_spec import (
    LAYER_PLAN_V1_SCHEMA_VERSION,
    LayerAuthorIdentity,
    LayerPlanV1,
    ProgramSpecParseError,
    build_layer_plan,
)

LAYER_PLAN_SCHEMA_VERSION = LAYER_PLAN_V1_SCHEMA_VERSION
_PLAN_MAX_CHARS = 40_000
_FORBIDDEN_MODEL_KEY_MARKERS = ("attestation", "sha256", "hash", "author_identity")
_LAYER_ROLES = ["background", "subject", "highlight", "shadow", "glow", "detail"]

_LAYER_PLAN_JSON_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "layers"],
    "properties": {
        "schema_version": {"const": LAYER_PLAN_SCHEMA_VERSION},
        "layers": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "layer_id",
                    "role",
                    "z_index",
                    "region",
                    "dominant_colors",
                    "confidence",
                ],
                "properties": {
                    "layer_id": {
                        "type": "string",
                        "pattern": "^[A-Za-z0-9_-]{1,64}$",
                    },
                    "role": {"enum": _LAYER_ROLES},
                    "z_index": {"type": "integer", "minimum": 0},
                    "region": {
                        "type": "object",
                        "description": (
                            "Normalized WebGL v_uv bounding box; origin is bottom-left."
                        ),
                        "additionalProperties": False,
                        "required": ["x", "y", "width", "height"],
                        "properties": {
                            name: {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            }
                            for name in ("x", "y", "width", "height")
                        },
                    },
                    "dominant_colors": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 4,
                        "items": {
                            "type": "array",
                            "minItems": 4,
                            "maxItems": 4,
                            "items": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            },
                        },
                    },
                    "confidence": {
                        "type": "number",
                        "minimum": 0.0,
                        "maximum": 1.0,
                    },
                    "notes": {"type": "string", "maxLength": 280},
                },
            },
        },
    },
}


class LayerPlanAuthorParseError(ValueError):
    """The model output is not an accepted complete LayerPlan JSON value."""

    def __init__(
        self,
        code: str,
        *,
        details: tuple[dict[str, str], ...] = (),
    ) -> None:
        self.code = code
        self.details = details
        super().__init__(code)


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"JSON does not allow non-finite number: {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _assert_no_trusted_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if any(
                marker in str(key).lower() for marker in _FORBIDDEN_MODEL_KEY_MARKERS
            ):
                raise LayerPlanAuthorParseError(
                    "untrusted_attestation_or_hash_field",
                    details=(
                        {
                            "location": str(key),
                            "type": "forbidden_field",
                            "message": "model must not provide trusted identity fields",
                        },
                    ),
                )
            _assert_no_trusted_fields(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_trusted_fields(item)


def parse_layer_plan_semantics(text: str) -> dict[str, Any]:
    """Strictly parse and validate untrusted LayerPlan semantics."""
    try:
        if len(text) > _PLAN_MAX_CHARS:
            raise ValueError("LayerPlan JSON exceeds size limit")
        payload = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except ValueError as exc:
        raise LayerPlanAuthorParseError("invalid_layer_plan_json") from exc
    _assert_no_trusted_fields(payload)
    if not isinstance(payload, dict):
        raise LayerPlanAuthorParseError("invalid_layer_plan_json")
    try:
        build_layer_plan(
            payload,
            reference_sha256="0" * 64,
            author_identity=LayerAuthorIdentity(
                model_ref="parse-probe",
                prompt_version="parse-probe",
                schema_version=LAYER_PLAN_SCHEMA_VERSION,
            ),
        )
    except ProgramSpecParseError as exc:
        raise LayerPlanAuthorParseError(
            "invalid_layer_plan_json",
            details=(
                {
                    "location": "",
                    "type": exc.code,
                    "message": str(exc)[:240],
                },
            ),
        ) from exc
    return payload


def assemble_layer_plan(
    payload: dict[str, Any],
    *,
    reference_sha256: str,
    author_identity: LayerAuthorIdentity,
    observations_ref: str | None = None,
) -> LayerPlanV1:
    """Build the canonical trusted LayerPlan from validated semantics."""
    return build_layer_plan(
        payload,
        reference_sha256=reference_sha256,
        author_identity=author_identity,
        observations_ref=observations_ref,
    )


def layer_plan_json_schema() -> dict[str, object]:
    """Return a detached JSON schema for the model response."""
    return cast(dict[str, object], json.loads(json.dumps(_LAYER_PLAN_JSON_SCHEMA)))


__all__ = [
    "LAYER_PLAN_SCHEMA_VERSION",
    "LayerPlanAuthorParseError",
    "LayerPlanV1",
    "assemble_layer_plan",
    "layer_plan_json_schema",
    "parse_layer_plan_semantics",
]
