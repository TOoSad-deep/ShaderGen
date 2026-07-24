"""ShaderGraph run 级有界多 program registry.

实现设计稿 7.3 节的 run 内 program cache：program key 绑定 compiler 版本、
topology、active parameter manifest、baked values 与渲染尺寸；相同 key 复用
prepared program，不同 key 并存；cache 容量与 compile 预算 fail-closed。registry
只服务单个 run，不引入线程池、持久化或跨 run 全局缓存。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol


class GraphProgramRegistryError(RuntimeError):
    """run 级 program registry 的失败基类."""


class GraphProgramBudgetError(GraphProgramRegistryError):
    """compile 预算耗尽时 fail-closed 抛出."""


class GraphProgramRegistryClosedError(GraphProgramRegistryError):
    """registry 已关闭仍被使用时抛出."""


class PreparedProgramProtocol(Protocol):
    """prepared program 的最小协议，与 PreparedWebGL1Renderer 的 close 语义对齐."""

    width: int
    height: int

    async def close(self) -> None:
        """幂等释放底层 GPU program."""
        ...


class ProgramRendererProtocol(Protocol):
    """可注入的 program 编译协议；生产实现为 PlaywrightWebGL1Renderer."""

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> PreparedProgramProtocol:
        """静态校验并一次性编译/链接固定 program."""
        ...


@dataclass(frozen=True)
class GraphProgramKey:
    """绑定 compiler/topology/active manifest/baked values/尺寸的 program 身份."""

    compiler_version: str
    topology_sha256: str
    active_parameter_manifest_sha256: str
    baked_parameter_sha256: str
    width: int
    height: int

    def __post_init__(self) -> None:
        """拒绝空身份字段与非正尺寸，保证 key 不可伪造为空."""
        for field_name in (
            "compiler_version",
            "topology_sha256",
            "active_parameter_manifest_sha256",
            "baked_parameter_sha256",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{field_name} 必须是非空字符串。")
        for dimension in (self.width, self.height):
            if isinstance(dimension, bool) or not isinstance(dimension, int):
                raise ValueError("width 和 height 必须是正整数。")
            if dimension <= 0:
                raise ValueError("width 和 height 必须是正整数。")


class GraphProgramRegistry:
    """单 run 有界 program cache：相同 key 复用，不同 key 并存，越界 fail-closed.

    registry 不持有 anchor 概念：caller 自行记录 anchor key，被拒 branch 只通过
    ``discard(branch_key)`` 释放自己的 program，不会替换或污染其他 key。
    """

    def __init__(
        self,
        renderer: ProgramRendererProtocol,
        *,
        max_programs: int = 4,
        max_compiles: int = 16,
    ) -> None:
        """注入 renderer 并固定 cache 容量与 compile 预算."""
        if (
            isinstance(max_programs, bool)
            or not isinstance(max_programs, int)
            or max_programs <= 0
        ):
            raise ValueError("max_programs 必须是正整数。")
        if (
            isinstance(max_compiles, bool)
            or not isinstance(max_compiles, int)
            or max_compiles <= 0
        ):
            raise ValueError("max_compiles 必须是正整数。")
        self._renderer = renderer
        self._max_programs = max_programs
        self._max_compiles = max_compiles
        self._programs: OrderedDict[GraphProgramKey, PreparedProgramProtocol] = (
            OrderedDict()
        )
        self._signatures: dict[GraphProgramKey, str] = {}
        self._compile_count = 0
        self._cache_hit_count = 0
        self._closed = False

    @property
    def compile_count(self) -> int:
        """返回已发起的编译次数（含失败编译，失败同样消耗预算）."""
        return self._compile_count

    @property
    def cache_hit_count(self) -> int:
        """返回 key 命中并复用 prepared program 的次数."""
        return self._cache_hit_count

    @property
    def cache_size(self) -> int:
        """返回当前存活的 prepared program 数."""
        return len(self._programs)

    def summary(self) -> dict[str, int]:
        """返回只含计数的安全摘要，不含 GLSL 或 uniform 值."""
        return {
            "compile_count": self._compile_count,
            "cache_hit_count": self._cache_hit_count,
            "cache_size": self.cache_size,
            "max_programs": self._max_programs,
            "max_compiles": self._max_compiles,
        }

    def __contains__(self, key: GraphProgramKey) -> bool:
        """判断 key 当前是否持有存活 program."""
        return key in self._programs

    async def get_or_prepare(
        self,
        key: GraphProgramKey,
        fragment_source: str,
        uniform_schema: Mapping[str, Any],
    ) -> PreparedProgramProtocol:
        """命中 key 时复用 prepared program，否则在预算与容量内编译新 program."""
        if self._closed:
            raise GraphProgramRegistryClosedError("program registry 已关闭。")
        signature = self._program_signature(fragment_source, uniform_schema)
        cached = self._programs.get(key)
        if cached is not None:
            if self._signatures[key] != signature:
                raise GraphProgramRegistryError(
                    "相同 GraphProgramKey 对应的源码或 uniform schema 发生变化。"
                )
            self._programs.move_to_end(key)
            self._cache_hit_count += 1
            return cached
        if self._compile_count >= self._max_compiles:
            raise GraphProgramBudgetError(
                f"compile 预算 {self._max_compiles} 已耗尽，拒绝编译新 program。"
            )
        self._compile_count += 1
        prepared = await self._renderer.prepare(
            fragment_source,
            key.width,
            key.height,
            uniform_schema,
        )
        try:
            while len(self._programs) >= self._max_programs:
                evicted_key = next(iter(self._programs))
                evicted = self._programs[evicted_key]
                await evicted.close()
                self._programs.pop(evicted_key)
                self._signatures.pop(evicted_key, None)
        except Exception:
            try:
                await prepared.close()
            except Exception:
                pass
            raise
        self._programs[key] = prepared
        self._signatures[key] = signature
        return prepared

    @staticmethod
    def _program_signature(
        fragment_source: str,
        uniform_schema: Mapping[str, Any],
    ) -> str:
        """绑定真实源码与 uniform 类型，防止错误 key 静默复用."""
        schema = tuple(
            sorted(
                (name, str(getattr(spec, "type", type(spec).__name__)))
                for name, spec in uniform_schema.items()
            )
        )
        payload = f"{fragment_source}\0{schema!r}".encode()
        return sha256(payload).hexdigest()

    async def discard(self, key: GraphProgramKey) -> bool:
        """释放指定 key 的 program（例如被拒 branch），不影响其他 key."""
        prepared = self._programs.get(key)
        if prepared is None:
            return False
        await prepared.close()
        self._programs.pop(key)
        self._signatures.pop(key, None)
        return True

    async def close_all(self) -> None:
        """释放全部 handle；失败项保留追踪，允许后续 close_all 重试."""
        if self._closed and not self._programs:
            return
        self._closed = True
        first_error: Exception | None = None
        for key, prepared in tuple(self._programs.items()):
            try:
                await prepared.close()
            except Exception as exc:  # noqa: BLE001 与现有 registry 关闭语义一致
                first_error = first_error or exc
            else:
                self._programs.pop(key, None)
                self._signatures.pop(key, None)
        if first_error is not None:
            raise first_error
