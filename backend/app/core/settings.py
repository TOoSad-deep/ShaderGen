"""Backend startup configuration for the current Direct pipeline."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from shaderforge.config import RUNTIME_TIMEOUTS

DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)
_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PUBLIC_ARTIFACT_ROOT = _ROOT / "output/png-to-shader"
DEFAULT_PRIVATE_ARTIFACT_ROOT = _ROOT / "output/png-to-shader-direct-private"


def _cors_origins(value: str | None) -> tuple[str, ...]:
    if value is None or not value.strip():
        return DEFAULT_CORS_ORIGINS
    origins = tuple(
        dict.fromkeys(item.strip() for item in value.split(",") if item.strip())
    )
    if not origins:
        return DEFAULT_CORS_ORIGINS
    if "*" in origins:
        raise ValueError("SHADERGEN_CORS_ORIGINS must explicitly list trusted origins")
    return origins


def _require_disjoint(first: Path, second: Path) -> None:
    left = first.expanduser().resolve(strict=False)
    right = second.expanduser().resolve(strict=False)
    if left == right or left.is_relative_to(right) or right.is_relative_to(left):
        raise ValueError("public and private artifact roots must be disjoint")


@dataclass(frozen=True, slots=True)
class BackendSettings:
    database_url: str | None = None
    log_level: str = "INFO"
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS
    public_artifact_root: Path = DEFAULT_PUBLIC_ARTIFACT_ROOT
    private_attempt_artifact_root: Path = DEFAULT_PRIVATE_ARTIFACT_ROOT
    attempt_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.attempt_seconds
    close_timeout_seconds: float = RUNTIME_TIMEOUTS.engine.close_seconds

    def __post_init__(self) -> None:
        _require_disjoint(
            self.public_artifact_root,
            self.private_attempt_artifact_root,
        )
        for name in ("attempt_timeout_seconds", "close_timeout_seconds"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value <= 0
            ):
                raise ValueError(f"{name} must be positive")

    @classmethod
    def from_env(cls, *, load_environment: bool = True) -> BackendSettings:
        if load_environment:
            load_dotenv()
        return cls(
            database_url=os.getenv("DATABASE_URL") or None,
            log_level=(os.getenv("LOG_LEVEL") or "INFO").strip().upper(),
            cors_origins=_cors_origins(os.getenv("SHADERGEN_CORS_ORIGINS")),
            public_artifact_root=Path(
                os.getenv("SHADERGEN_PUBLIC_ARTIFACT_ROOT")
                or DEFAULT_PUBLIC_ARTIFACT_ROOT
            ),
            private_attempt_artifact_root=Path(
                os.getenv("SHADERGEN_PRIVATE_ATTEMPT_ARTIFACT_ROOT")
                or DEFAULT_PRIVATE_ARTIFACT_ROOT
            ),
        )
