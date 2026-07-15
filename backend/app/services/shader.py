"""PNG-to-Shader V1 生成和项目 Memory 后端编排服务."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from agent.app.services import png_to_shader_v1

MemoryUnavailableError = png_to_shader_v1.MemoryUnavailableError
NoValidatedShaderError = png_to_shader_v1.NoValidatedShaderError
PublicArtifactNotFoundError = png_to_shader_v1.PublicArtifactNotFoundError
default_png_to_shader_v1_service = png_to_shader_v1.default_png_to_shader_v1_service


class ProjectBusyError(RuntimeError):
    """表示同一项目已有请求在执行."""


class ProjectLockRegistry:
    """单进程内的 project_id 非等待互斥锁."""

    def __init__(self) -> None:
        """初始化活动项目集合和保护锁."""
        self._active: set[str] = set()
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, project_id: str) -> AsyncIterator[None]:
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


def get_png_to_shader_v1_models() -> tuple[str, str]:
    """返回 PNG-to-Shader V1 的 Author 与视觉模型名."""
    return png_to_shader_v1.png_to_shader_v1_models()


async def generate_procedural_shader_from_image(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    quality_preset: str,
    instruction: str,
    service: png_to_shader_v1.PngToShaderV1Service,
) -> png_to_shader_v1.PngToShaderV1Result:
    """通过 Agent 公共接口执行 V1 自动闭环."""
    return await png_to_shader_v1.generate_png_to_shader_v1(
        image,
        content_type,
        project_id=project_id,
        run_id=run_id,
        quality_preset=quality_preset,
        instruction=instruction,
        service=service,
    )


def read_shader_run_artifact(
    run_id: str,
    artifact_name: str,
    *,
    service: png_to_shader_v1.PngToShaderV1Service,
) -> png_to_shader_v1.PublicArtifact:
    """通过 V1 Service 读取固定白名单 Artifact."""
    return service.read_public_artifact(run_id, artifact_name)


async def clear_png_to_shader_project_memory(
    project_id: str,
    *,
    service: png_to_shader_v1.PngToShaderV1Service,
) -> png_to_shader_v1.ClearPngToShaderMemoryResult:
    """清除 V1 checkpoint 和其长期策略 Memory."""
    return await service.clear_memory(project_id)
