"""Backend adapter for the current Direct generation runtime."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any


class PublicArtifactNotFoundError(FileNotFoundError):
    """A requested public parent Artifact does not exist."""


class ProjectBusyError(RuntimeError):
    """Another request already owns the project lock."""


class ProjectLockRegistry:
    def __init__(self) -> None:
        self._active: set[str] = set()
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def hold(self, project_id: str) -> AsyncIterator[None]:
        async with self._guard:
            if project_id in self._active:
                raise ProjectBusyError("project already has an active run")
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
    service: Any,
    filename: str | None = None,
    on_progress: Callable[[dict[str, Any], bytes | None], None] | None = None,
) -> Any:
    return await service.generate(
        image,
        content_type,
        project_id=project_id,
        run_id=run_id,
        quality_preset=quality_preset,
        instruction=instruction,
        filename=filename,
        on_progress=on_progress,
    )


async def read_shader_run_artifact(
    run_id: str,
    artifact_name: str,
    *,
    service: Any,
) -> Any:
    try:
        artifact = service.read_public_artifact(run_id, artifact_name)
        return await artifact if inspect.isawaitable(artifact) else artifact
    except (FileNotFoundError, ValueError) as exc:
        raise PublicArtifactNotFoundError("run artifact not found") from exc


async def close_generation_runtime(service: Any | None) -> None:
    if service is None:
        return
    close = getattr(service, "aclose", None) or getattr(service, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result
