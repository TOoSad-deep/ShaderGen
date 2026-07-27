"""PromotionAuthorizationV1 与可信 durable evidence registry 的运行时绑定."""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from backend.app.core.engine_policy import (
    Identifier,
    PromotionAuthorizationV1,
    PromotionStage,
    Sha256Text,
    ShaderEnginePolicyV1,
    promotion_authorization_sha256,
)

PROMOTION_REGISTRY_KIND = "layerplan_glsl_promotion_evidence"
PROMOTION_ARTIFACT_ROLE = "promotion_evidence_bundle"

_Uri = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=4,
        max_length=2048,
        pattern=r"^[a-z][a-z0-9+.-]*://[^\s]+$",
    ),
]


class PromotionAuthorizationError(ValueError):
    """晋升授权没有通过可信 registry 的 fail-closed 运行时校验."""


class _FrozenRegistryModel(BaseModel):
    """拒绝额外字段和宽松类型转换的 registry 模型基类."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _PromotionRegistrySummary(_FrozenRegistryModel):
    target_stage: PromotionStage
    d090_suite_report_sha256: Sha256Text
    automatic_gate_outcome: Literal["supported"]
    recursive_verifier_version: Identifier
    recursive_verification_result: Literal["verified"]
    human_blind_review_manifest_sha256: Sha256Text
    human_blind_review_result_sha256: Sha256Text
    human_blind_review_b_preference: float = Field(ge=0.5, le=1.0, strict=True)
    human_gate_outcome: Literal["supported"]
    direct_implementation_identity: Sha256Text


class _PromotionRegistryArtifact(_FrozenRegistryModel):
    role: Identifier
    path: _Uri
    availability: Literal["release", "object_store"]
    size_bytes: int = Field(gt=0, strict=True)
    sha256: Sha256Text
    immutability_status: Literal["immutable"]


class _PromotionRegistryEntry(_FrozenRegistryModel):
    evidence_id: Identifier
    kind: Literal["layerplan_glsl_promotion_evidence"]
    suite_run_id: Identifier
    durability_status: Literal["durable"]
    gate_status: Literal["passed"]
    summary: _PromotionRegistrySummary
    artifacts: list[_PromotionRegistryArtifact] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_unique_promotion_artifact(self) -> _PromotionRegistryEntry:
        roles = [artifact.role for artifact in self.artifacts]
        if len(roles) != len(set(roles)):
            raise ValueError("promotion registry artifact role 不得重复。")
        if roles.count(PROMOTION_ARTIFACT_ROLE) != 1:
            raise ValueError("promotion registry 必须且只能有一个 bundle Artifact。")
        if any(not item.strip() or len(item) > 500 for item in self.limitations):
            raise ValueError("promotion registry limitations 非法。")
        return self

    @property
    def promotion_artifact(self) -> _PromotionRegistryArtifact:
        """返回唯一的 durable promotion bundle Artifact."""
        return next(
            artifact
            for artifact in self.artifacts
            if artifact.role == PROMOTION_ARTIFACT_ROLE
        )


@dataclass(frozen=True, slots=True)
class PromotionAuthorizationVerification:
    """启动期验证成功后冻结的最小可信回执."""

    authorization_sha256: str
    registry_sha256: str
    registry_entry_id: str
    target_stage: PromotionStage
    durable_evidence_uri: str
    durable_evidence_sha256: str
    direct_implementation_identity: str


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionAuthorizationError("evidence registry 包含重复 JSON key。")
        result[key] = value
    return result


def _load_registry(path: str | Path | None) -> tuple[dict[str, Any], bytes]:
    if path is None or (isinstance(path, str) and not path.strip()):
        raise PromotionAuthorizationError(
            "canary/direct-default 缺少受信 evidence registry 路径。"
        )
    registry_path = Path(path)
    if registry_path.is_symlink() or not registry_path.is_file():
        raise PromotionAuthorizationError(
            "受信 evidence registry 缺失、不是普通文件或是 symlink。"
        )
    try:
        raw = registry_path.read_bytes()
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except PromotionAuthorizationError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionAuthorizationError(
            "受信 evidence registry 不是合法 JSON。"
        ) from exc
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "updated_at", "entries"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("updated_at"), str)
        or not isinstance(payload.get("entries"), list)
    ):
        raise PromotionAuthorizationError("受信 evidence registry 根契约非法。")
    return cast(dict[str, Any], payload), raw


def _select_entry(
    registry: dict[str, Any], evidence_id: str
) -> _PromotionRegistryEntry:
    raw_entries = cast(list[Any], registry["entries"])
    ids: set[str] = set()
    selected: dict[str, Any] | None = None
    for raw in raw_entries:
        if not isinstance(raw, dict):
            raise PromotionAuthorizationError("evidence registry entry 必须是 object。")
        raw_id = raw.get("evidence_id")
        if not isinstance(raw_id, str) or not raw_id:
            raise PromotionAuthorizationError("evidence registry entry id 非法。")
        if raw_id in ids:
            raise PromotionAuthorizationError("evidence registry entry id 重复。")
        ids.add(raw_id)
        if raw_id == evidence_id:
            selected = cast(dict[str, Any], raw)
    if selected is None:
        raise PromotionAuthorizationError("授权引用的 durable registry entry 不存在。")
    try:
        return _PromotionRegistryEntry.model_validate(selected)
    except ValidationError as exc:
        raise PromotionAuthorizationError(
            "授权引用的 registry entry 不满足 durable promotion 契约。"
        ) from exc


def _require_exact_authorization_binding(
    authorization: PromotionAuthorizationV1,
    entry: _PromotionRegistryEntry,
    *,
    current_direct_implementation_identity: str,
) -> None:
    summary = entry.summary
    artifact = entry.promotion_artifact
    exact_pairs = (
        (
            authorization.target_stage,
            summary.target_stage,
            "target_stage",
        ),
        (
            authorization.d090_suite_report_sha256,
            summary.d090_suite_report_sha256,
            "D090 suite hash",
        ),
        (
            authorization.automatic_gate_outcome,
            summary.automatic_gate_outcome,
            "automatic gate",
        ),
        (
            authorization.recursive_verifier_version,
            summary.recursive_verifier_version,
            "recursive verifier version",
        ),
        (
            authorization.recursive_verification_result,
            summary.recursive_verification_result,
            "recursive verification result",
        ),
        (
            authorization.human_blind_review_manifest_sha256,
            summary.human_blind_review_manifest_sha256,
            "human review manifest hash",
        ),
        (
            authorization.human_blind_review_result_sha256,
            summary.human_blind_review_result_sha256,
            "human review result hash",
        ),
        (
            authorization.human_blind_review_b_preference,
            summary.human_blind_review_b_preference,
            "human preference",
        ),
        (
            authorization.human_gate_outcome,
            summary.human_gate_outcome,
            "human gate",
        ),
        (
            authorization.durable_evidence_uri,
            artifact.path,
            "durable evidence URI",
        ),
        (
            authorization.durable_evidence_sha256,
            artifact.sha256,
            "durable evidence hash",
        ),
        (
            authorization.direct_implementation_identity,
            summary.direct_implementation_identity,
            "registry implementation identity",
        ),
        (
            authorization.direct_implementation_identity,
            current_direct_implementation_identity,
            "current implementation identity",
        ),
    )
    for authorized, registered, label in exact_pairs:
        if authorized != registered:
            raise PromotionAuthorizationError(f"{label} 与受信绑定不一致。")


def verify_runtime_promotion_authorization(
    policy: ShaderEnginePolicyV1,
    *,
    evidence_registry_path: str | Path | None,
    current_direct_implementation_identity: str,
) -> PromotionAuthorizationVerification | None:
    """校验生产 stage 的授权、durable entry 与当前 direct 实现身份."""
    if policy.stage not in {"canary", "direct_default"}:
        if policy.promotion_authorization is not None:
            raise PromotionAuthorizationError("非晋升 stage 不得携带授权。")
        return None
    authorization = policy.promotion_authorization
    if authorization is None or authorization.target_stage != policy.stage:
        raise PromotionAuthorizationError("晋升授权缺失或 target_stage 不匹配。")
    if (
        not isinstance(current_direct_implementation_identity, str)
        or len(current_direct_implementation_identity) != 64
        or any(
            character not in "0123456789abcdef"
            for character in current_direct_implementation_identity
        )
    ):
        raise PromotionAuthorizationError("当前 direct implementation identity 非法。")
    registry, registry_bytes = _load_registry(evidence_registry_path)
    entry = _select_entry(registry, authorization.durable_registry_entry_id)
    _require_exact_authorization_binding(
        authorization,
        entry,
        current_direct_implementation_identity=(current_direct_implementation_identity),
    )
    authorization_hash = promotion_authorization_sha256(authorization)
    assert authorization_hash is not None
    artifact = entry.promotion_artifact
    return PromotionAuthorizationVerification(
        authorization_sha256=authorization_hash,
        registry_sha256=sha256(registry_bytes).hexdigest(),
        registry_entry_id=entry.evidence_id,
        target_stage=authorization.target_stage,
        durable_evidence_uri=artifact.path,
        durable_evidence_sha256=artifact.sha256,
        direct_implementation_identity=current_direct_implementation_identity,
    )


def require_verification_matches_policy(
    policy: ShaderEnginePolicyV1,
    verification: PromotionAuthorizationVerification | None,
    *,
    kill_switch_active: bool = False,
) -> None:
    """确保回执绑定 policy；kill switch 生效时允许无回执紧急启动."""
    if kill_switch_active:
        return
    authorization = policy.promotion_authorization
    if policy.stage not in {"canary", "direct_default"}:
        if verification is not None:
            raise PromotionAuthorizationError("非晋升 stage 不得携带验证回执。")
        return
    if authorization is None or verification is None:
        raise PromotionAuthorizationError("晋升 stage 缺少可信授权验证回执。")
    expected_hash = promotion_authorization_sha256(authorization)
    if (
        verification.authorization_sha256 != expected_hash
        or verification.registry_entry_id != authorization.durable_registry_entry_id
        or verification.target_stage != policy.stage
        or verification.durable_evidence_uri != authorization.durable_evidence_uri
        or verification.durable_evidence_sha256 != authorization.durable_evidence_sha256
        or verification.direct_implementation_identity
        != authorization.direct_implementation_identity
    ):
        raise PromotionAuthorizationError("授权验证回执与冻结 policy 不匹配。")


__all__ = [
    "PROMOTION_ARTIFACT_ROLE",
    "PROMOTION_REGISTRY_KIND",
    "PromotionAuthorizationError",
    "PromotionAuthorizationVerification",
    "require_verification_matches_policy",
    "verify_runtime_promotion_authorization",
]
