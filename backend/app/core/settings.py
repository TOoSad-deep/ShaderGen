"""Backend 启动配置与环境变量解析."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _enabled(value: str | None) -> bool:
    """只把显式 true 值解析为启用."""
    return (value or "").strip().lower() in _TRUE_VALUES


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
    node_lab_enabled: bool = False
    node_lab_root: str | None = None
    node_lab_batch_root: str | None = None
    node_lab_real_model_enabled: bool = False

    @classmethod
    def from_env(cls, *, load_environment: bool = True) -> BackendSettings:
        """从根目录 `.env` 与进程环境构造不可变配置."""
        if load_environment:
            load_dotenv()
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            cors_origins=_cors_origins(os.getenv("SHADERGEN_CORS_ORIGINS")),
            node_lab_enabled=_enabled(os.getenv("SHADERGEN_NODE_LAB_ENABLED")),
            node_lab_root=os.getenv("SHADERGEN_NODE_LAB_ROOT") or None,
            node_lab_batch_root=os.getenv("SHADERGEN_NODE_LAB_BATCH_ROOT") or None,
            node_lab_real_model_enabled=_enabled(
                os.getenv("SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED")
            ),
        )
