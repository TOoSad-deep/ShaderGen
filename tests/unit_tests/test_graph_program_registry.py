"""GraphProgramRegistry 的 fake renderer 单元测试."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from shaderforge.rendering import (
    GraphProgramBudgetError,
    GraphProgramKey,
    GraphProgramRegistry,
    GraphProgramRegistryClosedError,
    GraphProgramRegistryError,
)


def _key(
    suffix: str = "a",
    *,
    width: int = 64,
    height: int = 64,
    baked: str = "baked",
) -> GraphProgramKey:
    return GraphProgramKey(
        compiler_version="compiler_v1",
        topology_sha256=f"topology_{suffix}",
        active_parameter_manifest_sha256="manifest_v1",
        baked_parameter_sha256=baked,
        width=width,
        height=height,
    )


class FakePrepared:
    """记录 close 语义的 fake prepared program."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.close_calls = 0
        self.close_failures_remaining = 0

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_failures_remaining > 0:
            self.close_failures_remaining -= 1
            raise RuntimeError("close failed")


class FakeRenderer:
    """记录 prepare 调用并可按 fragment source 注入失败的 fake renderer."""

    def __init__(self) -> None:
        self.prepare_calls: list[tuple[str, int, int]] = []
        self.prepared: list[FakePrepared] = []
        self.failing_sources: set[str] = set()

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> FakePrepared:
        self.prepare_calls.append((fragment_source, width, height))
        if fragment_source in self.failing_sources:
            raise RuntimeError("compile failed")
        prepared = FakePrepared(width, height)
        self.prepared.append(prepared)
        return prepared


@pytest.mark.anyio
async def test_same_key_reuses_prepared_program() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)
    key = _key()

    first = await registry.get_or_prepare(key, "frag_a", {})
    second = await registry.get_or_prepare(key, "frag_a", {})

    assert first is second
    assert renderer.prepare_calls == [("frag_a", 64, 64)]
    assert registry.compile_count == 1
    assert registry.cache_hit_count == 1
    assert registry.cache_size == 1


@pytest.mark.anyio
async def test_same_key_rejects_changed_source_or_schema() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)
    key = _key()
    await registry.get_or_prepare(key, "frag_a", {"u_a": object()})

    with pytest.raises(GraphProgramRegistryError, match="源码或 uniform schema"):
        await registry.get_or_prepare(key, "frag_b", {"u_a": object()})
    with pytest.raises(GraphProgramRegistryError, match="源码或 uniform schema"):
        await registry.get_or_prepare(key, "frag_a", {"u_b": object()})

    assert registry.compile_count == 1
    assert registry.cache_hit_count == 0
    assert registry.cache_size == 1


@pytest.mark.anyio
async def test_distinct_keys_coexist_in_cache() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)

    first = await registry.get_or_prepare(_key("a"), "frag_a", {})
    second = await registry.get_or_prepare(_key("b"), "frag_b", {})

    assert first is not second
    assert registry.compile_count == 2
    assert registry.cache_hit_count == 0
    assert registry.cache_size == 2


@pytest.mark.anyio
async def test_key_binds_baked_values_and_dimensions() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)

    await registry.get_or_prepare(_key("a", baked="v1"), "frag_a", {})
    await registry.get_or_prepare(_key("a", baked="v2"), "frag_a", {})
    await registry.get_or_prepare(_key("a", baked="v1", width=128), "frag_a", {})

    assert registry.compile_count == 3
    assert registry.cache_size == 3
    assert renderer.prepare_calls[1] == ("frag_a", 64, 64)
    assert renderer.prepare_calls[2] == ("frag_a", 128, 64)


@pytest.mark.anyio
async def test_compile_budget_is_fail_closed_but_hits_still_work() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer, max_compiles=2)
    key_a = _key("a")
    await registry.get_or_prepare(key_a, "frag_a", {})
    await registry.get_or_prepare(_key("b"), "frag_b", {})

    with pytest.raises(GraphProgramBudgetError, match="预算"):
        await registry.get_or_prepare(_key("c"), "frag_c", {})

    assert len(renderer.prepare_calls) == 2
    assert registry.compile_count == 2
    assert registry.cache_size == 2
    hit = await registry.get_or_prepare(key_a, "frag_a", {})
    assert hit is renderer.prepared[0]
    assert registry.cache_hit_count == 1


@pytest.mark.anyio
async def test_capacity_evicts_lru_and_releases_handle() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer, max_programs=2)
    key_a, key_b, key_c = _key("a"), _key("b"), _key("c")
    await registry.get_or_prepare(key_a, "frag_a", {})
    await registry.get_or_prepare(key_b, "frag_b", {})
    await registry.get_or_prepare(key_a, "frag_a", {})

    await registry.get_or_prepare(key_c, "frag_c", {})

    assert key_b not in registry
    assert renderer.prepared[1].close_calls == 1
    assert key_a in registry and key_c in registry
    assert renderer.prepared[0].close_calls == 0
    assert renderer.prepared[2].close_calls == 0
    assert registry.cache_size == 2


@pytest.mark.anyio
async def test_failed_compile_consumes_budget_without_caching() -> None:
    renderer = FakeRenderer()
    renderer.failing_sources.add("frag_bad")
    registry = GraphProgramRegistry(renderer, max_compiles=1)

    with pytest.raises(RuntimeError, match="compile failed"):
        await registry.get_or_prepare(_key("a"), "frag_bad", {})

    assert registry.compile_count == 1
    assert registry.cache_size == 0
    with pytest.raises(GraphProgramBudgetError):
        await registry.get_or_prepare(_key("b"), "frag_b", {})


@pytest.mark.anyio
async def test_failed_branch_compile_does_not_evict_anchor_at_capacity() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer, max_programs=1, max_compiles=2)
    anchor_key = _key("anchor")
    anchor = await registry.get_or_prepare(anchor_key, "frag_anchor", {})
    renderer.failing_sources.add("frag_branch")

    with pytest.raises(RuntimeError, match="compile failed"):
        await registry.get_or_prepare(_key("branch"), "frag_branch", {})

    assert anchor_key in registry
    assert registry.cache_size == 1
    assert anchor.close_calls == 0
    assert await registry.get_or_prepare(anchor_key, "frag_anchor", {}) is anchor


@pytest.mark.anyio
async def test_discard_releases_branch_without_touching_anchor() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)
    anchor_key, branch_key = _key("anchor"), _key("branch")
    anchor = await registry.get_or_prepare(anchor_key, "frag_anchor", {})
    branch = await registry.get_or_prepare(branch_key, "frag_branch", {})

    assert await registry.discard(branch_key) is True
    assert await registry.discard(branch_key) is False

    assert branch_key not in registry
    assert branch.close_calls == 1
    assert anchor_key in registry
    assert anchor.close_calls == 0
    hit = await registry.get_or_prepare(anchor_key, "frag_anchor", {})
    assert hit is anchor
    assert registry.cache_hit_count == 1


@pytest.mark.anyio
async def test_failed_eviction_close_keeps_anchor_tracked_for_retry() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer, max_programs=1)
    anchor_key = _key("anchor")
    anchor = await registry.get_or_prepare(anchor_key, "frag_anchor", {})
    anchor.close_failures_remaining = 1

    with pytest.raises(RuntimeError, match="close failed"):
        await registry.get_or_prepare(_key("branch"), "frag_branch", {})

    assert anchor_key in registry
    assert registry.cache_size == 1
    assert anchor.close_calls == 1
    assert renderer.prepared[1].close_calls == 1

    await registry.close_all()
    assert anchor.close_calls == 2
    assert registry.cache_size == 0


@pytest.mark.anyio
async def test_failed_discard_close_keeps_program_tracked_for_retry() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)
    branch_key = _key("branch")
    branch = await registry.get_or_prepare(branch_key, "frag_branch", {})
    branch.close_failures_remaining = 1

    with pytest.raises(RuntimeError, match="close failed"):
        await registry.discard(branch_key)

    assert branch_key in registry
    assert registry.cache_size == 1
    assert await registry.discard(branch_key) is True
    assert branch_key not in registry


@pytest.mark.anyio
async def test_close_all_retains_failed_handles_and_allows_retry() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)
    prepared = await registry.get_or_prepare(_key("a"), "frag_a", {})
    prepared.close_failures_remaining = 1

    with pytest.raises(RuntimeError, match="close failed"):
        await registry.close_all()

    assert registry.cache_size == 1
    with pytest.raises(GraphProgramRegistryClosedError):
        await registry.get_or_prepare(_key("b"), "frag_b", {})

    await registry.close_all()
    assert prepared.close_calls == 2
    assert registry.cache_size == 0


@pytest.mark.anyio
async def test_close_all_releases_every_handle_and_is_idempotent() -> None:
    renderer = FakeRenderer()
    registry = GraphProgramRegistry(renderer)
    await registry.get_or_prepare(_key("a"), "frag_a", {})
    await registry.get_or_prepare(_key("b"), "frag_b", {})

    await registry.close_all()
    await registry.close_all()

    assert [item.close_calls for item in renderer.prepared] == [1, 1]
    assert registry.cache_size == 0
    assert registry.compile_count == 2
    with pytest.raises(GraphProgramRegistryClosedError, match="已关闭"):
        await registry.get_or_prepare(_key("c"), "frag_c", {})
    assert len(renderer.prepare_calls) == 2


def test_summary_exposes_only_safe_counters() -> None:
    registry = GraphProgramRegistry(FakeRenderer(), max_programs=3, max_compiles=7)

    assert registry.summary() == {
        "compile_count": 0,
        "cache_hit_count": 0,
        "cache_size": 0,
        "max_programs": 3,
        "max_compiles": 7,
    }


def test_registry_rejects_non_positive_bounds() -> None:
    with pytest.raises(ValueError, match="max_programs"):
        GraphProgramRegistry(FakeRenderer(), max_programs=0)
    with pytest.raises(ValueError, match="max_compiles"):
        GraphProgramRegistry(FakeRenderer(), max_compiles=-1)


def test_key_rejects_empty_identity_and_bad_dimensions() -> None:
    with pytest.raises(ValueError, match="topology_sha256"):
        GraphProgramKey(
            compiler_version="compiler_v1",
            topology_sha256="",
            active_parameter_manifest_sha256="manifest_v1",
            baked_parameter_sha256="baked",
            width=64,
            height=64,
        )
    with pytest.raises(ValueError, match="正整数"):
        GraphProgramKey(
            compiler_version="compiler_v1",
            topology_sha256="topology",
            active_parameter_manifest_sha256="manifest_v1",
            baked_parameter_sha256="baked",
            width=0,
            height=64,
        )
