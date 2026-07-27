"""Node Lab 独立 HTTP 服务的启动配置."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_FACTORY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_.]*$")
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in _TRUE_VALUES


def _cors_origins(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return DEFAULT_CORS_ORIGINS
    origins = tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )
    if not origins:
        return DEFAULT_CORS_ORIGINS
    if "*" in origins:
        raise ValueError("NODELAB_CORS_ORIGINS 不允许使用 *；请显式列出可信 Origin。")
    return origins


@dataclass(frozen=True, slots=True)
class NodeLabServiceSettings:
    """保存独立服务进程启动时冻结的配置."""

    root: Path = Path("output/node-lab/service")
    batch_root: Path = Path("output/benchmarks/node-lab-service")
    pipeline_id: str = "node_lab"
    application_factory: str | None = None
    real_model_enabled: bool = False
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        """拒绝不稳定标识符和客户端可控 import 形态."""
        if not _IDENTIFIER_PATTERN.fullmatch(self.pipeline_id):
            raise ValueError("NODELAB_PIPELINE_ID 必须是受限标识符。")
        if self.application_factory is not None and not _FACTORY_PATTERN.fullmatch(
            self.application_factory
        ):
            raise ValueError("NODELAB_APPLICATION_FACTORY 必须使用 module:callable。")
        object.__setattr__(self, "root", self.root.resolve())
        object.__setattr__(self, "batch_root", self.batch_root.resolve())

    @classmethod
    def from_env(
        cls,
        *,
        load_environment: bool = True,
    ) -> NodeLabServiceSettings:
        """只从进程环境和可选根目录 `.env` 构造配置."""
        if load_environment:
            load_dotenv()
        return cls(
            root=Path(os.getenv("NODELAB_ROOT") or "output/node-lab/service"),
            batch_root=Path(
                os.getenv("NODELAB_BATCH_ROOT") or "output/benchmarks/node-lab-service"
            ),
            pipeline_id=(os.getenv("NODELAB_PIPELINE_ID") or "node_lab").strip(),
            application_factory=(os.getenv("NODELAB_APPLICATION_FACTORY") or None),
            real_model_enabled=_enabled(os.getenv("NODELAB_REAL_MODEL_ENABLED")),
            cors_origins=_cors_origins(os.getenv("NODELAB_CORS_ORIGINS")),
            log_level=(os.getenv("NODELAB_LOG_LEVEL") or "INFO").strip().upper(),
        )


__all__ = ["DEFAULT_CORS_ORIGINS", "NodeLabServiceSettings"]
