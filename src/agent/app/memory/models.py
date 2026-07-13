"""定义 Shader 项目长期记忆的数据结构."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast

MemoryKind = Literal["review", "constraint", "decision", "strategy"]
MemoryStatus = Literal["durable", "ephemeral", "degraded"]
MEMORY_SCHEMA_VERSION = 1
REVIEW_IMPORTANCE = 0.5
MAX_MEMORY_SUMMARY_CHARS = 2_000
_MEMORY_KINDS = {"review", "constraint", "decision", "strategy"}


def utc_now() -> datetime:
    """返回带 UTC 时区的当前时间."""
    return datetime.now(UTC)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


@dataclass(frozen=True)
class MemoryItem:
    """表示一条经过筛选、可跨运行复用的项目记忆."""

    schema_version: int
    memory_id: str
    kind: MemoryKind
    summary: str
    importance: float
    source_run_id: str
    glsl_sha256: str | None
    iteration: int | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        """校验并规范化记忆字段."""
        summary = self.summary.strip()
        if self.schema_version != MEMORY_SCHEMA_VERSION:
            raise ValueError("不支持的 Memory schema_version。")
        if not self.memory_id.strip():
            raise ValueError("memory_id 不能为空。")
        if self.kind not in _MEMORY_KINDS:
            raise ValueError("Memory kind 非法。")
        if not summary:
            raise ValueError("Memory summary 不能为空。")
        if len(summary) > MAX_MEMORY_SUMMARY_CHARS:
            raise ValueError("Memory summary 不能超过 2000 个字符。")
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError("Memory importance 必须在 0.0 到 1.0 之间。")
        if not self.source_run_id.strip():
            raise ValueError("source_run_id 不能为空。")
        if self.iteration is not None and self.iteration < 0:
            raise ValueError("iteration 不能为负数。")
        object.__setattr__(self, "summary", summary)
        object.__setattr__(self, "created_at", _normalize_datetime(self.created_at))
        object.__setattr__(self, "updated_at", _normalize_datetime(self.updated_at))

    def to_value(self) -> dict[str, Any]:
        """转换为 LangGraph Store 可保存的 JSON 字典."""
        return {
            "schema_version": self.schema_version,
            "memory_id": self.memory_id,
            "kind": self.kind,
            "summary": self.summary,
            "importance": self.importance,
            "source_run_id": self.source_run_id,
            "glsl_sha256": self.glsl_sha256,
            "iteration": self.iteration,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_value(cls, value: dict[str, Any]) -> MemoryItem:
        """从 LangGraph Store value 解析并校验记忆."""
        return cls(
            schema_version=int(value["schema_version"]),
            memory_id=str(value["memory_id"]),
            kind=cast(MemoryKind, str(value["kind"])),
            summary=str(value["summary"]),
            importance=float(value["importance"]),
            source_run_id=str(value["source_run_id"]),
            glsl_sha256=(
                None if value.get("glsl_sha256") is None else str(value["glsl_sha256"])
            ),
            iteration=(
                None if value.get("iteration") is None else int(value["iteration"])
            ),
            created_at=datetime.fromisoformat(str(value["created_at"])),
            updated_at=datetime.fromisoformat(str(value["updated_at"])),
        )


def review_memory_id(glsl_sha256: str) -> str:
    """返回同一 GLSL Review 的稳定 Store key."""
    digest = glsl_sha256.strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("glsl_sha256 必须是 64 位十六进制 SHA-256。")
    return f"review:{digest}"


def build_review_summary(evaluation: str, suggestions: tuple[str, ...]) -> str:
    """确定性拼接 Review 结果，不增加模型调用."""
    parts = [evaluation.strip()]
    cleaned = [suggestion.strip() for suggestion in suggestions if suggestion.strip()]
    if cleaned:
        parts.append("修改建议：" + "；".join(cleaned))
    summary = "\n".join(part for part in parts if part)
    if not summary:
        raise ValueError("Review 没有可晋升的摘要。")
    return summary[:MAX_MEMORY_SUMMARY_CHARS]
