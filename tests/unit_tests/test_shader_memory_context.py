from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from langgraph.store.memory import InMemoryStore

from agent.app.context.builder import ContextPolicy, build_context_pack
from agent.app.memory.models import MEMORY_SCHEMA_VERSION, MemoryItem
from agent.app.memory.store import (
    clear_project_memories,
    list_project_memories,
    memory_namespace,
    upsert_review_memory,
)
from backend.app.services.shader import ProjectBusyError, ProjectLockRegistry


def memory_item(
    memory_id: str,
    *,
    kind: str,
    summary: str,
    glsl_sha256: str | None = None,
    iteration: int | None = None,
    updated_offset: int = 0,
) -> MemoryItem:
    created = datetime(2026, 7, 13, tzinfo=UTC)
    return MemoryItem(
        schema_version=MEMORY_SCHEMA_VERSION,
        memory_id=memory_id,
        kind=kind,  # type: ignore[arg-type]
        summary=summary,
        importance=0.5,
        source_run_id=f"run-{memory_id}",
        glsl_sha256=glsl_sha256,
        iteration=iteration,
        created_at=created,
        updated_at=created + timedelta(minutes=updated_offset),
    )


@pytest.mark.anyio
async def test_review_memory_upsert_preserves_created_at_and_refreshes_value() -> None:
    store = InMemoryStore()
    digest = sha256(b"shader").hexdigest()

    first = await upsert_review_memory(
        store,
        project_id="project-1",
        source_run_id="run-1",
        glsl_sha256=digest,
        iteration=1,
        evaluation="初次评审",
        suggestions=("提高亮度",),
    )
    second = await upsert_review_memory(
        store,
        project_id="project-1",
        source_run_id="run-2",
        glsl_sha256=digest,
        iteration=2,
        evaluation="再次评审",
        suggestions=("降低饱和度",),
    )

    memories = await list_project_memories(store, "project-1")
    assert len(memories) == 1
    assert second.created_at == first.created_at
    assert second.updated_at >= first.updated_at
    assert second.source_run_id == "run-2"
    assert second.iteration == 2
    assert "降低饱和度" in second.summary


def test_context_builder_prioritizes_current_shader_review() -> None:
    current_hash = sha256(b"current").hexdigest()
    old_hash = sha256(b"old").hexdigest()
    memories = [
        memory_item(
            "review-old",
            kind="review",
            summary="旧版本建议",
            glsl_sha256=old_hash,
            iteration=1,
            updated_offset=20,
        ),
        memory_item(
            "review-current",
            kind="review",
            summary="当前版本建议",
            glsl_sha256=current_hash,
            iteration=2,
        ),
        memory_item("constraint-1", kind="constraint", summary="必须兼容 WebGL1"),
    ]

    pack = build_context_pack(
        {"phase": "reviewed", "iteration": 2, "last_glsl_sha256": current_hash},
        memories,
    )

    assert pack.current_review == "当前版本建议"
    assert pack.recent_reviews == ("旧版本建议",)
    assert pack.confirmed_constraints == ("必须兼容 WebGL1",)
    assert pack.selected_memory_ids[0] == "constraint-1"
    assert pack.estimated_tokens <= 2_000


def test_context_builder_drops_low_priority_items_over_budget() -> None:
    memories = [
        memory_item(
            f"review-{index}",
            kind="review",
            summary=(f"历史建议 {index} " * 80),
            glsl_sha256=sha256(str(index).encode()).hexdigest(),
            updated_offset=index,
        )
        for index in range(8)
    ]

    pack = build_context_pack(
        {"phase": "generated", "iteration": 8},
        memories,
        ContextPolicy(
            max_history_tokens=180,
            max_memory_candidates=50,
            max_historical_reviews=3,
            max_summary_chars=400,
        ),
    )

    assert pack.estimated_tokens <= 180
    assert pack.dropped_memory_count > 0
    assert len(pack.recent_reviews) < 3


@pytest.mark.anyio
async def test_clear_project_memories_restarts_pagination_from_zero() -> None:
    store = InMemoryStore()
    namespace = memory_namespace("project-clear")
    for index in range(7):
        item = memory_item(
            f"review-{index}",
            kind="review",
            summary=f"建议 {index}",
            glsl_sha256=sha256(str(index).encode()).hexdigest(),
        )
        await store.aput(namespace, item.memory_id, item.to_value(), index=False)

    deleted = await clear_project_memories(store, "project-clear", page_size=2)

    assert deleted == 7
    assert await store.asearch(namespace, limit=10) == []


@pytest.mark.anyio
async def test_project_lock_rejects_concurrent_request_without_waiting() -> None:
    registry = ProjectLockRegistry()

    async with registry.hold("project-lock"):
        with pytest.raises(ProjectBusyError):
            async with registry.hold("project-lock"):
                raise AssertionError("不应取得同一项目锁。")
