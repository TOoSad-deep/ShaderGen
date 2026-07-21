"""PNG-to-Shader V1 复用 ShaderForge 公共能力的 capability Executor."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from nodelab.integration import RouteDecider
from nodelab.models import (
    ArtifactDescriptor,
    CapabilityDescriptor,
    CapabilityExecutionRequest,
    NodeDescriptor,
    NodeExecutionResult,
    NodeLabError,
    ensure_json_object,
)
from shaderforge.public import (
    DEFAULT_ACCEPTANCE_POLICY,
    AcceptancePolicy,
    CandidateRecord,
    MetricWeights,
    PlaywrightWebGL1Renderer,
    RendererUnavailableError,
    RenderResult,
    evaluate_render,
    measure_target,
    normalize_target_png,
    select_current_best,
    validate_shader,
)


class ArtifactAccess(Protocol):
    """Adapter 访问不透明 Lab Artifact 的最小边界."""

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> ArtifactDescriptor:
        """保存 Artifact 并返回不含路径的 descriptor."""

    def read_artifact(
        self,
        lab_run_id: str,
        artifact_id: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        """按同一 LabRun 的不透明 id 读取 Artifact."""


class ShaderRenderer(Protocol):
    """真实 Renderer 与测试 Fake 共享的异步边界."""

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        """编译并渲染一个 Fragment Shader."""

    async def close(self) -> None:
        """关闭浏览器资源."""


class RendererFactory(Protocol):
    """为独立 capability 调用创建 Renderer."""

    def __call__(self) -> ShaderRenderer:
        """返回尚未启动的 Renderer."""


def default_renderer_factory() -> ShaderRenderer:
    """创建阶段 B 默认 Playwright WebGL1 Renderer."""
    return PlaywrightWebGL1Renderer()


@dataclass(frozen=True)
class CapabilityExecutionRuntime:
    """为 benchmark 注入可复用 Renderer，不进入 HTTP 请求契约."""

    renderer: ShaderRenderer | None = None
    close_renderer: bool = True
    browser_launch_count: int | None = None


def _input_error(
    request: CapabilityExecutionRequest,
    field: str,
    message: str,
) -> NodeLabError:
    return NodeLabError(
        "input_contract_invalid",
        message,
        stage="capability_input",
        lab_run_id=request.lab_run_id,
        details={"capability_id": request.capability_id, "field": field},
    )


def _required_str(request: CapabilityExecutionRequest, field: str) -> str:
    value = request.inputs.get(field)
    if not isinstance(value, str) or not value.strip():
        raise _input_error(request, field, f"{field} 必须是非空字符串。")
    return value


def _positive_int(
    request: CapabilityExecutionRequest,
    field: str,
    *,
    default: int | None = None,
) -> int:
    value = request.inputs.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise _input_error(request, field, f"{field} 必须是正整数。")
    return value


def _bounded_positive_int(
    request: CapabilityExecutionRequest,
    field: str,
    *,
    maximum: int,
    default: int | None = None,
) -> int:
    value = _positive_int(request, field, default=default)
    if value > maximum:
        raise _input_error(request, field, f"{field} 不能超过 {maximum}。")
    return value


def _optional_mapping(
    request: CapabilityExecutionRequest,
    field: str,
) -> dict[str, Any] | None:
    value = request.inputs.get(field)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _input_error(request, field, f"{field} 必须是 object。")
    return dict(value)


def _nonnegative_int(
    request: CapabilityExecutionRequest,
    field: str,
    *,
    default: int = 0,
) -> int:
    value = request.inputs.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _input_error(request, field, f"{field} 必须是非负整数。")
    return int(value)


def _json_bytes(value: object) -> bytes:
    """生成稳定、UTF-8 的私有 JSON Artifact."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _api_object(value: object) -> dict[str, Any]:
    """把领域 to_dict 中的 tuple 规范化为严格 JSON object."""
    normalized = json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return ensure_json_object(normalized)


class DeterministicCapabilityExecutor:
    """只执行八个独立 ShaderForge/路由 capability，不模拟生产 Node."""

    def __init__(
        self,
        artifacts: ArtifactAccess,
        *,
        renderer_factory: RendererFactory = default_renderer_factory,
        route_deciders: Mapping[str, RouteDecider] | None = None,
    ) -> None:
        """注入 Artifact、Renderer 和可选生产纯路由能力."""
        self._artifacts = artifacts
        self._renderer_factory = renderer_factory
        self._route_deciders = dict(route_deciders or {})

    def resolve_inputs(
        self,
        descriptor: NodeDescriptor,
        request: Any,
    ) -> dict[str, object]:
        """确定性节点没有隐式输入，完全使用父快照和显式覆盖."""
        del descriptor, request
        return {}

    async def execute(
        self,
        descriptor: NodeDescriptor,
        request: Any,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """核心包没有生产 Node 依赖；Node 必须由 services 组合根精确注入."""
        del state
        raise NodeLabError(
            "node_executor_not_configured",
            "生产 Node Executor 尚未由服务组合根注入。",
            stage="executor_resolution",
            lab_run_id=str(request.lab_run_id),
            node_id=descriptor.node_id,
        )

    async def execute_capability(
        self,
        request: CapabilityExecutionRequest,
        descriptor: CapabilityDescriptor,
        runtime: object | None = None,
    ) -> NodeExecutionResult:
        """执行一个 allowlist capability；descriptor 只用于统一调用签名."""
        handlers: dict[
            str,
            Callable[[CapabilityExecutionRequest], Awaitable[NodeExecutionResult]],
        ] = {
            "normalize-target": self._normalize_target,
            "measure-target": self._measure_target,
            "validate-shader": self._validate_shader,
            "evaluate-render": self._evaluate_render,
            "select-current-best": self._select_current_best,
            "decide-after-render": self._decide_after_render,
            "decide-after-selection": self._decide_after_selection,
        }
        handler = handlers.get(request.capability_id)
        if handler is None and request.capability_id != "render-shader":
            raise NodeLabError(
                "capability_not_found",
                "确定性 Adapter 不支持该 capability。",
                stage="adapter_dispatch",
                lab_run_id=request.lab_run_id,
                details={"capability_id": request.capability_id},
            )
        properties = descriptor.input_schema.get("properties", {})
        if isinstance(properties, dict):
            unknown = sorted(set(request.inputs) - set(properties))
            if unknown:
                raise _input_error(
                    request,
                    unknown[0],
                    f"输入包含未声明字段：{', '.join(unknown)}。",
                )
        typed_runtime: CapabilityExecutionRuntime | None
        if runtime is None:
            typed_runtime = None
        elif isinstance(runtime, CapabilityExecutionRuntime):
            typed_runtime = runtime
        else:
            raise NodeLabError(
                "capability_runtime_invalid",
                "Capability runtime 与 PNG-to-Shader V1 不兼容。",
                stage="capability_runtime",
                lab_run_id=request.lab_run_id,
            )
        try:
            if request.capability_id == "render-shader":
                return await self._render_shader(request, runtime=typed_runtime)
            if handler is None:
                raise NodeLabError(
                    "internal_invariant_failed",
                    "Capability handler 缺失。",
                    stage="adapter_dispatch",
                    lab_run_id=request.lab_run_id,
                )
            return await handler(request)
        except NodeLabError:
            raise
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise NodeLabError(
                "input_contract_invalid",
                "Capability 输入无法通过领域契约校验。",
                stage="capability_input",
                lab_run_id=request.lab_run_id,
                details={"capability_id": request.capability_id},
            ) from exc

    def _read_bytes(
        self,
        request: CapabilityExecutionRequest,
        field: str,
    ) -> tuple[ArtifactDescriptor, bytes]:
        artifact_id = _required_str(request, field)
        return self._artifacts.read_artifact(request.lab_run_id, artifact_id)

    def _read_shader(self, request: CapabilityExecutionRequest) -> str:
        descriptor, data = self._read_bytes(request, "shader_artifact_id")
        if descriptor.content_type not in {
            "text/plain; charset=utf-8",
            "application/x-glsl",
            "text/x-glsl",
        }:
            raise _input_error(
                request,
                "shader_artifact_id",
                "Shader Artifact content_type 不受支持。",
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise _input_error(
                request,
                "shader_artifact_id",
                "Shader Artifact 必须是 UTF-8。",
            ) from exc

    async def _normalize_target(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        _source, image = self._read_bytes(request, "source_artifact_id")
        max_long_side = _bounded_positive_int(
            request,
            "max_long_side",
            default=1024,
            maximum=1024,
        )
        normalized = normalize_target_png(image, max_long_side=max_long_side)
        artifact = self._artifacts.upload_artifact(
            lab_run_id=request.lab_run_id,
            kind="reference_png",
            content_type="image/png",
            data=normalized,
        )
        return NodeExecutionResult(
            output_patch={
                "normalized_artifact": artifact.to_dict(),
                "image_sha256": artifact.sha256,
            },
            artifacts=[artifact],
            provenance={"implementation": "shaderforge.public.normalize_target_png"},
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )

    async def _measure_target(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        _reference, image = self._read_bytes(request, "reference_artifact_id")
        max_long_side = _bounded_positive_int(
            request,
            "max_long_side",
            default=1024,
            maximum=1024,
        )
        measurements = _api_object(
            measure_target(image, max_long_side=max_long_side).to_dict()
        )
        artifact = self._artifacts.upload_artifact(
            lab_run_id=request.lab_run_id,
            kind="target_measurements",
            content_type="application/json; charset=utf-8",
            data=_json_bytes(measurements),
        )
        return NodeExecutionResult(
            output_patch={
                "target_measurements": measurements,
                "measurements_artifact": artifact.to_dict(),
            },
            artifacts=[artifact],
            provenance={"implementation": "shaderforge.public.measure_target"},
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )

    async def _validate_shader(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        source = self._read_shader(request)
        max_shader_chars = _bounded_positive_int(
            request,
            "max_shader_chars",
            default=30_000,
            maximum=30_000,
        )
        validation = validate_shader(source, max_shader_chars=max_shader_chars)
        validation_dict = _api_object(validation.to_dict())
        return NodeExecutionResult(
            outcome="success" if validation.valid else "rejected",
            output_patch={"validation": validation_dict},
            diagnostics={
                "error_codes": [item.code for item in validation.errors],
                "warning_codes": [item.code for item in validation.warnings],
            },
            provenance={"implementation": "shaderforge.public.validate_shader"},
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )

    async def _render_shader(
        self,
        request: CapabilityExecutionRequest,
        *,
        runtime: CapabilityExecutionRuntime | None = None,
    ) -> NodeExecutionResult:
        source = self._read_shader(request)
        width = _bounded_positive_int(request, "width", maximum=1024)
        height = _bounded_positive_int(request, "height", maximum=1024)
        renderer = (
            runtime.renderer
            if runtime is not None and runtime.renderer is not None
            else self._renderer_factory()
        )
        close_renderer = runtime.close_renderer if runtime is not None else True
        browser_launch_count = (
            runtime.browser_launch_count
            if runtime is not None and runtime.browser_launch_count is not None
            else 1
        )
        render_failed = True
        try:
            rendered = await renderer.render(source, width, height)
            render_failed = False
        except RendererUnavailableError as exc:
            raise NodeLabError(
                "renderer_unavailable",
                "Node Lab Renderer 不可用。",
                stage="renderer",
                retryable=True,
                lab_run_id=request.lab_run_id,
            ) from exc
        finally:
            if close_renderer:
                try:
                    await renderer.close()
                except Exception as exc:  # noqa: BLE001 - 关闭失败也必须安全归一化
                    if not render_failed:
                        raise NodeLabError(
                            "renderer_unavailable",
                            "Node Lab Renderer 清理失败。",
                            stage="renderer_cleanup",
                            retryable=True,
                            lab_run_id=request.lab_run_id,
                        ) from exc

        private_diagnostics = {
            "vertex_log": rendered.compile.vertex_log,
            "fragment_log": rendered.compile.fragment_log,
            "link_log": rendered.compile.link_log,
            "draw_error": rendered.compile.draw_error,
            "console_errors": list(rendered.console_errors),
        }
        diagnostics_artifact = self._artifacts.upload_artifact(
            lab_run_id=request.lab_run_id,
            kind="renderer_diagnostics",
            content_type="application/json; charset=utf-8",
            data=_json_bytes(private_diagnostics),
        )
        artifacts = [diagnostics_artifact]
        render_artifact: ArtifactDescriptor | None = None
        if rendered.success and rendered.image_bytes is not None:
            render_artifact = self._artifacts.upload_artifact(
                lab_run_id=request.lab_run_id,
                kind="render_png",
                content_type="image/png",
                data=rendered.image_bytes,
            )
            artifacts.append(render_artifact)
        output = {
            "render": {
                "success": rendered.success,
                "width": rendered.width,
                "height": rendered.height,
                "compile_success": rendered.compile.success,
                "draw_error": rendered.compile.draw_error,
                "static_validation": _api_object(
                    rendered.compile.static_validation.to_dict()
                ),
                "console_error_count": len(rendered.console_errors),
                "metadata": (
                    rendered.metadata.to_dict()
                    if rendered.metadata is not None
                    else None
                ),
                "renderer_duration_ms": rendered.duration_ms,
                "image_sha256": rendered.image_sha256,
            },
            "render_artifact": (
                render_artifact.to_dict() if render_artifact is not None else None
            ),
            "diagnostics_artifact": diagnostics_artifact.to_dict(),
        }
        return NodeExecutionResult(
            outcome="success" if rendered.success else "rejected",
            output_patch=output,
            artifacts=artifacts,
            diagnostics={
                "compile_success": rendered.compile.success,
                "console_error_count": len(rendered.console_errors),
            },
            provenance={
                "implementation": "shaderforge.public.PlaywrightWebGL1Renderer",
                "renderer_lifecycle": (
                    "cold_per_attempt" if close_renderer else "warm_per_suite"
                ),
            },
            usage={
                "model_call_count": 0,
                "browser_launch_count": browser_launch_count,
            },
        )

    async def _evaluate_render(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        _reference, reference = self._read_bytes(request, "reference_artifact_id")
        _render, rendered = self._read_bytes(request, "render_artifact_id")
        weights_value = _optional_mapping(request, "metric_weights")
        weights = MetricWeights(**weights_value) if weights_value is not None else None
        measurements = measure_target(reference)
        score = evaluate_render(
            reference,
            rendered,
            measurements=measurements,
            weights=weights or MetricWeights(),
        )
        score_dict = score.to_dict()
        artifact = self._artifacts.upload_artifact(
            lab_run_id=request.lab_run_id,
            kind="score_metrics",
            content_type="application/json; charset=utf-8",
            data=_json_bytes(score_dict),
        )
        return NodeExecutionResult(
            output_patch={
                "score": score_dict,
                "metrics_artifact": artifact.to_dict(),
            },
            artifacts=[artifact],
            provenance={"implementation": "shaderforge.public.evaluate_render"},
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )

    async def _select_current_best(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        candidate_value = _optional_mapping(request, "candidate")
        if candidate_value is None:
            raise _input_error(request, "candidate", "candidate 必须是 object。")
        current_value = _optional_mapping(request, "current_best")
        policy_value = _optional_mapping(request, "acceptance_policy")
        candidate = CandidateRecord.from_dict(candidate_value)
        current = (
            CandidateRecord.from_dict(current_value)
            if current_value is not None
            else None
        )
        policy = (
            AcceptancePolicy(**policy_value)
            if policy_value is not None
            else DEFAULT_ACCEPTANCE_POLICY
        )
        decision = select_current_best(current, candidate, policy)
        return NodeExecutionResult(
            outcome="success" if decision.accepted else "rejected",
            output_patch={"decision": decision.to_dict()},
            provenance={"implementation": "shaderforge.public.select_current_best"},
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )

    async def _decide_after_render(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        return self._route(request)

    def _route(self, request: CapabilityExecutionRequest) -> NodeExecutionResult:
        """调用 Provider 注入的生产纯路由函数."""
        decider = self._route_deciders.get(request.capability_id)
        if decider is None:
            raise NodeLabError(
                "capability_not_configured",
                "当前 NodeProvider 未暴露该路由 capability。",
                stage="adapter_dispatch",
                lab_run_id=request.lab_run_id,
                details={"capability_id": request.capability_id},
            )
        result = ensure_json_object(decider(request.inputs), path="$.route_result")
        action = result["next_action"]
        return NodeExecutionResult(
            outcome="stopped" if action == "finalize" else "success",
            output_patch=result,
            next_action=action,
            provenance={"implementation": f"{decider.__module__}.{decider.__name__}"},
            usage={"model_call_count": 0, "browser_launch_count": 0},
        )

    async def _decide_after_selection(
        self,
        request: CapabilityExecutionRequest,
    ) -> NodeExecutionResult:
        return self._route(request)
