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

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
DEFAULT_PRODUCTION_SHADOW_ARTIFACT_ROOT = (
    Path(__file__).resolve().parents[3] / "output/png-to-shader-shadow"
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


@dataclass(frozen=True, slots=True)
class BackendSettings:
    """保存一次 Backend 进程启动时冻结的配置."""

    database_url: str | None = None
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    engine_policy: ShaderEnginePolicyV1 = field(
        default_factory=disabled_shader_engine_policy
    )
    direct_glsl_kill_switch: bool = False
    production_shadow_artifact_root: Path = DEFAULT_PRODUCTION_SHADOW_ARTIFACT_ROOT
    production_shadow_queue_capacity: int = 4
    production_shadow_worker_count: int = 1
    production_shadow_attempt_timeout_seconds: float = 180.0
    production_shadow_close_timeout_seconds: float = 5.0
    production_shadow_resource_close_timeout_seconds: float = 3.0

    def __post_init__(self) -> None:
        """冻结前严格拒绝可能让 shadow 失去有界性的配置."""
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
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} 必须是正数。")

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
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            cors_origins=_cors_origins(os.getenv("SHADERGEN_CORS_ORIGINS")),
            engine_policy=load_shader_engine_policy(
                os.getenv("SHADERGEN_ENGINE_POLICY_PATH")
            ),
            direct_glsl_kill_switch=parse_direct_glsl_kill_switch(
                os.getenv("SHADERGEN_DIRECT_GLSL_KILL_SWITCH")
            ),
        )
