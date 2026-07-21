"""V2.4 多 capture/diagnostic Renderer plan、进度与复现性证据。"""
# ruff: noqa: D101, D102, D103, D401, D415

from __future__ import annotations

import json
from hashlib import sha256
from io import BytesIO
from itertools import combinations
from typing import Any, Literal, TypeVar

import numpy as np
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, model_validator

from shaderforge.compiler.models import DIAGNOSTIC_OWNERSHIP_POLICY_VERSION
from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

RENDER_PLAN_ARTIFACT_KIND = "renderer_plan"
RENDER_PLAN_SCHEMA_VERSION = "renderer_plan_v3"
RENDER_PROGRESS_ARTIFACT_KIND = "renderer_progress"
RENDER_PROGRESS_SCHEMA_VERSION = "renderer_progress_v2"
RENDER_REPEATABILITY_ARTIFACT_KIND = "render_repeatability_evidence"
RENDER_REPEATABILITY_SCHEMA_VERSION = "render_repeatability_evidence_v2"
RENDERER_ENVIRONMENT_ARTIFACT_KIND = "renderer_environment"
RENDERER_ENVIRONMENT_SCHEMA_VERSION = "renderer_environment_receipt_v3"
BEAUTY_CAPTURE_COUNT = 5
REPEATABILITY_RGB_MAE_LIMIT = 1.0 / 255.0
_JSON_CONTENT_TYPE = "application/json"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


class RenderPlanItemV2(FrozenModel):
    """一个稳定排序的逻辑 Renderer request。"""

    logical_request_ordinal: int = Field(ge=1)
    profile: Literal[
        "beauty_full_v1",
        "subject_visible_delta_full_v1",
        "instance_visible_delta_full_v1",
        "layer_visible_delta_lowres_v1",
    ]
    compilation_ref: ArtifactRefV2
    source_ref: ArtifactRefV2
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    beauty_capture_index: int | None = Field(default=None, ge=0, le=4)
    diagnostic_pass_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def _validate_role(self) -> RenderPlanItemV2:
        if self.profile == "beauty_full_v1":
            if self.beauty_capture_index is None or self.diagnostic_pass_id is not None:
                raise ValueError("Beauty plan item 必须且只能绑定 capture index。")
        elif self.diagnostic_pass_id is None or self.beauty_capture_index is not None:
            raise ValueError("Diagnostic plan item 必须且只能绑定 pass id。")
        return self


class RenderPlanV2(FrozenModel):
    """固定五次 beauty 后接全部 diagnostic 的 immutable plan。"""

    schema_version: Literal["renderer_plan_v3"] = "renderer_plan_v3"
    hash_version: Literal["renderer_plan_hash_v3"] = "renderer_plan_hash_v3"
    run_id: NonEmptyString
    attempt_id: NonEmptyString
    target_hypothesis_hash: Sha256Hex
    semantic_genome_hash: Sha256Hex
    budget_policy_hash: Sha256Hex
    ownership_policy_version: Literal[
        "stable_instance_ordinal_first_match_v1"
    ]
    items: tuple[RenderPlanItemV2, ...] = Field(min_length=6)
    plan_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_plan(self) -> RenderPlanV2:
        if self.ownership_policy_version != DIAGNOSTIC_OWNERSHIP_POLICY_VERSION:
            raise ValueError("Render plan ownership policy 不受支持。")
        if tuple(item.logical_request_ordinal for item in self.items) != tuple(
            range(1, len(self.items) + 1)
        ):
            raise ValueError("Render plan logical ordinals 必须是从 1 开始的连续序列。")
        beauties = self.items[:BEAUTY_CAPTURE_COUNT]
        if tuple(item.profile for item in beauties) != (
            "beauty_full_v1",
        ) * BEAUTY_CAPTURE_COUNT or tuple(
            item.beauty_capture_index for item in beauties
        ) != tuple(range(BEAUTY_CAPTURE_COUNT)):
            raise ValueError("Render plan 前五项必须是 capture 0..4 的 beauty。")
        diagnostics = self.items[BEAUTY_CAPTURE_COUNT:]
        diagnostic_ids = [
            item.diagnostic_pass_id
            for item in diagnostics
            if item.diagnostic_pass_id is not None
        ]
        if any(item.profile == "beauty_full_v1" for item in diagnostics):
            raise ValueError("五次 beauty 后不得再出现 beauty item。")
        if diagnostic_ids != sorted(set(diagnostic_ids)):
            raise ValueError("Diagnostic plan items 必须按 pass id 唯一排序。")
        if self.plan_hash != compute_render_plan_hash(self):
            raise ValueError("Render plan hash 不一致。")
        return self


class RenderCallOutcomeV2(FrozenModel):
    """一次 physical Renderer call 的持久结果。"""

    logical_request_ordinal: int = Field(ge=1)
    physical_call_ordinal: int = Field(ge=1, le=2)
    renderer_request_ref: ArtifactRefV2
    renderer_request_artifact_sha256: Sha256Hex
    renderer_request_hash: Sha256Hex
    outcome: Literal["success", "transient_failure", "failure", "unknown"]
    error_code: NonEmptyString | None = None
    renderer_environment_ref: ArtifactRefV2 | None = None
    renderer_environment_artifact_sha256: Sha256Hex | None = None
    renderer_environment_hash: Sha256Hex | None = None
    render_ref: ArtifactRefV2 | None = None
    render_sha256: Sha256Hex | None = None
    attempt_evidence_ref: ArtifactRefV2
    budget_revision_reserved: int = Field(ge=0)
    budget_revision_committed: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_outcome(self) -> RenderCallOutcomeV2:
        if self.renderer_request_ref.sha256 != self.renderer_request_artifact_sha256:
            raise ValueError("Render call request Artifact SHA/ref 不一致。")
        success_refs = (
            self.renderer_environment_ref,
            self.renderer_environment_artifact_sha256,
            self.renderer_environment_hash,
            self.render_ref,
            self.render_sha256,
        )
        if self.outcome == "success":
            if self.error_code is not None or any(item is None for item in success_refs):
                raise ValueError("成功 Render call 必须有完整 render/environment 且无错误。")
            assert self.render_ref is not None and self.render_sha256 is not None
            assert self.renderer_environment_ref is not None
            assert self.renderer_environment_artifact_sha256 is not None
            if self.render_ref.sha256 != self.render_sha256:
                raise ValueError("Render call PNG SHA/ref 不一致。")
            if (
                self.renderer_environment_ref.sha256
                != self.renderer_environment_artifact_sha256
            ):
                raise ValueError("Render call environment Artifact SHA/ref 不一致。")
        elif self.error_code is None or any(item is not None for item in success_refs):
            raise ValueError("非成功 Render call 必须有错误且不得伪造结果 refs。")
        if (
            self.budget_revision_committed is not None
            and self.budget_revision_committed <= self.budget_revision_reserved
        ):
            raise ValueError("Render call budget commit revision 必须后于 reservation。")
        return self


class RenderProgressV2(FrozenModel):
    """append-only logical request progress；next cursor 只能由 outcomes 推导。"""

    schema_version: Literal["renderer_progress_v2"] = "renderer_progress_v2"
    hash_version: Literal["renderer_progress_hash_v2"] = "renderer_progress_hash_v2"
    run_id: NonEmptyString
    attempt_id: NonEmptyString
    plan_ref: ArtifactRefV2
    plan_hash: Sha256Hex
    budget_policy_hash: Sha256Hex
    outcomes: tuple[RenderCallOutcomeV2, ...] = ()
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_progress(self) -> RenderProgressV2:
        expected_logical = 1
        expected_physical = 1
        environment_ref: ArtifactRefV2 | None = None
        previous_budget_revision = -1
        for index, outcome in enumerate(self.outcomes):
            if (
                outcome.budget_revision_committed is None
                and index != len(self.outcomes) - 1
            ):
                raise ValueError("只有最后一个 Render outcome 可以等待预算结算。")
            if (
                outcome.logical_request_ordinal != expected_logical
                or outcome.physical_call_ordinal != expected_physical
            ):
                raise ValueError("Render progress 不是 logical/physical call 的严格前缀。")
            if outcome.budget_revision_reserved <= previous_budget_revision:
                raise ValueError("Render progress 的预算 reservation revision 必须严格递增。")
            previous_budget_revision = outcome.budget_revision_reserved
            if outcome.budget_revision_committed is not None:
                if outcome.budget_revision_committed <= previous_budget_revision:
                    raise ValueError("Render progress 的预算 commit revision 必须后于 reservation。")
                previous_budget_revision = outcome.budget_revision_committed
            if outcome.outcome == "success":
                if environment_ref is None:
                    environment_ref = outcome.renderer_environment_ref
                elif outcome.renderer_environment_ref != environment_ref:
                    raise ValueError("同一 plan 的 Renderer environment 发生漂移。")
                expected_logical += 1
                expected_physical = 1
            else:
                if expected_physical == 2 or outcome.outcome == "failure":
                    # terminal failure 可以被保留用于失败 closure，但不能再追加下一 request。
                    expected_physical = 3
                else:
                    expected_physical = 2
        if expected_physical == 3 and self.outcomes:
            terminal = self.outcomes[-1]
            if terminal.logical_request_ordinal != expected_logical:
                raise ValueError("Terminal render failure 后不得继续执行。")
        if self.record_hash != compute_render_progress_hash(self):
            raise ValueError("Render progress hash 不一致。")
        return self

    @property
    def completed_logical_requests(self) -> int:
        return sum(
            item.outcome == "success"
            and item.budget_revision_committed is not None
            for item in self.outcomes
        )

    @property
    def next_logical_request_ordinal(self) -> int:
        return self.completed_logical_requests + 1

    @property
    def has_uncommitted_outcome(self) -> bool:
        return bool(
            self.outcomes
            and self.outcomes[-1].budget_revision_committed is None
        )

    @property
    def next_physical_call_ordinal(self) -> int:
        """返回当前 logical request 的下一个物理 call ordinal。"""
        if not self.outcomes:
            return 1
        latest = self.outcomes[-1]
        if latest.budget_revision_committed is None:
            raise ValueError("未结算 outcome 不能推导下一次 physical call。")
        if latest.outcome == "success":
            return 1
        if latest.outcome in {"transient_failure", "unknown"}:
            if latest.physical_call_ordinal >= 2:
                raise ValueError("当前 logical request 已耗尽两次物理调用。")
            return latest.physical_call_ordinal + 1
        raise ValueError("永久 Renderer failure 不允许重放。")


class BeautyPairRepeatabilityV2(FrozenModel):
    left_capture_index: int = Field(ge=0, le=4)
    right_capture_index: int = Field(ge=0, le=4)
    rgb_mae: float = Field(ge=0.0, le=1.0)
    passed: bool

    @model_validator(mode="after")
    def _validate_pair(self) -> BeautyPairRepeatabilityV2:
        if self.left_capture_index >= self.right_capture_index:
            raise ValueError("Repeatability pair index 必须递增。")
        if self.passed != (self.rgb_mae <= REPEATABILITY_RGB_MAE_LIMIT):
            raise ValueError("Repeatability pair pass 与冻结阈值不一致。")
        return self


class RenderRepeatabilityEvidenceV2(FrozenModel):
    """五张实际 beauty PNG 的全对全 RGB MAE 证据。"""

    schema_version: Literal["render_repeatability_evidence_v2"] = (
        "render_repeatability_evidence_v2"
    )
    hash_version: Literal["render_repeatability_hash_v2"] = (
        "render_repeatability_hash_v2"
    )
    run_id: NonEmptyString
    attempt_id: NonEmptyString
    capture_request_refs: tuple[ArtifactRefV2, ...] = Field(
        min_length=BEAUTY_CAPTURE_COUNT, max_length=BEAUTY_CAPTURE_COUNT
    )
    capture_render_refs: tuple[ArtifactRefV2, ...] = Field(
        min_length=BEAUTY_CAPTURE_COUNT, max_length=BEAUTY_CAPTURE_COUNT
    )
    renderer_environment_ref: ArtifactRefV2
    pairs: tuple[BeautyPairRepeatabilityV2, ...] = Field(min_length=10, max_length=10)
    passed: bool
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_evidence(self) -> RenderRepeatabilityEvidenceV2:
        request_ids = [item.artifact_id for item in self.capture_request_refs]
        if len(set(request_ids)) != BEAUTY_CAPTURE_COUNT:
            raise ValueError("五次 beauty capture 必须绑定五个唯一 logical request。")
        expected_pairs = tuple(combinations(range(BEAUTY_CAPTURE_COUNT), 2))
        actual_pairs = tuple(
            (item.left_capture_index, item.right_capture_index) for item in self.pairs
        )
        if actual_pairs != expected_pairs:
            raise ValueError("Repeatability evidence 必须完整覆盖五次 capture 的十组 pair。")
        if self.passed != all(item.passed for item in self.pairs):
            raise ValueError("Repeatability aggregate pass 与 pair 结果不一致。")
        if self.record_hash != compute_repeatability_hash(self):
            raise ValueError("Repeatability evidence hash 不一致。")
        return self


def compute_render_plan_hash(value: RenderPlanV2 | dict[str, Any]) -> str:
    return _record_hash(value, "renderer_plan_hash_v3", "plan_hash")


def compute_render_progress_hash(value: RenderProgressV2 | dict[str, Any]) -> str:
    return _record_hash(value, "renderer_progress_hash_v2", "record_hash")


def compute_repeatability_hash(
    value: RenderRepeatabilityEvidenceV2 | dict[str, Any],
) -> str:
    return _record_hash(value, "render_repeatability_hash_v2", "record_hash")


def _record_hash(value: FrozenModel | dict[str, Any], version: str, field: str) -> str:
    payload = (
        value.model_dump(mode="python", exclude={field})
        if isinstance(value, FrozenModel)
        else {key: item for key, item in value.items() if key != field}
    )
    return canonical_sha256({"hash_version": version, "record": payload})


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    if resolver.resolve(ref.artifact_id) != ref:
        raise ValueError("Render runtime Artifact ref identity 不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes or sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Render runtime Artifact bytes 完整性失败。")
    return data


def _strict_model(data: bytes, model: type[_ModelT]) -> _ModelT:
    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Render runtime JSON 包含重复 key：{key}。")
            result[key] = value
        return result

    json.loads(
        data,
        object_pairs_hook=reject_duplicates,
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"Render runtime JSON 包含非法常量：{value}。")
        ),
    )
    return model.model_validate_json(data, strict=True)


def materialize_render_model(
    *, catalog: ArtifactCatalog, run_id: str, value: FrozenModel
) -> ArtifactRefV2:
    contract = {
        RenderPlanV2: (RENDER_PLAN_ARTIFACT_KIND, RENDER_PLAN_SCHEMA_VERSION),
        RenderProgressV2: (
            RENDER_PROGRESS_ARTIFACT_KIND,
            RENDER_PROGRESS_SCHEMA_VERSION,
        ),
        RenderRepeatabilityEvidenceV2: (
            RENDER_REPEATABILITY_ARTIFACT_KIND,
            RENDER_REPEATABILITY_SCHEMA_VERSION,
        ),
    }
    try:
        kind, schema = contract[type(value)]
    except KeyError as exc:
        raise TypeError("不支持的 Render runtime Artifact model。") from exc
    if getattr(value, "run_id") != run_id:
        raise ValueError("Render runtime Artifact run_id 不一致。")
    return catalog.put(
        run_id=run_id,
        kind=kind,
        schema_version=schema,
        content_type=_JSON_CONTENT_TYPE,
        # canonical_sha256 只用于内部 record hash；Artifact payload 必须保持
        # schema 可严格恢复的 JSON number，不能把 binary64 投影成 hex string。
        data=value.model_dump_json().encode("utf-8"),
    )


def load_render_model(
    ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
    run_id: str,
) -> RenderPlanV2 | RenderProgressV2 | RenderRepeatabilityEvidenceV2:
    contract: dict[tuple[str, str], type[BaseModel]] = {
        (RENDER_PLAN_ARTIFACT_KIND, RENDER_PLAN_SCHEMA_VERSION): RenderPlanV2,
        (
            RENDER_PROGRESS_ARTIFACT_KIND,
            RENDER_PROGRESS_SCHEMA_VERSION,
        ): RenderProgressV2,
        (
            RENDER_REPEATABILITY_ARTIFACT_KIND,
            RENDER_REPEATABILITY_SCHEMA_VERSION,
        ): RenderRepeatabilityEvidenceV2,
    }
    if ref.content_type != _JSON_CONTENT_TYPE:
        raise ValueError("Render runtime Artifact content type 不符合契约。")
    model = contract.get((ref.kind, ref.schema_version))
    if model is None:
        raise ValueError("Render runtime Artifact kind/schema 不受支持。")
    loaded = _strict_model(_read_exact(resolver, ref), model)
    if getattr(loaded, "run_id") != run_id:
        raise ValueError("Render runtime Artifact 不属于当前 run。")
    assert isinstance(
        loaded, (RenderPlanV2, RenderProgressV2, RenderRepeatabilityEvidenceV2)
    )
    return loaded


def _rgb_array(data: bytes) -> np.ndarray:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return np.asarray(image.convert("RGB"), dtype=np.float64) / 255.0
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Repeatability capture 不是有效 PNG。") from exc


def build_repeatability_evidence(
    *,
    run_id: str,
    attempt_id: str,
    capture_request_refs: tuple[ArtifactRefV2, ...],
    capture_render_refs: tuple[ArtifactRefV2, ...],
    renderer_environment_ref: ArtifactRefV2,
    resolver: ArtifactResolver,
) -> RenderRepeatabilityEvidenceV2:
    if len(capture_request_refs) != BEAUTY_CAPTURE_COUNT or len(
        capture_render_refs
    ) != BEAUTY_CAPTURE_COUNT:
        raise ValueError("Repeatability 必须恰好接收五次 beauty capture。")
    arrays = tuple(_rgb_array(_read_exact(resolver, ref)) for ref in capture_render_refs)
    if len({array.shape for array in arrays}) != 1:
        raise ValueError("Repeatability beauty capture 尺寸不一致。")
    pairs = tuple(
        BeautyPairRepeatabilityV2(
            left_capture_index=left,
            right_capture_index=right,
            rgb_mae=float(np.mean(np.abs(arrays[left] - arrays[right]))),
            passed=float(np.mean(np.abs(arrays[left] - arrays[right])))
            <= REPEATABILITY_RGB_MAE_LIMIT,
        )
        for left, right in combinations(range(BEAUTY_CAPTURE_COUNT), 2)
    )
    raw: dict[str, Any] = {
        "schema_version": "render_repeatability_evidence_v2",
        "hash_version": "render_repeatability_hash_v2",
        "run_id": run_id,
        "attempt_id": attempt_id,
        "capture_request_refs": capture_request_refs,
        "capture_render_refs": capture_render_refs,
        "renderer_environment_ref": renderer_environment_ref,
        "pairs": pairs,
        "passed": all(item.passed for item in pairs),
        "record_hash": "0" * 64,
    }
    raw["record_hash"] = compute_repeatability_hash(raw)
    return RenderRepeatabilityEvidenceV2.model_validate(raw, strict=True)


__all__ = [
    "BEAUTY_CAPTURE_COUNT",
    "REPEATABILITY_RGB_MAE_LIMIT",
    "BeautyPairRepeatabilityV2",
    "RenderCallOutcomeV2",
    "RenderPlanItemV2",
    "RenderPlanV2",
    "RenderProgressV2",
    "RenderRepeatabilityEvidenceV2",
    "build_repeatability_evidence",
    "compute_render_plan_hash",
    "compute_render_progress_hash",
    "compute_repeatability_hash",
    "load_render_model",
    "materialize_render_model",
]
