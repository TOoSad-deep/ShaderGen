"""Intent Builder 产物的独立重建式 Validator。."""

from __future__ import annotations

from shaderforge.analysis import TargetMeasurementsV2
from shaderforge.intent.builder import (
    build_intent_variants,
    compute_intent_id,
)
from shaderforge.intent.ir import (
    IntentBuildContext,
    IntentBuildResult,
    IntentIR,
    VisualInterpretationV2,
)
from shaderforge.intent.models import RequestConstraintSet


def validate_intent_ir(
    intent: IntentIR,
    *,
    measurements: TargetMeasurementsV2,
    interpretation: VisualInterpretationV2,
    constraint_set: RequestConstraintSet,
    context: IntentBuildContext,
) -> None:
    """从四个冻结输入重建精确 variant，任何字段漂移都 fail closed。."""
    if intent.intent_id != compute_intent_id(intent):
        raise ValueError("intent_id 与 Intent 语义不一致。")
    rebuilt = build_intent_variants(
        measurements,
        interpretation,
        constraint_set,
        context,
    )
    expected = next(
        (
            item
            for item in rebuilt.variants
            if (
                item.target_hypothesis_id == intent.target_hypothesis_id
                and item.target_hypothesis_hash == intent.target_hypothesis_hash
            )
        ),
        None,
    )
    if expected is None:
        raise ValueError("绑定的 TargetHypothesis 未产生可行 Intent variant。")
    if intent != expected:
        raise ValueError("Intent 与冻结输入重建的 Builder variant 不一致。")


def validate_intent_build_result(
    result: IntentBuildResult,
    *,
    measurements: TargetMeasurementsV2,
    interpretation: VisualInterpretationV2,
    constraint_set: RequestConstraintSet,
    context: IntentBuildContext,
) -> None:
    """验证持久化 result 的输入 receipt、完整 partition 与全部 variants。."""
    expected = build_intent_variants(
        measurements,
        interpretation,
        constraint_set,
        context,
    )
    if result != expected:
        raise ValueError("IntentBuildResult 与四个冻结输入的重建结果不一致。")


__all__ = ["validate_intent_build_result", "validate_intent_ir"]
