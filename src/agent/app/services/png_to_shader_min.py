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
    template_version: str
    quality_preset: str
    current_best_mae: float
    current_best_loss: float
    metric_breakdown: dict[str, Any]
    render_count: int
    render_budget: int
    llm_call_count: int
    llm_budget: int
    refine_budget: int
    renderer_path: str
    target_mae: float
    target_loss: float
    target_reached: bool
    prepare_duration_ms: float
    uniform_render_count: int
    uniform_render_p95_ms: float
    scene: dict[str, Any]
    trace: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MinQualityBudget:
    """scene_mvp 各质量档位的硬预算。."""

    render_budget: int
    llm_budget: int
    refine_budget: int
    target_mae: float = 0.08
    target_loss: float = 0.08


MIN_QUALITY_BUDGETS = {
    "fast": MinQualityBudget(render_budget=48, llm_budget=2, refine_budget=1),
    "balanced": MinQualityBudget(render_budget=96, llm_budget=4, refine_budget=2),
    "high": MinQualityBudget(render_budget=160, llm_budget=6, refine_budget=3),
}


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
        quality_preset: str = "balanced",
        instruction: str = "",
    ) -> PngToShaderMinResult:
        """以显式 scene_mvp 的小批 draw 与有界模型预算执行完整链路。."""
        try:
            policy = MIN_QUALITY_BUDGETS[quality_preset]
        except KeyError as exc:
            raise ValueError(f"不支持的 scene_mvp 质量档位：{quality_preset}") from exc
        llm_budget = min(self.llm_budget, policy.llm_budget)
        refine_budget = min(self.refine_budget, policy.refine_budget)
        try:
            state = await self.graph.ainvoke(
                {
                    "project_id": project_id,
                    "run_id": run_id,
                    "image": image,
                    "content_type": content_type,
                    "instruction": instruction,
                    "quality_preset": quality_preset,
                    "render_budget": policy.render_budget,
                    "llm_budget": llm_budget,
                    "refine_budget": refine_budget,
                    "target_mae": policy.target_mae,
                    "target_loss": policy.target_loss,
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
            template_version=str(final["template_version"]),
            quality_preset=str(final["quality_preset"]),
            current_best_mae=float(final["current_best_mae"]),
            current_best_loss=float(final["current_best_loss"]),
            metric_breakdown=dict(final["metric_breakdown"]),
            render_count=int(final["render_count"]),
            render_budget=int(final["render_budget"]),
            llm_call_count=int(final["llm_call_count"]),
            llm_budget=int(final["llm_budget"]),
            refine_budget=int(final["refine_budget"]),
            renderer_path=str(final["renderer_path"]),
            target_mae=float(final["target_mae"]),
            target_loss=float(final["target_loss"]),
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
    refine_budget=3,
)


async def generate_png_to_shader_min(
    image: bytes,
    content_type: str,
    *,
    project_id: str,
    run_id: str,
    quality_preset: str = "balanced",
    instruction: str = "",
    service: PngToShaderMinService = default_png_to_shader_min_service,
) -> PngToShaderMinResult:
    """通过稳定入口执行 scene_mvp。."""
    return await service.generate(
        image,
        content_type,
        project_id=project_id,
        run_id=run_id,
        quality_preset=quality_preset,
        instruction=instruction,
    )


__all__ = [
    "MIN_QUALITY_BUDGETS",
    "MinPipelineError",
    "MinPublicArtifact",
    "MinPublicArtifactNotFoundError",
    "MinQualityBudget",
    "PngToShaderMinResult",
    "PngToShaderMinService",
    "create_png_to_shader_min_service",
    "default_png_to_shader_min_service",
    "generate_png_to_shader_min",
]
