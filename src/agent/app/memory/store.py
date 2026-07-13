"""提供基于 LangGraph Store 的 Shader 项目记忆操作."""

from __future__ import annotations

from langgraph.store.base import BaseStore

from agent.app.memory.models import (
    MEMORY_SCHEMA_VERSION,
    REVIEW_IMPORTANCE,
    MemoryItem,
    build_review_summary,
    review_memory_id,
    utc_now,
)

MEMORY_NAMESPACE_ROOT = ("shadergen", "v1")


def memory_namespace(project_id: str) -> tuple[str, ...]:
    """返回版本化项目 Memory namespace."""
    normalized = project_id.strip()
    if not normalized:
        raise ValueError("project_id 不能为空。")
    return (*MEMORY_NAMESPACE_ROOT, normalized, "memory")


async def list_project_memories(
    store: BaseStore,
    project_id: str,
    *,
    limit: int = 50,
) -> tuple[MemoryItem, ...]:
    """读取并校验一个项目的候选记忆."""
    items = await store.asearch(memory_namespace(project_id), limit=limit, offset=0)
    return tuple(MemoryItem.from_value(dict(item.value)) for item in items)


async def upsert_review_memory(
    store: BaseStore,
    *,
    project_id: str,
    source_run_id: str,
    glsl_sha256: str,
    iteration: int | None,
    evaluation: str,
    suggestions: tuple[str, ...],
) -> MemoryItem:
    """用稳定 key 幂等写入一条 Review Memory."""
    namespace = memory_namespace(project_id)
    memory_id = review_memory_id(glsl_sha256)
    existing = await store.aget(namespace, memory_id)
    now = utc_now()
    created_at = now
    if existing is not None:
        created_at = MemoryItem.from_value(dict(existing.value)).created_at

    item = MemoryItem(
        schema_version=MEMORY_SCHEMA_VERSION,
        memory_id=memory_id,
        kind="review",
        summary=build_review_summary(evaluation, suggestions),
        importance=REVIEW_IMPORTANCE,
        source_run_id=source_run_id,
        glsl_sha256=glsl_sha256.lower(),
        iteration=iteration,
        created_at=created_at,
        updated_at=now,
    )
    await store.aput(namespace, memory_id, item.to_value(), index=False)
    return item


async def clear_project_memories(
    store: BaseStore,
    project_id: str,
    *,
    page_size: int = 50,
) -> int:
    """从 offset 0 分页逐项清除项目 Memory，避免边删边翻页漏项."""
    if page_size <= 0:
        raise ValueError("page_size 必须大于 0。")
    namespace = memory_namespace(project_id)
    deleted = 0
    while True:
        items = await store.asearch(namespace, limit=page_size, offset=0)
        if not items:
            return deleted
        for item in items:
            await store.adelete(namespace, item.key)
            deleted += 1
