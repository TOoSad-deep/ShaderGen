from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Any, cast

import pytest
from PIL import Image
from pydantic import ValidationError

from shaderforge.benchmark.v2_3_actual_chromium_replay import (
    V2_3ActualChromiumCandidateReceiptV1,
    V2_3ActualChromiumExecutionItemV1,
    V2_3ActualChromiumReplayError,
    V2_3ActualChromiumReplayRunner,
    V2_3ActualRendererEnvironmentV1,
    build_actual_renderer_environment,
    compare_replayed_render_pixels,
    compute_actual_candidate_receipt_hash,
    compute_actual_execution_item_hash,
    compute_actual_renderer_environment_hash,
    compute_actual_renderer_protocol_hash,
)
from shaderforge.evaluation.rendered_structure import (
    RendererEnvironmentReceiptV3,
    compute_renderer_environment_hash,
)
from shaderforge.rendering.models import RendererMetadata
from shaderforge.store import ArtifactRefV2


def _png(
    rgba: tuple[int, int, int, int],
    *,
    size: tuple[int, int] = (4, 4),
    changed_pixel: tuple[int, int, tuple[int, int, int, int]] | None = None,
) -> bytes:
    image = Image.new("RGBA", size, rgba)
    if changed_pixel is not None:
        x, y, value = changed_pixel
        image.putpixel((x, y), value)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _ref(
    artifact_id: str,
    *,
    kind: str = "test",
    schema_version: str = "test_v1",
    content_type: str = "application/json",
) -> ArtifactRefV2:
    return ArtifactRefV2(
        artifact_id=artifact_id,
        sha256=(artifact_id[0] if artifact_id[0] in "abcdef" else "a") * 64,
        kind=kind,
        schema_version=schema_version,
        content_type=content_type,
        size_bytes=1,
    )


def _persisted_environment() -> RendererEnvironmentReceiptV3:
    payload: dict[str, Any] = {
        "renderer_version": "playwright_webgl1_v1",
        "browser_version": "Chromium 1",
        "gl_version": "WebGL 1.0",
        "glsl_version": "WebGL GLSL ES 1.0",
        "gl_vendor": "Google Inc.",
        "gl_renderer": "ANGLE SwiftShader",
        "webgl_context_kind": "webgl1",
        "canvas_alpha": False,
        "canvas_antialias": False,
        "canvas_depth": False,
        "canvas_stencil": False,
        "canvas_alpha_mode": "force_opaque_alpha_v1",
        "canvas_clear_color_rgba": (1.0, 1.0, 1.0, 1.0),
        "premultiplied_alpha": False,
        "preserve_drawing_buffer": True,
        "environment_hash": "0" * 64,
    }
    payload["environment_hash"] = compute_renderer_environment_hash(payload)
    return RendererEnvironmentReceiptV3.model_validate(payload, strict=True)


@dataclass(frozen=True)
class _MetadataStub:
    renderer_version: str = "playwright_webgl1_v1"
    browser_version: str = "Chromium 1"
    gl_version: str = "WebGL 1.0"
    glsl_version: str = "WebGL GLSL ES 1.0"
    gl_vendor: str = "Google Inc."
    gl_renderer: str = "ANGLE SwiftShader"
    include_context: bool = True

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "renderer_version": self.renderer_version,
            "browser_version": self.browser_version,
            "gl_version": self.gl_version,
            "glsl_version": self.glsl_version,
            "gl_vendor": self.gl_vendor,
            "gl_renderer": self.gl_renderer,
        }
        if self.include_context:
            result.update(
                {
                    "webgl_context_kind": "webgl1",
                    "canvas_alpha": False,
                    "canvas_antialias": False,
                    "canvas_depth": False,
                    "canvas_stencil": False,
                    "premultiplied_alpha": False,
                    "preserve_drawing_buffer": True,
                    "canvas_clear_color_rgba": (1.0, 1.0, 1.0, 1.0),
                }
            )
        return result


def _actual_environment() -> V2_3ActualRendererEnvironmentV1:
    return build_actual_renderer_environment(
        metadata=cast(RendererMetadata, _MetadataStub()),
        persisted=_persisted_environment(),
    )


def test_beauty_comparison_uses_frozen_mae_and_exact_alpha() -> None:
    baseline = _png((20, 30, 40, 255))
    within = _png((21, 31, 41, 255))
    over = _png((22, 32, 42, 255))
    alpha_changed = _png((20, 30, 40, 254))

    assert compare_replayed_render_pixels(
        profile="beauty_full_v1",
        persisted_png=baseline,
        replay_png=within,
        width=4,
        height=4,
    ).passed
    assert not compare_replayed_render_pixels(
        profile="beauty_full_v1",
        persisted_png=baseline,
        replay_png=over,
        width=4,
        height=4,
    ).passed
    assert not compare_replayed_render_pixels(
        profile="beauty_full_v1",
        persisted_png=baseline,
        replay_png=alpha_changed,
        width=4,
        height=4,
    ).passed


def test_diagnostic_requires_channel_delta_and_exact_threshold_mask() -> None:
    # 当前冻结 visible-delta threshold 为 8；7 -> 8 只有一个 byte 差异，
    # 但 classification mask 改变，因此必须拒绝。
    below = _png((7, 0, 0, 255))
    threshold_crossed = _png((8, 0, 0, 255))
    same_side = _png((6, 0, 0, 255))

    crossed = compare_replayed_render_pixels(
        profile="subject_visible_delta_full_v1",
        persisted_png=below,
        replay_png=threshold_crossed,
        width=4,
        height=4,
    )
    assert crossed.max_rgba_channel_delta == 1
    assert crossed.diagnostic_mask_exact is False
    assert not crossed.passed

    assert compare_replayed_render_pixels(
        profile="subject_visible_delta_full_v1",
        persisted_png=below,
        replay_png=same_side,
        width=4,
        height=4,
    ).passed
    assert not compare_replayed_render_pixels(
        profile="subject_visible_delta_full_v1",
        persisted_png=below,
        replay_png=_png((9, 0, 0, 255)),
        width=4,
        height=4,
    ).passed


def test_actual_environment_requires_measured_context_and_exact_binding() -> None:
    actual = _actual_environment()
    assert actual.context.context_kind == "webgl1"
    assert actual.context.preserve_drawing_buffer is True
    assert actual.environment_hash == compute_actual_renderer_environment_hash(actual)

    with pytest.raises(
        V2_3ActualChromiumReplayError,
        match="context attributes",
    ) as missing:
        build_actual_renderer_environment(
            metadata=cast(RendererMetadata, _MetadataStub(include_context=False)),
            persisted=_persisted_environment(),
        )
    assert missing.value.code == "renderer_context_metadata_unavailable"

    drifted = _MetadataStub(browser_version="Chromium 2")
    with pytest.raises(V2_3ActualChromiumReplayError) as drift:
        build_actual_renderer_environment(
            metadata=cast(RendererMetadata, drifted),
            persisted=_persisted_environment(),
        )
    assert drift.value.code == "renderer_environment_mismatch"


def test_execution_receipt_hash_and_pixel_pass_are_not_self_reported() -> None:
    comparison = compare_replayed_render_pixels(
        profile="beauty_full_v1",
        persisted_png=_png((20, 30, 40, 255)),
        replay_png=_png((20, 30, 40, 255)),
        width=4,
        height=4,
    )
    payload: dict[str, Any] = {
        "run_id": "run-1",
        "candidate_id": "candidate-1",
        "attempt_id": "attempt-1",
        "candidate_ref": _ref(
            "candidate", kind="candidate_record", schema_version="candidate_record_v3"
        ),
        "render_plan_ref": _ref(
            "plan", kind="renderer_plan", schema_version="renderer_plan_v3"
        ),
        "render_plan_hash": "b" * 64,
        "logical_request_ordinal": 1,
        "profile": "beauty_full_v1",
        "beauty_capture_index": 0,
        "diagnostic_pass_id": None,
        "compilation_ref": _ref(
            "compilation",
            kind="compilation_bundle",
            schema_version="compilation_bundle_v1",
        ),
        "source_ref": _ref(
            "source",
            kind="compiled_glsl",
            schema_version="compiled_glsl_es_100_v1",
            content_type="text/x-glsl; charset=utf-8",
        ),
        "source_sha256": "a" * 64,
        "renderer_request_ref": _ref(
            "request",
            kind="renderer_request_receipt",
            schema_version="renderer_request_receipt_v2",
        ),
        "renderer_request_hash": "c" * 64,
        "persisted_renderer_environment_ref": _ref(
            "environment",
            kind="renderer_environment",
            schema_version="renderer_environment_receipt_v3",
        ),
        "persisted_renderer_environment_hash": _persisted_environment().environment_hash,
        "persisted_render_ref": _ref(
            "render",
            kind="render_png",
            schema_version="render_png_v2",
            content_type="image/png",
        ),
        "persisted_render_sha256": "a" * 64,
        "actual_environment": _actual_environment(),
        "static_validation_passed": True,
        "vertex_compile_passed": True,
        "fragment_compile_passed": True,
        "program_link_passed": True,
        "draw_passed": True,
        "readback_passed": True,
        "console_errors": (),
        "pixel_comparison": comparison,
        "record_hash": "0" * 64,
    }
    payload["record_hash"] = compute_actual_execution_item_hash(payload)
    receipt = V2_3ActualChromiumExecutionItemV1.model_validate(payload, strict=True)
    assert receipt.record_hash == compute_actual_execution_item_hash(receipt)

    with pytest.raises(ValidationError, match="hash"):
        V2_3ActualChromiumExecutionItemV1.model_validate(
            {**payload, "renderer_request_hash": "d" * 64}, strict=True
        )

    item_payloads: list[dict[str, Any]] = []
    for ordinal in range(1, 7):
        item = dict(payload)
        profile = (
            "beauty_full_v1"
            if ordinal <= 5
            else "subject_visible_delta_full_v1"
        )
        item.update(
            {
                "logical_request_ordinal": ordinal,
                "profile": profile,
                "beauty_capture_index": ordinal - 1 if ordinal <= 5 else None,
                "diagnostic_pass_id": None if ordinal <= 5 else "subject-visible",
                "renderer_request_hash": f"{ordinal:x}" * 64,
                "pixel_comparison": compare_replayed_render_pixels(
                    profile=profile,
                    persisted_png=_png((20, 30, 40, 255)),
                    replay_png=_png((20, 30, 40, 255)),
                    width=4,
                    height=4,
                ),
                "record_hash": "0" * 64,
            }
        )
        if ordinal == 6:
            item.update(
                {
                    "compilation_ref": _ref(
                        "diagnostic-compilation",
                        kind="diagnostic_compilation_bundle",
                        schema_version="diagnostic_compilation_bundle_v3",
                    ),
                    "source_ref": _ref(
                        "diagnostic-source",
                        kind="diagnostic_glsl",
                        schema_version="diagnostic_glsl_es_100_v3",
                        content_type="text/x-glsl; charset=utf-8",
                    ),
                    "source_sha256": "d" * 64,
                    "persisted_render_ref": _ref(
                        "diagnostic-render",
                        kind="diagnostic_render_png",
                        schema_version="diagnostic_render_png_v3",
                        content_type="image/png",
                    ),
                    "persisted_render_sha256": "d" * 64,
                }
            )
        item["record_hash"] = compute_actual_execution_item_hash(item)
        item_payloads.append(item)
    items = tuple(
        V2_3ActualChromiumExecutionItemV1.model_validate(item, strict=True)
        for item in item_payloads
    )
    candidate_payload: dict[str, Any] = {
        "run_id": receipt.run_id,
        "candidate_id": receipt.candidate_id,
        "attempt_id": receipt.attempt_id,
        "candidate_ref": receipt.candidate_ref,
        "render_plan_ref": receipt.render_plan_ref,
        "render_plan_hash": receipt.render_plan_hash,
        "renderer_protocol_hash": receipt.actual_environment.renderer_protocol_hash,
        "actual_environment_hash": receipt.actual_environment.environment_hash,
        "item_receipts": items,
        "beauty_execution_count": 5,
        "diagnostic_execution_count": 1,
        "record_hash": "0" * 64,
    }
    candidate_payload["record_hash"] = compute_actual_candidate_receipt_hash(
        candidate_payload
    )
    candidate = V2_3ActualChromiumCandidateReceiptV1.model_validate(
        candidate_payload, strict=True
    )
    assert candidate.record_hash == compute_actual_candidate_receipt_hash(candidate)

    duplicate = {**candidate_payload, "item_receipts": (*items[:4], items[3], items[5])}
    duplicate["record_hash"] = compute_actual_candidate_receipt_hash(duplicate)
    with pytest.raises(ValidationError, match="连续"):
        V2_3ActualChromiumCandidateReceiptV1.model_validate(duplicate, strict=True)

    drift_environment = receipt.actual_environment.model_dump(mode="python")
    drift_environment["browser_version"] = "Chromium drift"
    drift_environment["environment_hash"] = compute_actual_renderer_environment_hash(
        drift_environment
    )
    drift_item = dict(item_payloads[-1])
    drift_item["actual_environment"] = V2_3ActualRendererEnvironmentV1.model_validate(
        drift_environment, strict=True
    )
    drift_item["record_hash"] = compute_actual_execution_item_hash(drift_item)
    drift_receipt = V2_3ActualChromiumExecutionItemV1.model_validate(
        drift_item, strict=True
    )
    environment_drift = {
        **candidate_payload,
        "item_receipts": (*items[:-1], drift_receipt),
        "record_hash": "0" * 64,
    }
    environment_drift["record_hash"] = compute_actual_candidate_receipt_hash(
        environment_drift
    )
    with pytest.raises(ValidationError, match="environment"):
        V2_3ActualChromiumCandidateReceiptV1.model_validate(
            environment_drift, strict=True
        )


def test_runner_has_no_renderer_factory_injection_and_protocol_is_bound() -> None:
    assert tuple(inspect_parameter for inspect_parameter in __import__(
        "inspect"
    ).signature(V2_3ActualChromiumReplayRunner).parameters) == ()
    protocol_hash = compute_actual_renderer_protocol_hash()
    assert len(protocol_hash) == 64
    assert protocol_hash != "0" * 64
