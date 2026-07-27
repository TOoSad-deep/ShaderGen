"""scene_mvp 产品生成的后端适配服务."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, cast

from agent.app.services import png_to_shader_min

PublicArtifactNotFoundError = png_to_shader_min.MinPublicArtifactNotFoundError


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


async def read_shader_run_artifact(
    run_id: str,
    artifact_name: str,
    *,
    service: Any,
) -> png_to_shader_min.MinPublicArtifact:
    """从 scene_mvp Service 读取固定白名单 Artifact."""
    try:
        artifact = service.read_public_artifact(run_id, artifact_name)
        if inspect.isawaitable(artifact):
            artifact = await artifact
        return cast(png_to_shader_min.MinPublicArtifact, artifact)
    except (FileNotFoundError, ValueError) as exc:
        raise PublicArtifactNotFoundError("未找到运行 Artifact。") from exc
