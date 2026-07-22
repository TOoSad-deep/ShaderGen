"""PNG-to-Shader 产品生成和项目 Memory 后端编排服务."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from agent.app.services import png_to_shader_min, png_to_shader_v1

MemoryUnavailableError = png_to_shader_v1.MemoryUnavailableError
NoValidatedShaderError = png_to_shader_v1.NoValidatedShaderError
PublicArtifactNotFoundError = png_to_shader_v1.PublicArtifactNotFoundError
default_png_to_shader_v1_service = png_to_shader_v1.default_png_to_shader_v1_service


def get_default_png_to_shader_min_service() -> Any | None:
    """返回应用 lifespan 注入的 scene_mvp 默认服务."""
    return png_to_shader_min.default_png_to_shader_min_service


async def close_png_to_shader_min_service(service: Any | None) -> None:
    """幂等关闭 lifespan 注入的最小流水线服务（若其声明关闭接口）."""
    if service is None:
        return
    close = getattr(service, "aclose", None) or getattr(service, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


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


async def generate_scene_shader_from_image(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    quality_preset: str,
    instruction: str,
    service: png_to_shader_min.PngToShaderMinService,
    on_progress: png_to_shader_min.MinProgressCallback | None = None,
) -> png_to_shader_min.PngToShaderMinResult:
    """通过 Agent 公共接口执行 scene_mvp 最小流水线."""
    return await png_to_shader_min.generate_png_to_shader_min(
        image,
        content_type,
        project_id=project_id,
        run_id=run_id,
        quality_preset=quality_preset,
        instruction=instruction,
        on_progress=on_progress,
        service=service,
    )


def read_shader_run_artifact(
    run_id: str,
    artifact_name: str,
    *,
    service: png_to_shader_v1.PngToShaderV1Service,
    min_service: Any | None = None,
) -> png_to_shader_v1.PublicArtifact | png_to_shader_min.MinPublicArtifact:
    """从 procedural_v1 或 scene_mvp Service 读取固定白名单 Artifact."""
    try:
        return service.read_public_artifact(run_id, artifact_name)
    except PublicArtifactNotFoundError as procedural_error:
        if min_service is None:
            raise
        try:
            return cast(
                png_to_shader_min.MinPublicArtifact,
                min_service.read_public_artifact(run_id, artifact_name),
            )
        except Exception as exc:
            if isinstance(exc, (FileNotFoundError, ValueError)) or (
                type(exc).__name__ == "PublicArtifactNotFoundError"
            ):
                raise PublicArtifactNotFoundError(
                    "未找到运行 Artifact。"
                ) from procedural_error
            raise


async def clear_png_to_shader_project_memory(
    project_id: str,
    *,
    service: png_to_shader_v1.PngToShaderV1Service,
) -> png_to_shader_v1.ClearPngToShaderMemoryResult:
    """清除 V1 checkpoint 和其长期策略 Memory."""
    return await service.clear_memory(project_id)
