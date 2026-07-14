"""Shader 生成、评审和项目 Memory 公共服务."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from agent.app.config.model_config import SHADER_GEN_MODEL_NAME
from agent.app.graphs.shader_generation_graph import (
    build_default_shader_generation_graph,
    shader_generation_checkpointer,
    shader_generation_graph,
    shader_generation_store,
)
from agent.app.memory.models import MemoryStatus
from agent.app.memory.store import clear_project_memories
from agent.app.parsers.shader_response import ParsedShaderReview
from agent.app.parsers.shader_response import extract_glsl as _extract_glsl
from agent.app.parsers.shader_response import (
    parse_shader_review_response as _parse_shader_review_response,
)
from agent.app.services.errors import MemoryUnavailableError


@dataclass(frozen=True)
class ShaderGenerationResult:
    """Agent 对后端暴露的 Shader 生成结果."""

    project_id: str
    glsl: str
    glsl_model_name: str
    vision_model_name: str
    memory_status: MemoryStatus
    model_calls: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    logs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ShaderReviewResult:
    """Agent 对渲染结果的评估和修改建议."""

    project_id: str
    evaluation: str
    suggestions: tuple[str, ...]
    review_model_name: str
    memory_status: MemoryStatus
    model_calls: tuple[dict[str, Any], ...] = ()
    events: tuple[dict[str, Any], ...] = ()
    logs: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ClearMemoryResult:
    """项目 Memory 清除计数."""

    deleted_memories: int


class ShaderGenerationService:
    """持有已注入 persistence 的 Shader Graph 服务."""

    def __init__(
        self,
        graph: Any,
        checkpointer: Any,
        store: Any,
        memory_status: MemoryStatus,
    ) -> None:
        """保存已编译图和对应 persistence 资源."""
        self.graph = graph
        self.checkpointer = checkpointer
        self.store = store
        self.memory_status = memory_status

    async def invoke(self, project_id: str, state: dict[str, Any]) -> dict[str, Any]:
        """在指定项目 thread 中调用图并识别 persistence 失败."""
        try:
            result = await self.graph.ainvoke(
                state,
                {"configurable": {"thread_id": project_id}},
            )
            return cast(dict[str, Any], result)
        except Exception as exc:
            module = type(exc).__module__
            if module.startswith(("psycopg", "langgraph.checkpoint")):
                raise MemoryUnavailableError("任务记忆暂时不可用。") from exc
            raise

    async def clear_memory(self, project_id: str) -> ClearMemoryResult:
        """清除 checkpoint thread 与项目 Store namespace."""
        try:
            await self.checkpointer.adelete_thread(project_id)
            deleted = await clear_project_memories(self.store, project_id)
        except Exception as exc:
            raise MemoryUnavailableError("清除项目记忆失败。") from exc
        return ClearMemoryResult(deleted_memories=deleted)


def create_shader_generation_service(
    *,
    checkpointer: Any,
    store: Any,
    memory_status: MemoryStatus,
) -> ShaderGenerationService:
    """使用外部 persistence 创建后端可注入的服务."""
    graph = build_default_shader_generation_graph(checkpointer=checkpointer, store=store)
    return ShaderGenerationService(graph, checkpointer, store, memory_status)


default_shader_generation_service = ShaderGenerationService(
    shader_generation_graph,
    shader_generation_checkpointer,
    shader_generation_store,
    "ephemeral",
)


def shader_generation_models() -> tuple[str, str]:
    """返回当前 Shader 生成链路使用的模型名."""
    return SHADER_GEN_MODEL_NAME, SHADER_GEN_MODEL_NAME


def extract_glsl(text: str) -> str:
    """从模型输出中提取 GLSL 代码."""
    return _extract_glsl(text)


def parse_shader_review_response(text: str) -> ParsedShaderReview:
    """解析模型输出的渲染评审 JSON."""
    return _parse_shader_review_response(text)


async def generate_glsl_from_image(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    service: ShaderGenerationService = default_shader_generation_service,
) -> ShaderGenerationResult:
    """根据图片生成 fragment shader，并恢复同项目轻量状态."""
    state = await service.invoke(
        project_id,
        {
            "operation": "generate",
            "project_id": project_id,
            "image": image,
            "content_type": content_type,
            "run_id": run_id,
            "memory_status": service.memory_status,
            "model_calls": (),
            "events": (),
            "logs": (),
        },
    )
    return ShaderGenerationResult(
        project_id=project_id,
        glsl=state["glsl"],
        glsl_model_name=state["glsl_model_name"],
        vision_model_name=state["vision_model_name"],
        memory_status=state.get("memory_status", service.memory_status),
        model_calls=state.get("model_calls", ()),
        events=state.get("events", ()),
        logs=state.get("logs", ()),
    )


async def review_shader_render(
    original_image: bytes,
    original_content_type: str,
    rendered_image: bytes,
    rendered_content_type: str,
    glsl: str,
    *,
    project_id: str,
    run_id: str,
    service: ShaderGenerationService = default_shader_generation_service,
) -> ShaderReviewResult:
    """根据原图、渲染图和 GLSL 生成评审并晋升项目记忆."""
    from hashlib import sha256

    state = await service.invoke(
        project_id,
        {
            "operation": "review",
            "project_id": project_id,
            "image": original_image,
            "content_type": original_content_type,
            "rendered_image": rendered_image,
            "rendered_content_type": rendered_content_type,
            "glsl": glsl,
            "last_glsl_sha256": sha256(glsl.encode("utf-8")).hexdigest(),
            "run_id": run_id,
            "memory_status": service.memory_status,
            "model_calls": (),
            "events": (),
            "logs": (),
        },
    )
    return ShaderReviewResult(
        project_id=project_id,
        evaluation=state["evaluation"],
        suggestions=state["suggestions"],
        review_model_name=state["review_model_name"],
        memory_status=state.get("memory_status", service.memory_status),
        model_calls=state.get("model_calls", ()),
        events=state.get("events", ()),
        logs=state.get("logs", ()),
    )


async def clear_project_memory(
    project_id: str,
    *,
    service: ShaderGenerationService = default_shader_generation_service,
) -> ClearMemoryResult:
    """清除指定项目的任务内和长期记忆."""
    return await service.clear_memory(project_id)
