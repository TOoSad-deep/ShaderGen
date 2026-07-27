"""Direct GLSL 生产灰度 policy 的严格服务端契约."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)
from yaml.constructor import ConstructorError

POLICY_SCHEMA_VERSION: Literal["shader_engine_policy_v1"] = "shader_engine_policy_v1"
PROMOTION_SCHEMA_VERSION: Literal["promotion_authorization_v1"] = (
    "promotion_authorization_v1"
)
DEFAULT_DISABLED_POLICY_ID = "default-disabled-v1"
DEFAULT_DIRECT_POLICY_ID = "default-direct-glsl-v1"

PolicyStage = Literal["disabled", "production_shadow", "canary", "direct_default"]
PromotionStage = Literal["canary", "direct_default"]
EngineId = Literal["shader_graph_v1", "direct_glsl_layerplan_v1"]
Identifier = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$",
    ),
]
Sha256Text = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DurableUri = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=4,
        max_length=2048,
        pattern=r"^[a-z][a-z0-9+.-]*://[^\s]+$",
    ),
]


class EnginePolicyConfigurationError(ValueError):
    """表示 policy 文件或 kill switch 配置不可信."""


class _StrictSafeLoader(yaml.SafeLoader):
    """在 SafeLoader 基础上拒绝重复 mapping key."""


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in seen
        except TypeError as exc:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        seen.add(key)
    return dict(loader.construct_mapping(node, deep=deep))


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


class _FrozenPolicyModel(BaseModel):
    """禁止额外字段与实例修改的 policy 模型基类."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class PromotionAuthorizationV1(_FrozenPolicyModel):
    """绑定一次 canary 或 direct-default 晋升所需的完整审批证据."""

    schema_version: Literal["promotion_authorization_v1"] = PROMOTION_SCHEMA_VERSION
    authorization_id: Identifier
    target_stage: PromotionStage
    d090_suite_report_sha256: Sha256Text
    automatic_gate_outcome: Literal["supported"]
    recursive_verifier_version: Identifier
    recursive_verification_result: Literal["verified"]
    human_blind_review_manifest_sha256: Sha256Text
    human_blind_review_result_sha256: Sha256Text
    human_blind_review_b_preference: float = Field(ge=0.5, le=1.0, strict=True)
    human_gate_outcome: Literal["supported"]
    durable_registry_entry_id: Identifier
    durable_evidence_uri: DurableUri
    durable_evidence_sha256: Sha256Text
    durability_status: Literal["durable"]
    direct_implementation_identity: Sha256Text
    max_canary_percent: int = Field(ge=1, le=100, strict=True)
    approved_at: datetime
    adr_id: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=4,
            max_length=64,
            pattern=r"^ADR-[A-Za-z0-9][A-Za-z0-9._-]*$",
        ),
    ]

    @field_validator("approved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        """审批时间必须显式绑定时区."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("approved_at 必须包含时区。")
        return value


class ShaderEnginePolicyV1(_FrozenPolicyModel):
    """Backend 启动时解析并冻结的 engine policy."""

    schema_version: Literal["shader_engine_policy_v1"] = POLICY_SCHEMA_VERSION
    policy_id: Identifier
    stage: PolicyStage
    shadow_percent: int = Field(ge=0, le=100, strict=True)
    canary_percent: int = Field(ge=0, le=100, strict=True)
    bucket_basis: Literal["project_id_v1"]
    direct_engine: Literal["direct_glsl_layerplan_v1"]
    fallback_engine: Literal["shader_graph_v1"]
    promotion_authorization: PromotionAuthorizationV1 | None

    @model_validator(mode="after")
    def validate_stage_combination(self) -> ShaderEnginePolicyV1:
        """拒绝阶段、比例与晋升授权的非法组合."""
        authorization = self.promotion_authorization
        if self.stage == "disabled":
            if self.shadow_percent or self.canary_percent or authorization is not None:
                raise ValueError("disabled 必须使用 0 比例且不得携带晋升授权。")
        elif self.stage == "production_shadow":
            if self.canary_percent or authorization is not None:
                raise ValueError("production_shadow 不得配置 canary 比例或晋升授权。")
        elif self.stage == "canary":
            if self.shadow_percent:
                raise ValueError("canary 不得同时配置 production shadow。")
            if self.canary_percent == 0:
                raise ValueError("canary_percent 必须大于 0。")
            if authorization is None or authorization.target_stage != "canary":
                raise ValueError("canary 必须绑定目标为 canary 的完整晋升授权。")
            if self.canary_percent > authorization.max_canary_percent:
                raise ValueError("canary_percent 超过晋升授权上限。")
        else:
            if self.shadow_percent:
                raise ValueError("direct_default 不得同时配置 production shadow。")
            if authorization is not None and authorization.target_stage != "direct_default":
                raise ValueError(
                    "direct_default 携带授权时，目标必须为 direct_default。"
                )
            if self.canary_percent != 100:
                raise ValueError("direct_default 必须配置 canary_percent=100。")
            if authorization is not None and authorization.max_canary_percent != 100:
                raise ValueError(
                    "direct_default 晋升授权必须配置 max_canary_percent=100。"
                )
        return self


@dataclass(frozen=True, slots=True)
class EnginePolicyResolution:
    """kill switch 解析后的只读有效阶段."""

    configured_stage: PolicyStage
    effective_stage: PolicyStage
    kill_switch_active: bool


def disabled_shader_engine_policy() -> ShaderEnginePolicyV1:
    """构造缺省 fail-closed policy."""
    return ShaderEnginePolicyV1(
        policy_id=DEFAULT_DISABLED_POLICY_ID,
        stage="disabled",
        shadow_percent=0,
        canary_percent=0,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=None,
    )


def direct_default_shader_engine_policy() -> ShaderEnginePolicyV1:
    """构造单环境开发默认使用的 direct-first policy."""
    return ShaderEnginePolicyV1(
        policy_id=DEFAULT_DIRECT_POLICY_ID,
        stage="direct_default",
        shadow_percent=0,
        canary_percent=100,
        bucket_basis="project_id_v1",
        direct_engine="direct_glsl_layerplan_v1",
        fallback_engine="shader_graph_v1",
        promotion_authorization=None,
    )


def shader_engine_policy_sha256(policy: ShaderEnginePolicyV1) -> str:
    """计算与 YAML 排版和字段顺序无关的 canonical SHA-256."""
    payload = json.dumps(
        policy.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def promotion_authorization_sha256(
    authorization: PromotionAuthorizationV1 | None,
) -> str | None:
    """计算晋升授权 canonical SHA-256；无授权时返回 ``None``."""
    if authorization is None:
        return None
    payload = json.dumps(
        authorization.model_dump(mode="json"),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(payload).hexdigest()


def load_shader_engine_policy(
    path: str | Path | None,
) -> ShaderEnginePolicyV1:
    """严格加载受信 YAML；未配置路径时返回 direct-default policy."""
    if path is None or (isinstance(path, str) and not path.strip()):
        return direct_default_shader_engine_policy()
    resolved = Path(path).expanduser()
    try:
        raw_text = resolved.read_text(encoding="utf-8")
        documents = list(yaml.load_all(raw_text, Loader=_StrictSafeLoader))
        if len(documents) != 1 or not isinstance(documents[0], dict):
            raise EnginePolicyConfigurationError(
                "policy YAML 必须恰好包含一个 object。"
            )
        return ShaderEnginePolicyV1.model_validate(documents[0])
    except EnginePolicyConfigurationError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError) as exc:
        raise EnginePolicyConfigurationError(
            f"无法加载可信 Shader engine policy: {resolved}"
        ) from exc


def parse_direct_glsl_kill_switch(value: str | None) -> bool:
    """严格解析 kill switch；仅接受未设置、``0`` 或 ``1``."""
    if value is None or not value.strip():
        return False
    normalized = value.strip()
    if normalized == "0":
        return False
    if normalized == "1":
        return True
    raise EnginePolicyConfigurationError(
        "SHADERGEN_DIRECT_GLSL_KILL_SWITCH 只允许 0 或 1。"
    )


def resolve_engine_policy(
    policy: ShaderEnginePolicyV1,
    *,
    kill_switch_active: bool,
) -> EnginePolicyResolution:
    """应用最高优先级 kill switch，不改写已冻结的原 policy."""
    return EnginePolicyResolution(
        configured_stage=policy.stage,
        effective_stage="disabled" if kill_switch_active else policy.stage,
        kill_switch_active=kill_switch_active,
    )


def stable_project_bucket(*, policy_id: str, project_id: str) -> int:
    """按 project_id_v1 算法返回稳定的 ``0..9999`` bucket."""
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise ValueError("policy_id 必须是非空字符串。")
    if not isinstance(project_id, str) or not project_id.strip():
        raise ValueError("project_id 必须是非空字符串。")
    payload = f"project_id_v1\0{policy_id}\0{project_id}".encode()
    return int.from_bytes(sha256(payload).digest()[:8], "big") % 10_000


def bucket_matches_percent(bucket: int, percent: int) -> bool:
    """判断 bucket 是否命中百分比边界."""
    if (
        isinstance(bucket, bool)
        or not isinstance(bucket, int)
        or not 0 <= bucket < 10_000
    ):
        raise ValueError("bucket 必须是 0..9999 的整数。")
    if (
        isinstance(percent, bool)
        or not isinstance(percent, int)
        or not 0 <= percent <= 100
    ):
        raise ValueError("percent 必须是 0..100 的整数。")
    return bucket < percent * 100
