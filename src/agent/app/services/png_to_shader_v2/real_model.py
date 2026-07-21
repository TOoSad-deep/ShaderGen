"""V2 VisualInterpretation 真实模型的可恢复、显式预算边界。"""
# ruff: noqa: D102, D107, D415

from __future__ import annotations

import fcntl
import json
import os
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from typing import Iterator, Literal, NoReturn, Protocol
from uuid import uuid4

from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import Field, model_validator

from agent.app.contracts.llm import LLMCallOptions, TokenUsage
from agent.app.messages.png_to_shader_v1 import (
    labeled_image_parts,
    multimodal_human_message,
    text_part,
)
from agent.app.prompts.prompt_loader import PromptDefinition
from agent.app.states.png_to_shader_v2_state import BudgetVectorV2, PngToShaderV2State
from agent.app.states.png_to_shader_v2_state_store import LocalPngToShaderV2StateStore
from shaderforge.contracts import (
    FrozenModel,
    NonEmptyString,
    Sha256Hex,
    canonical_sha256,
)
from shaderforge.intent import (
    IntentBuildContext,
    RequestConstraintSet,
    VisualInterpretationParseError,
    VisualInterpretationV2,
    materialize_visual_interpretation_call,
    parse_visual_interpretation_v2,
)
from shaderforge.store import ArtifactCatalog, ArtifactRefV2

from .model_receipts import ModelCallReceiptV1, ModelCallReservationV1


class RealModelCallPolicyV1(FrozenModel):
    """一次 V2 Interpretation 调用的冻结身份、价格和硬上限。"""

    schema_version: Literal["png_to_shader_v2_real_model_call_policy_v1"] = (
        "png_to_shader_v2_real_model_call_policy_v1"
    )
    provider_id: NonEmptyString
    model_id: NonEmptyString
    pricing_policy_id: NonEmptyString
    input_micros_per_million_tokens: int = Field(ge=0)
    output_micros_per_million_tokens: int = Field(ge=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_cost_usd_micros: int = Field(gt=0)
    max_output_artifact_bytes: int = Field(gt=0)

    @property
    def pricing_policy_sha256(self) -> str:
        return canonical_sha256(
            {
                "pricing_policy_id": self.pricing_policy_id,
                "input_micros_per_million_tokens": self.input_micros_per_million_tokens,
                "output_micros_per_million_tokens": self.output_micros_per_million_tokens,
            }
        )

    def cost_usd_micros(self, usage: TokenUsage) -> int:
        if usage.input_tokens is None or usage.output_tokens is None:
            raise ValueError("真实模型响应必须提供 input/output token receipt。")
        numerator = (
            usage.input_tokens * self.input_micros_per_million_tokens
            + usage.output_tokens * self.output_micros_per_million_tokens
        )
        return (numerator + 999_999) // 1_000_000


class DurableGatewayResultV1(FrozenModel):
    """provider 以稳定 operation id 可恢复的响应 receipt。"""

    schema_version: Literal["png_to_shader_v2_durable_gateway_result_v1"] = (
        "png_to_shader_v2_durable_gateway_result_v1"
    )
    invocation_id: NonEmptyString
    provider_receipt_id: NonEmptyString
    provider_id: NonEmptyString
    requested_model_id: NonEmptyString
    actual_model_id: NonEmptyString
    raw_response: NonEmptyString
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class DurableLLMGateway(Protocol):
    """必须支持稳定 invocation_id 去重与事后恢复的外部模型边界。"""

    async def recover(self, invocation_id: str) -> DurableGatewayResultV1 | None:
        """按 provider 持久 operation id 恢复已完成结果。"""

    async def invoke_once(
        self,
        *,
        invocation_id: str,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> DurableGatewayResultV1:
        """相同 invocation_id 必须去重，绝不能产生第二次计费调用。"""


class NonDurableLLMGatewayError(RuntimeError):
    """普通 LLMGateway 没有 recover/dedupe 协议，严格模式拒绝调用。"""


class RealModelIdentityError(RuntimeError):
    """provider/model/prompt/pricing/input identity 发生错绑。"""


class RealModelOperationIncomplete(RuntimeError):
    """持久 operation/State 对账不完整或被并发修改，fail closed。"""


class RealModelCommittedFailure(RuntimeError):
    """已结算且不会自动重调的 typed 模型失败。"""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"真实模型 operation 已结算失败：{status}。")


RealModelFailureStatus = Literal[
    "parse_failed",
    "output_budget_exceeded",
    "provider_indeterminate",
    "receipt_invalid",
    "interpretation_validation_failed",
]


class RealModelFailureClosureV1(FrozenModel):
    """不依赖 raw response 持久化的、不会自动重调的模型失败闭包。"""

    schema_version: Literal["png_to_shader_v2_real_model_failure_closure_v1"] = (
        "png_to_shader_v2_real_model_failure_closure_v1"
    )
    status: RealModelFailureStatus
    invocation_id: NonEmptyString
    trusted_provider_receipt: bool
    provider_receipt_id: NonEmptyString | None = None
    response_sha256: Sha256Hex | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd_micros: int = Field(ge=0)
    output_artifact_bytes: int = Field(ge=0)

    @model_validator(mode="after")
    def _validate_receipt(self) -> RealModelFailureClosureV1:
        if self.trusted_provider_receipt and (
            self.provider_receipt_id is None or self.response_sha256 is None
        ):
            raise ValueError("可信失败 closure 必须绑定 provider receipt 与 response hash。")
        if not self.trusted_provider_receipt and any(
            value is not None
            for value in (self.provider_receipt_id, self.response_sha256)
        ):
            raise ValueError("不可信 provider 结果不得伪造 receipt identity。")
        return self


class VisualInterpretationGatewayAdapter:
    """把 durable gateway 响应限制为 VisualInterpretationV2。"""

    def __init__(
        self,
        *,
        gateway: DurableLLMGateway,
        prompt: PromptDefinition,
        policy: RealModelCallPolicyV1,
    ) -> None:
        if not callable(getattr(gateway, "recover", None)) or not callable(
            getattr(gateway, "invoke_once", None)
        ):
            raise NonDurableLLMGatewayError(
                "真实模型 gateway 必须实现 durable recover/invoke_once 协议。"
            )
        self._gateway = gateway
        self.prompt = prompt
        self.policy = policy
        self.prompt_sha256 = sha256(prompt.prompt.encode("utf-8")).hexdigest()

    def build_messages(
        self,
        *,
        normalized_reference_png: bytes,
        measurements: object,
        constraints: RequestConstraintSet,
        context: IntentBuildContext,
    ) -> tuple[BaseMessage, ...]:
        authorized = tuple(
            ref.model_dump(mode="json")
            if hasattr(ref, "model_dump")
            else {
                "artifact_id": ref.artifact_id,
                "sha256": ref.sha256,
                "kind": ref.kind,
                "schema_version": ref.schema_version,
                "content_type": ref.content_type,
                "size_bytes": ref.size_bytes,
            }
            for ref in context.allowed_interpretation_evidence_refs
        )
        parts = [
            text_part("target_measurements", measurements),
            text_part("request_constraint_set", constraints),
            text_part("render_contract", {"contract_id": context.contract_id}),
            text_part("allowed_primitive_ids", context.allowed_primitive_ids),
            text_part("allowed_template_ids", context.allowed_template_ids),
            text_part("authorized_evidence_refs", authorized),
            text_part(
                "visual_interpretation_output_schema",
                VisualInterpretationV2.model_json_schema(mode="validation"),
            ),
            *labeled_image_parts(
                "normalized_reference_image", normalized_reference_png, "image/png"
            ),
        ]
        return (
            SystemMessage(content=self.prompt.prompt),
            multimodal_human_message(parts),
        )

    def call_options(self) -> LLMCallOptions:
        """返回进入 request identity 与 gateway 的同一冻结调用参数。"""
        return LLMCallOptions(
            model_ref=self.policy.model_id,
            temperature=0,
            thinking="off",
            capture_reasoning=False,
            response_format="json_object",
            max_output_tokens=self.policy.max_output_tokens,
        )

    def request_sha256(self, messages: Sequence[BaseMessage]) -> str:
        """冻结完整 messages、Prompt metadata、模型选项与 schema 身份。"""
        return canonical_sha256(
            {
                "schema_version": "png_to_shader_v2_real_model_request_v1",
                "provider_id": self.policy.provider_id,
                "prompt": {
                    "name": self.prompt.name,
                    "version": self.prompt.version,
                    "text": self.prompt.prompt,
                },
                "messages": tuple(message.model_dump(mode="python") for message in messages),
                "options": self.call_options(),
                "visual_interpretation_schema": VisualInterpretationV2.model_json_schema(
                    mode="validation"
                ),
            }
        )

    async def recover_or_invoke(
        self,
        *,
        invocation_id: str,
        messages: Sequence[BaseMessage],
    ) -> DurableGatewayResultV1:
        recovered = await self._gateway.recover(invocation_id)
        if recovered is not None:
            return self._validate_result(invocation_id, recovered)
        result = await self._gateway.invoke_once(
            invocation_id=invocation_id,
            messages=messages,
            options=self.call_options(),
        )
        return self._validate_result(invocation_id, result)

    def _validate_result(
        self, invocation_id: str, result: DurableGatewayResultV1
    ) -> DurableGatewayResultV1:
        if result.invocation_id != invocation_id:
            raise RealModelIdentityError("provider result invocation_id 错绑。")
        if result.provider_id != self.policy.provider_id:
            raise RealModelIdentityError("provider result provider_id 错绑。")
        if result.requested_model_id != self.policy.model_id:
            raise RealModelIdentityError("provider result requested model 错绑。")
        if result.actual_model_id != self.policy.model_id:
            raise RealModelIdentityError("provider 未返回冻结的实际 model identity。")
        if result.input_tokens > self.policy.max_input_tokens:
            raise RealModelIdentityError(
                "provider input token receipt 超 reservation。"
            )
        if result.output_tokens > self.policy.max_output_tokens:
            raise RealModelIdentityError(
                "provider output token receipt 超 reservation。"
            )
        return result


class RealModelOperationJournalV1(FrozenModel):
    """独立 operation journal；不保存 raw response，只保存可恢复身份/ref。"""

    schema_version: Literal["png_to_shader_v2_real_model_operation_v2"] = (
        "png_to_shader_v2_real_model_operation_v2"
    )
    run_id: NonEmptyString
    revision: int = Field(ge=0)
    phase: Literal["prepared", "reserved", "materialized", "committed"]
    invocation_id: NonEmptyString
    binding_sha256: Sha256Hex
    reservation: ModelCallReservationV1
    pre_budget_used: BudgetVectorV2
    pre_catalog_bytes: int = Field(ge=0)
    receipt: ModelCallReceiptV1 | None = None
    audit_ref: ArtifactRefV2 | None = None
    failure_status: RealModelFailureStatus | None = None
    failure_closure: RealModelFailureClosureV1 | None = None
    failure_budget: BudgetVectorV2 | None = None

    @model_validator(mode="after")
    def _validate_phase(self) -> RealModelOperationJournalV1:
        completed = self.receipt is not None or (
            self.failure_status is not None
            and self.failure_closure is not None
            and self.failure_budget is not None
        )
        if self.phase in {"materialized", "committed"} and not completed:
            raise ValueError(
                "materialized/committed operation 必须绑定 receipt/audit。"
            )
        if self.phase in {"prepared", "reserved"} and completed:
            raise ValueError("未 materialize operation 不得提前绑定 receipt/audit。")
        if self.receipt is not None and (
            self.failure_status is not None
            or self.failure_closure is not None
            or self.failure_budget is not None
        ):
            raise ValueError("成功 receipt 与失败 closure 不得同时存在。")
        if self.receipt is not None and self.audit_ref is None:
            raise ValueError("成功 receipt 必须绑定审计 Artifact。")
        if self.failure_closure is not None:
            if self.failure_budget is None or (
                self.failure_closure.status != self.failure_status
                or self.failure_closure.output_artifact_bytes
                != self.failure_budget.artifact_bytes
            ):
                raise ValueError("失败 closure 与 operation budget/status 不一致。")
        return self


class LocalRealModelOperationStore:
    """单机 flock/fsync/atomic-replace operation journal。"""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def load_optional(self, run_id: str) -> RealModelOperationJournalV1 | None:
        path, lock = self._paths(run_id)
        with self._lock(lock):
            if not path.exists():
                return None
            return self._read(path, run_id)

    def initialize(self, value: RealModelOperationJournalV1) -> None:
        path, lock = self._paths(value.run_id)
        with self._lock(lock):
            if path.exists():
                raise RuntimeError("real model operation 已存在。")
            self._persist(path, value)

    def replace(
        self, current: RealModelOperationJournalV1, value: RealModelOperationJournalV1
    ) -> None:
        path, lock = self._paths(current.run_id)
        with self._lock(lock):
            loaded = self._read(path, current.run_id)
            if loaded != current or value.revision != current.revision + 1:
                raise RuntimeError("real model operation revision 冲突。")
            self._persist(path, value)

    def _paths(self, run_id: str) -> tuple[Path, Path]:
        identity = sha256(run_id.encode()).hexdigest()
        return self._root / f"{identity}.json", self._root / f"{identity}.lock"

    @contextmanager
    def _lock(self, path: Path) -> Iterator[None]:
        with path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _persist(self, path: Path, value: RealModelOperationJournalV1) -> None:
        payload = value.model_dump_json().encode()
        envelope = json.dumps(
            {"sha256": sha256(payload).hexdigest(), "payload": payload.decode()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(envelope)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory = os.open(self._root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _read(path: Path, run_id: str) -> RealModelOperationJournalV1:
        try:
            envelope = json.loads(
                path.read_bytes(),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"非法 JSON 常量：{value}")
                ),
            )
            if not isinstance(envelope, dict) or set(envelope) != {"sha256", "payload"}:
                raise ValueError("envelope fields")
            payload = envelope["payload"]
            digest = envelope["sha256"]
            if (
                not isinstance(payload, str)
                or not isinstance(digest, str)
                or sha256(payload.encode()).hexdigest() != digest
            ):
                raise ValueError("SHA mismatch")
            json.loads(
                payload,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_non_finite_constant,
            )
            value = RealModelOperationJournalV1.model_validate_json(
                payload, strict=True
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise RuntimeError("real model operation 完整性校验失败。") from exc
        if value.run_id != run_id:
            raise RuntimeError("real model operation run_id 错绑。")
        return value


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"real model operation 包含重复 JSON key：{key}。")
        value[key] = item
    return value


def _reject_non_finite_constant(value: str) -> NoReturn:
    raise ValueError(f"real model operation 包含非法 JSON 常量：{value}。")


def build_real_model_reservation(
    *,
    run_id: str,
    policy: RealModelCallPolicyV1,
    prompt_sha256: str,
    request_sha256: str,
    measurements_ref: ArtifactRefV2,
    constraint_set_ref: ArtifactRefV2,
) -> ModelCallReservationV1:
    """从冻结调用策略和输入 refs 构造稳定 invocation reservation。"""
    binding = canonical_sha256(
        {
            "run_id": run_id,
            "provider_id": policy.provider_id,
            "model_id": policy.model_id,
            "prompt_sha256": prompt_sha256,
            "request_sha256": request_sha256,
            "pricing_policy_sha256": policy.pricing_policy_sha256,
            "measurements_ref": measurements_ref,
            "constraint_set_ref": constraint_set_ref,
        }
    )
    return ModelCallReservationV1(
        invocation_id=f"v2vi-{binding}",
        provider_id=policy.provider_id,
        model_id=policy.model_id,
        prompt_sha256=prompt_sha256,
        request_sha256=request_sha256,
        pricing_policy_sha256=policy.pricing_policy_sha256,
        measurements_ref=measurements_ref,
        constraint_set_ref=constraint_set_ref,
        max_input_tokens=policy.max_input_tokens,
        max_output_tokens=policy.max_output_tokens,
        max_cost_usd_micros=policy.max_cost_usd_micros,
        max_output_artifact_bytes=policy.max_output_artifact_bytes,
    )


async def execute_real_visual_interpretation(
    *,
    state_store: LocalPngToShaderV2StateStore,
    state: PngToShaderV2State,
    operation_store: LocalRealModelOperationStore,
    adapter: VisualInterpretationGatewayAdapter,
    catalog: ArtifactCatalog,
    normalized_reference_png: bytes,
    measurements: object,
    constraints: RequestConstraintSet,
    context: IntentBuildContext,
    fault_injector: Callable[[str], None] = lambda _point: None,
) -> tuple[PngToShaderV2State, ModelCallReceiptV1, ArtifactRefV2]:
    """执行或恢复一次真实 Interpretation；provider 必须支持 operation 恢复。"""
    messages = adapter.build_messages(
        normalized_reference_png=normalized_reference_png,
        measurements=measurements,
        constraints=constraints,
        context=context,
    )
    reservation = build_real_model_reservation(
        run_id=state.run_id,
        policy=adapter.policy,
        prompt_sha256=adapter.prompt_sha256,
        request_sha256=adapter.request_sha256(messages),
        measurements_ref=state.measurements_ref,
        constraint_set_ref=state.request_constraint_set_ref,
    )
    binding_sha = canonical_sha256(reservation.model_dump(mode="json"))
    operation = operation_store.load_optional(state.run_id)
    if operation is None:
        operation = RealModelOperationJournalV1(
            run_id=state.run_id,
            revision=0,
            phase="prepared",
            invocation_id=reservation.invocation_id,
            binding_sha256=binding_sha,
            reservation=reservation,
            pre_budget_used=state.budget_state.used,
            pre_catalog_bytes=catalog.total_size_bytes(),
        )
        operation_store.initialize(operation)
    elif (
        operation.binding_sha256 != binding_sha or operation.reservation != reservation
    ):
        raise RealModelIdentityError("real model operation 与当前输入 identity 错绑。")
    if operation.phase == "committed":
        current = state_store.load_last_confirmed(state.run_id)
        if any(
            getattr(current.budget_state.reserved, field)
            for field in ("model_calls", "model_tokens", "cost_usd_micros")
        ):
            raise RealModelOperationIncomplete(
                "committed model operation 的 State 仍含模型 reservation。"
            )
        if operation.failure_status is not None:
            raise RealModelCommittedFailure(operation.failure_status)
        assert operation.receipt is not None and operation.audit_ref is not None
        return current, operation.receipt, operation.audit_ref

    current = state_store.load_last_confirmed(state.run_id)
    maximum = reservation.budget_vector
    if operation.phase == "prepared":
        if current.budget_state.reserved == maximum:
            reserved = current
        elif current.budget_state.reserved == _zero_budget():
            reserved = state_store.reserve_budget(
                state.run_id,
                maximum,
                expected_budget_revision=current.budget_state.revision,
            )
            fault_injector("real_model.after_state_reserve_before_journal")
        else:
            raise RealModelOperationIncomplete(
                "prepared operation 对应未知 State reservation；禁止调用。"
            )
        updated = operation.model_copy(
            update={"revision": operation.revision + 1, "phase": "reserved"}
        )
        operation_store.replace(operation, updated)
        operation = updated
        current = reserved
    elif operation.phase == "reserved" and current.budget_state.reserved != maximum:
        raise RealModelOperationIncomplete(
            "operation 已 reserved，但 State reservation 不完整；禁止重复调用。"
        )

    if operation.phase == "materialized":
        if operation.failure_status is not None:
            assert operation.failure_budget is not None
            actual = operation.failure_budget
            receipt = None
        else:
            assert operation.receipt is not None
            receipt = operation.receipt
            actual = receipt.budget_vector
    else:
        try:
            result = await adapter.recover_or_invoke(
                invocation_id=reservation.invocation_id,
                messages=messages,
            )
        except RealModelIdentityError:
            actual = _untrusted_failure_budget(reservation)
            closure = _failure_closure(
                reservation=reservation,
                status="receipt_invalid",
                actual=actual,
            )
            updated = operation.model_copy(
                update={
                    "revision": operation.revision + 1,
                    "phase": "materialized",
                    "failure_status": closure.status,
                    "failure_closure": closure,
                    "failure_budget": actual,
                }
            )
            operation_store.replace(operation, updated)
            operation = updated
            receipt = None
        except Exception:
            # durable gateway 的 recover/invoke_once 无法给出可信 receipt 时，禁止
            # 自动二次调用；按 reservation 的 token/cost 最坏值闭合。
            actual = _untrusted_failure_budget(reservation)
            closure = _failure_closure(
                reservation=reservation,
                status="provider_indeterminate",
                actual=actual,
            )
            updated = operation.model_copy(
                update={
                    "revision": operation.revision + 1,
                    "phase": "materialized",
                    "failure_status": closure.status,
                    "failure_closure": closure,
                    "failure_budget": actual,
                }
            )
            operation_store.replace(operation, updated)
            operation = updated
            receipt = None
        else:
            fault_injector("real_model.after_provider_before_materialize")
            usage = TokenUsage(
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                total_tokens=result.input_tokens + result.output_tokens,
            )
            try:
                cost = adapter.policy.cost_usd_micros(usage)
            except ValueError:
                cost = reservation.max_cost_usd_micros
            if cost > reservation.max_cost_usd_micros:
                actual = _untrusted_failure_budget(reservation)
                closure = _failure_closure(
                    reservation=reservation,
                    status="receipt_invalid",
                    actual=actual,
                )
                updated = operation.model_copy(
                    update={
                        "revision": operation.revision + 1,
                        "phase": "materialized",
                        "failure_status": closure.status,
                        "failure_closure": closure,
                        "failure_budget": actual,
                    }
                )
                receipt = None
            else:
                updated, receipt, actual = _materialize_trusted_result(
                    operation=operation,
                    reservation=reservation,
                    adapter=adapter,
                    result=result,
                    state=state,
                    context=context,
                    catalog=catalog,
                    cost_usd_micros=cost,
                )
            operation_store.replace(operation, updated)
            operation = updated
        fault_injector("real_model.after_materialize_before_budget_commit")

    current = state_store.load_last_confirmed(state.run_id)
    if (
        current.budget_state.reserved == _zero_budget()
        and current.budget_state.used == _add_budget(operation.pre_budget_used, actual)
    ):
        updated = operation.model_copy(
            update={"revision": operation.revision + 1, "phase": "committed"}
        )
        operation_store.replace(operation, updated)
        if updated.failure_status is not None:
            raise RealModelCommittedFailure(updated.failure_status)
        assert receipt is not None and updated.audit_ref is not None
        return current, receipt, updated.audit_ref
    if current.budget_state.reserved != maximum:
        raise RealModelOperationIncomplete("模型 budget reservation 被并发修改。")
    committed = state_store.commit_budget(
        state.run_id,
        reservation=maximum,
        used=actual,
        expected_budget_revision=current.budget_state.revision,
    )
    fault_injector("real_model.after_budget_commit_before_journal")
    updated = operation.model_copy(
        update={"revision": operation.revision + 1, "phase": "committed"}
    )
    operation_store.replace(operation, updated)
    if updated.failure_status is not None:
        raise RealModelCommittedFailure(updated.failure_status)
    assert receipt is not None and updated.audit_ref is not None
    return committed, receipt, updated.audit_ref


def _materialize_trusted_result(
    *,
    operation: RealModelOperationJournalV1,
    reservation: ModelCallReservationV1,
    adapter: VisualInterpretationGatewayAdapter,
    result: DurableGatewayResultV1,
    state: PngToShaderV2State,
    context: IntentBuildContext,
    catalog: ArtifactCatalog,
    cost_usd_micros: int,
) -> tuple[RealModelOperationJournalV1, ModelCallReceiptV1 | None, BudgetVectorV2]:
    """先在纯内存 Catalog 构造全部 canonical payload，再允许真实写入。"""
    inputs = _unique_input_refs(state, context)
    parser_status: Literal["succeeded", "failed"]
    parser_error_code: str | None
    interpretation: VisualInterpretationV2 | None
    failure_status: RealModelFailureStatus | None = None
    try:
        interpretation = parse_visual_interpretation_v2(result.raw_response)
    except VisualInterpretationParseError:
        interpretation = None
        parser_status = "failed"
        parser_error_code = "visual_interpretation_parse_failed"
        failure_status = "parse_failed"
    else:
        parser_status = "succeeded"
        parser_error_code = None

    preflight = _PreflightArtifactCatalog(catalog, run_id=state.run_id)
    try:
        planned = materialize_visual_interpretation_call(
            catalog=preflight,
            run_id=state.run_id,
            prompt_name=adapter.prompt.name,
            prompt_version=adapter.prompt.version,
            prompt_text=adapter.prompt.prompt,
            model_id=result.actual_model_id,
            input_artifact_refs=inputs,
            raw_response=result.raw_response,
            attempt_count=1,
            repair_count=0,
            parser_status=parser_status,
            interpretation=interpretation,
            parser_error_code=parser_error_code,
        )
    except ValueError:
        actual = _trusted_failure_budget(result, cost_usd_micros, artifact_bytes=0)
        closure = _failure_closure(
            reservation=reservation,
            status="interpretation_validation_failed",
            actual=actual,
            result=result,
        )
        return (
            operation.model_copy(
                update={
                    "revision": operation.revision + 1,
                    "phase": "materialized",
                    "failure_status": closure.status,
                    "failure_closure": closure,
                    "failure_budget": actual,
                }
            ),
            None,
            actual,
        )
    planned_bytes = preflight.total_size_bytes() - operation.pre_catalog_bytes
    if planned_bytes < 0:
        raise RealModelIdentityError("Catalog bytes 小于模型 operation 冻结起点。")
    if planned_bytes > reservation.max_output_artifact_bytes:
        actual = _trusted_failure_budget(result, cost_usd_micros, artifact_bytes=0)
        closure = _failure_closure(
            reservation=reservation,
            status="output_budget_exceeded",
            actual=actual,
            result=result,
        )
        return (
            operation.model_copy(
                update={
                    "revision": operation.revision + 1,
                    "phase": "materialized",
                    "failure_status": closure.status,
                    "failure_closure": closure,
                    "failure_budget": actual,
                }
            ),
            None,
            actual,
        )

    bundle = materialize_visual_interpretation_call(
        catalog=catalog,
        run_id=state.run_id,
        prompt_name=adapter.prompt.name,
        prompt_version=adapter.prompt.version,
        prompt_text=adapter.prompt.prompt,
        model_id=result.actual_model_id,
        input_artifact_refs=inputs,
        raw_response=result.raw_response,
        attempt_count=1,
        repair_count=0,
        parser_status=parser_status,
        interpretation=interpretation,
        parser_error_code=parser_error_code,
    )
    if bundle.audit_ref != planned.audit_ref:
        raise RealModelIdentityError("preflight 与真实 materialization identity 不一致。")
    output_bytes = catalog.total_size_bytes() - operation.pre_catalog_bytes
    if output_bytes != planned_bytes or output_bytes > reservation.max_output_artifact_bytes:
        raise RealModelIdentityError("Catalog 实际去重 delta 与 preflight 不一致。")
    actual = _trusted_failure_budget(
        result, cost_usd_micros, artifact_bytes=output_bytes
    )
    if failure_status is not None:
        closure = _failure_closure(
            reservation=reservation,
            status=failure_status,
            actual=actual,
            result=result,
        )
        return (
            operation.model_copy(
                update={
                    "revision": operation.revision + 1,
                    "phase": "materialized",
                    "audit_ref": bundle.audit_ref,
                    "failure_status": closure.status,
                    "failure_closure": closure,
                    "failure_budget": actual,
                }
            ),
            None,
            actual,
        )
    assert bundle.audit.visual_interpretation_ref is not None
    receipt = ModelCallReceiptV1(
        invocation_id=reservation.invocation_id,
        provider_receipt_id=result.provider_receipt_id,
        provider_id=result.provider_id,
        model_id=result.actual_model_id,
        prompt_sha256=reservation.prompt_sha256,
        request_sha256=reservation.request_sha256,
        pricing_policy_sha256=reservation.pricing_policy_sha256,
        measurements_ref=reservation.measurements_ref,
        constraint_set_ref=reservation.constraint_set_ref,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cost_usd_micros=cost_usd_micros,
        interpretation_ref=bundle.audit.visual_interpretation_ref,
        output_artifact_bytes=output_bytes,
        audit_ref=bundle.audit_ref,
    )
    return (
        operation.model_copy(
            update={
                "revision": operation.revision + 1,
                "phase": "materialized",
                "receipt": receipt,
                "audit_ref": bundle.audit_ref,
            }
        ),
        receipt,
        receipt.budget_vector,
    )


class _PreflightArtifactCatalog:
    """只在内存中计算 LocalArtifactCatalog 的稳定 ref 与去重增量。"""

    def __init__(self, source: ArtifactCatalog, *, run_id: str) -> None:
        self._source = source
        self._run_id = run_id
        self._planned: dict[str, tuple[ArtifactRefV2, bytes]] = {}
        self._existing_ids = {ref.artifact_id for ref in source.list_refs()}

    @property
    def new_bytes(self) -> int:
        return sum(
            ref.size_bytes
            for artifact_id, (ref, _payload) in self._planned.items()
            if artifact_id not in self._existing_ids
        )

    def put(
        self,
        *,
        run_id: str,
        kind: str,
        schema_version: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactRefV2:
        if run_id != self._run_id or not isinstance(data, bytes):
            raise ValueError("preflight Catalog run/data identity 无效。")
        content_sha = sha256(data).hexdigest()
        identity = json.dumps(
            {
                "content_type": content_type,
                "kind": kind,
                "run_id": run_id,
                "schema_version": schema_version,
                "sha256": content_sha,
                "size_bytes": len(data),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        ref = ArtifactRefV2(
            artifact_id=f"art_{sha256(identity).hexdigest()}",
            sha256=content_sha,
            kind=kind,
            schema_version=schema_version,
            content_type=content_type,
            size_bytes=len(data),
        )
        if ref.artifact_id in self._existing_ids:
            existing = self._source.resolve(ref.artifact_id)
            if existing != ref or self._source.read_bytes(ref.artifact_id) != data:
                raise RealModelIdentityError("preflight 命中既有 Artifact identity 冲突。")
            return existing
        existing_plan = self._planned.get(ref.artifact_id)
        if existing_plan is not None and existing_plan != (ref, data):
            raise RealModelIdentityError("preflight Artifact identity 冲突。")
        self._planned[ref.artifact_id] = (ref, data)
        return ref

    def resolve(self, artifact_id: str) -> ArtifactRefV2:
        planned = self._planned.get(artifact_id)
        if planned is not None:
            return planned[0]
        return self._source.resolve(artifact_id)

    def read_bytes(self, artifact_id: str) -> bytes:
        planned = self._planned.get(artifact_id)
        if planned is not None:
            return planned[1]
        return self._source.read_bytes(artifact_id)

    def list_refs(self) -> tuple[ArtifactRefV2, ...]:
        refs = {ref.artifact_id: ref for ref in self._source.list_refs()}
        refs.update({key: value[0] for key, value in self._planned.items()})
        return tuple(refs[key] for key in sorted(refs))

    def total_size_bytes(self) -> int:
        return sum(ref.size_bytes for ref in self.list_refs())


def _trusted_failure_budget(
    result: DurableGatewayResultV1,
    cost_usd_micros: int,
    *,
    artifact_bytes: int,
) -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=1,
        model_tokens=result.input_tokens + result.output_tokens,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=artifact_bytes,
        cost_usd_micros=cost_usd_micros,
    )


def _untrusted_failure_budget(
    reservation: ModelCallReservationV1,
) -> BudgetVectorV2:
    return reservation.budget_vector.model_copy(update={"artifact_bytes": 0})


def _failure_closure(
    *,
    reservation: ModelCallReservationV1,
    status: RealModelFailureStatus,
    actual: BudgetVectorV2,
    result: DurableGatewayResultV1 | None = None,
) -> RealModelFailureClosureV1:
    trusted = result is not None
    return RealModelFailureClosureV1(
        status=status,
        invocation_id=reservation.invocation_id,
        trusted_provider_receipt=trusted,
        provider_receipt_id=(result.provider_receipt_id if result is not None else None),
        response_sha256=(
            sha256(result.raw_response.encode("utf-8")).hexdigest()
            if result is not None
            else None
        ),
        input_tokens=(result.input_tokens if result is not None else reservation.max_input_tokens),
        output_tokens=(result.output_tokens if result is not None else reservation.max_output_tokens),
        cost_usd_micros=actual.cost_usd_micros,
        output_artifact_bytes=actual.artifact_bytes,
    )


def _unique_input_refs(
    state: PngToShaderV2State, context: IntentBuildContext
) -> tuple[ArtifactRefV2, ...]:
    refs: list[ArtifactRefV2] = []
    seen: set[str] = set()
    for ref in (
        state.measurements_ref,
        state.request_constraint_set_ref,
        *context.allowed_interpretation_evidence_refs,
    ):
        if ref.artifact_id not in seen:
            refs.append(ref)
            seen.add(ref.artifact_id)
    return tuple(refs)


def _zero_budget() -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=0,
        candidate_attempts=0,
        artifact_bytes=0,
        cost_usd_micros=0,
    )


def _add_budget(left: BudgetVectorV2, right: BudgetVectorV2) -> BudgetVectorV2:
    return BudgetVectorV2(
        **{
            name: getattr(left, name) + getattr(right, name)
            for name in BudgetVectorV2.model_fields
        }
    )


__all__ = [
    "DurableGatewayResultV1",
    "DurableLLMGateway",
    "LocalRealModelOperationStore",
    "NonDurableLLMGatewayError",
    "RealModelCallPolicyV1",
    "RealModelCommittedFailure",
    "RealModelFailureClosureV1",
    "RealModelFailureStatus",
    "RealModelIdentityError",
    "RealModelOperationIncomplete",
    "RealModelOperationJournalV1",
    "VisualInterpretationGatewayAdapter",
    "build_real_model_reservation",
    "execute_real_visual_interpretation",
]
