"""LayerPlan/direct GLSL shadow suite 的冻结 manifest/gate 契约（D086）.

本模块只负责看结果前的协议冻结与 fail-closed 加载，不执行真实模型调度、
跨 run 聚合、人工盲评或 evidence registry 写入。冻结链为：

1. manifest 绑定参考图、instruction、预算与 AB/BA 调度；
2. gate 绑定 manifest 文件 SHA-256 与两种臂序的 ``ShadowABConfig`` 指纹；
3. gate 文件 SHA-256 由加载器返回，供后续 suite 报告锚定。
"""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from math import isfinite
from pathlib import Path
from statistics import median
from types import MappingProxyType
from typing import Any, Literal, cast
from uuid import uuid4

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from agent.app.contracts.llm import LLMGateway
from agent.app.services.layerplan_glsl_shadow import (
    ARM_A,
    ARM_B,
    REPORT_SCHEMA_VERSION,
    SHADOW_EXPERIMENT_ID,
    ArmId,
    LayerPlanGlslShadowRunner,
    ShadowABConfig,
    ShadowABConfigError,
    ShadowRenderer,
    shadow_run_id,
    verify_shadow_run,
    write_shadow_run,
)
from shaderforge.evaluation import MIN_SCENE_METRIC_VERSION
from shaderforge.program_spec import canonical_json

MANIFEST_SCHEMA_VERSION = "layerplan_glsl_shadow_manifest_v1"
GATE_SCHEMA_VERSION = "layerplan_glsl_shadow_gate_v1"
OrderLabel = Literal["AB", "BA"]

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_SAMPLE_ID_PATTERN = re.compile(r"^[a-z0-9_]{1,64}$")
_ORDER_BY_LABEL: dict[OrderLabel, tuple[ArmId, ArmId]] = {
    "AB": (ARM_A, ARM_B),
    "BA": (ARM_B, ARM_A),
}


class ShadowSuiteContractError(ValueError):
    """冻结 manifest/gate 违反契约或哈希绑定的 fail-closed 错误."""


class ShadowSharedArmConfig(BaseModel):
    """两臂共享的预算与画布配置；臂序由逐轮 schedule 给出."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    direct_author_llm_budget: int = Field(ge=0)
    compile_budget_per_arm: int = Field(ge=0)
    draw_budget_per_arm: int = Field(ge=0)
    refine_budget_per_arm: int = Field(ge=0)
    plan_llm_budget: int = Field(ge=0)
    canvas_width: int | None = None
    canvas_height: int | None = None


class _ManifestSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    sample_id: str = Field(min_length=1, max_length=64)
    reference_path: str = Field(min_length=1, max_length=200)
    reference_sha256: str = Field(pattern=_SHA256_PATTERN)
    reference_content_type: Literal["image/png"]
    instruction: str = Field(max_length=2_000)
    instruction_sha256: str = Field(pattern=_SHA256_PATTERN)


class _ManifestRoot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["layerplan_glsl_shadow_manifest_v1"]
    experiment_id: str = Field(min_length=1, max_length=100)
    run_classification: Literal["independent_experiment"]
    report_schema_version: str = Field(min_length=1, max_length=100)
    frozen_at: date
    rounds: int = Field(ge=2, le=64)
    arm_order_schedule: list[OrderLabel] = Field(min_length=2, max_length=64)
    config: ShadowSharedArmConfig
    samples: list[_ManifestSample] = Field(min_length=1, max_length=64)


class _GatePrimaryEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    metric: Literal["current_best_loss"]
    comparison: Literal["paired_per_sample_per_round"]
    improvement_margin: float = Field(gt=0.0, le=1.0)
    min_improved_sample_ratio: float = Field(gt=0.0, le=1.0)


class _GateOrderEffect(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    rule: Literal["consistent_direction_required"]


class _GateInconclusivePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    counting: Literal["inconclusive_counts_against_arm_b"]
    max_inconclusive_sample_ratio: float = Field(ge=0.0, lt=1.0)


class _GateHumanReview(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    required: Literal[True]
    min_arm_b_preference_rate: float = Field(gt=0.0, le=1.0)
    tie_policy: Literal["ties_not_counted_as_b_win"]


class _GateRoot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["layerplan_glsl_shadow_gate_v1"]
    experiment_id: str = Field(min_length=1, max_length=100)
    run_classification: Literal["independent_experiment"]
    report_schema_version: str = Field(min_length=1, max_length=100)
    metric_version: str = Field(min_length=1, max_length=100)
    frozen_at: date
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    config_fingerprints: dict[str, str]
    primary_endpoint: _GatePrimaryEndpoint
    order_effect: _GateOrderEffect
    inconclusive_policy: _GateInconclusivePolicy
    human_review: _GateHumanReview
    durability_requirement: Literal["durable_required_for_promotion"]

    @model_validator(mode="after")
    def validate_config_fingerprints(self) -> _GateRoot:
        """两种臂序必须且只能各有一个合法指纹."""
        if set(self.config_fingerprints) != {"AB", "BA"}:
            raise ValueError("config_fingerprints 必须且只能包含 AB、BA。")
        for fingerprint in self.config_fingerprints.values():
            if re.fullmatch(_SHA256_PATTERN, fingerprint) is None:
                raise ValueError("config_fingerprints 必须是小写 SHA-256。")
        return self


@dataclass(frozen=True)
class ShadowSuiteSample:
    """已校验的单个冻结样本."""

    sample_id: str
    reference_path: str
    reference_sha256: str
    reference_content_type: str
    instruction: str
    instruction_sha256: str


@dataclass(frozen=True)
class ShadowSuiteManifest:
    """已校验 manifest；round_index 对外统一使用从 1 开始的编号."""

    path: Path
    manifest_sha256: str
    experiment_id: str
    report_schema_version: str
    frozen_at: date
    rounds: int
    arm_order_schedule: tuple[tuple[ArmId, ArmId], ...]
    shared_config: ShadowSharedArmConfig
    samples: tuple[ShadowSuiteSample, ...]

    def arm_order(self, round_index: int) -> tuple[ArmId, ArmId]:
        """返回指定轮次的冻结臂序."""
        if isinstance(round_index, bool) or not 1 <= round_index <= self.rounds:
            raise ShadowSuiteContractError(
                f"round_index 必须在 1..{self.rounds}：{round_index}"
            )
        return self.arm_order_schedule[round_index - 1]

    def arm_config(self, round_index: int) -> ShadowABConfig:
        """为指定轮次构造并复验完整 ``ShadowABConfig``."""
        return self._config_for_order(self.arm_order(round_index))

    def config_fingerprint_for_order(self, label: OrderLabel) -> str:
        """返回 AB 或 BA 臂序下的完整配置指纹."""
        return self._config_for_order(_ORDER_BY_LABEL[label]).fingerprint()

    def _config_for_order(
        self, arm_order: tuple[ArmId, ArmId]
    ) -> ShadowABConfig:
        return ShadowABConfig(
            direct_author_llm_budget=self.shared_config.direct_author_llm_budget,
            compile_budget_per_arm=self.shared_config.compile_budget_per_arm,
            draw_budget_per_arm=self.shared_config.draw_budget_per_arm,
            refine_budget_per_arm=self.shared_config.refine_budget_per_arm,
            plan_llm_budget=self.shared_config.plan_llm_budget,
            arm_order=arm_order,
            canvas_width=self.shared_config.canvas_width,
            canvas_height=self.shared_config.canvas_height,
        )


@dataclass(frozen=True)
class ShadowSuiteGate:
    """已校验的预声明 gate；聚合逻辑在后续小步实现."""

    path: Path
    gate_sha256: str
    manifest_sha256: str
    experiment_id: str
    report_schema_version: str
    metric_version: str
    frozen_at: date
    config_fingerprints: MappingProxyType[str, str]
    improvement_margin: float
    min_improved_sample_ratio: float
    max_inconclusive_sample_ratio: float
    min_arm_b_preference_rate: float


@dataclass(frozen=True)
class VerifiedSuiteRun:
    """单个已复验 shadow run 的 suite 级配对摘要."""

    sample_id: str
    round_index: int
    order_label: OrderLabel
    run_id: str
    report_sha256: str
    status: str
    arm_a_status: str
    arm_b_status: str
    arm_a_loss: float | None
    arm_b_loss: float | None

    @property
    def delta_b_minus_a(self) -> float | None:
        """返回 B-A；负值表示 LayerPlan 臂更好."""
        if self.arm_a_loss is None or self.arm_b_loss is None:
            return None
        return self.arm_b_loss - self.arm_a_loss

    def to_dict(self) -> dict[str, Any]:
        """返回 suite 报告中的稳定摘要."""
        return {
            "sample_id": self.sample_id,
            "round_index": self.round_index,
            "order_label": self.order_label,
            "run_id": self.run_id,
            "report_sha256": self.report_sha256,
            "status": self.status,
            "arms": {
                "A": {"status": self.arm_a_status, "loss": self.arm_a_loss},
                "B": {"status": self.arm_b_status, "loss": self.arm_b_loss},
            },
            "delta_b_minus_a": self.delta_b_minus_a,
        }


def _load_yaml_mapping(path: Path, *, kind: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ShadowSuiteContractError(f"{kind} 不存在、不是文件或是 symlink：{path}")
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ShadowSuiteContractError(f"{kind} 无法安全读取：{path}") from exc
    if not isinstance(payload, dict):
        raise ShadowSuiteContractError(f"{kind} 顶层必须是 mapping：{path}")
    return cast(dict[str, Any], payload)


def load_shadow_suite_manifest(path: Path | str) -> ShadowSuiteManifest:
    """严格加载冻结 manifest；不在此函数中隐式读取图片内容."""
    manifest_path = Path(path)
    payload = _load_yaml_mapping(manifest_path, kind="manifest")
    try:
        root = _ManifestRoot.model_validate(payload)
    except ValidationError as exc:
        raise ShadowSuiteContractError(
            f"manifest 违反冻结契约：{manifest_path}"
        ) from exc

    if root.experiment_id != SHADOW_EXPERIMENT_ID:
        raise ShadowSuiteContractError("manifest experiment_id 与 runner 不一致。")
    if root.report_schema_version != REPORT_SCHEMA_VERSION:
        raise ShadowSuiteContractError(
            "manifest report_schema_version 与 runner 不一致。"
        )
    if len(root.arm_order_schedule) != root.rounds:
        raise ShadowSuiteContractError("rounds 必须等于 arm_order_schedule 长度。")
    if set(root.arm_order_schedule) != {"AB", "BA"}:
        raise ShadowSuiteContractError("调度必须同时包含 AB 与 BA 以交叉平衡。")

    sample_ids: set[str] = set()
    samples: list[ShadowSuiteSample] = []
    for sample in root.samples:
        if _SAMPLE_ID_PATTERN.fullmatch(sample.sample_id) is None:
            raise ShadowSuiteContractError(
                f"sample_id 不符合冻结格式：{sample.sample_id}"
            )
        if sample.sample_id in sample_ids:
            raise ShadowSuiteContractError(f"sample_id 重复：{sample.sample_id}")
        sample_ids.add(sample.sample_id)
        expected_path = f"images/{sample.sample_id}.png"
        if sample.reference_path != expected_path:
            raise ShadowSuiteContractError(
                f"reference_path 必须为 {expected_path}。"
            )
        instruction_sha256 = sha256(sample.instruction.encode("utf-8")).hexdigest()
        if instruction_sha256 != sample.instruction_sha256:
            raise ShadowSuiteContractError(
                f"instruction hash 漂移：{sample.sample_id}"
            )
        samples.append(
            ShadowSuiteSample(
                sample_id=sample.sample_id,
                reference_path=sample.reference_path,
                reference_sha256=sample.reference_sha256,
                reference_content_type=sample.reference_content_type,
                instruction=sample.instruction,
                instruction_sha256=sample.instruction_sha256,
            )
        )

    schedule = tuple(_ORDER_BY_LABEL[label] for label in root.arm_order_schedule)
    result = ShadowSuiteManifest(
        path=manifest_path,
        manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
        experiment_id=root.experiment_id,
        report_schema_version=root.report_schema_version,
        frozen_at=root.frozen_at,
        rounds=root.rounds,
        arm_order_schedule=schedule,
        shared_config=root.config,
        samples=tuple(samples),
    )
    try:
        for round_index in range(1, result.rounds + 1):
            result.arm_config(round_index)
    except ShadowABConfigError as exc:
        raise ShadowSuiteContractError("manifest 的共享配置不可执行。") from exc
    return result


def load_shadow_suite_gate(
    path: Path | str, *, manifest: ShadowSuiteManifest
) -> ShadowSuiteGate:
    """加载 gate，并复验它对 manifest 与两种完整配置指纹的绑定."""
    gate_path = Path(path)
    payload = _load_yaml_mapping(gate_path, kind="gate")
    try:
        root = _GateRoot.model_validate(payload)
    except ValidationError as exc:
        raise ShadowSuiteContractError(f"gate 违反冻结契约：{gate_path}") from exc

    if root.experiment_id != manifest.experiment_id:
        raise ShadowSuiteContractError("gate experiment_id 与 manifest 不一致。")
    if root.report_schema_version != manifest.report_schema_version:
        raise ShadowSuiteContractError(
            "gate report_schema_version 与 manifest 不一致。"
        )
    if root.metric_version != MIN_SCENE_METRIC_VERSION:
        raise ShadowSuiteContractError("gate metric_version 与实现不一致。")
    if root.manifest_sha256 != manifest.manifest_sha256:
        raise ShadowSuiteContractError("gate 绑定的 manifest hash 已漂移。")
    for label in ("AB", "BA"):
        expected = manifest.config_fingerprint_for_order(label)
        if root.config_fingerprints[label] != expected:
            raise ShadowSuiteContractError(f"{label} 配置指纹已漂移。")

    return ShadowSuiteGate(
        path=gate_path,
        gate_sha256=sha256(gate_path.read_bytes()).hexdigest(),
        manifest_sha256=root.manifest_sha256,
        experiment_id=root.experiment_id,
        report_schema_version=root.report_schema_version,
        metric_version=root.metric_version,
        frozen_at=root.frozen_at,
        config_fingerprints=MappingProxyType(dict(root.config_fingerprints)),
        improvement_margin=root.primary_endpoint.improvement_margin,
        min_improved_sample_ratio=root.primary_endpoint.min_improved_sample_ratio,
        max_inconclusive_sample_ratio=(
            root.inconclusive_policy.max_inconclusive_sample_ratio
        ),
        min_arm_b_preference_rate=(
            root.human_review.min_arm_b_preference_rate
        ),
    )


def resolve_verified_sample_images(
    manifest: ShadowSuiteManifest,
) -> dict[str, Path]:
    """解析并复验全部参考图，拒绝缺失、symlink、越界或内容漂移."""
    try:
        root = manifest.path.parent.resolve(strict=True)
    except OSError as exc:
        raise ShadowSuiteContractError("manifest 根目录不存在。") from exc

    resolved_images: dict[str, Path] = {}
    for sample in manifest.samples:
        candidate = manifest.path.parent / sample.reference_path
        if candidate.is_symlink() or not candidate.is_file():
            raise ShadowSuiteContractError(
                f"参考图不存在、不是文件或是 symlink：{sample.sample_id}"
            )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ShadowSuiteContractError(
                f"参考图无法解析：{sample.sample_id}"
            ) from exc
        if not resolved.is_relative_to(root):
            raise ShadowSuiteContractError(f"参考图越过 manifest 根：{sample.sample_id}")
        if sha256(resolved.read_bytes()).hexdigest() != sample.reference_sha256:
            raise ShadowSuiteContractError(f"参考图 hash 漂移：{sample.sample_id}")
        resolved_images[sample.sample_id] = resolved
    return resolved_images


def _arm_summary(
    payload: Mapping[str, Any], arm_id: ArmId
) -> tuple[str, float | None]:
    arms = payload.get("arms")
    if not isinstance(arms, list):
        raise ShadowSuiteContractError("单 run 报告缺少 arms。")
    matches = [
        arm
        for arm in arms
        if isinstance(arm, dict) and arm.get("arm_id") == arm_id
    ]
    if len(matches) != 1:
        raise ShadowSuiteContractError(f"单 run 报告的 Arm {arm_id} 不唯一。")
    arm = matches[0]
    status = arm.get("status")
    if status not in {"ok", "inconclusive"}:
        raise ShadowSuiteContractError(f"Arm {arm_id} status 非法。")
    current_best = arm.get("current_best")
    if current_best is None:
        return status, None
    if not isinstance(current_best, dict):
        raise ShadowSuiteContractError(f"Arm {arm_id} current_best 非法。")
    loss = current_best.get("loss")
    if isinstance(loss, bool) or not isinstance(loss, (int, float)):
        raise ShadowSuiteContractError(f"Arm {arm_id} loss 非法。")
    numeric_loss = float(loss)
    if not isfinite(numeric_loss) or numeric_loss < 0.0:
        raise ShadowSuiteContractError(f"Arm {arm_id} loss 必须是有限非负数。")
    return status, numeric_loss


def _verified_suite_run(
    *,
    sample: ShadowSuiteSample,
    round_index: int,
    order_label: OrderLabel,
    manifest: ShadowSuiteManifest,
    run_dir: Path,
) -> VerifiedSuiteRun:
    """复验单 run 证据及其与 suite 冻结输入的全部绑定."""
    payload = verify_shadow_run(run_dir)
    expected_order = list(manifest.arm_order(round_index))
    expected_config = manifest.arm_config(round_index)
    expected_values = {
        "reference_sha256": sample.reference_sha256,
        "reference_content_type": sample.reference_content_type,
        "instruction_sha256": sample.instruction_sha256,
        "config_fingerprint": expected_config.fingerprint(),
        "execution_order": expected_order,
    }
    for key, expected in expected_values.items():
        if payload.get(key) != expected:
            raise ShadowSuiteContractError(
                f"单 run 与 suite 冻结输入不一致：{sample.sample_id}/{round_index}/{key}"
            )
    report_sha256 = payload.get("report_sha256")
    status = payload.get("status")
    if not isinstance(report_sha256, str) or status not in {"ok", "inconclusive"}:
        raise ShadowSuiteContractError("单 run 报告身份或状态非法。")
    arm_a_status, arm_a_loss = _arm_summary(payload, ARM_A)
    arm_b_status, arm_b_loss = _arm_summary(payload, ARM_B)
    return VerifiedSuiteRun(
        sample_id=sample.sample_id,
        round_index=round_index,
        order_label=order_label,
        run_id=run_dir.name,
        report_sha256=report_sha256,
        status=status,
        arm_a_status=arm_a_status,
        arm_b_status=arm_b_status,
        arm_a_loss=arm_a_loss,
        arm_b_loss=arm_b_loss,
    )


def aggregate_shadow_suite(
    records: tuple[VerifiedSuiteRun, ...],
    *,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
) -> dict[str, Any]:
    """按预声明 gate 聚合配对结果；inconclusive 样本不得静默排除."""
    expected_pairs = {
        (sample.sample_id, round_index)
        for sample in manifest.samples
        for round_index in range(1, manifest.rounds + 1)
    }
    actual_pairs = {(record.sample_id, record.round_index) for record in records}
    if len(actual_pairs) != len(records) or actual_pairs != expected_pairs:
        raise ShadowSuiteContractError("suite records 缺失、重复或包含未知样本轮次。")

    samples: list[dict[str, Any]] = []
    improved_count = 0
    inconclusive_count = 0
    comparable_deltas_by_order: dict[OrderLabel, list[float]] = {
        "AB": [],
        "BA": [],
    }
    for sample in manifest.samples:
        sample_records = sorted(
            (record for record in records if record.sample_id == sample.sample_id),
            key=lambda item: item.round_index,
        )
        comparable = [
            record.delta_b_minus_a
            for record in sample_records
            if record.status == "ok"
            and record.arm_a_status == "ok"
            and record.arm_b_status == "ok"
            and record.delta_b_minus_a is not None
        ]
        is_inconclusive = len(comparable) != manifest.rounds
        sample_median = (
            float(median(comparable))
            if not is_inconclusive
            else None
        )
        improved = (
            sample_median is not None
            and sample_median <= -gate.improvement_margin
        )
        if is_inconclusive:
            inconclusive_count += 1
        if improved:
            improved_count += 1
        for record in sample_records:
            delta = record.delta_b_minus_a
            if (
                record.status == "ok"
                and record.arm_a_status == "ok"
                and record.arm_b_status == "ok"
                and delta is not None
            ):
                comparable_deltas_by_order[record.order_label].append(delta)
        samples.append(
            {
                "sample_id": sample.sample_id,
                "status": "inconclusive" if is_inconclusive else "comparable",
                "median_delta_b_minus_a": sample_median,
                "improved_beyond_margin": improved,
                "rounds": [record.to_dict() for record in sample_records],
            }
        )

    sample_count = len(manifest.samples)
    improved_ratio = improved_count / sample_count
    inconclusive_ratio = inconclusive_count / sample_count
    order_medians: dict[str, float | None] = {}
    for label, deltas in comparable_deltas_by_order.items():
        order_medians[label] = float(median(deltas)) if deltas else None
    order_consistent = all(
        value is not None and value < 0.0 for value in order_medians.values()
    )
    automatic_passed = (
        improved_ratio >= gate.min_improved_sample_ratio
        and inconclusive_ratio <= gate.max_inconclusive_sample_ratio
        and order_consistent
    )
    return {
        "primary_endpoint": {
            "metric": "current_best_loss",
            "delta_convention": "B_minus_A_negative_is_better",
            "improvement_margin": gate.improvement_margin,
            "improved_sample_count": improved_count,
            "sample_count": sample_count,
            "improved_sample_ratio": improved_ratio,
            "required_ratio": gate.min_improved_sample_ratio,
        },
        "inconclusive": {
            "sample_count": inconclusive_count,
            "sample_ratio": inconclusive_ratio,
            "maximum_ratio": gate.max_inconclusive_sample_ratio,
            "counting": "inconclusive_counts_against_arm_b",
        },
        "order_effect": {
            "median_delta_b_minus_a": order_medians,
            "consistent_direction": order_consistent,
            "rule": "consistent_direction_required",
        },
        "samples": samples,
        "automatic_gate": {
            "passed": automatic_passed,
            "outcome": "supported" if automatic_passed else "not_supported",
        },
        "promotion_decision": (
            "no_go_pending_human_and_durable"
            if automatic_passed
            else "no_go_automatic_gate_failed"
        ),
    }


def build_shadow_suite_report(
    records: tuple[VerifiedSuiteRun, ...],
    *,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
) -> dict[str, Any]:
    """构造不含自身 hash 的 suite 报告主体."""
    return {
        "report_schema_version": "layerplan_glsl_shadow_suite_report_v1",
        "experiment_id": manifest.experiment_id,
        "run_classification": "independent_experiment",
        "manifest": {
            "name": manifest.path.name,
            "sha256": manifest.manifest_sha256,
        },
        "gate": {"name": gate.path.name, "sha256": gate.gate_sha256},
        "schedule": {
            "rounds": manifest.rounds,
            "arm_orders": [
                "".join(manifest.arm_order(index))
                for index in range(1, manifest.rounds + 1)
            ],
        },
        "runs": [record.to_dict() for record in records],
        "aggregate": aggregate_shadow_suite(records, manifest=manifest, gate=gate),
        "durability_status": "local_private_not_registered",
        "human_review_status": "pending",
        "validity_notes": [
            "无 seed 且 temperature=1；AB/BA 交叉平衡只能降低顺序混杂，"
            "不能证明 LayerPlan 是唯一因果变量。",
            "自动 gate 通过也不得晋升；仍需独立人工盲评与 durable 跨环境证据。",
        ],
    }


def _suite_id(report_body: Mapping[str, Any]) -> str:
    digest = sha256(canonical_json(dict(report_body)).encode("utf-8")).hexdigest()
    return f"shadow-suite-{digest[:12]}"


def write_shadow_suite_report(
    report_body: Mapping[str, Any], output_root: Path
) -> Path:
    """以同根 staging + 原子 rename 写私有 suite 报告."""
    if output_root.is_symlink():
        raise ShadowSuiteContractError("output_root 不得是 symlink。")
    output_root.mkdir(parents=True, exist_ok=True)
    suite_id = _suite_id(report_body)
    suite_dir = output_root / suite_id
    staging = output_root / f".{suite_id}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    staging.mkdir(mode=0o700)
    try:
        payload = dict(report_body)
        payload["suite_report_sha256"] = sha256(
            canonical_json(payload).encode("utf-8")
        ).hexdigest()
        report_path = staging / "suite_report.json"
        report_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
        os.chmod(report_path, 0o600)
        if suite_dir.exists() or suite_dir.is_symlink():
            raise FileExistsError(f"shadow suite 目录已存在：{suite_dir}")
        os.rename(staging, suite_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return suite_dir


def verify_shadow_suite_report(
    suite_dir: Path,
    *,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
) -> dict[str, Any]:
    """复验 suite 报告、本地权限、冻结输入及其引用的全部单 run."""
    if suite_dir.is_symlink() or not suite_dir.is_dir():
        raise ShadowSuiteContractError("suite 目录无效。")
    if suite_dir.stat().st_mode & 0o077:
        raise ShadowSuiteContractError("suite 目录权限过宽。")
    report_path = suite_dir / "suite_report.json"
    if report_path.is_symlink() or not report_path.is_file():
        raise ShadowSuiteContractError("suite_report.json 缺失或是 symlink。")
    if report_path.stat().st_mode & 0o077:
        raise ShadowSuiteContractError("suite_report.json 权限过宽。")
    if any(path.name != "suite_report.json" for path in suite_dir.iterdir()):
        raise ShadowSuiteContractError("suite 目录包含未声明文件。")
    try:
        payload = yaml.safe_load(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ShadowSuiteContractError("suite_report.json 无法读取。") from exc
    if not isinstance(payload, dict):
        raise ShadowSuiteContractError("suite_report.json 必须是 object。")
    report_hash = payload.pop("suite_report_sha256", None)
    actual_hash = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    if report_hash != actual_hash:
        raise ShadowSuiteContractError("suite_report_sha256 不匹配。")
    if suite_dir.name != _suite_id(payload):
        raise ShadowSuiteContractError("suite 目录名与内容寻址身份不匹配。")
    manifest_ref = payload.get("manifest")
    gate_ref = payload.get("gate")
    if not isinstance(manifest_ref, dict) or not isinstance(gate_ref, dict):
        raise ShadowSuiteContractError("suite 报告缺少 manifest/gate 绑定。")
    if manifest_ref.get("sha256") != manifest.manifest_sha256:
        raise ShadowSuiteContractError("suite manifest 绑定已漂移。")
    if gate_ref.get("sha256") != gate.gate_sha256:
        raise ShadowSuiteContractError("suite gate 绑定已漂移。")
    runs = payload.get("runs")
    if not isinstance(runs, list):
        raise ShadowSuiteContractError("suite 报告缺少 runs。")
    samples_by_id = {sample.sample_id: sample for sample in manifest.samples}
    verified_records: list[VerifiedSuiteRun] = []
    for run in runs:
        if not isinstance(run, dict):
            raise ShadowSuiteContractError("suite run 摘要非法。")
        sample_id = run.get("sample_id")
        round_index = run.get("round_index")
        order_label = run.get("order_label")
        run_id = run.get("run_id")
        expected_report_hash = run.get("report_sha256")
        if (
            not isinstance(sample_id, str)
            or sample_id not in samples_by_id
            or isinstance(round_index, bool)
            or not isinstance(round_index, int)
            or order_label not in {"AB", "BA"}
            or not isinstance(run_id, str)
            or "/" in run_id
            or "\\" in run_id
        ):
            raise ShadowSuiteContractError("suite run_id 非法。")
        expected_order_label = cast(
            OrderLabel, "".join(manifest.arm_order(round_index))
        )
        if order_label != expected_order_label:
            raise ShadowSuiteContractError(
                "suite order_label 与冻结 schedule 不一致。"
            )
        record = _verified_suite_run(
            sample=samples_by_id[sample_id],
            round_index=round_index,
            order_label=expected_order_label,
            manifest=manifest,
            run_dir=suite_dir.parent / run_id,
        )
        if record.report_sha256 != expected_report_hash:
            raise ShadowSuiteContractError(f"suite 引用的 run 已漂移：{run_id}")
        if record.to_dict() != run:
            raise ShadowSuiteContractError(f"suite run 摘要与原始证据不一致：{run_id}")
        verified_records.append(record)
    expected_body = build_shadow_suite_report(
        tuple(verified_records), manifest=manifest, gate=gate
    )
    if canonical_json(payload) != canonical_json(expected_body):
        raise ShadowSuiteContractError("suite 聚合或冻结元数据与原始证据不一致。")
    payload["suite_report_sha256"] = report_hash
    return cast(dict[str, Any], payload)


async def run_shadow_suite(
    *,
    gateway: LLMGateway,
    renderer: ShadowRenderer,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
    output_root: Path,
    runner_factory: (
        Callable[[ShadowABConfig], LayerPlanGlslShadowRunner] | None
    ) = None,
) -> Path:
    """顺序执行冻结 suite，写入并复验全部私有证据."""
    images = resolve_verified_sample_images(manifest)
    records: list[VerifiedSuiteRun] = []
    for sample in manifest.samples:
        for round_index in range(1, manifest.rounds + 1):
            config = manifest.arm_config(round_index)
            runner = (
                runner_factory(config)
                if runner_factory is not None
                else LayerPlanGlslShadowRunner(
                    gateway=gateway,
                    renderer=renderer,
                    config=config,
                )
            )
            reference_image = images[sample.sample_id].read_bytes()
            if sha256(reference_image).hexdigest() != sample.reference_sha256:
                raise ShadowSuiteContractError(
                    f"模型调用前参考图 hash 漂移：{sample.sample_id}"
                )
            result = await runner.run(
                reference_image,
                content_type=sample.reference_content_type,
                instruction=sample.instruction,
            )
            if shadow_run_id(result) == "":
                raise ShadowSuiteContractError("runner 返回空 run identity。")
            run_dir = write_shadow_run(result, output_root)
            order_label = cast(
                OrderLabel, "".join(manifest.arm_order(round_index))
            )
            records.append(
                _verified_suite_run(
                    sample=sample,
                    round_index=round_index,
                    order_label=order_label,
                    manifest=manifest,
                    run_dir=run_dir,
                )
            )
    report_body = build_shadow_suite_report(
        tuple(records), manifest=manifest, gate=gate
    )
    suite_dir = write_shadow_suite_report(report_body, output_root)
    verify_shadow_suite_report(suite_dir, manifest=manifest, gate=gate)
    return suite_dir


__all__ = [
    "GATE_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "ShadowSharedArmConfig",
    "ShadowSuiteContractError",
    "ShadowSuiteGate",
    "ShadowSuiteManifest",
    "ShadowSuiteSample",
    "VerifiedSuiteRun",
    "aggregate_shadow_suite",
    "build_shadow_suite_report",
    "load_shadow_suite_gate",
    "load_shadow_suite_manifest",
    "resolve_verified_sample_images",
    "run_shadow_suite",
    "verify_shadow_suite_report",
    "write_shadow_suite_report",
]
