"""PNG-to-Shader V1 生产模型 Node 的 Node Lab 受控适配层.

本模块位于 Service 边界，允许复用生产 Node、Prompt、Parser 和有界预算包装器；
``agent.app.lab`` 内核因此继续保持对 Node 和具体 Gateway 的零依赖。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMGateway,
    LLMInvocationError,
    LLMResponse,
    TokenUsage,
)
from agent.app.contracts.png_to_shader_v1 import (
    AuthorMode,
    ShaderAuthorResult,
    VisualAnalysis,
    VisualReview,
)
from agent.app.lab.models import (
    NodeExecutionResult,
    NodeLabError,
    ensure_json_object,
)
from agent.app.nodes.png_to_shader_v1.model import (
    AUTHOR_PROMPTS,
    VISUAL_ANALYSIS_PROMPT,
    VISUAL_CRITIC_PROMPT,
    make_bounded_model_node,
    make_shader_author_compile_repair_node,
    make_shader_author_initial_node,
    make_shader_author_visual_refine_node,
    make_visual_analysis_node,
    make_visual_critic_node,
)
from agent.app.nodes.png_to_shader_v1.model import bounded as bounded_model
from agent.app.prompts.prompt_loader import PromptDefinition
from shaderforge.contracts import BudgetPolicy, QualityPreset, budget_for_preset

ROOT = Path(__file__).resolve().parents[7]
DEFAULT_MODEL_FIXTURE_PATH = (
    ROOT / "benchmarks/node_lab/png_to_shader_v1/fixtures/model_roles_v1.json"
)
MAX_REPLAY_OUTPUT_CHARS = 100_000

ModelRole = Literal["visual_analysis", "shader_author", "visual_critic"]
ModelExecutionMode = Literal["fixture", "mock", "real"]
ModelNode = Callable[[Mapping[str, Any]], Any]

_SAFE_AUDIT_FIELDS = frozenset(
    {
        "role",
        "mode",
        "attempt",
        "requested_model_ref",
        "model_ref",
        "model_identity_source",
        "response_format",
        "prompt_version",
        "repair_prompt_version",
        "latency_ms",
        "output_sha256",
        "parse_status",
        "error_codes",
        "validation_issues",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }
)
_SAFE_EVENT_PAYLOAD_FIELDS = frozenset(
    {
        "error_type",
        "attempted_calls",
        "timeout_source",
        "timeout_seconds",
        "stage_elapsed_seconds",
        "elapsed_seconds",
        "remaining_wall_seconds",
        "reserved_wall_seconds",
        "used_model_calls",
        "attempt_count_incomplete",
        "retryable",
        "consumed_calls",
    }
)


class LabArtifactReader(Protocol):
    """只通过同一 LabRun 不透明 id 读写私有 Artifact."""

    def read_artifact(self, lab_run_id: str, artifact_id: str) -> tuple[Any, bytes]:
        """返回 descriptor 和经过完整性校验的 bytes."""

    def upload_artifact(
        self,
        *,
        lab_run_id: str,
        kind: str,
        content_type: str,
        data: bytes,
    ) -> Any:
        """保存私有 Artifact 并返回 descriptor."""


@dataclass(frozen=True)
class _RoleSpec:
    node_id: str
    role: ModelRole
    mode: AuthorMode | None
    stage: str
    prompt_name: str
    prompt: PromptDefinition
    schema_model: type[BaseModel]
    factory: Callable[[LLMGateway], Callable[[Mapping[str, Any]], Any]]
    default_fixture_id: str
    attempt_counter_field: str | None = None


_ROLE_SPECS = {
    "visual_analysis": _RoleSpec(
        node_id="visual_analysis",
        role="visual_analysis",
        mode=None,
        stage="visual_analysis",
        prompt_name="visual_analysis_v1",
        prompt=VISUAL_ANALYSIS_PROMPT,
        schema_model=VisualAnalysis,
        factory=make_visual_analysis_node,
        default_fixture_id="visual-analysis-success-v1",
    ),
    "author_initial": _RoleSpec(
        node_id="author_initial",
        role="shader_author",
        mode=AuthorMode.INITIAL,
        stage="author_initial",
        prompt_name="shader_author_initial_v1",
        prompt=AUTHOR_PROMPTS[AuthorMode.INITIAL],
        schema_model=ShaderAuthorResult,
        factory=make_shader_author_initial_node,
        default_fixture_id="author-initial-success-v1",
    ),
    "author_compile_repair": _RoleSpec(
        node_id="author_compile_repair",
        role="shader_author",
        mode=AuthorMode.COMPILE_REPAIR,
        stage="author_compile_repair",
        prompt_name="shader_author_compile_repair_v1",
        prompt=AUTHOR_PROMPTS[AuthorMode.COMPILE_REPAIR],
        schema_model=ShaderAuthorResult,
        factory=make_shader_author_compile_repair_node,
        default_fixture_id="author-compile-repair-success-v1",
        attempt_counter_field="compile_repair_count",
    ),
    "visual_critic": _RoleSpec(
        node_id="visual_critic",
        role="visual_critic",
        mode=None,
        stage="visual_critic",
        prompt_name="visual_critic_v1",
        prompt=VISUAL_CRITIC_PROMPT,
        schema_model=VisualReview,
        factory=make_visual_critic_node,
        default_fixture_id="visual-critic-success-v1",
    ),
    "author_visual_refine": _RoleSpec(
        node_id="author_visual_refine",
        role="shader_author",
        mode=AuthorMode.VISUAL_REFINE,
        stage="author_visual_refine",
        prompt_name="shader_author_visual_refine_v1",
        prompt=AUTHOR_PROMPTS[AuthorMode.VISUAL_REFINE],
        schema_model=ShaderAuthorResult,
        factory=make_shader_author_visual_refine_node,
        default_fixture_id="author-visual-refine-success-v1",
        attempt_counter_field="visual_refinement_count",
    ),
}

SUPPORTED_NODE_IDS = frozenset(_ROLE_SPECS)


@dataclass(frozen=True)
class _ReplayItem:
    kind: Literal["semantic", "json_repair"]
    text: str
    text_sha256: str
    model_ref: str
    latency_ms: int = 0
    usage: TokenUsage = TokenUsage(input_tokens=0, output_tokens=0, total_tokens=0)


@dataclass(frozen=True)
class _Fixture:
    fixture_id: str
    fixture_version: str
    node_id: str
    role: ModelRole
    mode: AuthorMode | None
    prompt_version: str
    content_sha256: str
    responses: tuple[_ReplayItem, ...]


class _FixtureCatalog:
    """加载版本化模型原始响应，但从不把原文放入 provenance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        data = self.path.read_bytes()
        self.file_sha256 = sha256(data).hexdigest()
        try:
            root = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("Node Lab 模型 Fixture JSON 无法解析。") from exc
        if not isinstance(root, dict) or root.get("schema_version") != (
            "node_lab_model_fixtures_v1"
        ):
            raise ValueError("Node Lab 模型 Fixture schema_version 不受支持。")
        raw_fixtures = root.get("fixtures")
        if not isinstance(raw_fixtures, list):
            raise ValueError("Node Lab 模型 Fixture 列表缺失。")
        fixtures = [self._parse_fixture(value) for value in raw_fixtures]
        self._by_id = {fixture.fixture_id: fixture for fixture in fixtures}
        if len(self._by_id) != len(fixtures):
            raise ValueError("Node Lab 模型 Fixture id 重复。")

    @staticmethod
    def _json_text(value: Any) -> str:
        if isinstance(value, str):
            result = value
        else:
            result = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        if len(result) > MAX_REPLAY_OUTPUT_CHARS:
            raise ValueError("Node Lab 模型 Fixture 原始响应超过字符上限。")
        return result

    @classmethod
    def _parse_fixture(cls, value: Any) -> _Fixture:
        if not isinstance(value, dict):
            raise ValueError("Node Lab 模型 Fixture 必须是 JSON object。")
        node_id = str(value.get("node_id", ""))
        spec = _spec(node_id)
        role = str(value.get("role", ""))
        raw_mode = value.get("mode")
        mode = AuthorMode(str(raw_mode)) if raw_mode is not None else None
        prompt_version = str(value.get("prompt_version", ""))
        if (
            role != spec.role
            or mode != spec.mode
            or prompt_version != spec.prompt.version
        ):
            raise ValueError(
                "Node Lab 模型 Fixture 的角色、mode 或 Prompt 绑定不一致。"
            )
        raw_responses = value.get("responses")
        if not isinstance(raw_responses, list) or not 1 <= len(raw_responses) <= 2:
            raise ValueError("模型 Fixture 必须包含一到两个 replay 响应。")
        responses: list[_ReplayItem] = []
        for index, raw in enumerate(raw_responses):
            if not isinstance(raw, dict):
                raise ValueError("模型 Fixture replay 响应必须是 JSON object。")
            kind = str(raw.get("kind", ""))
            expected_kind = "semantic" if index == 0 else "json_repair"
            if kind != expected_kind:
                raise ValueError("模型 Fixture replay 响应顺序不合法。")
            text = cls._json_text(raw.get("raw_output"))
            responses.append(
                _ReplayItem(
                    kind=cast(Literal["semantic", "json_repair"], kind),
                    text=text,
                    text_sha256=sha256(text.encode("utf-8")).hexdigest(),
                    model_ref=f"fixture:{value.get('fixture_id', 'unknown')}:{kind}",
                )
            )
        canonical = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        fixture_id = str(value.get("fixture_id", ""))
        fixture_version = str(value.get("fixture_version", ""))
        if not fixture_id or not fixture_version:
            raise ValueError("模型 Fixture id/version 不能为空。")
        return _Fixture(
            fixture_id=fixture_id,
            fixture_version=fixture_version,
            node_id=node_id,
            role=cast(ModelRole, role),
            mode=mode,
            prompt_version=prompt_version,
            content_sha256=sha256(canonical).hexdigest(),
            responses=tuple(responses),
        )

    def get(self, fixture_id: str, *, node_id: str) -> _Fixture:
        try:
            fixture = self._by_id[fixture_id]
        except KeyError as exc:
            raise NodeLabError(
                "fixture_not_found",
                "未找到请求的模型 Fixture。",
                stage="model_fixture_resolution",
                node_id=node_id,
                details={"fixture_id": fixture_id},
            ) from exc
        if fixture.node_id != node_id:
            raise NodeLabError(
                "fixture_node_mismatch",
                "模型 Fixture 与目标节点不匹配。",
                stage="model_fixture_resolution",
                node_id=node_id,
                details={"fixture_id": fixture_id},
            )
        return fixture


class _ReplayGateway:
    """Fixture/Mock 专用 Gateway；仅重放内存文本，绝不访问外部模型."""

    def __init__(self, responses: Sequence[_ReplayItem]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[Sequence[BaseMessage], LLMCallOptions]] = []

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        self.calls.append((messages, options))
        if not self._responses:
            raise LLMInvocationError(
                "Node Lab replay 响应已耗尽。",
                model_ref=options.model_ref,
                provider="node_lab_replay",
                retryable=False,
            )
        item = self._responses.pop(0)
        return LLMResponse(
            message=AIMessage(content=item.text),
            text=item.text,
            reasoning_content=None,
            model_ref=item.model_ref,
            requested_model_ref=options.model_ref,
            model_identity_source="configured_fallback",
            latency_ms=item.latency_ms,
            usage=item.usage,
        )


class _PreviewCaptured(RuntimeError):
    def __init__(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> None:
        self.messages = tuple(messages)
        self.options = options
        super().__init__("Node Lab preview captured")


class _PreviewGateway:
    """只截获生产 Node 已组装消息；不持有也不调用真实 Gateway."""

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        raise _PreviewCaptured(messages, options)


def _spec(node_id: str) -> _RoleSpec:
    try:
        return _ROLE_SPECS[node_id]
    except KeyError as exc:
        raise NodeLabError(
            "node_adapter_not_implemented",
            "该节点不是 Node Lab 支持的模型角色。",
            stage="model_role_resolution",
            node_id=node_id,
        ) from exc


def _descriptor_value(descriptor: Any, name: str) -> Any:
    if isinstance(descriptor, Mapping):
        return descriptor.get(name)
    return getattr(descriptor, name, None)


def _request_value(request: Any, name: str, default: Any = None) -> Any:
    if isinstance(request, Mapping):
        return request.get(name, default)
    return getattr(request, name, default)


def _artifact_value(descriptor: Any, name: str) -> Any:
    if isinstance(descriptor, Mapping):
        return descriptor.get(name)
    return getattr(descriptor, name, None)


def _safe_audit(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    safe = {key: item for key, item in value.items() if key in _SAFE_AUDIT_FIELDS}
    issues = safe.get("validation_issues")
    if isinstance(issues, list):
        safe["validation_issues"] = [
            sanitized
            for issue in issues
            if (sanitized := _safe_validation_issue(issue)) is not None
        ]
    return ensure_json_object(safe)


def _safe_validation_issue(value: Any) -> dict[str, str] | None:
    """保留稳定 code/path，但隐藏 mock 可控的未知字段名和原始消息."""
    if not isinstance(value, Mapping):
        return None
    code = str(value.get("code", "schema_validation"))
    path = str(value.get("path", "$"))
    if code == "unknown_field":
        path = "$.<unknown_field>"
    messages = {
        "binding_mismatch": "输出与当前角色输入绑定不一致。",
        "compile_scope_violation": "Compile repair 超出允许修改范围。",
        "duplicate_key": "JSON 包含重复 key。",
        "invalid_json": "输出不是合法 JSON object。",
        "invalid_literal": "字段值不在允许枚举内。",
        "missing_field": "输出缺少必需字段。",
        "non_finite_number": "JSON 包含非有限数。",
        "not_json_object": "顶层输出不是 JSON object。",
        "output_too_large": "输出超过结构化响应字符上限。",
        "role_violation": "输出违反角色职责边界。",
        "schema_validation": "输出未通过严格 Schema。",
        "unexpected_wrapper": "输出包含不受支持的包装。",
        "unknown_field": "输出包含未声明字段。",
    }
    return {"code": code, "path": path, "message": messages.get(code, "输出不合法。")}


def _safe_event(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    payload = value.get("payload")
    safe_payload = (
        {
            key: item
            for key, item in payload.items()
            if key in _SAFE_EVENT_PAYLOAD_FIELDS
        }
        if isinstance(payload, Mapping)
        else {}
    )
    return ensure_json_object(
        {
            "stage": str(value.get("stage", "")),
            "event_type": str(value.get("event_type", "")),
            "payload": safe_payload,
        }
    )


def _safe_log(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    context = value.get("context")
    safe_context: dict[str, Any] = {}
    if isinstance(context, Mapping):
        for key in (
            "strategy",
            "repaired_paths",
            "source_error_codes",
            "source_error_paths",
        ):
            if key in context:
                safe_context[key] = context[key]
    return ensure_json_object(
        {
            "level": str(value.get("level", "warning")),
            "source": str(value.get("source", "agent.node_lab")),
            "message": str(value.get("message", "模型输出执行了受限本地归一化")),
            "context": safe_context,
        }
    )


def _project_result(spec: _RoleSpec, result: Mapping[str, Any]) -> dict[str, Any]:
    """显式选择生产输出字段，拒绝任意 raw/reasoning/供应商字段穿透."""
    role_fields = {
        "visual_analysis": ("visual_analysis", "visual_analysis_model"),
        "author_initial": (
            "author_result",
            "glsl",
            "author_model",
            "candidate_provenance",
        ),
        "author_compile_repair": (
            "author_result",
            "glsl",
            "author_model",
            "candidate_provenance",
        ),
        "visual_critic": ("visual_review", "visual_critic_model"),
        "author_visual_refine": (
            "author_result",
            "glsl",
            "author_model",
            "candidate_provenance",
        ),
    }[spec.node_id]
    safe: dict[str, Any] = {
        key: result[key]
        for key in (
            *role_fields,
            "phase",
            "model_call_count",
            "stop_reason",
            "compile_repair_count",
            "visual_refinement_count",
        )
        if key in result
    }
    if calls := result.get("model_calls"):
        safe["model_calls"] = [audit for item in calls if (audit := _safe_audit(item))]
    if events := result.get("events"):
        safe["events"] = [event for item in events if (event := _safe_event(item))]
    if logs := result.get("logs"):
        safe["logs"] = [item for value in logs if (item := _safe_log(value))]
    return ensure_json_object(safe, path="$.model_role_output")


def _new_audits(
    state: Mapping[str, Any],
    projected: Mapping[str, Any],
) -> list[dict[str, Any]]:
    previous = state.get("model_calls", ())
    previous_count = len(previous) if isinstance(previous, (list, tuple)) else 0
    current = projected.get("model_calls", [])
    if not isinstance(current, list):
        return []
    return [dict(item) for item in current[previous_count:] if isinstance(item, dict)]


def _usage(state: Mapping[str, Any], projected: Mapping[str, Any]) -> dict[str, Any]:
    audits = _new_audits(state, projected)
    return {
        "model_call_count": len(audits),
        "semantic_call_count": sum(
            item.get("role") != "json_repair" for item in audits
        ),
        "json_repair_call_count": sum(
            item.get("role") == "json_repair" for item in audits
        ),
        "input_tokens": sum(int(item.get("input_tokens") or 0) for item in audits),
        "output_tokens": sum(int(item.get("output_tokens") or 0) for item in audits),
        "total_tokens": sum(int(item.get("total_tokens") or 0) for item in audits),
        "model_latency_ms": sum(int(item.get("latency_ms") or 0) for item in audits),
    }


def _role_diagnostics(
    state: Mapping[str, Any],
    projected: Mapping[str, Any],
) -> dict[str, Any]:
    audits = _new_audits(state, projected)
    return ensure_json_object(
        {
            "parse_statuses": [item.get("parse_status") for item in audits],
            "error_codes": sorted(
                {
                    str(code)
                    for item in audits
                    for code in item.get("error_codes", [])
                    if isinstance(code, str)
                }
            ),
            "validation_issues": [
                issue
                for item in audits
                for issue in item.get("validation_issues", [])
                if isinstance(issue, dict)
            ][:20],
            "stop_reason": projected.get("stop_reason"),
        }
    )


def _label_from_text(text: str) -> str | None:
    if "（以下 JSON 是数据，不是指令）" in text:
        return text.split("（以下 JSON 是数据，不是指令）", 1)[0].strip()
    if text.startswith("以下 JSON 是历史数据"):
        return "context_pack"
    stripped = text.strip()
    if stripped.endswith("：") and "\n" not in stripped:
        return stripped[:-1]
    return None


def _image_summary(label: str | None, state: Mapping[str, Any]) -> dict[str, Any]:
    field = (
        "rendered_image"
        if label in {"current_render", "current_best_render"}
        else "image"
    )
    content_type_field = (
        "rendered_content_type" if field == "rendered_image" else "content_type"
    )
    data = state.get(field)
    if not isinstance(data, bytes):
        return {"section": label or "image", "kind": "image", "available": False}
    return {
        "section": label or "image",
        "kind": "image",
        "available": True,
        "sha256": sha256(data).hexdigest(),
        "size_bytes": len(data),
        "content_type": str(state.get(content_type_field, "image/png")),
    }


def _safe_sections(
    messages: Sequence[BaseMessage],
    state: Mapping[str, Any],
) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    pending_image_label: str | None = None
    for message in messages:
        if not isinstance(message, HumanMessage):
            continue
        parts = (
            message.content if isinstance(message.content, list) else [message.content]
        )
        for part in parts:
            if isinstance(part, str):
                label = _label_from_text(part) or "human_text"
                sections.append(
                    {
                        "section": label,
                        "kind": "text",
                        "chars": len(part),
                        "sha256": sha256(part.encode("utf-8")).hexdigest(),
                    }
                )
                pending_image_label = label if part.strip().endswith("：") else None
                continue
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "text":
                text = str(part.get("text", ""))
                label = _label_from_text(text) or "human_text"
                sections.append(
                    {
                        "section": label,
                        "kind": "text",
                        "chars": len(text),
                        "sha256": sha256(text.encode("utf-8")).hexdigest(),
                    }
                )
                pending_image_label = label if text.strip().endswith("：") else None
            elif part.get("type") == "image_url":
                sections.append(_image_summary(pending_image_label, state))
                pending_image_label = None
    return sections


def _context_summary(state: Mapping[str, Any]) -> dict[str, Any]:
    value = state.get("context_pack")
    if value is None:
        return {"present": False, "selected_memory_ids": [], "estimated_tokens": 0}
    if hasattr(value, "to_dict") and callable(value.to_dict):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        return {"present": True, "selected_memory_ids": [], "estimated_tokens": None}
    ids = value.get("selected_memory_ids", ())
    return {
        "present": True,
        "selected_memory_ids": [str(item) for item in ids]
        if isinstance(ids, (list, tuple))
        else [],
        "estimated_tokens": value.get("estimated_tokens"),
    }


def _budget_summary(
    spec: _RoleSpec,
    state: Mapping[str, Any],
    clock: Callable[[], float],
) -> dict[str, Any]:
    raw = state.get("budget_policy")
    if raw is None:
        return {
            "configured": False,
            "stage_timeout_cap_seconds": bounded_model.STAGE_TIMEOUT_CAP_SECONDS.get(
                spec.stage,
                bounded_model.DEFAULT_STAGE_TIMEOUT_CAP_SECONDS,
            ),
        }
    try:
        policy = raw if isinstance(raw, BudgetPolicy) else BudgetPolicy(**dict(raw))
    except (TypeError, ValueError) as exc:
        raise NodeLabError(
            "input_contract_invalid",
            "模型节点预算契约不合法。",
            stage="model_budget_preview",
            node_id=spec.node_id,
            details={"error_type": type(exc).__name__},
        ) from exc
    used = int(state.get("model_call_count", 0))
    remaining_calls = max(0, policy.max_model_calls - used)
    started_at = float(state.get("started_at", clock()))
    elapsed = max(0.0, clock() - started_at)
    remaining_wall = max(0.0, policy.max_wall_time_seconds - elapsed)
    reserve = min(
        bounded_model.MAX_DOWNSTREAM_RESERVE_SECONDS,
        policy.max_wall_time_seconds * bounded_model.DOWNSTREAM_RESERVE_RATIO,
    )
    stage_cap = bounded_model.STAGE_TIMEOUT_CAP_SECONDS.get(
        spec.stage,
        bounded_model.DEFAULT_STAGE_TIMEOUT_CAP_SECONDS,
    )
    callable_wall = max(0.0, remaining_wall - reserve)
    return {
        "configured": True,
        "max_model_calls": policy.max_model_calls,
        "used_model_calls": used,
        "remaining_model_calls": remaining_calls,
        "max_wall_time_seconds": policy.max_wall_time_seconds,
        "elapsed_seconds": round(elapsed, 3),
        "remaining_wall_seconds": round(remaining_wall, 3),
        "downstream_reserve_seconds": round(reserve, 3),
        "stage_timeout_cap_seconds": stage_cap,
        "effective_timeout_seconds": round(min(stage_cap, callable_wall), 3),
        "structured_output_max_attempts": min(2, remaining_calls),
        "json_repair_allowed": remaining_calls >= 2 and callable_wall > 0,
    }


async def _preview_async(
    spec: _RoleSpec,
    state: Mapping[str, Any],
    clock: Callable[[], float],
) -> NodeExecutionResult:
    node = spec.factory(_PreviewGateway())
    try:
        await node(state)
    except _PreviewCaptured as captured:
        return _preview_result(spec, state, captured, clock)
    except NodeLabError:
        raise
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise NodeLabError(
            "input_contract_invalid",
            "模型角色 preview 输入不符合生产契约。",
            stage="model_preview",
            node_id=spec.node_id,
            details={"error_type": type(exc).__name__},
        ) from exc
    raise RuntimeError("模型角色 preview 未捕获到生产调用。")


def _preview_result(
    spec: _RoleSpec,
    state: Mapping[str, Any],
    captured: _PreviewCaptured,
    clock: Callable[[], float],
) -> NodeExecutionResult:
    system_prompt = next(
        (
            str(message.content)
            for message in captured.messages
            if isinstance(message, SystemMessage)
        ),
        spec.prompt.prompt,
    )
    prompt_sha256 = sha256(system_prompt.encode("utf-8")).hexdigest()
    preview = {
        "schema_version": "node_lab_model_preview_v1",
        "node_id": spec.node_id,
        "role": spec.role,
        "mode": spec.mode.value if spec.mode else None,
        "prompt": {
            "prompt_id": spec.prompt_name,
            "version": spec.prompt.version,
            "sha256": prompt_sha256,
            "system_prompt": system_prompt,
        },
        "message_sections": _safe_sections(captured.messages, state),
        "output_schema": spec.schema_model.model_json_schema(mode="validation"),
        "context": _context_summary(state),
        "model_options": {
            "model_ref": captured.options.model_ref,
            "temperature": captured.options.temperature,
            "thinking": captured.options.thinking,
            "capture_reasoning": captured.options.capture_reasoning,
            "response_format": captured.options.response_format,
        },
        "budget": _budget_summary(spec, state, clock),
        "gateway_call_count": 0,
    }
    return NodeExecutionResult(
        outcome="success",
        output_patch={"preview": preview},
        diagnostics={"preview_only": True},
        provenance={
            "execution_source": "production_prompt_preview",
            "prompt_version": spec.prompt.version,
            "prompt_sha256": prompt_sha256,
        },
        usage={"model_call_count": 0, "total_tokens": 0},
    )


class ModelRoleExecutor:
    """五个模型节点共用的 preview/fixture/mock/real Service Executor."""

    def __init__(
        self,
        artifacts: LabArtifactReader,
        *,
        gateway: LLMGateway | None = None,
        real_model_enabled: bool = False,
        fixture_path: str | Path = DEFAULT_MODEL_FIXTURE_PATH,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """绑定私有 Artifact、可选真实 Gateway、Fixture 和 clock."""
        self._artifacts = artifacts
        self._gateway = gateway
        self._real_model_enabled = real_model_enabled
        self._fixtures = _FixtureCatalog(fixture_path)
        self._clock = clock

    def resolve_inputs(
        self,
        descriptor: Any,
        request: Any,
    ) -> dict[str, object]:
        """模型 Fixture 只提供原始响应，不覆盖父步骤的累计预算状态."""
        del request
        _spec(str(_descriptor_value(descriptor, "node_id")))
        return {}

    def preflight(self, descriptor: Any, request: Any) -> None:
        """在创建步骤前执行真实模型双开关门禁."""
        spec = _spec(str(_descriptor_value(descriptor, "node_id")))
        if bool(_request_value(request, "preview_only", False)) or (
            _request_value(request, "effect_mode", "lab_commit") == "preview"
        ):
            return
        mode = str(_request_value(request, "execution_mode", "fixture"))
        if mode != "real":
            return
        allow_model_call = bool(_request_value(request, "allow_model_call", False))
        if (
            not self._real_model_enabled
            or not allow_model_call
            or self._gateway is None
        ):
            raise NodeLabError(
                "real_model_not_allowed",
                "Node Lab 真实模型调用未满足全部显式开关。",
                stage="real_model_gate",
                lab_run_id=str(_request_value(request, "lab_run_id", "")),
                node_id=spec.node_id,
                details={
                    "server_enabled": self._real_model_enabled,
                    "request_allowed": allow_model_call,
                },
            )

    def _read_artifact(self, lab_run_id: str, artifact_id: str) -> tuple[Any, bytes]:
        descriptor, data = self._artifacts.read_artifact(lab_run_id, artifact_id)
        if str(_artifact_value(descriptor, "lab_run_id")) != lab_run_id:
            raise NodeLabError(
                "artifact_integrity_failed",
                "模型输入 Artifact 与当前 LabRun 不匹配。",
                stage="model_artifact_read",
                lab_run_id=lab_run_id,
            )
        return descriptor, data

    def _materialize_state(
        self, request: Any, state: Mapping[str, object]
    ) -> dict[str, Any]:
        lab_run_id = str(_request_value(request, "lab_run_id", ""))
        prepared = dict(state)
        prepared.setdefault(
            "budget_policy",
            asdict(budget_for_preset(QualityPreset.BALANCED)),
        )
        prepared.setdefault("started_at", self._clock())
        prepared.setdefault("model_call_count", 0)
        prepared.setdefault("model_calls", [])
        prepared.setdefault("events", [])
        prepared.setdefault("logs", [])
        bindings = (
            ("image", ("reference_artifact_id", "image_artifact_id"), False),
            (
                "rendered_image",
                ("rendered_image_artifact_id", "render_artifact_id"),
                False,
            ),
            ("glsl", ("glsl_artifact_id",), True),
        )
        for target, artifact_fields, decode_text in bindings:
            if target in prepared:
                continue
            artifact_id = next(
                (
                    value
                    for field in artifact_fields
                    if isinstance((value := prepared.get(field)), str) and value
                ),
                None,
            )
            if not isinstance(artifact_id, str) or not artifact_id:
                continue
            descriptor, data = self._read_artifact(lab_run_id, artifact_id)
            if decode_text:
                try:
                    prepared[target] = data.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise NodeLabError(
                        "input_contract_invalid",
                        "GLSL Artifact 必须是 UTF-8 文本。",
                        stage="model_artifact_read",
                        lab_run_id=lab_run_id,
                    ) from exc
            else:
                prepared[target] = data
                content_type_target = (
                    "rendered_content_type"
                    if target == "rendered_image"
                    else "content_type"
                )
                prepared.setdefault(
                    content_type_target,
                    str(_artifact_value(descriptor, "content_type") or "image/png"),
                )
        json_bindings = (
            ("context_pack", "context_pack_artifact_id"),
            ("visual_analysis", "visual_analysis_artifact_id"),
            ("visual_review", "visual_review_artifact_id"),
            ("candidate_provenance", "candidate_provenance_artifact_id"),
        )
        for target, artifact_field in json_bindings:
            if target in prepared:
                continue
            json_artifact_id = prepared.get(artifact_field)
            if not isinstance(json_artifact_id, str) or not json_artifact_id:
                continue
            _descriptor, data = self._read_artifact(lab_run_id, json_artifact_id)
            try:
                value = json.loads(data)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise NodeLabError(
                    "input_contract_invalid",
                    "模型输入 JSON Artifact 无法解析。",
                    stage="model_artifact_read",
                    lab_run_id=lab_run_id,
                    details={"field": artifact_field},
                ) from exc
            prepared[target] = ensure_json_object(value, path=f"$.{target}")
        author_artifact_id = prepared.get("author_artifact_id")
        if not isinstance(author_artifact_id, str) or not author_artifact_id:
            author_artifact_id = prepared.get("previous_author_artifact_id")
        if isinstance(author_artifact_id, str) and author_artifact_id:
            _descriptor, data = self._read_artifact(lab_run_id, author_artifact_id)
            try:
                author_value = ensure_json_object(
                    json.loads(data),
                    path="$.author_result",
                )
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                raise NodeLabError(
                    "input_contract_invalid",
                    "Author JSON Artifact 无法解析。",
                    stage="model_artifact_read",
                    lab_run_id=lab_run_id,
                    details={"field": "author_artifact_id"},
                ) from exc
            prepared.setdefault("author_result", author_value)
            if str(_request_value(request, "node_id", "")) == "author_compile_repair":
                prepared.setdefault("previous_author_result", author_value)
        return prepared

    def _upload_json(
        self,
        *,
        lab_run_id: str,
        kind: str,
        value: Mapping[str, Any],
    ) -> Any:
        data = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        return self._artifacts.upload_artifact(
            lab_run_id=lab_run_id,
            kind=kind,
            content_type="application/json; charset=utf-8",
            data=data,
        )

    def _artifactize_projected(
        self,
        *,
        spec: _RoleSpec,
        lab_run_id: str,
        projected: dict[str, Any],
    ) -> tuple[dict[str, Any], list[Any]]:
        """把模型业务大对象写入私有 Artifact，并返回安全公开投影."""
        safe = dict(projected)
        artifacts: list[Any] = []
        if analysis := safe.get("visual_analysis"):
            descriptor = self._upload_json(
                lab_run_id=lab_run_id,
                kind="visual-analysis",
                value=cast(Mapping[str, Any], analysis),
            )
            artifacts.append(descriptor)
            safe["visual_analysis_artifact_id"] = str(
                _artifact_value(descriptor, "artifact_id")
            )
        if review := safe.get("visual_review"):
            descriptor = self._upload_json(
                lab_run_id=lab_run_id,
                kind="visual-review",
                value=cast(Mapping[str, Any], review),
            )
            artifacts.append(descriptor)
            safe["visual_review_artifact_id"] = str(
                _artifact_value(descriptor, "artifact_id")
            )
        author = safe.pop("author_result", None)
        glsl = safe.pop("glsl", None)
        provenance = safe.pop("candidate_provenance", None)
        if isinstance(author, Mapping):
            descriptor = self._upload_json(
                lab_run_id=lab_run_id,
                kind="shader-author-result",
                value=author,
            )
            artifacts.append(descriptor)
            safe["author_artifact_id"] = str(_artifact_value(descriptor, "artifact_id"))
            safe["author_summary"] = {
                key: author.get(key)
                for key in (
                    "author_version",
                    "mode",
                    "base_candidate_id",
                    "strategy_summary",
                    "implemented_layers",
                    "changed_problem_domain",
                    "changed_parameters",
                    "protected_regions",
                    "expected_metric_changes",
                    "known_limitations",
                )
            }
        if isinstance(glsl, str):
            descriptor = self._artifacts.upload_artifact(
                lab_run_id=lab_run_id,
                kind="shader-glsl",
                content_type="text/plain; charset=utf-8",
                data=glsl.encode("utf-8"),
            )
            artifacts.append(descriptor)
            safe["glsl_artifact_id"] = str(_artifact_value(descriptor, "artifact_id"))
            safe["glsl_sha256"] = sha256(glsl.encode("utf-8")).hexdigest()
            safe["glsl_chars"] = len(glsl)
        if isinstance(provenance, Mapping):
            descriptor = self._upload_json(
                lab_run_id=lab_run_id,
                kind="candidate-provenance",
                value=provenance,
            )
            artifacts.append(descriptor)
            safe["candidate_provenance_artifact_id"] = str(
                _artifact_value(descriptor, "artifact_id")
            )
        return ensure_json_object(safe, path=f"$.{spec.node_id}.output"), artifacts

    def _fixture_replay(
        self, spec: _RoleSpec, request: Any
    ) -> tuple[_ReplayGateway, dict[str, Any]]:
        fixture_id = str(
            _request_value(request, "fixture_id", None) or spec.default_fixture_id
        )
        fixture = self._fixtures.get(fixture_id, node_id=spec.node_id)
        return _ReplayGateway(fixture.responses), {
            "fixture_id": fixture.fixture_id,
            "fixture_version": fixture.fixture_version,
            "fixture_sha256": fixture.content_sha256,
            "fixture_file_sha256": self._fixtures.file_sha256,
        }

    def _mock_replay(
        self, spec: _RoleSpec, request: Any
    ) -> tuple[_ReplayGateway, dict[str, Any]]:
        lab_run_id = str(_request_value(request, "lab_run_id", ""))
        artifact_id = _request_value(request, "mock_response_artifact_id", None)
        if not isinstance(artifact_id, str) or not artifact_id:
            raise NodeLabError(
                "input_contract_invalid",
                "mock 模式必须提供 mock_response_artifact_id。",
                stage="model_mock_resolution",
                lab_run_id=lab_run_id,
                node_id=spec.node_id,
            )
        descriptor, data = self._read_artifact(lab_run_id, artifact_id)
        try:
            raw_text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NodeLabError(
                "mock_response_invalid",
                "Mock 模型响应必须是 UTF-8 文本。",
                stage="model_mock_resolution",
                lab_run_id=lab_run_id,
                node_id=spec.node_id,
            ) from exc
        responses = self._mock_items(raw_text, artifact_id)
        artifact_sha256 = str(
            _artifact_value(descriptor, "sha256") or sha256(data).hexdigest()
        )
        return _ReplayGateway(responses), {
            "mock_response_artifact_id": artifact_id,
            "mock_response_sha256": artifact_sha256,
        }

    @staticmethod
    def _mock_items(raw_text: str, artifact_id: str) -> tuple[_ReplayItem, ...]:
        values: list[Any]
        try:
            envelope = json.loads(raw_text)
        except json.JSONDecodeError:
            envelope = None
        if isinstance(envelope, dict) and envelope.get("schema_version") == (
            "node_lab_mock_responses_v1"
        ):
            values = envelope.get("responses", [])
            if not isinstance(values, list) or not 1 <= len(values) <= 2:
                raise NodeLabError(
                    "mock_response_invalid",
                    "Mock 响应 envelope 必须包含一到两个 responses。",
                    stage="model_mock_resolution",
                )
        else:
            values = [{"kind": "semantic", "raw_output": raw_text}]
        result: list[_ReplayItem] = []
        for index, value in enumerate(values):
            if not isinstance(value, Mapping):
                raise NodeLabError(
                    "mock_response_invalid",
                    "Mock replay 响应必须是 JSON object。",
                    stage="model_mock_resolution",
                )
            expected_kind = "semantic" if index == 0 else "json_repair"
            if str(value.get("kind", "")) != expected_kind:
                raise NodeLabError(
                    "mock_response_invalid",
                    "Mock replay 响应顺序不合法。",
                    stage="model_mock_resolution",
                )
            raw_output = value.get("raw_output", "")
            text = (
                raw_output
                if isinstance(raw_output, str)
                else json.dumps(
                    raw_output,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            )
            if len(text) > MAX_REPLAY_OUTPUT_CHARS:
                raise NodeLabError(
                    "mock_response_invalid",
                    "Mock 模型响应超过字符上限。",
                    stage="model_mock_resolution",
                )
            result.append(
                _ReplayItem(
                    kind=cast(Literal["semantic", "json_repair"], expected_kind),
                    text=text,
                    text_sha256=sha256(text.encode("utf-8")).hexdigest(),
                    model_ref=f"mock:{artifact_id}:{expected_kind}",
                )
            )
        return tuple(result)

    async def execute(
        self,
        descriptor: Any,
        request: Any,
        state: Mapping[str, object],
    ) -> NodeExecutionResult:
        """通过生产 Prompt/Parser 执行 preview、fixture、mock 或 real."""
        node_id = str(_descriptor_value(descriptor, "node_id"))
        spec = _spec(node_id)
        self.preflight(descriptor, request)
        prepared = self._materialize_state(request, state)
        if bool(_request_value(request, "preview_only", False)) or (
            _request_value(request, "effect_mode", "lab_commit") == "preview"
        ):
            return await _preview_async(spec, prepared, self._clock)

        mode = str(_request_value(request, "execution_mode", "fixture"))
        replay_provenance: dict[str, Any] = {}
        gateway: LLMGateway
        if mode == "fixture":
            replay, replay_provenance = self._fixture_replay(spec, request)
            gateway = replay
        elif mode == "mock":
            replay, replay_provenance = self._mock_replay(spec, request)
            gateway = replay
        elif mode == "real":
            allow_model_call = bool(_request_value(request, "allow_model_call", False))
            if (
                not self._real_model_enabled
                or not allow_model_call
                or self._gateway is None
            ):
                raise NodeLabError(
                    "real_model_not_allowed",
                    "Node Lab 真实模型调用未满足全部显式开关。",
                    stage="real_model_gate",
                    lab_run_id=str(_request_value(request, "lab_run_id", "")),
                    node_id=spec.node_id,
                    details={
                        "server_enabled": self._real_model_enabled,
                        "request_allowed": allow_model_call,
                    },
                )
            gateway = self._gateway
        else:
            raise NodeLabError(
                "unsupported_execution_mode",
                "模型角色只支持 fixture、mock 或 real 模式。",
                stage="model_executor_resolution",
                node_id=spec.node_id,
                details={"execution_mode": mode},
            )

        delegate = spec.factory(gateway)
        bounded = make_bounded_model_node(
            delegate,
            stage=spec.stage,
            clock=self._clock,
            attempt_counter_field=spec.attempt_counter_field,
        )
        try:
            raw_result = await bounded(prepared)
        except NodeLabError:
            raise
        except (KeyError, TypeError, ValueError, ValidationError) as exc:
            raise NodeLabError(
                "input_contract_invalid",
                "模型节点输入不符合生产契约。",
                stage="model_node_execution",
                lab_run_id=str(_request_value(request, "lab_run_id", "")),
                node_id=spec.node_id,
                details={"error_type": type(exc).__name__},
            ) from exc

        projected = _project_result(spec, raw_result)
        lab_run_id = str(_request_value(request, "lab_run_id", ""))
        projected, artifacts = self._artifactize_projected(
            spec=spec,
            lab_run_id=lab_run_id,
            projected=projected,
        )
        stopped = bool(projected.get("stop_reason")) and not any(
            field in projected
            for field in (
                "visual_analysis_artifact_id",
                "author_artifact_id",
                "visual_review_artifact_id",
            )
        )
        provenance = {
            "execution_source": mode,
            "role": spec.role,
            "mode": spec.mode.value if spec.mode else None,
            "prompt_id": spec.prompt_name,
            "prompt_version": spec.prompt.version,
            "prompt_sha256": sha256(spec.prompt.prompt.encode("utf-8")).hexdigest(),
            **replay_provenance,
        }
        return NodeExecutionResult(
            outcome="stopped" if stopped else "success",
            output_patch=projected,
            diagnostics=_role_diagnostics(prepared, projected),
            provenance=provenance,
            usage=_usage(prepared, projected),
            artifacts=artifacts,
        )


__all__ = [
    "DEFAULT_MODEL_FIXTURE_PATH",
    "ModelRoleExecutor",
    "SUPPORTED_NODE_IDS",
]
