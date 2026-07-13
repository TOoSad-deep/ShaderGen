"""Shader 生成、评审和项目 Memory 后端编排服务."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from agent.app.services import shader_generation

MemoryUnavailableError = shader_generation.MemoryUnavailableError
default_shader_generation_service = (
    shader_generation.default_shader_generation_service
)


class ProjectBusyError(RuntimeError):
    """表示同一项目已有请求在执行."""


class ProjectLockRegistry:
    """单进程内的 project_id 非等待互斥锁."""

    def __init__(self) -> None:
        """初始化活动项目集合和保护锁."""
        self._active: set[str] = set()
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, project_id: str):
        """占用 project_id；已占用时立即抛出冲突."""
        async with self._guard:
            if project_id in self._active:
                raise ProjectBusyError("当前项目已有任务正在执行。")
            self._active.add(project_id)
        try:
            yield
        finally:
            async with self._guard:
                self._active.discard(project_id)


def get_shader_generation_models() -> tuple[str, str]:
    """返回当前 Shader 生成链路的模型名."""
    return shader_generation.shader_generation_models()


async def generate_shader_from_image(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    service: shader_generation.ShaderGenerationService,
) -> shader_generation.ShaderGenerationResult:
    """通过 Agent 公共接口生成 Shader."""
    return await shader_generation.generate_glsl_from_image(
        image,
        content_type,
        project_id=project_id,
        run_id=run_id,
        service=service,
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
    service: shader_generation.ShaderGenerationService,
) -> shader_generation.ShaderReviewResult:
    """通过 Agent 公共接口评审渲染结果."""
    return await shader_generation.review_shader_render(
        original_image,
        original_content_type,
        rendered_image,
        rendered_content_type,
        glsl,
        project_id=project_id,
        run_id=run_id,
        service=service,
    )


async def clear_shader_project_memory(
    project_id: str,
    *,
    service: shader_generation.ShaderGenerationService,
) -> shader_generation.ClearMemoryResult:
    """通过 Agent 公共接口清除项目 Memory."""
    return await shader_generation.clear_project_memory(project_id, service=service)
