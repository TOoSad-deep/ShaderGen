"""从 confirmed State v4 派生 V2.3 actual-render gate case capability。"""
# ruff: noqa: D103, D107, D415

from __future__ import annotations

from hashlib import sha256
from typing import Any, Literal

from pydantic import Field

from agent.app.states.png_to_shader_v2_state import (
    CHECKPOINT_SCHEMA_VERSION_V4,
    GRAPH_ID_V2,
    GRAPH_VERSION_V2_4,
    STATE_SCHEMA_VERSION_V4,
    PngToShaderV2State,
    build_checkpoint_namespace_v2,
    serialize_state_v2,
)
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
)
from shaderforge.benchmark import (
    V2_3_RENDERED_SEEDS_PER_HYPOTHESIS,
    V2_3ActualChromiumCandidateReceiptV1,
    V2_3ActualChromiumReplayError,
    V2_3ActualChromiumReplayRunner,
    V2_3RenderedGraphCaseOutcome,
    V2_3RenderedLayerPrediction,
    V2_3VerifiedRenderedCaseCapability,
    _issue_v2_3_verified_rendered_case_capability,
    compute_v2_3_actual_replay_receipts_root,
    compute_v2_3_rendered_case_outcome_hash,
)
from shaderforge.contracts import (
    REQUIRED_LAYER_ORDER,
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
)
from shaderforge.evaluation import (
    TypedCandidateArtifactBundleV2,
    load_typed_candidate_artifacts,
)
from shaderforge.store import ArtifactRefV2, ArtifactResolver


class V2_3RenderedCaseCollectionIdentity(FrozenModel):
    """Manifest/config 给 collector 的非预测 case identity。"""

    manifest_id: NonEmptyString
    dataset_version: NonEmptyString
    manifest_sha256: Sha256Hex
    taxonomy_sha256: Sha256Hex
    config_sha256: Sha256Hex
    threshold_policy_hash: Sha256Hex
    input_intent_outcomes_sha256: Sha256Hex
    input_compiler_outcomes_sha256: Sha256Hex
    split: Literal["development", "validation"]
    case_id: NonEmptyString
    source_image_sha256: Sha256Hex
    expected_hypothesis_count: int = Field(ge=1)


class V2_3RenderedCaseCollectionResult:
    """不可序列化的门禁能力与可持久化 replay receipts。"""

    __slots__ = ("_capability", "_receipts")

    def __init__(
        self,
        *,
        capability: V2_3VerifiedRenderedCaseCapability,
        receipts: tuple[V2_3ActualChromiumCandidateReceiptV1, ...],
    ) -> None:
        self._capability = capability
        self._receipts = receipts

    @property
    def capability(self) -> V2_3VerifiedRenderedCaseCapability:
        """返回只能在当前进程用于正式 gate 的 sealed capability。"""
        return self._capability

    @property
    def receipts(self) -> tuple[V2_3ActualChromiumCandidateReceiptV1, ...]:
        """返回可由调用方持久化的逐 Candidate actual replay receipts。"""
        return self._receipts


class _CollectedCandidateFacts:
    __slots__ = ("bundles", "candidate_refs")

    def __init__(
        self,
        *,
        bundles: tuple[TypedCandidateArtifactBundleV2, ...],
        candidate_refs: tuple[ArtifactRefV2, ...],
    ) -> None:
        self.bundles = bundles
        self.candidate_refs = candidate_refs


def _zero_layer_predictions() -> tuple[V2_3RenderedLayerPrediction, ...]:
    return tuple(
        V2_3RenderedLayerPrediction(
            layer=layer,
            enabled=False,
            prediction_available=False,
            visible=None,
            diagnostic_render_ref=None,
        )
        for layer in REQUIRED_LAYER_ORDER
    )


def _state_candidate_refs(state: PngToShaderV2State) -> tuple[ArtifactRefV2, ...]:
    return tuple(
        ref
        for ref in state.candidate_summary_refs
        if ref.kind == "candidate_record"
        and ref.schema_version == "candidate_record_v3"
        and ref.content_type == "application/json"
    )


def _strict_state_invariants(state: PngToShaderV2State) -> int:
    if (
        state.state_schema_version != STATE_SCHEMA_VERSION_V4
        or state.graph_id != GRAPH_ID_V2
        or state.graph_version != GRAPH_VERSION_V2_4
        or state.checkpoint_schema_version != CHECKPOINT_SCHEMA_VERSION_V4
        or state.checkpoint_namespace != build_checkpoint_namespace_v2(state.run_id)
    ):
        raise ValueError("Rendered gate 只接受 Graph 2.4 confirmed State v4。")
    if (
        state.phase != "finalized"
        or state.stop_reason != "completed_with_objective_best"
        or state.hypothesis_cursor != len(state.hypothesis_branches)
    ):
        raise ValueError("Rendered gate success 要求 finalized objective-best State。")
    reserved = state.budget_state.reserved.model_dump(mode="python")
    if any(value != 0 for value in reserved.values()):
        raise ValueError("Rendered gate final State 所有 reservation 必须归零。")
    if not state.hypothesis_branches:
        raise ValueError("Rendered gate State 至少需要一个 hypothesis branch。")
    for branch in state.hypothesis_branches:
        if (
            branch.status != "completed"
            or len(branch.seed_refs) != V2_3_RENDERED_SEEDS_PER_HYPOTHESIS
            or branch.seed_cursor != len(branch.seed_refs)
            or branch.hypothesis_best_id is None
        ):
            raise ValueError("每个 hypothesis 必须完成三个 seed 并冻结 branch best。")
    expected = len(state.hypothesis_branches) * V2_3_RENDERED_SEEDS_PER_HYPOTHESIS
    if (
        len(state.candidate_summary_refs) != expected
        or state.objective_best_ref is None
        or state.objective_best_id is None
    ):
        raise ValueError("Final State Candidate 分母或 objective best 不闭合。")
    return expected


def _load_all_candidates(
    state: PngToShaderV2State,
    *,
    resolver: ArtifactResolver,
    expected: int,
) -> _CollectedCandidateFacts:
    refs = _state_candidate_refs(state)
    if refs != state.candidate_summary_refs or len(refs) != expected:
        raise ValueError("Success State 必须只包含 expected 数量的 Candidate v3 refs。")
    bundles = tuple(
        load_typed_candidate_artifacts(ref, resolver=resolver, run_id=state.run_id)
        for ref in refs
    )
    if len({item.candidate.candidate_id for item in bundles}) != expected:
        raise ValueError("Candidate ids 必须唯一覆盖全部 attempts。")
    if len({item.render_plan.attempt_id for item in bundles}) != expected:
        raise ValueError("Candidate attempt ids 必须唯一覆盖全部 attempts。")
    by_hypothesis = {
        branch.target_hypothesis_hash: tuple(
            item
            for item in bundles
            if item.candidate.target_hypothesis_hash == branch.target_hypothesis_hash
        )
        for branch in state.hypothesis_branches
    }
    if any(len(items) != V2_3_RENDERED_SEEDS_PER_HYPOTHESIS for items in by_hypothesis.values()):
        raise ValueError("每个 hypothesis 必须闭合三个 Candidate。")
    if any(
        len({item.candidate.semantic_genome_hash for item in items})
        != V2_3_RENDERED_SEEDS_PER_HYPOTHESIS
        for items in by_hypothesis.values()
    ):
        raise ValueError("每个 hypothesis 的三个 Candidate 必须有不同 semantic Genome。")
    for branch in state.hypothesis_branches:
        branch_ids = {
            item.candidate.candidate_id
            for item in by_hypothesis[branch.target_hypothesis_hash]
        }
        if branch.hypothesis_best_id not in branch_ids:
            raise ValueError("Branch best 不属于本 branch Candidate closure。")
    if state.objective_best_ref not in refs:
        raise ValueError("objective_best_ref 不属于全部 Candidate refs。")
    selected = bundles[refs.index(state.objective_best_ref)]
    if selected.candidate.candidate_id != state.objective_best_id:
        raise ValueError("objective_best id/ref 不一致。")
    return _CollectedCandidateFacts(bundles=bundles, candidate_refs=refs)


async def _replay_all_candidates(
    facts: _CollectedCandidateFacts,
    *,
    resolver: ArtifactResolver,
    run_id: str,
    runner: V2_3ActualChromiumReplayRunner,
) -> tuple[V2_3ActualChromiumCandidateReceiptV1, ...]:
    receipts = []
    for ref in facts.candidate_refs:
        receipts.append(
            await runner.replay_candidate(ref, resolver=resolver, run_id=run_id)
        )
    result = tuple(receipts)
    if len({item.record_hash for item in result}) != len(result):
        raise ValueError("Actual replay Candidate receipt hashes 不得重复。")
    if len({item.actual_environment_hash for item in result}) != 1:
        raise ValueError("全部 Candidate actual Chromium environment 必须唯一。")
    for candidate_ref, bundle, receipt in zip(
        facts.candidate_refs, facts.bundles, result, strict=True
    ):
        if (
            receipt.candidate_ref != candidate_ref
            or receipt.candidate_id != bundle.candidate.candidate_id
            or receipt.attempt_id != bundle.render_plan.attempt_id
            or receipt.render_plan_ref != bundle.candidate.render_plan_ref
            or receipt.render_plan_hash != bundle.render_plan.plan_hash
        ):
            raise ValueError("Actual replay receipt 与 Candidate/RenderPlan identity 不一致。")
        persisted_hash = (
            bundle.rendered_closure_projection.renderer_environment_hash
        )
        if any(
            item.persisted_renderer_environment_hash != persisted_hash
            for item in receipt.item_receipts
        ):
            raise ValueError("Actual replay 未逐项验证 Candidate persisted environment。")
    return result


def _render_counts(
    bundles: tuple[TypedCandidateArtifactBundleV2, ...],
) -> dict[str, int]:
    beauty = len(bundles) * 5
    diagnostics = sum(len(item.render_plan.items) - 5 for item in bundles)
    nominal = sum(len(item.render_plan.items) for item in bundles)
    outcomes = tuple(
        outcome for item in bundles for outcome in item.render_progress.outcomes
    )
    transient = sum(item.outcome == "transient_failure" for item in outcomes)
    unknown = sum(item.outcome == "unknown" for item in outcomes)
    return {
        "beauty_capture_count": beauty,
        "diagnostic_render_count": diagnostics,
        "nominal_render_request_count": nominal,
        "logical_render_request_attempt_count": nominal,
        "physical_render_call_count": len(outcomes),
        "render_retry_count": transient + unknown,
        "transient_render_retry_count": transient,
        "unknown_render_retry_count": unknown,
        "unknown_render_result_count": unknown,
    }


def _selected_layer_predictions(
    selected: TypedCandidateArtifactBundleV2,
) -> tuple[V2_3RenderedLayerPrediction, ...]:
    diagnostic_refs = {
        receipt.layer: receipt.render_ref
        for receipt in selected.rendered_structure_evidence.diagnostic_receipts
        if receipt.pass_kind == "layer_visible_delta" and receipt.layer is not None
    }
    rows = selected.rendered_structure_verification.layer_contribution_results
    return tuple(
        V2_3RenderedLayerPrediction(
            layer=row.layer,
            enabled=row.enabled_in_genome,
            prediction_available=True,
            visible=row.predicted_visible,
            diagnostic_render_ref=(
                diagnostic_refs[row.layer] if row.enabled_in_genome else None
            ),
        )
        for row in rows
    )


def _identity_payload(identity: V2_3RenderedCaseCollectionIdentity) -> dict[str, Any]:
    return {
        key: value
        for key, value in identity.model_dump(mode="python").items()
        if key != "expected_hypothesis_count"
    }


def _success_outcome(
    *,
    identity: V2_3RenderedCaseCollectionIdentity,
    state: PngToShaderV2State,
    facts: _CollectedCandidateFacts,
    receipts: tuple[V2_3ActualChromiumCandidateReceiptV1, ...],
) -> V2_3RenderedGraphCaseOutcome:
    expected = len(state.hypothesis_branches) * V2_3_RENDERED_SEEDS_PER_HYPOTHESIS
    selected_index = facts.candidate_refs.index(state.objective_best_ref)
    selected = facts.bundles[selected_index]
    counts = _render_counts(facts.bundles)
    if (
        state.budget_state.used.candidate_attempts != expected
        or state.budget_state.used.render_calls != counts["physical_render_call_count"]
        or state.budget_state.used.model_calls != 0
        or state.budget_state.used.model_tokens != 0
        or state.budget_state.used.cost_usd_micros != 0
    ):
        raise ValueError("Final State budget 与 Candidate progress/model-off 契约不闭合。")
    receipt_hashes = tuple(item.record_hash for item in receipts)
    actual_environment_hash = receipts[0].actual_environment_hash
    persisted_environments = {
        item.rendered_closure_projection.renderer_environment_hash
        for item in facts.bundles
    }
    if len(persisted_environments) != 1:
        raise ValueError("全部 Candidate persisted Renderer environment 必须唯一。")
    verification = selected.rendered_structure_verification
    payload: dict[str, Any] = {
        **_identity_payload(identity),
        "success": True,
        "terminal_phase": state.phase,
        "stop_reason": state.stop_reason,
        "final_state_sha256": sha256(serialize_state_v2(state)).hexdigest(),
        "hypothesis_count": len(state.hypothesis_branches),
        "expected_seed_attempt_count": expected,
        "seed_attempt_count": sum(
            branch.seed_cursor for branch in state.hypothesis_branches
        ),
        "attempt_artifact_closure_count": len(facts.bundles),
        "successful_candidate_count": len(facts.bundles),
        "branch_best_count": sum(
            branch.hypothesis_best_id is not None
            for branch in state.hypothesis_branches
        ),
        "all_candidate_refs": facts.candidate_refs,
        "actual_replay_receipt_hashes": receipt_hashes,
        "actual_replay_receipts_root": compute_v2_3_actual_replay_receipts_root(
            facts.candidate_refs, receipt_hashes
        ),
        "selected_candidate_ref": selected.candidate_ref,
        "selected_candidate_record_hash": selected.candidate.record_hash,
        "render_plan_ref": selected.candidate.render_plan_ref,
        "render_plan_record_hash": selected.render_plan.plan_hash,
        "render_progress_ref": selected.candidate.render_progress_ref,
        "render_progress_record_hash": selected.render_progress.record_hash,
        "render_repeatability_ref": selected.candidate.render_repeatability_ref,
        "render_repeatability_record_hash": selected.repeatability.record_hash,
        "rendered_structure_evidence_ref": (
            selected.candidate.rendered_structure_evidence_ref
        ),
        "rendered_structure_evidence_record_hash": (
            selected.rendered_structure_evidence.record_hash
        ),
        "rendered_structure_verification_ref": (
            selected.candidate.rendered_structure_verification_ref
        ),
        "rendered_structure_verification_record_hash": verification.record_hash,
        "prediction_source": (
            "selected_candidate_rendered_structure_verification_v4"
        ),
        "verification_status": verification.status,
        "measured_topology": verification.measured_topology,
        "measured_instance_count": verification.measured_instance_count,
        "measured_hole_count": verification.measured_hole_count,
        "layer_predictions": _selected_layer_predictions(selected),
        **counts,
        "render_budget_used": counts["physical_render_call_count"],
        "render_budget_reserved": state.budget_state.reserved.render_calls,
        "renderer_environment_hash": actual_environment_hash,
        "persisted_renderer_environment_hash": next(iter(persisted_environments)),
        "failure_codes": (),
        "record_hash": "0" * 64,
    }
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    return V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)


def _failure_outcome(
    *,
    identity: V2_3RenderedCaseCollectionIdentity,
    state: PngToShaderV2State | None,
    candidate_refs: tuple[ArtifactRefV2, ...],
    bundles: tuple[TypedCandidateArtifactBundleV2, ...],
    failure_code: str,
) -> V2_3RenderedGraphCaseOutcome:
    hypothesis_count = (
        len(state.hypothesis_branches)
        if state is not None and state.hypothesis_branches
        else identity.expected_hypothesis_count
    )
    counts = _render_counts(bundles)
    payload: dict[str, Any] = {
        **_identity_payload(identity),
        "success": False,
        "terminal_phase": state.phase if state is not None else None,
        "stop_reason": state.stop_reason if state is not None else None,
        "final_state_sha256": (
            sha256(serialize_state_v2(state)).hexdigest()
            if state is not None
            else None
        ),
        "hypothesis_count": hypothesis_count,
        "expected_seed_attempt_count": (
            hypothesis_count * V2_3_RENDERED_SEEDS_PER_HYPOTHESIS
        ),
        "seed_attempt_count": (
            sum(branch.seed_cursor for branch in state.hypothesis_branches)
            if state is not None
            else 0
        ),
        "attempt_artifact_closure_count": len(bundles),
        "successful_candidate_count": len(bundles),
        "branch_best_count": (
            sum(
                branch.hypothesis_best_id is not None
                for branch in state.hypothesis_branches
            )
            if state is not None
            else 0
        ),
        "all_candidate_refs": candidate_refs,
        "actual_replay_receipt_hashes": (),
        "actual_replay_receipts_root": None,
        "selected_candidate_ref": None,
        "selected_candidate_record_hash": None,
        "render_plan_ref": None,
        "render_plan_record_hash": None,
        "render_progress_ref": None,
        "render_progress_record_hash": None,
        "render_repeatability_ref": None,
        "render_repeatability_record_hash": None,
        "rendered_structure_evidence_ref": None,
        "rendered_structure_evidence_record_hash": None,
        "rendered_structure_verification_ref": None,
        "rendered_structure_verification_record_hash": None,
        "prediction_source": None,
        "verification_status": None,
        "measured_topology": None,
        "measured_instance_count": None,
        "measured_hole_count": None,
        "layer_predictions": _zero_layer_predictions(),
        **counts,
        "render_budget_used": counts["physical_render_call_count"],
        "render_budget_reserved": (
            state.budget_state.reserved.render_calls if state is not None else 0
        ),
        "renderer_environment_hash": None,
        "persisted_renderer_environment_hash": None,
        "failure_codes": (failure_code,),
        "record_hash": "0" * 64,
    }
    payload["record_hash"] = compute_v2_3_rendered_case_outcome_hash(payload)
    return V2_3RenderedGraphCaseOutcome.model_validate(payload, strict=True)


async def collect_v2_3_verified_rendered_case(
    *,
    state_store: LocalPngToShaderV2StateStore,
    run_id: str,
    resolver: ArtifactResolver,
    identity: V2_3RenderedCaseCollectionIdentity,
    replay_runner: V2_3ActualChromiumReplayRunner,
) -> V2_3RenderedCaseCollectionResult:
    """从 confirmed State 和 concrete replay 派生 sealed case capability。"""
    if type(state_store) is not LocalPngToShaderV2StateStore:
        raise TypeError("Strict collector 只接受 concrete Local V2 State Store。")
    if type(replay_runner) is not V2_3ActualChromiumReplayRunner:
        raise TypeError("Strict collector 只接受 concrete Chromium replay runner。")
    state: PngToShaderV2State | None = None
    facts = _CollectedCandidateFacts(bundles=(), candidate_refs=())
    receipts: tuple[V2_3ActualChromiumCandidateReceiptV1, ...] = ()
    collection_phase = "load_confirmed_state"
    try:
        state = state_store.load_last_confirmed(run_id)
        collection_phase = "validate_confirmed_state"
        if state.run_id != run_id:
            raise ValueError("Confirmed State run_id 不一致。")
        expected = _strict_state_invariants(state)
        if len(state.hypothesis_branches) != identity.expected_hypothesis_count:
            raise ValueError("Confirmed State hypothesis 分母与 case identity 不一致。")
        collection_phase = "load_candidate_closure"
        facts = _load_all_candidates(state, resolver=resolver, expected=expected)
        collection_phase = "replay_candidates"
        receipts = await _replay_all_candidates(
            facts, resolver=resolver, run_id=run_id, runner=replay_runner
        )
        collection_phase = "derive_case_outcome"
        outcome = _success_outcome(
            identity=identity, state=state, facts=facts, receipts=receipts
        )
    except Exception as exc:  # case 失败必须保留在冻结分母内
        detail = (
            exc.code
            if isinstance(exc, V2_3ActualChromiumReplayError)
            else type(exc).__name__
        )
        failure_code = f"strict_collection_failed:{collection_phase}:{detail}"
        candidate_refs = _state_candidate_refs(state) if state is not None else ()
        outcome = _failure_outcome(
            identity=identity,
            state=state,
            candidate_refs=candidate_refs,
            bundles=facts.bundles,
            failure_code=failure_code,
        )
    capability = _issue_v2_3_verified_rendered_case_capability(outcome)
    return V2_3RenderedCaseCollectionResult(
        capability=capability,
        receipts=receipts if outcome.success else (),
    )


__all__ = [
    "V2_3RenderedCaseCollectionIdentity",
    "V2_3RenderedCaseCollectionResult",
    "collect_v2_3_verified_rendered_case",
]
