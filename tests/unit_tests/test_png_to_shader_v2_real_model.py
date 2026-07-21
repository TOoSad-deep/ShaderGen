from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from agent.app.prompts.prompt_loader import PromptDefinition
from agent.app.services.png_to_shader_v2.real_model import (
    DurableGatewayResultV1,
    LocalRealModelOperationStore,
    RealModelCallPolicyV1,
    RealModelCommittedFailure,
    RealModelIdentityError,
    VisualInterpretationGatewayAdapter,
    execute_real_visual_interpretation,
)
from agent.app.states.png_to_shader_v2_state import (
    BudgetStateV2,
    BudgetVectorV2,
    PngToShaderV2State,
    build_checkpoint_namespace_v2,
)
from agent.app.states.png_to_shader_v2_state_store import LocalPngToShaderV2StateStore
from shaderforge.contracts import REQUIRED_LAYER_ORDER
from shaderforge.intent import (
    Constraint,
    ContractConstraintValue,
    IntentBuildContext,
    LayerHypothesis,
    PrimitiveCandidate,
    RequiredLayerAssessment,
    StrategyHypothesis,
    VisualInterpretationV2,
    build_request_constraint_set,
)
from shaderforge.store import LocalArtifactCatalog, LocalArtifactStore


def _zero() -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


def _policy() -> RealModelCallPolicyV1:
    return RealModelCallPolicyV1(
        provider_id="durable-fake",
        model_id="fake:model-v1",
        pricing_policy_id="fake-price-v1",
        input_micros_per_million_tokens=1_000_000,
        output_micros_per_million_tokens=2_000_000,
        max_input_tokens=100,
        max_output_tokens=100,
        max_cost_usd_micros=1_000,
        max_output_artifact_bytes=100_000,
    )


def _setup(tmp_path: Path, run_id: str, *, model_tokens: int = 1_000):
    catalog = LocalArtifactCatalog(
        LocalArtifactStore(tmp_path / "artifacts").register_run("project", run_id),
        run_id=run_id,
    )
    measurement_ref = catalog.put(
        run_id=run_id,
        kind="target_measurements",
        schema_version="target_measurements_v2_2",
        content_type="application/json",
        data=b'{"measurement":"fixture"}',
    )
    constraints = build_request_constraint_set(
        constraint_set_id="constraints",
        target_sha256="a" * 64,
        request_revision=0,
        constraints=(
            Constraint(
                constraint_id="contract",
                kind="contract",
                strength="hard",
                scope="global",
                value=ContractConstraintValue(
                    contract_id="webgl1_static_no_texture_v1"
                ),
                source="render_contract",
                source_revision=0,
                confidence=1.0,
                verification_status="verified",
            ),
        ),
        evidence_refs=(measurement_ref,),
    )
    constraint_ref = catalog.put(
        run_id=run_id,
        kind="request_constraint_set",
        schema_version="request_constraint_set_v1",
        content_type="application/json",
        data=constraints.model_dump_json().encode(),
    )
    context = IntentBuildContext(
        contract_id="webgl1_static_no_texture_v1",
        primitive_catalog_version="png_to_shader_expected_primitives_v1",
        primitive_catalog_sha256="1" * 64,
        template_catalog_version="png_to_shader_expected_primitives_v1",
        template_catalog_sha256="2" * 64,
        allowed_primitive_ids=("ellipse_sdf",),
        allowed_template_ids=("geometry.ellipse_sdf.v0",),
        allowed_interpretation_evidence_refs=(measurement_ref,),
    )
    limits = BudgetVectorV2(
        wall_time_ms=1_000,
        model_calls=1,
        model_tokens=model_tokens,
        render_calls=10,
        candidate_attempts=10,
        artifact_bytes=200_000,
        cost_usd_micros=2_000,
    )
    state = PngToShaderV2State(
        checkpoint_namespace=build_checkpoint_namespace_v2(run_id),
        project_id="project",
        run_id=run_id,
        run_revision=0,
        phase="initialized",
        evaluation_revision=0,
        measurements_ref=measurement_ref,
        visual_interpretation_ref=None,
        request_constraint_set_ref=constraint_ref,
        hypothesis_branches=(),
        hypothesis_cursor=0,
        objective_best_id=None,
        candidate_summary_refs=(),
        budget_state=BudgetStateV2(
            policy_hash="b" * 64,
            revision=0,
            limits=limits,
            used=_zero(),
            reserved=_zero(),
            exhausted_dimensions=(),
        ),
        stop_reason=None,
    )
    state_store = LocalPngToShaderV2StateStore(tmp_path / "states")
    state_store.initialize(state)
    operation_store = LocalRealModelOperationStore(tmp_path / "operations")
    return catalog, constraints, context, state, state_store, operation_store


def _interpretation(evidence_ref) -> VisualInterpretationV2:
    return VisualInterpretationV2(
        summary="模型判断主体由基础填色构成。",
        layer_hypotheses=(
            LayerHypothesis(
                layer_id="base",
                role="base_fill",
                order=0,
                confidence=0.9,
                region_description="主体内部",
                primitive_candidates=("ellipse_sdf",),
                evidence_refs=(evidence_ref,),
            ),
        ),
        required_layer_assessments=tuple(
            RequiredLayerAssessment(
                layer=layer,
                status="required" if layer == "base_fill" else "not_required",
                confidence=0.9,
                rationale="测试模型闭集判断。",
                evidence_refs=(evidence_ref,),
            )
            for layer in REQUIRED_LAYER_ORDER
        ),
        primitive_candidates=(
            PrimitiveCandidate(
                candidate_id="base-candidate",
                primitive_id="ellipse_sdf",
                layer_id="base",
                confidence=0.9,
                evidence_refs=(evidence_ref,),
            ),
        ),
        strategy_hypotheses=(
            StrategyHypothesis(
                strategy_id="minimal",
                template_ids=("geometry.ellipse_sdf.v0",),
                required_layer_ids=("base",),
                complexity="low",
                confidence=0.9,
                evidence_refs=(evidence_ref,),
            ),
        ),
        evidence_refs=(evidence_ref,),
    )


class _Gateway:
    def __init__(self, result: DurableGatewayResultV1) -> None:
        self.result = result
        self.calls = 0
        self.completed = False

    async def recover(self, invocation_id: str):
        return self.result if self.completed else None

    async def invoke_once(self, *, invocation_id: str, messages, options):
        del messages, options
        self.calls += 1
        self.completed = True
        self.result = self.result.model_copy(update={"invocation_id": invocation_id})
        return self.result


def _adapter(
    gateway: _Gateway, policy: RealModelCallPolicyV1 | None = None
) -> VisualInterpretationGatewayAdapter:
    return VisualInterpretationGatewayAdapter(
        gateway=gateway,
        prompt=PromptDefinition(
            name="analyze_visual_layers_v2",
            version="analyze_visual_layers_v2_test",
            prompt="只返回 VisualInterpretationV2 JSON。",
        ),
        policy=policy or _policy(),
    )


def _result(raw_response: str, **updates) -> DurableGatewayResultV1:
    values = {
        "invocation_id": "placeholder",
        "provider_receipt_id": "provider-receipt-1",
        "provider_id": "durable-fake",
        "requested_model_id": "fake:model-v1",
        "actual_model_id": "fake:model-v1",
        "raw_response": raw_response,
        "input_tokens": 40,
        "output_tokens": 30,
    }
    values.update(updates)
    return DurableGatewayResultV1(**values)


def test_real_adapter_success_and_repeat_resume_are_idempotent(tmp_path: Path) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-success"
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    adapter = _adapter(gateway)

    first = asyncio.run(
        execute_real_visual_interpretation(
            state_store=state_store,
            state=state,
            operation_store=operations,
            adapter=adapter,
            catalog=catalog,
            normalized_reference_png=b"png-fixture",
            measurements={"fixture": True},
            constraints=constraints,
            context=context,
        )
    )
    second = asyncio.run(
        execute_real_visual_interpretation(
            state_store=state_store,
            state=first[0],
            operation_store=operations,
            adapter=adapter,
            catalog=catalog,
            normalized_reference_png=b"png-fixture",
            measurements={"fixture": True},
            constraints=constraints,
            context=context,
        )
    )

    assert gateway.calls == 1
    assert second[1:] == first[1:]
    assert first[0].budget_state.used.model_calls == 1
    assert first[0].budget_state.used.model_tokens == 70
    assert first[0].budget_state.used.cost_usd_micros == 100
    assert first[1].audit_ref == first[2]
    assert first[0].budget_state.reserved == _zero()


@pytest.mark.parametrize(
    "fault_point",
    (
        "real_model.after_state_reserve_before_journal",
        "real_model.after_provider_before_materialize",
        "real_model.after_budget_commit_before_journal",
    ),
)
def test_model_operation_crash_recovers_without_second_call(
    tmp_path: Path, fault_point: str
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-crash"
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    adapter = _adapter(gateway)

    fired = False

    def crash(point: str) -> None:
        nonlocal fired
        if point == fault_point and not fired:
            fired = True
            raise RuntimeError("crash")

    with pytest.raises(RuntimeError, match="crash"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=adapter,
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
                fault_injector=crash,
            )
        )
    recovered_state = state_store.load_last_confirmed(state.run_id)
    asyncio.run(
        execute_real_visual_interpretation(
            state_store=state_store,
            state=recovered_state,
            operation_store=operations,
            adapter=adapter,
            catalog=catalog,
            normalized_reference_png=b"png-fixture",
            measurements={},
            constraints=constraints,
            context=context,
        )
    )
    assert gateway.calls == 1


def test_model_operation_request_digest_rejects_context_drift_before_call(
    tmp_path: Path,
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-request-digest"
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    adapter = _adapter(gateway)

    def crash(point: str) -> None:
        if point == "real_model.after_state_reserve_before_journal":
            raise RuntimeError("crash-before-call")

    with pytest.raises(RuntimeError, match="crash-before-call"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=adapter,
                catalog=catalog,
                normalized_reference_png=b"png-a",
                measurements={"fixture": "a"},
                constraints=constraints,
                context=context,
                fault_injector=crash,
            )
        )
    changed_context = context.model_copy(
        update={"allowed_template_ids": ("geometry.ellipse_sdf.v1",)}
    )
    with pytest.raises(RealModelIdentityError, match="identity"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state_store.load_last_confirmed(state.run_id),
                operation_store=operations,
                adapter=adapter,
                catalog=catalog,
                normalized_reference_png=b"png-a",
                measurements={"fixture": "a"},
                constraints=constraints,
                context=changed_context,
            )
        )
    assert gateway.calls == 0


def test_model_request_digest_covers_image_and_prompt_metadata(tmp_path: Path) -> None:
    catalog, constraints, context, state, _state_store, _operations = _setup(
        tmp_path, "real-request-projection"
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    first = _adapter(gateway)
    renamed_prompt = VisualInterpretationGatewayAdapter(
        gateway=gateway,
        prompt=PromptDefinition(
            name=first.prompt.name,
            version="changed-version",
            prompt=first.prompt.prompt,
        ),
        policy=first.policy,
    )
    messages_a = first.build_messages(
        normalized_reference_png=b"png-a",
        measurements={"fixture": True},
        constraints=constraints,
        context=context,
    )
    messages_b = first.build_messages(
        normalized_reference_png=b"png-b",
        measurements={"fixture": True},
        constraints=constraints,
        context=context,
    )
    assert first.request_sha256(messages_a) != first.request_sha256(messages_b)
    assert first.request_sha256(messages_a) != renamed_prompt.request_sha256(messages_a)


def test_budget_and_identity_fail_before_or_without_duplicate_provider_call(
    tmp_path: Path,
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-budget", model_tokens=10
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    with pytest.raises(ValueError, match="model_tokens"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=_adapter(gateway),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    assert gateway.calls == 0

    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-identity"
    )
    bad = _result(
        _interpretation(state.measurements_ref).model_dump_json(),
        actual_model_id="fake:wrong-model",
    )
    gateway = _Gateway(bad)
    with pytest.raises(RealModelCommittedFailure, match="receipt_invalid"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=_adapter(gateway),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    assert gateway.calls == 1
    failed = state_store.load_last_confirmed(state.run_id)
    assert failed.budget_state.reserved == _zero()
    assert failed.budget_state.used.model_calls == 1
    with pytest.raises(RealModelCommittedFailure, match="receipt_invalid"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=failed,
                operation_store=operations,
                adapter=_adapter(gateway),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    assert gateway.calls == 1


def test_parser_failure_is_audited_and_fail_closed(tmp_path: Path) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-parser"
    )
    gateway = _Gateway(_result('{"invalid":true}'))
    with pytest.raises(RealModelCommittedFailure, match="parse_failed"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=_adapter(gateway),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    assert gateway.calls == 1
    failed_state = state_store.load_last_confirmed(state.run_id)
    assert failed_state.budget_state.reserved == _zero()
    assert failed_state.budget_state.used.model_calls == 1
    with pytest.raises(RealModelCommittedFailure, match="parse_failed"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=failed_state,
                operation_store=operations,
                adapter=_adapter(gateway),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    assert gateway.calls == 1
    assert any(ref.kind == "visual_interpretation_call_audit" for ref in catalog.list_refs())


def test_output_oversize_is_rejected_before_any_model_artifact_write(
    tmp_path: Path,
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-oversize"
    )
    result = _result(_interpretation(state.measurements_ref).model_dump_json())
    gateway = _Gateway(result)
    policy = _policy().model_copy(update={"max_output_artifact_bytes": 64})
    before_refs = catalog.list_refs()

    with pytest.raises(RealModelCommittedFailure, match="output_budget_exceeded"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=_adapter(gateway, policy),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )

    assert catalog.list_refs() == before_refs
    operation = operations.load_optional(state.run_id)
    assert operation is not None
    assert operation.failure_status == "output_budget_exceeded"
    assert operation.failure_closure is not None
    assert operation.failure_closure.output_artifact_bytes == 0
    assert state_store.load_last_confirmed(state.run_id).budget_state.reserved == _zero()


class _IndeterminateGateway(_Gateway):
    async def invoke_once(self, *, invocation_id: str, messages, options):
        del invocation_id, messages, options
        self.calls += 1
        raise TimeoutError("provider result unknown")


def test_provider_indeterminate_is_conservatively_closed_without_retry(
    tmp_path: Path,
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-provider-indeterminate"
    )
    gateway = _IndeterminateGateway(_result("{}"))
    adapter = _adapter(gateway)
    for current in (state, state_store.load_last_confirmed(state.run_id)):
        with pytest.raises(RealModelCommittedFailure, match="provider_indeterminate"):
            asyncio.run(
                execute_real_visual_interpretation(
                    state_store=state_store,
                    state=current,
                    operation_store=operations,
                    adapter=adapter,
                    catalog=catalog,
                    normalized_reference_png=b"png-fixture",
                    measurements={},
                    constraints=constraints,
                    context=context,
                )
            )
    assert gateway.calls == 1
    failed = state_store.load_last_confirmed(state.run_id)
    assert failed.budget_state.reserved == _zero()
    assert failed.budget_state.used.model_tokens == 200
    operation = operations.load_optional(state.run_id)
    assert operation is not None and operation.failure_closure is not None
    assert operation.failure_closure.trusted_provider_receipt is False


def test_unauthorized_interpretation_evidence_is_typed_and_writes_nothing(
    tmp_path: Path,
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-evidence-invalid"
    )
    unauthorized = replace(
        state.measurements_ref,
        artifact_id="unregistered-evidence",
        sha256="f" * 64,
    )
    gateway = _Gateway(_result(_interpretation(unauthorized).model_dump_json()))
    before_refs = catalog.list_refs()
    with pytest.raises(
        RealModelCommittedFailure, match="interpretation_validation_failed"
    ):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=_adapter(gateway),
                catalog=catalog,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    assert catalog.list_refs() == before_refs
    assert gateway.calls == 1


def test_real_model_operation_journal_tamper_is_rejected(tmp_path: Path) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-journal-tamper"
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    asyncio.run(
        execute_real_visual_interpretation(
            state_store=state_store,
            state=state,
            operation_store=operations,
            adapter=_adapter(gateway),
            catalog=catalog,
            normalized_reference_png=b"png-fixture",
            measurements={},
            constraints=constraints,
            context=context,
        )
    )
    journal_path = next((tmp_path / "operations").glob("*.json"))
    envelope = json.loads(journal_path.read_text())
    envelope["payload"] = envelope["payload"].replace("committed", "reserved", 1)
    journal_path.write_text(json.dumps(envelope))
    with pytest.raises(RuntimeError, match="完整性"):
        operations.load_optional(state.run_id)


@pytest.mark.parametrize(
    "payload_mutation",
    ("inner_duplicate", "inner_non_finite", "outer_duplicate", "outer_non_finite"),
)
def test_real_model_operation_rejects_strict_inner_payload_json(
    tmp_path: Path,
    payload_mutation: str,
) -> None:
    run_id = f"real-operation-strict-{payload_mutation}"
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, run_id
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    asyncio.run(
        execute_real_visual_interpretation(
            state_store=state_store,
            state=state,
            operation_store=operations,
            adapter=_adapter(gateway),
            catalog=catalog,
            normalized_reference_png=b"png-fixture",
            measurements={},
            constraints=constraints,
            context=context,
        )
    )
    path = next((tmp_path / "operations").glob("*.json"))
    envelope = json.loads(path.read_text())
    payload = envelope["payload"]
    if payload_mutation == "inner_duplicate":
        payload = payload.replace(
            f'"run_id":"{run_id}"',
            f'"run_id":"shadow","run_id":"{run_id}"',
            1,
        )
    elif payload_mutation == "inner_non_finite":
        payload = payload.replace('"revision":3', '"revision":NaN', 1)
    if payload_mutation == "outer_duplicate":
        digest = sha256(payload.encode()).hexdigest()
        path.write_text(
            "{" + f'"sha256":"{digest}","sha256":"{digest}",' +
            f'"payload":{json.dumps(payload)}' + "}"
        )
    elif payload_mutation == "outer_non_finite":
        path.write_text("{" + '"sha256":NaN,' + f'"payload":{json.dumps(payload)}' + "}")
    else:
        path.write_text(
            json.dumps(
                {"sha256": sha256(payload.encode()).hexdigest(), "payload": payload}
            )
        )
    with pytest.raises(RuntimeError, match="完整性"):
        operations.load_optional(run_id)


class _CrashAfterCatalogPut:
    def __init__(self, catalog: LocalArtifactCatalog, *, crash_kind: str) -> None:
        self._catalog = catalog
        self._crash_kind = crash_kind
        self._fired = False

    def put(self, **kwargs):
        ref = self._catalog.put(**kwargs)
        if kwargs["kind"] == self._crash_kind and not self._fired:
            self._fired = True
            raise OSError("materialization-put-crash")
        return ref

    def resolve(self, artifact_id: str):
        return self._catalog.resolve(artifact_id)

    def read_bytes(self, artifact_id: str):
        return self._catalog.read_bytes(artifact_id)

    def list_refs(self):
        return self._catalog.list_refs()

    def total_size_bytes(self):
        return self._catalog.total_size_bytes()


def test_partial_materialization_put_recovers_from_operation_baseline(
    tmp_path: Path,
) -> None:
    catalog, constraints, context, state, state_store, operations = _setup(
        tmp_path, "real-partial-materialization"
    )
    gateway = _Gateway(_result(_interpretation(state.measurements_ref).model_dump_json()))
    adapter = _adapter(gateway)
    crashing = _CrashAfterCatalogPut(
        catalog, crash_kind="visual_interpretation_raw_response"
    )
    with pytest.raises(OSError, match="materialization-put-crash"):
        asyncio.run(
            execute_real_visual_interpretation(
                state_store=state_store,
                state=state,
                operation_store=operations,
                adapter=adapter,
                catalog=crashing,
                normalized_reference_png=b"png-fixture",
                measurements={},
                constraints=constraints,
                context=context,
            )
        )
    recovered = asyncio.run(
        execute_real_visual_interpretation(
            state_store=state_store,
            state=state_store.load_last_confirmed(state.run_id),
            operation_store=operations,
            adapter=adapter,
            catalog=catalog,
            normalized_reference_png=b"png-fixture",
            measurements={},
            constraints=constraints,
            context=context,
        )
    )
    assert gateway.calls == 1
    assert recovered[0].budget_state.reserved == _zero()
    assert recovered[1].output_artifact_bytes == (
        catalog.total_size_bytes()
        - operations.load_optional(state.run_id).pre_catalog_bytes
    )
