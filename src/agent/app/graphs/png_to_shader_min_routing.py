"""最小 scene Graph 的纯路由函数。."""

from __future__ import annotations

from typing import Any, Literal, cast

AfterRender = Literal["optimize_base", "finalize"]
AfterOptimization = Literal["optimize_feature", "author_refine", "finalize"]


def _route(state: dict[str, Any], allowed: set[str]) -> str:
    action = state.get("next_action")
    if action not in allowed:
        raise ValueError(f"最小 Graph next_action 无效：{action!r}")
    return cast(str, action)


def route_after_render(state: dict[str, Any]) -> AfterRender:
    """路由首帧事实验证后的基础优化或终止。."""
    return cast(AfterRender, _route(state, {"optimize_base", "finalize"}))


def route_after_base(state: dict[str, Any]) -> AfterOptimization:
    """路由基础优化后的特征、Refine 或终止。."""
    return cast(
        AfterOptimization,
        _route(state, {"optimize_feature", "author_refine", "finalize"}),
    )


def route_after_feature(state: dict[str, Any]) -> AfterOptimization:
    """路由单个特征优化后的循环、Refine 或终止。."""
    return cast(
        AfterOptimization,
        _route(state, {"optimize_feature", "author_refine", "finalize"}),
    )


__all__ = ["route_after_base", "route_after_feature", "route_after_render"]
