"""scene_mvp 最小 Graph 的稳定应用服务。."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.app.contracts.llm import LLMGateway
from agent.app.graphs.png_to_shader_min_graph import (
    PNG_TO_SHADER_MIN_RECURSION_LIMIT,
    build_png_to_shader_min_graph,
    png_to_shader_min_artifact_store,
    png_to_shader_min_graph,
    png_to_shader_min_renderer_registry,
)
from agent.app.nodes.png_to_shader_min import MinRendererRegistry
from agent.app.nodes.png_to_shader_min.model_author import effective_llm_budget
from shaderforge.store import LocalArtifactStore


class MinPipelineError(RuntimeError):
    """表示最小流水线没有产生可公开结果。."""


class MinPublicArtifactNotFoundError(FileNotFoundError):
    """表示白名单内产物不存在。."""


@dataclass(frozen=True)
class MinPublicArtifact:
    """可由 Backend 安全返回的白名单产物。."""

    data: bytes
    content_type: str
    filename: str


@dataclass(frozen=True)
class PngToShaderMinResult:
    """Backend 可直接映射的最小流水线结果。."""

    project_id: str
    run_id: str
    glsl: str
    render_width: int
    render_height: int
    status: str
    stop_reason: str
    current_best_mae: float
    render_count: int
    llm_call_count: int
    renderer_path: str
    target_mae: float
    target_reached: bool
    prepare_duration_ms: float
    uniform_render_count: int
    uniform_render_p95_ms: float
    scene: dict[str, Any]
    trace: tuple[dict[str, Any], ...]


_PUBLIC_ARTIFACTS = {
    "final-render": ("final/render.png", "image/png", "final-render.png"),
    "metrics": (
        "final/metrics.json",
        "application/json; charset=utf-8",
        "metrics.json",
    ),
    "manifest": (
        "final/manifest.json",
        "application/json; charset=utf-8",
        "manifest.json",
    ),
}


class PngToShaderMinService:
    """执行最小 Graph，并在所有路径兜底关闭 run Renderer。."""

    def __init__(
        self,
        graph: Any,
        artifacts: LocalArtifactStore,
        renderers: MinRendererRegistry,
        *,
        llm_budget: int = 0,
        refine_budget: int = 0,
    ) -> None:
        """绑定同一组合根中的 Graph、Artifact、Renderer 和保守模型预算。."""
        self.graph = graph
        self.artifacts = artifacts
        self.renderers = renderers
        self.llm_budget = effective_llm_budget(llm_budget)
        self.refine_budget = max(0, int(refine_budget))

    async def generate(
        self,
        image: bytes,
        content_type: str,
        *,
        project_id: str,
        run_id: str,
        instruction: str = "",
    ) -> PngToShaderMinResult:
        """以显式 scene_mvp 的小批 draw 与有界模型预算执行完整链路。."""
        try:
            state = await self.graph.ainvoke(
                {
                    "project_id": project_id,
                    "run_id": run_id,
                    "image": image,
                    "content_type": content_type,
                    "instruction": instruction,
                    "render_budget": 40,
                    "llm_budget": self.llm_budget,
                    "refine_budget": self.refine_budget,
                    "target_mae": 0.08,
                },
                {"recursion_limit": PNG_TO_SHADER_MIN_RECURSION_LIMIT},
            )
        finally:
            await self.renderers.close(project_id, run_id)
        final = state.get("final_result") if isinstance(state, dict) else None
        if not isinstance(final, dict) or not final.get("glsl"):
            raise MinPipelineError(
                str(state.get("stop_reason", "scene_mvp_no_result"))
                if isinstance(state, dict)
                else "scene_mvp_no_result"
            )
        return PngToShaderMinResult(
            project_id=str(final["project_id"]),
            run_id=str(final["run_id"]),
            glsl=str(final["glsl"]),
            render_width=int(final["render_width"]),
            render_height=int(final["render_height"]),
            status=str(final["status"]),
            stop_reason=str(final["stop_reason"]),
            current_best_mae=float(final["current_best_mae"]),
            render_count=int(final["render_count"]),
            llm_call_count=int(final["llm_call_count"]),
            renderer_path=str(final["renderer_path"]),
            target_mae=float(final["target_mae"]),
            target_reached=bool(final["target_reached"]),
            prepare_duration_ms=float(final["prepare_duration_ms"]),
            uniform_render_count=int(final["uniform_render_count"]),
            uniform_render_p95_ms=float(final["uniform_render_p95_ms"]),
            scene=dict(final["scene"]),
            trace=tuple(final["trace"]),
        )

    def read_public_artifact(self, run_id: str, name: str) -> MinPublicArtifact:
        """只读取 final-render、metrics 和 manifest。."""
        descriptor = _PUBLIC_ARTIFACTS.get(name)
        if descriptor is None:
            raise ValueError("不支持的 scene_mvp Artifact 名称。")
        relative_path, content_type, filename = descriptor
        try:
            data = self.artifacts.resolve_run(run_id).read_bytes(relative_path)
        except FileNotFoundError as exc:
            raise MinPublicArtifactNotFoundError(name) from exc
        return MinPublicArtifact(data, content_type, filename)


def create_png_to_shader_min_service(
    *,
    artifact_store: LocalArtifactStore | None = None,
    gateway: LLMGateway | None = None,
    llm_budget: int = 0,
    refine_budget: int = 0,
) -> PngToShaderMinService:
    """创建测试或独立运行使用的最小服务组合根。."""
    artifacts = artifact_store or png_to_shader_min_artifact_store
    renderers = MinRendererRegistry()
    graph = build_png_to_shader_min_graph(
        artifact_store=artifacts,
        renderer_registry=renderers,
        gateway=gateway,
    )
    return PngToShaderMinService(
        graph,
        artifacts,
        renderers,
        llm_budget=llm_budget,
        refine_budget=refine_budget,
    )


default_png_to_shader_min_service = PngToShaderMinService(
    png_to_shader_min_graph,
    png_to_shader_min_artifact_store,
    png_to_shader_min_renderer_registry,
    llm_budget=6,
    refine_budget=1,
)


async def generate_png_to_shader_min(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    instruction: str = "",
    service: PngToShaderMinService = default_png_to_shader_min_service,
) -> PngToShaderMinResult:
    """通过稳定入口执行 scene_mvp。."""
    return await service.generate(
        image,
        content_type,
        project_id=project_id,
        run_id=run_id,
        instruction=instruction,
    )


__all__ = [
    "MinPipelineError",
    "MinPublicArtifact",
    "MinPublicArtifactNotFoundError",
    "PngToShaderMinResult",
    "PngToShaderMinService",
    "create_png_to_shader_min_service",
    "default_png_to_shader_min_service",
    "generate_png_to_shader_min",
]
