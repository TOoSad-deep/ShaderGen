"""V2.3 gate 对 Candidate RenderPlan 的独立 Chromium/WebGL1 重放。"""
# ruff: noqa: D103, D105, D107, D415

from __future__ import annotations

import inspect
from functools import lru_cache
from hashlib import sha256
from io import BytesIO
from typing import Any, Literal, Mapping

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import Field, model_validator

import shaderforge.rendering.webgl1_renderer as concrete_renderer_module
from shaderforge.contracts import (
    FiniteFloat,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
    canonical_sha256,
)
from shaderforge.contracts.png_to_shader_v1 import WEBGL1_STATIC_NO_TEXTURE_V1
from shaderforge.evaluation.candidate_artifacts import (
    TypedCandidateArtifactBundleV2,
    load_typed_candidate_artifacts,
)
from shaderforge.evaluation.render_runtime_artifacts import (
    BEAUTY_CAPTURE_COUNT,
    REPEATABILITY_RGB_MAE_LIMIT,
)
from shaderforge.evaluation.rendered_structure import (
    RendererEnvironmentReceiptV3,
    project_visible_delta_mask_v3,
)
from shaderforge.rendering.models import RendererMetadata, RenderResult
from shaderforge.rendering.webgl1_renderer import (
    HOST_HTML,
    RENDERER_VERSION,
    VERTEX_SHADER,
    PlaywrightWebGL1Renderer,
)
from shaderforge.store import ArtifactRefV2, ArtifactResolver

ACTUAL_CHROMIUM_EXECUTION_ITEM_SCHEMA_VERSION: Literal[
    "v2_3_actual_chromium_execution_item_v1"
] = "v2_3_actual_chromium_execution_item_v1"
ACTUAL_CHROMIUM_CANDIDATE_RECEIPT_SCHEMA_VERSION: Literal[
    "v2_3_actual_chromium_candidate_receipt_v1"
] = "v2_3_actual_chromium_candidate_receipt_v1"
ACTUAL_CHROMIUM_REPLAY_HASH_VERSION: Literal[
    "v2_3_actual_chromium_replay_hash_v1"
] = "v2_3_actual_chromium_replay_hash_v1"


class V2_3ActualChromiumReplayError(RuntimeError):
    """独立 Chromium replay 无法形成可信闭包。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class V2_3ActualWebGLContextV1(FrozenModel):
    """由浏览器实际 context/readback metadata 构造的环境投影。"""

    context_kind: Literal["webgl1"]
    alpha: bool
    antialias: bool
    depth: bool
    stencil: bool
    premultiplied_alpha: bool
    preserve_drawing_buffer: bool
    clear_color_rgba: tuple[
        FiniteFloat, FiniteFloat, FiniteFloat, FiniteFloat
    ]

    @model_validator(mode="after")
    def _validate_context(self) -> V2_3ActualWebGLContextV1:
        if any(value < 0.0 or value > 1.0 for value in self.clear_color_rgba):
            raise ValueError("WebGL clear color 必须位于 0 到 1。")
        return self


class V2_3ActualRendererEnvironmentV1(FrozenModel):
    """concrete renderer 与浏览器实测 context 的完整环境身份。"""

    renderer_version: NonEmptyString
    renderer_protocol_hash: Sha256Hex
    browser_version: NonEmptyString
    gl_version: NonEmptyString
    glsl_version: NonEmptyString
    gl_vendor: NonEmptyString
    gl_renderer: NonEmptyString
    context: V2_3ActualWebGLContextV1
    environment_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_hash(self) -> V2_3ActualRendererEnvironmentV1:
        if self.environment_hash != compute_actual_renderer_environment_hash(self):
            raise ValueError("Actual Chromium environment hash 不一致。")
        return self


class V2_3ReplayPixelComparisonV1(FrozenModel):
    """persisted render 与独立 Chromium readback 的逐像素比较。"""

    profile: Literal[
        "beauty_full_v1",
        "subject_visible_delta_full_v1",
        "instance_visible_delta_full_v1",
        "layer_visible_delta_lowres_v1",
    ]
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    persisted_rgba_sha256: Sha256Hex
    replay_rgba_sha256: Sha256Hex
    rgb_mae: FiniteFloat = Field(ge=0.0, le=1.0)
    max_rgba_channel_delta: int = Field(ge=0, le=255)
    alpha_exact: bool
    persisted_diagnostic_mask_sha256: Sha256Hex | None = None
    replay_diagnostic_mask_sha256: Sha256Hex | None = None
    diagnostic_mask_exact: bool | None = None
    passed: bool

    @model_validator(mode="after")
    def _validate_result(self) -> V2_3ReplayPixelComparisonV1:
        beauty = self.profile == "beauty_full_v1"
        mask_values = (
            self.persisted_diagnostic_mask_sha256,
            self.replay_diagnostic_mask_sha256,
            self.diagnostic_mask_exact,
        )
        if beauty:
            if any(item is not None for item in mask_values):
                raise ValueError("Beauty pixel comparison 不得伪造 diagnostic mask。")
            expected = (
                self.rgb_mae <= REPEATABILITY_RGB_MAE_LIMIT and self.alpha_exact
            )
        else:
            if any(item is None for item in mask_values):
                raise ValueError("Diagnostic pixel comparison 必须包含双方 mask identity。")
            expected = (
                self.max_rgba_channel_delta <= 1
                and self.alpha_exact
                and self.diagnostic_mask_exact is True
            )
        if self.passed != expected:
            raise ValueError("Pixel comparison passed 与冻结阈值不一致。")
        return self


class V2_3ActualChromiumExecutionItemV1(FrozenModel):
    """一个 RenderPlan item 的独立 concrete Chromium execution receipt。"""

    schema_version: Literal["v2_3_actual_chromium_execution_item_v1"] = (
        ACTUAL_CHROMIUM_EXECUTION_ITEM_SCHEMA_VERSION
    )
    hash_version: Literal["v2_3_actual_chromium_replay_hash_v1"] = (
        ACTUAL_CHROMIUM_REPLAY_HASH_VERSION
    )
    run_id: NonEmptyString
    candidate_id: NonEmptyString
    attempt_id: NonEmptyString
    candidate_ref: ArtifactRefV2
    render_plan_ref: ArtifactRefV2
    render_plan_hash: Sha256Hex
    logical_request_ordinal: int = Field(ge=1)
    profile: Literal[
        "beauty_full_v1",
        "subject_visible_delta_full_v1",
        "instance_visible_delta_full_v1",
        "layer_visible_delta_lowres_v1",
    ]
    beauty_capture_index: int | None = Field(default=None, ge=0, le=4)
    diagnostic_pass_id: NonEmptyString | None = None
    compilation_ref: ArtifactRefV2
    source_ref: ArtifactRefV2
    source_sha256: Sha256Hex
    renderer_request_ref: ArtifactRefV2
    renderer_request_hash: Sha256Hex
    persisted_renderer_environment_ref: ArtifactRefV2
    persisted_renderer_environment_hash: Sha256Hex
    persisted_render_ref: ArtifactRefV2
    persisted_render_sha256: Sha256Hex
    actual_environment: V2_3ActualRendererEnvironmentV1
    static_validation_passed: Literal[True]
    vertex_compile_passed: Literal[True]
    fragment_compile_passed: Literal[True]
    program_link_passed: Literal[True]
    draw_passed: Literal[True]
    readback_passed: Literal[True]
    console_errors: tuple[NonEmptyString, ...] = ()
    pixel_comparison: V2_3ReplayPixelComparisonV1
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_receipt(self) -> V2_3ActualChromiumExecutionItemV1:
        if self.source_ref.sha256 != self.source_sha256:
            raise ValueError("Replay source ref/hash 不一致。")
        if self.persisted_render_ref.sha256 != self.persisted_render_sha256:
            raise ValueError("Persisted render ref/hash 不一致。")
        if self.console_errors:
            raise ValueError("Actual Chromium execution 不得包含 console/page error。")
        if not self.pixel_comparison.passed:
            raise ValueError("Actual Chromium replay pixels 未闭合。")
        if self.pixel_comparison.profile != self.profile:
            raise ValueError("Execution receipt/pixel comparison profile 不一致。")
        if self.profile == "beauty_full_v1":
            if self.beauty_capture_index is None or self.diagnostic_pass_id is not None:
                raise ValueError("Beauty execution identity 不完整。")
            expected_source = ("compiled_glsl", "compiled_glsl_es_100_v1")
            expected_compilation = ("compilation_bundle", "compilation_bundle_v1")
            expected_render = ("render_png", "render_png_v2")
        elif self.diagnostic_pass_id is None or self.beauty_capture_index is not None:
            raise ValueError("Diagnostic execution identity 不完整。")
        else:
            expected_source = ("diagnostic_glsl", "diagnostic_glsl_es_100_v3")
            expected_compilation = (
                "diagnostic_compilation_bundle",
                "diagnostic_compilation_bundle_v3",
            )
            expected_render = ("diagnostic_render_png", "diagnostic_render_png_v3")
        expected_refs = (
            (
                self.candidate_ref,
                "candidate_record",
                "candidate_record_v3",
                "application/json",
            ),
            (
                self.render_plan_ref,
                "renderer_plan",
                "renderer_plan_v3",
                "application/json",
            ),
            (
                self.compilation_ref,
                *expected_compilation,
                "application/json",
            ),
            (
                self.source_ref,
                *expected_source,
                "text/x-glsl; charset=utf-8",
            ),
            (
                self.renderer_request_ref,
                "renderer_request_receipt",
                "renderer_request_receipt_v2",
                "application/json",
            ),
            (
                self.persisted_renderer_environment_ref,
                "renderer_environment",
                "renderer_environment_receipt_v3",
                "application/json",
            ),
            (self.persisted_render_ref, *expected_render, "image/png"),
        )
        if any(
            (ref.kind, ref.schema_version, ref.content_type)
            != (kind, schema_version, content_type)
            for ref, kind, schema_version, content_type in expected_refs
        ):
            raise ValueError("Actual execution receipt Artifact metadata 不符合冻结契约。")
        if self.record_hash != compute_actual_execution_item_hash(self):
            raise ValueError("Actual Chromium execution item hash 不一致。")
        return self


class V2_3ActualChromiumCandidateReceiptV1(FrozenModel):
    """精确覆盖一个 Candidate RenderPlan 的独立执行闭包。"""

    schema_version: Literal["v2_3_actual_chromium_candidate_receipt_v1"] = (
        ACTUAL_CHROMIUM_CANDIDATE_RECEIPT_SCHEMA_VERSION
    )
    hash_version: Literal["v2_3_actual_chromium_replay_hash_v1"] = (
        ACTUAL_CHROMIUM_REPLAY_HASH_VERSION
    )
    run_id: NonEmptyString
    candidate_id: NonEmptyString
    attempt_id: NonEmptyString
    candidate_ref: ArtifactRefV2
    render_plan_ref: ArtifactRefV2
    render_plan_hash: Sha256Hex
    renderer_protocol_hash: Sha256Hex
    actual_environment_hash: Sha256Hex
    item_receipts: tuple[V2_3ActualChromiumExecutionItemV1, ...] = Field(
        min_length=6
    )
    beauty_execution_count: Literal[5]
    diagnostic_execution_count: int = Field(ge=1)
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_candidate(self) -> V2_3ActualChromiumCandidateReceiptV1:
        if tuple(item.logical_request_ordinal for item in self.item_receipts) != tuple(
            range(1, len(self.item_receipts) + 1)
        ):
            raise ValueError("Candidate replay receipts 必须连续且完整覆盖 RenderPlan。")
        if any(
            (
                item.run_id,
                item.candidate_id,
                item.attempt_id,
                item.candidate_ref,
                item.render_plan_ref,
                item.render_plan_hash,
            )
            != (
                self.run_id,
                self.candidate_id,
                self.attempt_id,
                self.candidate_ref,
                self.render_plan_ref,
                self.render_plan_hash,
            )
            for item in self.item_receipts
        ):
            raise ValueError("Candidate/item replay identity 不一致。")
        beauties = tuple(
            item for item in self.item_receipts if item.profile == "beauty_full_v1"
        )
        diagnostics = tuple(
            item for item in self.item_receipts if item.profile != "beauty_full_v1"
        )
        if (
            len(beauties) != BEAUTY_CAPTURE_COUNT
            or tuple(item.beauty_capture_index for item in beauties)
            != tuple(range(BEAUTY_CAPTURE_COUNT))
            or self.beauty_execution_count != len(beauties)
            or self.diagnostic_execution_count != len(diagnostics)
        ):
            raise ValueError("Candidate replay 未覆盖 5 beauty + 全 diagnostics。")
        if any(
            item.actual_environment.renderer_protocol_hash
            != self.renderer_protocol_hash
            for item in self.item_receipts
        ):
            raise ValueError("Candidate replay renderer protocol 发生漂移。")
        if any(
            item.actual_environment.environment_hash != self.actual_environment_hash
            for item in self.item_receipts
        ):
            raise ValueError("Candidate replay Chromium/WebGL environment 发生漂移。")
        if self.record_hash != compute_actual_candidate_receipt_hash(self):
            raise ValueError("Actual Chromium Candidate receipt hash 不一致。")
        return self


@lru_cache(maxsize=1)
def compute_actual_renderer_protocol_hash() -> str:
    """绑定 concrete renderer implementation、host 与 canonical contract。"""
    try:
        implementation_source = inspect.getsource(concrete_renderer_module)
    except (OSError, TypeError) as exc:  # pragma: no cover - strict installed runtime
        raise V2_3ActualChromiumReplayError(
            "renderer_protocol_source_unavailable",
            "无法读取 concrete Chromium renderer implementation source。",
        ) from exc
    return canonical_sha256(
        {
            "renderer_version": RENDERER_VERSION,
            "renderer_implementation_source": implementation_source,
            "host_html": HOST_HTML,
            "vertex_shader": VERTEX_SHADER,
            "render_contract": WEBGL1_STATIC_NO_TEXTURE_V1.to_dict(),
        }
    )


def compute_actual_renderer_environment_hash(
    value: V2_3ActualRendererEnvironmentV1 | dict[str, Any],
) -> str:
    payload = (
        value.model_dump(mode="python", exclude={"environment_hash"})
        if isinstance(value, V2_3ActualRendererEnvironmentV1)
        else {key: item for key, item in value.items() if key != "environment_hash"}
    )
    return canonical_sha256(
        {"hash_version": ACTUAL_CHROMIUM_REPLAY_HASH_VERSION, "environment": payload}
    )


def _record_hash(value: FrozenModel | dict[str, Any], field: str) -> str:
    payload = (
        value.model_dump(mode="python", exclude={field})
        if isinstance(value, FrozenModel)
        else {key: item for key, item in value.items() if key != field}
    )
    return canonical_sha256(
        {"hash_version": ACTUAL_CHROMIUM_REPLAY_HASH_VERSION, "record": payload}
    )


def compute_actual_execution_item_hash(
    value: V2_3ActualChromiumExecutionItemV1 | dict[str, Any],
) -> str:
    normalized = (
        value
        if isinstance(value, V2_3ActualChromiumExecutionItemV1)
        else {
            "schema_version": ACTUAL_CHROMIUM_EXECUTION_ITEM_SCHEMA_VERSION,
            "hash_version": ACTUAL_CHROMIUM_REPLAY_HASH_VERSION,
            **value,
        }
    )
    return _record_hash(normalized, "record_hash")


def compute_actual_candidate_receipt_hash(
    value: V2_3ActualChromiumCandidateReceiptV1 | dict[str, Any],
) -> str:
    normalized = (
        value
        if isinstance(value, V2_3ActualChromiumCandidateReceiptV1)
        else {
            "schema_version": ACTUAL_CHROMIUM_CANDIDATE_RECEIPT_SCHEMA_VERSION,
            "hash_version": ACTUAL_CHROMIUM_REPLAY_HASH_VERSION,
            **value,
        }
    )
    return _record_hash(normalized, "record_hash")


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    if resolver.resolve(ref.artifact_id) != ref:
        raise V2_3ActualChromiumReplayError(
            "artifact_ref_identity_mismatch", "Replay Artifact resolver identity 不一致。"
        )
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes or sha256(data).hexdigest() != ref.sha256:
        raise V2_3ActualChromiumReplayError(
            "artifact_bytes_integrity_failed", "Replay Artifact bytes 完整性失败。"
        )
    return data


def _decode_rgba(data: bytes, *, width: int, height: int) -> np.ndarray:
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG":
                raise V2_3ActualChromiumReplayError(
                    "render_not_png", "Replay render 不是 PNG。"
                )
            image.load()
            if image.size != (width, height):
                raise V2_3ActualChromiumReplayError(
                    "render_size_mismatch", "Replay render 尺寸与 RenderPlan 不一致。"
                )
            return np.asarray(image.convert("RGBA"), dtype=np.uint8)
    except (UnidentifiedImageError, OSError) as exc:
        raise V2_3ActualChromiumReplayError(
            "render_png_decode_failed", "Replay render PNG 无法解码。"
        ) from exc


def compare_replayed_render_pixels(
    *,
    profile: Literal[
        "beauty_full_v1",
        "subject_visible_delta_full_v1",
        "instance_visible_delta_full_v1",
        "layer_visible_delta_lowres_v1",
    ],
    persisted_png: bytes,
    replay_png: bytes,
    width: int,
    height: int,
) -> V2_3ReplayPixelComparisonV1:
    """按冻结 beauty/delta 容差比较 decoded RGBA，而非 PNG encoder bytes。"""
    persisted = _decode_rgba(persisted_png, width=width, height=height)
    replay = _decode_rgba(replay_png, width=width, height=height)
    channel_delta = np.abs(persisted.astype(np.int16) - replay.astype(np.int16))
    rgb_mae = float(channel_delta[:, :, :3].mean() / 255.0)
    alpha_exact = bool(np.array_equal(persisted[:, :, 3], replay[:, :, 3]))
    payload: dict[str, Any] = {
        "profile": profile,
        "width": width,
        "height": height,
        "persisted_rgba_sha256": sha256(persisted.tobytes()).hexdigest(),
        "replay_rgba_sha256": sha256(replay.tobytes()).hexdigest(),
        "rgb_mae": rgb_mae,
        "max_rgba_channel_delta": int(channel_delta.max()),
        "alpha_exact": alpha_exact,
    }
    if profile == "beauty_full_v1":
        payload.update(
            {
                "persisted_diagnostic_mask_sha256": None,
                "replay_diagnostic_mask_sha256": None,
                "diagnostic_mask_exact": None,
                "passed": rgb_mae <= REPEATABILITY_RGB_MAE_LIMIT and alpha_exact,
            }
        )
    else:
        persisted_mask = project_visible_delta_mask_v3(persisted_png)
        replay_mask = project_visible_delta_mask_v3(replay_png)
        mask_exact = persisted_mask == replay_mask
        payload.update(
            {
                "persisted_diagnostic_mask_sha256": (
                    persisted_mask.canonical_bitmask_sha256
                ),
                "replay_diagnostic_mask_sha256": (
                    replay_mask.canonical_bitmask_sha256
                ),
                "diagnostic_mask_exact": mask_exact,
                "passed": int(channel_delta.max()) <= 1 and alpha_exact and mask_exact,
            }
        )
    return V2_3ReplayPixelComparisonV1.model_validate(payload, strict=True)


def _metadata_mapping(metadata: RendererMetadata) -> Mapping[str, Any]:
    raw = metadata.to_dict()
    if not isinstance(raw, Mapping):  # pragma: no cover - current dataclass guarantee
        raise V2_3ActualChromiumReplayError(
            "renderer_metadata_invalid", "Renderer metadata 不是 mapping。"
        )
    return raw


def build_actual_renderer_environment(
    *,
    metadata: RendererMetadata,
    persisted: RendererEnvironmentReceiptV3,
) -> V2_3ActualRendererEnvironmentV1:
    """只接受浏览器实测 context attributes；缺失时 fail closed。"""
    raw = _metadata_mapping(metadata)
    clear_raw = raw.get("canvas_clear_color_rgba")
    context_kind = raw.get("webgl_context_kind")
    if (
        not isinstance(clear_raw, (tuple, list))
        or len(clear_raw) != 4
        or context_kind != "webgl1"
    ):
        raise V2_3ActualChromiumReplayError(
            "renderer_context_metadata_unavailable",
            "Renderer 尚未暴露实测 WebGL1 context attributes/clear color。",
        )
    try:
        context = V2_3ActualWebGLContextV1.model_validate(
            {
                "context_kind": context_kind,
                "alpha": raw["canvas_alpha"],
                "antialias": raw["canvas_antialias"],
                "depth": raw["canvas_depth"],
                "stencil": raw["canvas_stencil"],
                "premultiplied_alpha": raw["premultiplied_alpha"],
                "preserve_drawing_buffer": raw["preserve_drawing_buffer"],
                "clear_color_rgba": tuple(clear_raw),
            },
            strict=True,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise V2_3ActualChromiumReplayError(
            "renderer_context_metadata_invalid",
            "Renderer WebGL1 context metadata 不符合冻结 schema。",
        ) from exc
    expected_alpha = persisted.canvas_alpha_mode == "preserve_transparent_alpha_v1"
    if (
        context.alpha != expected_alpha
        or context.alpha != persisted.canvas_alpha
        or context.antialias != persisted.canvas_antialias
        or context.depth != persisted.canvas_depth
        or context.stencil != persisted.canvas_stencil
        or context.premultiplied_alpha != persisted.premultiplied_alpha
        or context.preserve_drawing_buffer != persisted.preserve_drawing_buffer
        or context.clear_color_rgba != persisted.canvas_clear_color_rgba
        or metadata.renderer_version != persisted.renderer_version
        or metadata.browser_version != persisted.browser_version
        or metadata.gl_version != persisted.gl_version
        or metadata.glsl_version != persisted.glsl_version
        or metadata.gl_vendor != persisted.gl_vendor
        or metadata.gl_renderer != persisted.gl_renderer
    ):
        raise V2_3ActualChromiumReplayError(
            "renderer_environment_mismatch",
            "独立 replay 环境与 Candidate 冻结 Renderer environment 不一致。",
        )
    payload: dict[str, Any] = {
        "renderer_version": metadata.renderer_version,
        "renderer_protocol_hash": compute_actual_renderer_protocol_hash(),
        "browser_version": metadata.browser_version,
        "gl_version": metadata.gl_version,
        "glsl_version": metadata.glsl_version,
        "gl_vendor": metadata.gl_vendor,
        "gl_renderer": metadata.gl_renderer,
        "context": context,
        "environment_hash": "0" * 64,
    }
    payload["environment_hash"] = compute_actual_renderer_environment_hash(payload)
    return V2_3ActualRendererEnvironmentV1.model_validate(payload, strict=True)


def _load_persisted_environment(
    resolver: ArtifactResolver, ref: ArtifactRefV2
) -> RendererEnvironmentReceiptV3:
    expected_schema = str(
        RendererEnvironmentReceiptV3.model_fields["schema_version"].default
    )
    if (
        ref.kind != "renderer_environment"
        or ref.schema_version != expected_schema
        or ref.content_type != "application/json"
    ):
        raise V2_3ActualChromiumReplayError(
            "renderer_environment_ref_invalid",
            "Candidate Renderer environment ref metadata 不符合契约。",
        )
    try:
        return RendererEnvironmentReceiptV3.model_validate_json(
            _read_exact(resolver, ref), strict=True
        )
    except ValueError as exc:
        raise V2_3ActualChromiumReplayError(
            "renderer_environment_artifact_invalid",
            "Candidate Renderer environment Artifact 无法严格恢复。",
        ) from exc


class V2_3ActualChromiumReplayRunner:
    """内部密封 concrete Playwright Renderer 的异步 gate replay worker。"""

    def __init__(self) -> None:
        self._renderer: PlaywrightWebGL1Renderer | None = None

    async def __aenter__(self) -> V2_3ActualChromiumReplayRunner:
        if self._renderer is not None:
            raise RuntimeError("Actual Chromium replay runner 不得重复进入。")
        # 严格门禁不接受 factory/protocol 注入，防止 fixture 冒充 actual backend。
        renderer = PlaywrightWebGL1Renderer(contract=WEBGL1_STATIC_NO_TEXTURE_V1)
        await renderer.__aenter__()
        self._renderer = renderer
        return self

    async def __aexit__(self, *_: object) -> None:
        renderer = self._renderer
        if renderer is not None:
            await renderer.close()
            self._renderer = None

    async def replay_candidate(
        self,
        candidate_ref: ArtifactRefV2,
        *,
        resolver: ArtifactResolver,
        run_id: str,
    ) -> V2_3ActualChromiumCandidateReceiptV1:
        """strict-load Candidate 后逐项真实执行完整 RenderPlan。"""
        renderer = self._renderer
        if renderer is None:
            raise RuntimeError("Actual Chromium replay runner 尚未进入 async context。")
        bundle = load_typed_candidate_artifacts(
            candidate_ref, resolver=resolver, run_id=run_id
        )
        return await self._replay_loaded_candidate(
            bundle, candidate_ref=candidate_ref, resolver=resolver, renderer=renderer
        )

    async def _replay_loaded_candidate(
        self,
        bundle: TypedCandidateArtifactBundleV2,
        *,
        candidate_ref: ArtifactRefV2,
        resolver: ArtifactResolver,
        renderer: PlaywrightWebGL1Renderer,
    ) -> V2_3ActualChromiumCandidateReceiptV1:
        plan = bundle.render_plan
        successful = tuple(
            item for item in bundle.render_progress.outcomes if item.outcome == "success"
        )
        if len(successful) != len(plan.items):
            raise V2_3ActualChromiumReplayError(
                "render_plan_success_closure_incomplete",
                "Candidate progress 未完整覆盖 RenderPlan success 分母。",
            )
        item_receipts: list[V2_3ActualChromiumExecutionItemV1] = []
        for plan_item, outcome in zip(plan.items, successful, strict=True):
            if (
                outcome.render_ref is None
                or outcome.renderer_environment_ref is None
                or outcome.renderer_environment_hash is None
            ):
                raise V2_3ActualChromiumReplayError(
                    "render_success_refs_missing", "成功 Render outcome 缺少 typed refs。"
                )
            source_bytes = _read_exact(resolver, plan_item.source_ref)
            try:
                source = source_bytes.decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise V2_3ActualChromiumReplayError(
                    "glsl_utf8_invalid", "RenderPlan GLSL 不是严格 UTF-8。"
                ) from exc
            persisted_png = _read_exact(resolver, outcome.render_ref)
            persisted_environment = _load_persisted_environment(
                resolver, outcome.renderer_environment_ref
            )
            # 每个 logical item 都执行一次；5 个相同 beauty source 也禁止去重。
            result = await renderer.render(
                source, plan_item.width, plan_item.height
            )
            receipt = self._build_item_receipt(
                bundle=bundle,
                candidate_ref=candidate_ref,
                plan_item=plan_item,
                outcome=outcome,
                persisted_environment=persisted_environment,
                persisted_png=persisted_png,
                result=result,
            )
            item_receipts.append(receipt)
        payload: dict[str, Any] = {
            "run_id": bundle.candidate.run_id,
            "candidate_id": bundle.candidate.candidate_id,
            "attempt_id": plan.attempt_id,
            "candidate_ref": candidate_ref,
            "render_plan_ref": bundle.candidate.render_plan_ref,
            "render_plan_hash": plan.plan_hash,
            "renderer_protocol_hash": compute_actual_renderer_protocol_hash(),
            "actual_environment_hash": item_receipts[0].actual_environment.environment_hash,
            "item_receipts": tuple(item_receipts),
            "beauty_execution_count": BEAUTY_CAPTURE_COUNT,
            "diagnostic_execution_count": len(plan.items) - BEAUTY_CAPTURE_COUNT,
            "record_hash": "0" * 64,
        }
        payload["record_hash"] = compute_actual_candidate_receipt_hash(payload)
        return V2_3ActualChromiumCandidateReceiptV1.model_validate(
            payload, strict=True
        )

    def _build_item_receipt(
        self,
        *,
        bundle: TypedCandidateArtifactBundleV2,
        candidate_ref: ArtifactRefV2,
        plan_item: Any,
        outcome: Any,
        persisted_environment: RendererEnvironmentReceiptV3,
        persisted_png: bytes,
        result: RenderResult,
    ) -> V2_3ActualChromiumExecutionItemV1:
        if persisted_environment.environment_hash != outcome.renderer_environment_hash:
            raise V2_3ActualChromiumReplayError(
                "persisted_renderer_environment_hash_mismatch",
                "Render outcome 与 persisted Renderer environment hash 不一致。",
            )
        if (
            not result.success
            or not result.compile.success
            or not result.compile.static_validation.valid
            or result.compile.draw_error is not None
            or result.image_bytes is None
            or result.metadata is None
            or (result.width, result.height) != (plan_item.width, plan_item.height)
        ):
            raise V2_3ActualChromiumReplayError(
                "actual_chromium_execution_failed",
                "独立 Chromium compile/link/draw/readback 未完整成功。",
            )
        if result.console_errors:
            raise V2_3ActualChromiumReplayError(
                "actual_chromium_console_error",
                "独立 Chromium execution 产生 console/page error。",
            )
        actual_environment = build_actual_renderer_environment(
            metadata=result.metadata, persisted=persisted_environment
        )
        comparison = compare_replayed_render_pixels(
            profile=plan_item.profile,
            persisted_png=persisted_png,
            replay_png=result.image_bytes,
            width=plan_item.width,
            height=plan_item.height,
        )
        if not comparison.passed:
            raise V2_3ActualChromiumReplayError(
                "actual_chromium_pixels_mismatch",
                "独立 Chromium pixels 与 Candidate persisted render 不一致。",
            )
        assert outcome.render_ref is not None
        assert outcome.renderer_environment_ref is not None
        assert outcome.renderer_environment_hash is not None
        payload: dict[str, Any] = {
            "run_id": bundle.candidate.run_id,
            "candidate_id": bundle.candidate.candidate_id,
            "attempt_id": bundle.render_plan.attempt_id,
            "candidate_ref": candidate_ref,
            "render_plan_ref": bundle.candidate.render_plan_ref,
            "render_plan_hash": bundle.render_plan.plan_hash,
            "logical_request_ordinal": plan_item.logical_request_ordinal,
            "profile": plan_item.profile,
            "beauty_capture_index": plan_item.beauty_capture_index,
            "diagnostic_pass_id": plan_item.diagnostic_pass_id,
            "compilation_ref": plan_item.compilation_ref,
            "source_ref": plan_item.source_ref,
            "source_sha256": plan_item.source_ref.sha256,
            "renderer_request_ref": outcome.renderer_request_ref,
            "renderer_request_hash": outcome.renderer_request_hash,
            "persisted_renderer_environment_ref": outcome.renderer_environment_ref,
            "persisted_renderer_environment_hash": outcome.renderer_environment_hash,
            "persisted_render_ref": outcome.render_ref,
            "persisted_render_sha256": outcome.render_ref.sha256,
            "actual_environment": actual_environment,
            # Concrete renderer 的 success 语义由绑定进 protocol hash 的实现冻结：
            # success 仅在 vertex/fragment compile、link、draw 和 PNG readback 后成立。
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
        return V2_3ActualChromiumExecutionItemV1.model_validate(payload, strict=True)


__all__ = [
    "ACTUAL_CHROMIUM_CANDIDATE_RECEIPT_SCHEMA_VERSION",
    "ACTUAL_CHROMIUM_EXECUTION_ITEM_SCHEMA_VERSION",
    "ACTUAL_CHROMIUM_REPLAY_HASH_VERSION",
    "V2_3ActualChromiumCandidateReceiptV1",
    "V2_3ActualChromiumExecutionItemV1",
    "V2_3ActualChromiumReplayError",
    "V2_3ActualChromiumReplayRunner",
    "V2_3ActualRendererEnvironmentV1",
    "V2_3ActualWebGLContextV1",
    "V2_3ReplayPixelComparisonV1",
    "build_actual_renderer_environment",
    "compare_replayed_render_pixels",
    "compute_actual_candidate_receipt_hash",
    "compute_actual_execution_item_hash",
    "compute_actual_renderer_environment_hash",
    "compute_actual_renderer_protocol_hash",
]
