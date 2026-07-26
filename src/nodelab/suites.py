"""Node Lab 通用 benchmark suite allowlist."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from nodelab.models import NodeLabError


class SuiteRegistry:
    """把公开 suite id 映射为所属 Pipeline 的版本化 manifest."""

    def __init__(self, suites: Mapping[str, str | Path] | None = None) -> None:
        """冻结 suite 映射；空映射表示当前 Pipeline 不提供内置 suite."""
        self._suites = {
            suite_id: Path(path).resolve() for suite_id, path in (suites or {}).items()
        }

    def describe(self) -> tuple[str, ...]:
        """返回稳定 suite id，不暴露本地路径."""
        return tuple(self._suites)

    def resolve(self, suite_id: str) -> Path:
        """解析已登记 manifest；未知或缺失文件 fail closed."""
        try:
            path = self._suites[suite_id]
        except KeyError as exc:
            raise NodeLabError(
                "suite_not_found",
                "Node Lab suite 未由当前 Pipeline Provider 登记。",
                stage="suite_registry",
                details={"suite_id": suite_id},
            ) from exc
        if not path.is_file():
            raise NodeLabError(
                "suite_not_found",
                "Node Lab suite manifest 不存在。",
                stage="suite_registry",
                details={"suite_id": suite_id},
            )
        return path


__all__ = ["SuiteRegistry"]
