"""Backend 启动配置与环境变量解析."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from backend.app.core.engine_policy import (
    EnginePolicyResolution,
    ShaderEnginePolicyV1,
    disabled_shader_engine_policy,
    load_shader_engine_policy,
    parse_direct_glsl_kill_switch,
    resolve_engine_policy,
    shader_engine_policy_sha256,
)
from backend.app.core.promotion_authorization import (
    PromotionAuthorizationVerification,
    require_verification_matches_policy,
    verify_runtime_promotion_authorization,
)
from shaderforge.config import RUNTIME_TIMEOUTS

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
DEFAULT_PRODUCTION_SHADOW_ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[3] / "output/png-to-shader-shadow"
)
DEFAULT_ENGINE_ROLLOUT_PRIVATE_ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[3] / "output/png-to-shader-rollout-private"
)


def _cors_origins(value: str | None) -> tuple[str, ...]:
    """解析逗号分隔的浏览器 Origin 白名单."""
    if value is None or not value.strip():
        return DEFAULT_CORS_ORIGINS
    origins = tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )
    if not origins:
        return DEFAULT_CORS_ORIGINS
    if "*" in origins:
        raise ValueError("SHADERGEN_CORS_ORIGINS 不允许使用 *；请显式列出可信 Origin。")
    return origins


def _resolved_artifact_root(path: Path) -> Path:
    """解析现有 symlink/父目录，用于启动期同根与嵌套判断."""
    return path.expanduser().resolve(strict=False)


def _require_disjoint_artifact_roots(
    first_name: str,
    first_path: Path,
    second_name: str,
    second_path: Path,
) -> None:
    """拒绝相同或任一方向嵌套的公开/私有 Artifact 根."""
    first = _resolved_artifact_root(first_path)
    second = _resolved_artifact_root(second_path)
    if (
        first == second
        or first.is_relative_to(second)
        or second.is_relative_to(first)
    ):
        raise ValueError(
            f"{first_name} 与 {second_name} 必须是彼此隔离、互不嵌套的目录。"
        )


@dataclass(frozen=True, slots=True)
class BackendSettings:
    """保存一次 Backend 进程启动时冻结的配置."""

    database_url: str | None = None
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    engine_policy: ShaderEnginePolicyV1 = field(
        default_factory=disabled_shader_engine_policy
    )
    promotion_authorization_verification: PromotionAuthorizationVerification | None = (
        None
    )
    direct_glsl_kill_switch: bool = False
    production_shadow_artifact_root: Path = DEFAULT_PRODUCTION_SHADOW_ARTIFACT_ROOT
    production_shadow_queue_capacity: int = 4
    production_shadow_worker_count: int = 1
    production_shadow_attempt_timeout_seconds: float = (
        RUNTIME_TIMEOUTS.production_shadow.attempt_seconds
    )
    production_shadow_close_timeout_seconds: float = (
        RUNTIME_TIMEOUTS.production_shadow.close_seconds
    )
    production_shadow_resource_close_timeout_seconds: float = (
        RUNTIME_TIMEOUTS.production_shadow.resource_close_seconds
    )
    engine_rollout_private_artifact_root: Path = (
        DEFAULT_ENGINE_ROLLOUT_PRIVATE_ARTIFACT_ROOT
    )
    engine_rollout_attempt_timeout_seconds: float = (
        RUNTIME_TIMEOUTS.engine.attempt_seconds
    )
    engine_rollout_close_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.close_seconds

    def __post_init__(self) -> None:
        """冻结前严格拒绝可能让 shadow 失去有界性的配置."""
        require_verification_matches_policy(
            self.engine_policy,
            self.promotion_authorization_verification,
            kill_switch_active=self.direct_glsl_kill_switch,
        )
        for name in (
            "production_shadow_queue_capacity",
            "production_shadow_worker_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数。")
        for name in (
            "production_shadow_attempt_timeout_seconds",
            "production_shadow_close_timeout_seconds",
            "production_shadow_resource_close_timeout_seconds",
            "engine_rollout_attempt_timeout_seconds",
            "engine_rollout_close_timeout_seconds",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是正数。")
        self.require_artifact_root_isolation()

    def require_artifact_root_isolation(
        self,
        *,
        public_artifact_root: Path | None = None,
    ) -> None:
        """启动期拒绝 rollout/private、shadow 与可识别公开根相互嵌套."""
        _require_disjoint_artifact_roots(
            "engine_rollout_private_artifact_root",
            self.engine_rollout_private_artifact_root,
            "production_shadow_artifact_root",
            self.production_shadow_artifact_root,
        )
        if public_artifact_root is None:
            return
        _require_disjoint_artifact_roots(
            "engine_rollout_private_artifact_root",
            self.engine_rollout_private_artifact_root,
            "public_artifact_root",
            public_artifact_root,
        )
        _require_disjoint_artifact_roots(
            "production_shadow_artifact_root",
            self.production_shadow_artifact_root,
            "public_artifact_root",
            public_artifact_root,
        )

    @property
    def engine_policy_sha256(self) -> str:
        """返回启动时冻结 policy 的 canonical SHA-256."""
        return shader_engine_policy_sha256(self.engine_policy)

    @property
    def engine_policy_resolution(self) -> EnginePolicyResolution:
        """返回应用 kill switch 后的只读有效阶段."""
        return resolve_engine_policy(
            self.engine_policy,
            kill_switch_active=self.direct_glsl_kill_switch,
        )

    @classmethod
    def from_env(cls, *, load_environment: bool = True) -> BackendSettings:
        """从根目录 `.env` 与进程环境构造不可变配置."""
        if load_environment:
            load_dotenv()
        policy = load_shader_engine_policy(os.getenv("SHADERGEN_ENGINE_POLICY_PATH"))
        kill_switch_active = parse_direct_glsl_kill_switch(
            os.getenv("SHADERGEN_DIRECT_GLSL_KILL_SWITCH")
        )
        promotion_verification = None
        if not kill_switch_active and policy.stage in {"canary", "direct_default"}:
            from agent.app.services.layerplan_glsl_shadow_suite import (
                current_direct_glsl_implementation_identity,
            )

            identity = current_direct_glsl_implementation_identity().get(
                "identity_sha256"
            )
            promotion_verification = verify_runtime_promotion_authorization(
                policy,
                evidence_registry_path=os.getenv("SHADERGEN_EVIDENCE_REGISTRY_PATH"),
                current_direct_implementation_identity=(
                    identity if isinstance(identity, str) else ""
                ),
            )
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            cors_origins=_cors_origins(os.getenv("SHADERGEN_CORS_ORIGINS")),
            engine_policy=policy,
            promotion_authorization_verification=promotion_verification,
            direct_glsl_kill_switch=kill_switch_active,
            engine_rollout_private_artifact_root=Path(
                os.getenv("SHADERGEN_ENGINE_ROLLOUT_PRIVATE_ARTIFACT_ROOT")
                or DEFAULT_ENGINE_ROLLOUT_PRIVATE_ARTIFACT_ROOT
            ),
        )
