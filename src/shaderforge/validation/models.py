"""Shader 静态校验结果模型."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class ValidationViolation:
    """一条可定位的契约或安全违规."""

    code: str
    message: str
    severity: Severity
    line: int | None = None


@dataclass(frozen=True)
class ValidationResult:
    """完整静态校验结果."""

    valid: bool
    violations: tuple[ValidationViolation, ...]
    source_chars: int
    contract_id: str

    @property
    def errors(self) -> tuple[ValidationViolation, ...]:
        """返回阻止编译的 error."""
        return tuple(item for item in self.violations if item.severity == "error")

    @property
    def warnings(self) -> tuple[ValidationViolation, ...]:
        """返回不会单独阻止编译的 warning."""
        return tuple(item for item in self.violations if item.severity == "warning")

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化字典."""
        return asdict(self)


@dataclass(frozen=True)
class ShaderRepairResult:
    """一次受限确定性 Shader 修复的结果与定位证据."""

    source: str
    strategy: str
    repaired_lines: tuple[int, ...]
    replacement_count: int

    def safe_audit_dict(self) -> dict[str, Any]:
        """返回不包含完整 GLSL 的安全事件摘要."""
        return {
            "strategy": self.strategy,
            "repaired_lines": list(self.repaired_lines),
            "replacement_count": self.replacement_count,
        }
