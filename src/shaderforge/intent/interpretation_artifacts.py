"""VisualInterpretationV2 模型调用的内容寻址审计封套。."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
from typing import Any, Literal

from pydantic import Field, model_validator

from shaderforge.contracts import FrozenModel, NonEmptyString, Sha256Hex
from shaderforge.contracts.canonical import canonical_sha256
from shaderforge.intent.ir import VisualInterpretationV2
from shaderforge.intent.parsing import (
    VisualInterpretationParseError,
    parse_visual_interpretation_v2,
)
from shaderforge.store import ArtifactCatalog, ArtifactRefV2, ArtifactResolver

VISUAL_INTERPRETATION_PROMPT_SCHEMA_VERSION: Literal[
    "visual_interpretation_prompt_snapshot_v1"
] = "visual_interpretation_prompt_snapshot_v1"
VISUAL_INTERPRETATION_RAW_RESPONSE_SCHEMA_VERSION: Literal[
    "visual_interpretation_raw_response_v1"
] = "visual_interpretation_raw_response_v1"
VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION: Literal[
    "visual_interpretation_call_audit_v2"
] = "visual_interpretation_call_audit_v2"
VISUAL_INTERPRETATION_CALL_AUDIT_HASH_VERSION: Literal[
    "visual_interpretation_call_audit_hash_v2"
] = "visual_interpretation_call_audit_hash_v2"
VISUAL_INTERPRETATION_PARSER_VERSION: Literal["visual_interpretation_parser_v2_2"] = (
    "visual_interpretation_parser_v2_2"
)

_TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
_JSON_CONTENT_TYPE = "application/json"


class VisualInterpretationPromptIdentity(FrozenModel):
    """冻结 Prompt 名称、版本、内容 hash 和可重放快照。."""

    name: NonEmptyString
    version: NonEmptyString
    sha256: Sha256Hex
    snapshot_ref: ArtifactRefV2

    @model_validator(mode="after")
    def _validate_snapshot(self) -> VisualInterpretationPromptIdentity:
        _require_ref_metadata(
            self.snapshot_ref,
            kind="visual_interpretation_prompt",
            schema_version=VISUAL_INTERPRETATION_PROMPT_SCHEMA_VERSION,
            content_type=_TEXT_CONTENT_TYPE,
        )
        if self.snapshot_ref.sha256 != self.sha256:
            raise ValueError("Prompt snapshot 与 prompt SHA-256 不一致。")
        return self


class VisualInterpretationCallAudit(FrozenModel):
    """一次最终 VisualInterpretation 解析尝试的不可变 provenance。."""

    schema_version: Literal["visual_interpretation_call_audit_v2"] = (
        VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION
    )
    hash_version: Literal["visual_interpretation_call_audit_hash_v2"] = (
        VISUAL_INTERPRETATION_CALL_AUDIT_HASH_VERSION
    )
    parser_version: Literal["visual_interpretation_parser_v2_2"] = (
        VISUAL_INTERPRETATION_PARSER_VERSION
    )
    prompt: VisualInterpretationPromptIdentity
    model_id: NonEmptyString
    input_artifact_refs: tuple[ArtifactRefV2, ...] = Field(min_length=1)
    raw_response_ref: ArtifactRefV2
    raw_response_sha256: Sha256Hex
    attempt_count: int = Field(ge=1)
    repair_count: int = Field(ge=0)
    parser_status: Literal["succeeded", "failed"]
    parser_error_code: NonEmptyString | None
    visual_interpretation_ref: ArtifactRefV2 | None
    visual_interpretation_sha256: Sha256Hex | None
    record_hash: Sha256Hex

    @model_validator(mode="after")
    def _validate_call(self) -> VisualInterpretationCallAudit:
        if self.attempt_count != self.repair_count + 1:
            raise ValueError("attempt_count 必须等于 initial attempt 加 repair_count。")
        input_ids = [item.artifact_id for item in self.input_artifact_refs]
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("input_artifact_refs 不得重复。")
        _require_ref_metadata(
            self.raw_response_ref,
            kind="visual_interpretation_raw_response",
            schema_version=VISUAL_INTERPRETATION_RAW_RESPONSE_SCHEMA_VERSION,
            content_type=_TEXT_CONTENT_TYPE,
        )
        if self.raw_response_ref.sha256 != self.raw_response_sha256:
            raise ValueError("raw response ref 与 SHA-256 不一致。")
        if self.parser_status == "succeeded":
            if self.parser_error_code is not None:
                raise ValueError("成功解析不得携带 parser_error_code。")
            if (
                self.visual_interpretation_ref is None
                or self.visual_interpretation_sha256 is None
            ):
                raise ValueError("成功解析必须绑定 VisualInterpretation Artifact。")
            _require_ref_metadata(
                self.visual_interpretation_ref,
                kind="visual_interpretation",
                schema_version="visual_interpretation_v2_1",
                content_type=_JSON_CONTENT_TYPE,
            )
            if (
                self.visual_interpretation_ref.sha256
                != self.visual_interpretation_sha256
            ):
                raise ValueError("VisualInterpretation ref 与 SHA-256 不一致。")
        elif (
            self.parser_error_code is None
            or self.visual_interpretation_ref is not None
            or self.visual_interpretation_sha256 is not None
        ):
            raise ValueError("失败解析必须记录 error 且不得伪造成功 Artifact。")
        if self.record_hash != compute_visual_interpretation_call_audit_hash(self):
            raise ValueError("VisualInterpretation call audit record_hash 不一致。")
        return self


class VisualInterpretationArtifactBundle(FrozenModel):
    """持久化审计记录及成功时的严格解析结果。."""

    audit_ref: ArtifactRefV2
    audit: VisualInterpretationCallAudit
    interpretation: VisualInterpretationV2 | None

    @model_validator(mode="after")
    def _validate_bundle(self) -> VisualInterpretationArtifactBundle:
        _require_ref_metadata(
            self.audit_ref,
            kind="visual_interpretation_call_audit",
            schema_version=VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION,
            content_type=_JSON_CONTENT_TYPE,
        )
        if self.audit.parser_status == "succeeded":
            if self.interpretation is None:
                raise ValueError("成功 audit bundle 必须包含 Interpretation。")
        elif self.interpretation is not None:
            raise ValueError("失败 audit bundle 不得包含 Interpretation。")
        return self


def _require_ref_metadata(
    ref: ArtifactRefV2,
    *,
    kind: str,
    schema_version: str,
    content_type: str,
) -> None:
    if (
        ref.kind != kind
        or ref.schema_version != schema_version
        or ref.content_type != content_type
    ):
        raise ValueError("ArtifactRefV2 kind/schema/content-type 不符合审计契约。")


def _read_exact(resolver: ArtifactResolver, ref: ArtifactRefV2) -> bytes:
    resolved = resolver.resolve(ref.artifact_id)
    if resolved != ref:
        raise ValueError("Artifact resolver 返回的引用身份不一致。")
    data = resolver.read_bytes(ref.artifact_id)
    if len(data) != ref.size_bytes:
        raise ValueError("Artifact bytes 长度与引用不一致。")
    if sha256(data).hexdigest() != ref.sha256:
        raise ValueError("Artifact bytes SHA-256 与引用不一致。")
    return data


def _stable_model_json(model: FrozenModel) -> bytes:
    return model.model_dump_json().encode("utf-8")


def compute_visual_interpretation_call_audit_hash(
    audit: VisualInterpretationCallAudit | Mapping[str, Any],
) -> str:
    """计算排除自身字段的审计记录 hash。."""
    if isinstance(audit, VisualInterpretationCallAudit):
        record = audit.model_dump(mode="python", exclude={"record_hash"})
    else:
        record = dict(audit)
        record.pop("record_hash", None)
    return canonical_sha256(
        {
            "hash_version": VISUAL_INTERPRETATION_CALL_AUDIT_HASH_VERSION,
            "record": record,
        }
    )


def _build_audit(**values: Any) -> VisualInterpretationCallAudit:
    raw = {
        "schema_version": VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION,
        "hash_version": VISUAL_INTERPRETATION_CALL_AUDIT_HASH_VERSION,
        "parser_version": VISUAL_INTERPRETATION_PARSER_VERSION,
        **values,
    }
    raw["record_hash"] = compute_visual_interpretation_call_audit_hash(raw)
    return VisualInterpretationCallAudit.model_validate(raw, strict=True)


def _validate_interpretation_evidence(
    interpretation: VisualInterpretationV2,
    input_artifact_refs: tuple[ArtifactRefV2, ...],
) -> None:
    allowed = set(input_artifact_refs)
    referenced = (
        interpretation.evidence_refs
        + tuple(
            ref
            for layer in interpretation.layer_hypotheses
            for ref in layer.evidence_refs
        )
        + tuple(
            ref
            for candidate in interpretation.primitive_candidates
            for ref in candidate.evidence_refs
        )
        + tuple(
            ref
            for strategy in interpretation.strategy_hypotheses
            for ref in strategy.evidence_refs
        )
        + tuple(
            ref
            for assessment in interpretation.required_layer_assessments
            for ref in assessment.evidence_refs
        )
        + tuple(
            ref
            for uncertainty in interpretation.uncertainties
            for ref in uncertainty.evidence_refs
        )
    )
    if any(ref not in allowed for ref in referenced):
        raise ValueError("VisualInterpretation 引用了调用输入之外的 Artifact。")


def materialize_visual_interpretation_call(
    *,
    catalog: ArtifactCatalog,
    run_id: str,
    prompt_name: str,
    prompt_version: str,
    prompt_text: str,
    model_id: str,
    input_artifact_refs: tuple[ArtifactRefV2, ...],
    raw_response: str,
    attempt_count: int,
    repair_count: int,
    parser_status: Literal["succeeded", "failed"],
    interpretation: VisualInterpretationV2 | None = None,
    parser_error_code: str | None = None,
) -> VisualInterpretationArtifactBundle:
    """物化一次调用；Parser 失败时只写 raw/audit，不写成功 Artifact。."""
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id 不能为空。")
    if not isinstance(prompt_name, str) or not prompt_name.strip():
        raise ValueError("prompt_name 不能为空。")
    if not isinstance(prompt_version, str) or not prompt_version.strip():
        raise ValueError("prompt_version 不能为空。")
    if not isinstance(prompt_text, str) or not prompt_text:
        raise ValueError("prompt_text 不能为空。")
    if not isinstance(model_id, str) or not model_id.strip():
        raise ValueError("model_id 不能为空。")
    if not isinstance(raw_response, str) or not raw_response:
        raise ValueError("raw_response 不能为空。")
    if not input_artifact_refs:
        raise ValueError("input_artifact_refs 不能为空。")
    input_ids = [ref.artifact_id for ref in input_artifact_refs]
    if len(input_ids) != len(set(input_ids)):
        raise ValueError("input_artifact_refs 不得重复。")
    if attempt_count < 1 or repair_count < 0 or attempt_count != repair_count + 1:
        raise ValueError("attempt_count 必须等于 initial attempt 加 repair_count。")

    parsed: VisualInterpretationV2 | None = None
    if parser_status == "succeeded":
        if parser_error_code is not None or interpretation is None:
            raise ValueError("成功解析需要 Interpretation，且不得携带 error code。")
        parsed = parse_visual_interpretation_v2(raw_response)
        if parsed != interpretation:
            raise ValueError("提供的 Interpretation 与 raw response 解析结果不一致。")
        _validate_interpretation_evidence(parsed, input_artifact_refs)
    elif parser_status == "failed":
        if parser_error_code is None or interpretation is not None:
            raise ValueError("失败解析需要 error code，且不得携带 Interpretation。")
        try:
            parse_visual_interpretation_v2(raw_response)
        except VisualInterpretationParseError:
            pass
        else:
            raise ValueError("raw response 可成功解析，不得记录为 parser failed。")
    else:
        raise ValueError("parser_status 只允许 succeeded 或 failed。")
    for ref in input_artifact_refs:
        _read_exact(catalog, ref)

    prompt_bytes = prompt_text.encode("utf-8")
    prompt_ref = catalog.put(
        run_id=run_id,
        kind="visual_interpretation_prompt",
        schema_version=VISUAL_INTERPRETATION_PROMPT_SCHEMA_VERSION,
        content_type=_TEXT_CONTENT_TYPE,
        data=prompt_bytes,
    )
    prompt = VisualInterpretationPromptIdentity(
        name=prompt_name,
        version=prompt_version,
        sha256=sha256(prompt_bytes).hexdigest(),
        snapshot_ref=prompt_ref,
    )
    raw_bytes = raw_response.encode("utf-8")
    raw_ref = catalog.put(
        run_id=run_id,
        kind="visual_interpretation_raw_response",
        schema_version=VISUAL_INTERPRETATION_RAW_RESPONSE_SCHEMA_VERSION,
        content_type=_TEXT_CONTENT_TYPE,
        data=raw_bytes,
    )

    output_ref: ArtifactRefV2 | None = None
    output_sha256: str | None = None
    if parsed is not None:
        output_bytes = _stable_model_json(parsed)
        output_ref = catalog.put(
            run_id=run_id,
            kind="visual_interpretation",
            schema_version="visual_interpretation_v2_1",
            content_type=_JSON_CONTENT_TYPE,
            data=output_bytes,
        )
        output_sha256 = output_ref.sha256

    audit = _build_audit(
        prompt=prompt,
        model_id=model_id,
        input_artifact_refs=input_artifact_refs,
        raw_response_ref=raw_ref,
        raw_response_sha256=raw_ref.sha256,
        attempt_count=attempt_count,
        repair_count=repair_count,
        parser_status=parser_status,
        parser_error_code=parser_error_code,
        visual_interpretation_ref=output_ref,
        visual_interpretation_sha256=output_sha256,
    )
    audit_ref = catalog.put(
        run_id=run_id,
        kind="visual_interpretation_call_audit",
        schema_version=VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
        data=_stable_model_json(audit),
    )
    return VisualInterpretationArtifactBundle(
        audit_ref=audit_ref,
        audit=audit,
        interpretation=parsed,
    )


def load_visual_interpretation_call(
    audit_ref: ArtifactRefV2,
    *,
    resolver: ArtifactResolver,
) -> VisualInterpretationArtifactBundle:
    """重载并重新验证 Prompt、输入、raw response、Parser 与输出绑定。."""
    _require_ref_metadata(
        audit_ref,
        kind="visual_interpretation_call_audit",
        schema_version=VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION,
        content_type=_JSON_CONTENT_TYPE,
    )
    audit_bytes = _read_exact(resolver, audit_ref)
    audit = VisualInterpretationCallAudit.model_validate_json(
        audit_bytes,
        strict=True,
    )
    prompt_bytes = _read_exact(resolver, audit.prompt.snapshot_ref)
    if sha256(prompt_bytes).hexdigest() != audit.prompt.sha256:
        raise ValueError("重载 Prompt hash 不一致。")
    for ref in audit.input_artifact_refs:
        _read_exact(resolver, ref)
    raw_bytes = _read_exact(resolver, audit.raw_response_ref)
    try:
        raw_response = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("raw response 不是 UTF-8。") from exc

    interpretation: VisualInterpretationV2 | None = None
    if audit.parser_status == "succeeded":
        parsed = parse_visual_interpretation_v2(raw_response)
        assert audit.visual_interpretation_ref is not None
        output_bytes = _read_exact(resolver, audit.visual_interpretation_ref)
        interpretation = VisualInterpretationV2.model_validate_json(
            output_bytes,
            strict=True,
        )
        if parsed != interpretation:
            raise ValueError("重载 raw response 与 Interpretation Artifact 不一致。")
        _validate_interpretation_evidence(
            interpretation,
            audit.input_artifact_refs,
        )
    else:
        try:
            parse_visual_interpretation_v2(raw_response)
        except VisualInterpretationParseError:
            pass
        else:
            raise ValueError("失败 audit 的 raw response 当前可成功解析。")
    return VisualInterpretationArtifactBundle(
        audit_ref=audit_ref,
        audit=audit,
        interpretation=interpretation,
    )


__all__ = [
    "VISUAL_INTERPRETATION_CALL_AUDIT_HASH_VERSION",
    "VISUAL_INTERPRETATION_CALL_AUDIT_SCHEMA_VERSION",
    "VISUAL_INTERPRETATION_PARSER_VERSION",
    "VISUAL_INTERPRETATION_PROMPT_SCHEMA_VERSION",
    "VISUAL_INTERPRETATION_RAW_RESPONSE_SCHEMA_VERSION",
    "VisualInterpretationArtifactBundle",
    "VisualInterpretationCallAudit",
    "VisualInterpretationPromptIdentity",
    "compute_visual_interpretation_call_audit_hash",
    "load_visual_interpretation_call",
    "materialize_visual_interpretation_call",
]
