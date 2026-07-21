"""独立的 Node Lab 模型角色 benchmark runner.

默认只运行离线 Fixture；真实模型模式由 CLI、环境变量和本模块预算门禁共同
控制。本模块只通过 Node Lab Application 调用生产 Node，因此不会复制 Prompt、
Parser 或现有 M5/AI-off benchmark 的执行语义。
"""

from __future__ import annotations

import asyncio
import json
import re
import statistics
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

import yaml
from langchain_core.messages import BaseMessage, SystemMessage
from pydantic import Field, model_validator
from typing_extensions import Self

from agent.app.config.model_config import NodeModelConfig
from agent.app.contracts.llm import (
    LLMCallOptions,
    LLMGateway,
    LLMInvocationError,
    LLMResponse,
)
from agent.app.contracts.png_to_shader_v1 import AuthorMode
from agent.app.lab.benchmark import source_environment
from agent.app.lab.models import (
    Identifier,
    LabRunCreateRequest,
    NodeLabModel,
    StepExecutionRequest,
    ensure_json_object,
)
from agent.app.nodes.png_to_shader_v1 import (
    AUTHOR_PROMPTS,
    SHADER_AUTHOR_MODEL_CONFIG,
    STRUCTURED_OUTPUT_REPAIR_PROMPT,
    VISUAL_ANALYSIS_MODEL_CONFIG,
    VISUAL_ANALYSIS_PROMPT,
    VISUAL_CRITIC_MODEL_CONFIG,
    VISUAL_CRITIC_PROMPT,
)
from agent.app.prompts.prompt_loader import PromptDefinition
from agent.app.services.node_lab import create_node_lab_application
from shaderforge.public import (
    WEBGL1_STATIC_NO_TEXTURE_V1,
    RunArtifactStore,
    measure_target,
)

ROOT = Path(__file__).resolve().parents[4]
BENCHMARKS_ROOT = ROOT / "benchmarks"
DEFAULT_MODEL_BENCHMARK_MANIFEST = (
    ROOT / "benchmarks/node_lab/png_to_shader_v1/model-manifest.yaml"
)
DEFAULT_MODEL_BENCHMARK_OUTPUT_ROOT = ROOT / "output/benchmarks/node-lab-model"
DEFAULT_MODEL_BENCHMARK_LAB_ROOT = ROOT / "output/node-lab/model-benchmark-runs"

_RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SUPPORTED_NODE_ORDER = (
    "visual_analysis",
    "author_initial",
    "author_compile_repair",
    "visual_critic",
    "author_visual_refine",
)
_MAX_REPETITIONS = 20


class ModelBenchmarkArtifact(NodeLabModel):
    """Manifest 中一个冻结文件输入."""

    path: str = Field(min_length=1, max_length=500)
    content_type: Literal["image/png", "application/json"]
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelBenchmarkCase(NodeLabModel):
    """一个生产模型节点及其固定输入/响应 Fixture 绑定."""

    case_id: Identifier
    node_id: Literal[
        "visual_analysis",
        "author_initial",
        "author_compile_repair",
        "visual_critic",
        "author_visual_refine",
    ]
    input_profile: Literal[
        "visual_analysis_v1",
        "author_initial_v1",
        "author_compile_repair_v1",
        "visual_critic_v1",
        "author_visual_refine_v1",
    ]
    response_fixture_id: Identifier
    upstream_fixture_ids: list[Identifier] = Field(default_factory=list, max_length=4)
    prompt_id: Identifier
    prompt_version: Identifier
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ModelBenchmarkModelConfig(NodeLabModel):
    """冻结生产 Node 应使用的模型语义参数."""

    model_ref_source: Literal["SHADER_GEN_MODEL_NAME"]
    requested_model_ref: str = Field(
        min_length=3,
        max_length=200,
        pattern=r"^[A-Za-z0-9._-]+:[A-Za-z0-9._-]+$",
    )
    temperature: float = Field(ge=0, le=2, allow_inf_nan=False)
    thinking: Literal["default", "on", "off"]
    capture_reasoning: bool
    response_format: Literal["text", "json_object"]


class ModelBenchmarkBudgets(NodeLabModel):
    """真实模型 suite 的整套硬预算."""

    max_semantic_calls: int = Field(gt=0, le=1000)
    max_json_repair_calls: int = Field(gt=0, le=1000)
    max_output_tokens_per_call: int = Field(gt=0, le=1_000_000)
    max_total_tokens: int = Field(gt=0, le=10_000_000)
    max_wall_time_seconds: int = Field(gt=0, le=86_400)
    max_estimated_cost_usd: float = Field(gt=0, le=100_000, allow_inf_nan=False)
    input_cost_per_million_tokens_usd: float = Field(
        gt=0,
        le=10_000,
        allow_inf_nan=False,
    )
    output_cost_per_million_tokens_usd: float = Field(
        gt=0,
        le=10_000,
        allow_inf_nan=False,
    )
    price_version: Identifier

    @model_validator(mode="after")
    def validate_global_reservation(self) -> Self:
        """保证 token 上限覆盖单次调用，成本上限覆盖全 token 最坏单价."""
        if self.max_total_tokens < self.max_output_tokens_per_call:
            raise ValueError("max_total_tokens 不能小于 max_output_tokens_per_call。")
        worst_rate = max(
            self.input_cost_per_million_tokens_usd,
            self.output_cost_per_million_tokens_usd,
        )
        reserved_cost = self.max_total_tokens * worst_rate / 1_000_000
        if self.max_estimated_cost_usd + 1e-12 < reserved_cost:
            raise ValueError("成本预算不足以覆盖 max_total_tokens 的最坏单价。")
        return self


class ModelBenchmarkManifest(NodeLabModel):
    """独立模型 benchmark 的固定 manifest."""

    schema_version: Literal["node_lab_model_benchmark_manifest_v1"]
    suite_id: Identifier
    default_execution_mode: Literal["fixture"] = "fixture"
    repetitions: int = Field(ge=1, le=_MAX_REPETITIONS)
    reference_image: ModelBenchmarkArtifact
    model_fixtures: ModelBenchmarkArtifact
    model_call_config: ModelBenchmarkModelConfig
    budgets: ModelBenchmarkBudgets
    cases: list[ModelBenchmarkCase] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_five_roles(self) -> Self:
        """Suite 必须且只能覆盖五个模型节点，并保持固定顺序."""
        node_ids = tuple(case.node_id for case in self.cases)
        if node_ids != _SUPPORTED_NODE_ORDER:
            raise ValueError("模型 benchmark 必须按固定顺序覆盖五个模型节点。")
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("模型 benchmark case_id 不能重复。")
        expected_profiles = tuple(f"{node_id}_v1" for node_id in node_ids)
        if tuple(case.input_profile for case in self.cases) != expected_profiles:
            raise ValueError("模型 benchmark input_profile 与 node_id 不匹配。")
        required_semantic = len(self.cases) * self.repetitions
        if self.budgets.max_semantic_calls < required_semantic:
            raise ValueError("max_semantic_calls 小于 suite 的固定语义调用数。")
        if self.budgets.max_json_repair_calls < required_semantic:
            raise ValueError("max_json_repair_calls 必须覆盖每个角色一次 JSON 修复。")
        return self


@dataclass(frozen=True)
class ValidatedModelBenchmarkSuite:
    """已验证路径、hash、Prompt、模型参数和 Fixture 绑定的 suite."""

    manifest: ModelBenchmarkManifest
    manifest_path: Path
    manifest_sha256: str
    reference_path: Path
    fixture_path: Path
    fixtures: dict[str, dict[str, Any]]
    fixture_hashes: dict[str, str]
    resolved_model_configs: dict[str, dict[str, Any]]

    def summary(self) -> dict[str, Any]:
        """返回可直接供 CLI 展示的验证摘要."""
        return {
            "schema_version": self.manifest.schema_version,
            "suite_id": self.manifest.suite_id,
            "manifest_sha256": self.manifest_sha256,
            "case_count": len(self.manifest.cases),
            "repetitions": self.manifest.repetitions,
            "default_execution_mode": self.manifest.default_execution_mode,
            "reference_image_sha256": self.manifest.reference_image.sha256,
            "fixture_file_sha256": self.manifest.model_fixtures.sha256,
            "fixture_hashes": dict(self.fixture_hashes),
            "model_configs": dict(self.resolved_model_configs),
            "requested_model_ref": self.manifest.model_call_config.requested_model_ref,
            "price_version": self.manifest.budgets.price_version,
        }


def _stable_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _safe_benchmark_file(manifest_path: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        candidate = (manifest_path.parent / relative).resolve(strict=False)
    else:
        candidate = (manifest_path.parent / relative).resolve(strict=False)
    if (
        not candidate.is_relative_to(BENCHMARKS_ROOT.resolve())
        or not candidate.is_file()
    ):
        raise ValueError("模型 benchmark 文件必须位于 benchmarks 内且真实存在。")
    return candidate


def _prompt_specs() -> dict[str, tuple[str, PromptDefinition, NodeModelConfig]]:
    return {
        "visual_analysis": (
            "visual_analysis_v1",
            VISUAL_ANALYSIS_PROMPT,
            VISUAL_ANALYSIS_MODEL_CONFIG,
        ),
        "author_initial": (
            "shader_author_initial_v1",
            AUTHOR_PROMPTS[AuthorMode.INITIAL],
            SHADER_AUTHOR_MODEL_CONFIG,
        ),
        "author_compile_repair": (
            "shader_author_compile_repair_v1",
            AUTHOR_PROMPTS[AuthorMode.COMPILE_REPAIR],
            SHADER_AUTHOR_MODEL_CONFIG,
        ),
        "visual_critic": (
            "visual_critic_v1",
            VISUAL_CRITIC_PROMPT,
            VISUAL_CRITIC_MODEL_CONFIG,
        ),
        "author_visual_refine": (
            "shader_author_visual_refine_v1",
            AUTHOR_PROMPTS[AuthorMode.VISUAL_REFINE],
            SHADER_AUTHOR_MODEL_CONFIG,
        ),
    }


def _model_config_dict(config: NodeModelConfig) -> dict[str, Any]:
    call = config.call
    return {
        "model_ref": call.model_ref,
        "temperature": call.temperature,
        "thinking": call.thinking,
        "capture_reasoning": call.capture_reasoning,
        "response_format": call.response_format,
    }


def _load_fixture_catalog(
    path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    try:
        root = json.loads(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("模型 benchmark Fixture JSON 无法解析。") from exc
    if not isinstance(root, dict) or root.get("schema_version") != (
        "node_lab_model_fixtures_v1"
    ):
        raise ValueError("模型 benchmark Fixture schema_version 不受支持。")
    raw_fixtures = root.get("fixtures")
    if not isinstance(raw_fixtures, list):
        raise ValueError("模型 benchmark Fixture 列表缺失。")
    fixtures: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    for raw in raw_fixtures:
        if not isinstance(raw, dict) or not isinstance(raw.get("fixture_id"), str):
            raise ValueError("模型 benchmark Fixture 条目不合法。")
        fixture_id = raw["fixture_id"]
        if fixture_id in fixtures:
            raise ValueError("模型 benchmark Fixture id 重复。")
        fixtures[fixture_id] = raw
        hashes[fixture_id] = sha256(_stable_json_bytes(raw)).hexdigest()
    return fixtures, hashes


def load_model_benchmark_manifest(
    manifest_path: str | Path = DEFAULT_MODEL_BENCHMARK_MANIFEST,
) -> ValidatedModelBenchmarkSuite:
    """加载并完整验证独立模型 benchmark manifest."""
    path = Path(manifest_path).resolve()
    raw_bytes = path.read_bytes()
    manifest = ModelBenchmarkManifest.model_validate(yaml.safe_load(raw_bytes))
    reference_path = _safe_benchmark_file(path, manifest.reference_image.path)
    fixture_path = _safe_benchmark_file(path, manifest.model_fixtures.path)
    for spec, resolved in (
        (manifest.reference_image, reference_path),
        (manifest.model_fixtures, fixture_path),
    ):
        if sha256(resolved.read_bytes()).hexdigest() != spec.sha256:
            raise ValueError(f"{resolved.name} 的 SHA-256 与 manifest 不一致。")

    fixtures, fixture_hashes = _load_fixture_catalog(fixture_path)
    prompt_specs = _prompt_specs()
    resolved_configs: dict[str, dict[str, Any]] = {}
    expected_model = manifest.model_call_config
    for case in manifest.cases:
        prompt_id, prompt, model_config = prompt_specs[case.node_id]
        prompt_hash = sha256(prompt.prompt.encode("utf-8")).hexdigest()
        if (
            case.prompt_id != prompt_id
            or case.prompt_version != prompt.version
            or case.prompt_sha256 != prompt_hash
        ):
            raise ValueError(f"{case.case_id} 的生产 Prompt 绑定发生漂移。")
        fixture_ids = [case.response_fixture_id, *case.upstream_fixture_ids]
        for fixture_id in fixture_ids:
            if fixture_id not in fixtures:
                raise ValueError(f"{case.case_id} 引用了未知模型 Fixture。")
        response_fixture = fixtures[case.response_fixture_id]
        if (
            response_fixture.get("node_id") != case.node_id
            or response_fixture.get("prompt_version") != case.prompt_version
        ):
            raise ValueError(f"{case.case_id} 的响应 Fixture 与节点不匹配。")
        resolved_config = _model_config_dict(model_config)
        if any(
            resolved_config[key] != getattr(expected_model, key)
            for key in (
                "temperature",
                "thinking",
                "capture_reasoning",
                "response_format",
            )
        ):
            raise ValueError(f"{case.case_id} 的生产模型参数与 manifest 不一致。")
        resolved_configs[case.node_id] = resolved_config

    return ValidatedModelBenchmarkSuite(
        manifest=manifest,
        manifest_path=path,
        manifest_sha256=sha256(raw_bytes).hexdigest(),
        reference_path=reference_path,
        fixture_path=fixture_path,
        fixtures=fixtures,
        fixture_hashes=fixture_hashes,
        resolved_model_configs=resolved_configs,
    )


def _fixture_raw_output(
    suite: ValidatedModelBenchmarkSuite,
    fixture_id: str,
) -> dict[str, Any]:
    fixture = suite.fixtures[fixture_id]
    responses = fixture.get("responses")
    if not isinstance(responses, list) or not responses:
        raise ValueError("模型 benchmark Fixture 缺少 semantic 响应。")
    response = responses[0]
    if not isinstance(response, dict) or response.get("kind") != "semantic":
        raise ValueError("模型 benchmark Fixture 首项必须是 semantic 响应。")
    raw = response.get("raw_output")
    if not isinstance(raw, dict):
        raise ValueError("模型 benchmark 上游 Fixture 必须使用 JSON object 输出。")
    return ensure_json_object(raw)


def _fixture_by_node(
    suite: ValidatedModelBenchmarkSuite,
    case: ModelBenchmarkCase,
    node_id: str,
) -> dict[str, Any]:
    for fixture_id in case.upstream_fixture_ids:
        fixture = suite.fixtures[fixture_id]
        if fixture.get("node_id") == node_id:
            return _fixture_raw_output(suite, fixture_id)
    raise ValueError(f"{case.case_id} 缺少 {node_id} 上游 Fixture。")


def _estimate_cost(
    *,
    input_tokens: int,
    output_tokens: int,
    budgets: ModelBenchmarkBudgets,
) -> float:
    return (
        input_tokens * budgets.input_cost_per_million_tokens_usd
        + output_tokens * budgets.output_cost_per_million_tokens_usd
    ) / 1_000_000


def _usage_bucket() -> dict[str, Any]:
    return {
        "call_count": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model_latency_ms": 0,
        "estimated_cost_usd": 0.0,
    }


def _usage_delta(after: Mapping[str, Any], before: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for kind in ("semantic", "json_repair"):
        result[kind] = {
            key: round(float(after[kind][key]) - float(before[kind][key]), 9)
            if key == "estimated_cost_usd"
            else int(after[kind][key]) - int(before[kind][key])
            for key in _usage_bucket()
        }
    result["total"] = _sum_usage(result)
    return result


def _sum_usage(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: round(
            sum(float(value[kind][key]) for kind in ("semantic", "json_repair")),
            9,
        )
        if key == "estimated_cost_usd"
        else sum(int(value[kind][key]) for kind in ("semantic", "json_repair"))
        for key in _usage_bucket()
    }


class BudgetedModelGateway:
    """在真实 Gateway 外施加 suite 级调用、token、wall 和成本硬预算."""

    def __init__(
        self,
        delegate: LLMGateway,
        budgets: ModelBenchmarkBudgets,
        requested_model_ref: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        initial_usage: Mapping[str, Any] | None = None,
        elapsed_seconds: float = 0.0,
    ) -> None:
        """绑定下游 Gateway、整套硬预算和单调时钟."""
        self._delegate = delegate
        self._budgets = budgets
        self._requested_model_ref = requested_model_ref
        self._clock = clock
        self._started_at = clock()
        self._elapsed_offset_seconds = max(0.0, elapsed_seconds)
        self._usage: dict[str, dict[str, Any]] = {}
        for kind in ("semantic", "json_repair"):
            bucket = _usage_bucket()
            source = initial_usage.get(kind, {}) if initial_usage is not None else {}
            if isinstance(source, Mapping):
                for key in bucket:
                    bucket[key] = source.get(key, bucket[key])
            self._usage[kind] = bucket

    def _elapsed_seconds(self) -> float:
        return self._elapsed_offset_seconds + max(0.0, self._clock() - self._started_at)

    def snapshot(self) -> dict[str, Any]:
        """返回只含聚合数字的安全 usage 快照."""
        buckets = {
            kind: dict(cast(Mapping[str, Any], value))
            for kind, value in self._usage.items()
        }
        return {**buckets, "total": _sum_usage(buckets)}

    @staticmethod
    def _kind(messages: Sequence[BaseMessage]) -> Literal["semantic", "json_repair"]:
        repair_hash = sha256(
            STRUCTURED_OUTPUT_REPAIR_PROMPT.prompt.encode("utf-8")
        ).hexdigest()
        for message in messages:
            if isinstance(message, SystemMessage):
                content_hash = sha256(str(message.content).encode("utf-8")).hexdigest()
                return "json_repair" if content_hash == repair_hash else "semantic"
        return "semantic"

    def _fail(self, code: str, options: LLMCallOptions) -> LLMInvocationError:
        return LLMInvocationError(
            f"Node Lab 模型 benchmark 硬预算拒绝调用：{code}。",
            model_ref=options.model_ref,
            provider="node_lab_model_benchmark",
            retryable=False,
        )

    @staticmethod
    def _timeout(code: str) -> TimeoutError:
        return TimeoutError(f"Node Lab 模型 benchmark 硬预算超时：{code}。")

    @staticmethod
    def _input_token_upper_bound(messages: Sequence[BaseMessage]) -> int:
        """以 UTF-8 byte 数给出保守 token 上界，并包含消息 envelope 余量."""
        return sum(
            len(
                json.dumps(
                    message.content,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            )
            + 256
            for message in messages
        )

    async def ainvoke(
        self,
        messages: Sequence[BaseMessage],
        options: LLMCallOptions,
    ) -> LLMResponse:
        """预算允许时调用下游，并按 semantic/repair 统计 usage."""
        kind = self._kind(messages)
        effective_options = replace(
            options,
            model_ref=self._requested_model_ref,
            max_output_tokens=self._budgets.max_output_tokens_per_call,
        )
        snapshot = self.snapshot()
        call_limit = (
            self._budgets.max_semantic_calls
            if kind == "semantic"
            else self._budgets.max_json_repair_calls
        )
        if int(snapshot[kind]["call_count"]) >= call_limit:
            raise self._fail(f"max_{kind}_calls_exhausted", options)
        if self._elapsed_seconds() >= self._budgets.max_wall_time_seconds:
            raise self._timeout("max_wall_time_exhausted")
        if int(snapshot["total"]["total_tokens"]) >= self._budgets.max_total_tokens:
            raise self._fail("max_total_tokens_exhausted", options)
        if (
            float(snapshot["total"]["estimated_cost_usd"])
            >= self._budgets.max_estimated_cost_usd
        ):
            raise self._fail("max_estimated_cost_exhausted", options)

        input_token_upper_bound = self._input_token_upper_bound(messages)
        call_token_reservation = (
            input_token_upper_bound + self._budgets.max_output_tokens_per_call
        )
        remaining_tokens = self._budgets.max_total_tokens - int(
            snapshot["total"]["total_tokens"]
        )
        if remaining_tokens < call_token_reservation:
            raise self._fail("max_tokens_per_call_reservation_unavailable", options)
        reserved_cost = _estimate_cost(
            input_tokens=input_token_upper_bound,
            output_tokens=self._budgets.max_output_tokens_per_call,
            budgets=self._budgets,
        )
        remaining_cost = self._budgets.max_estimated_cost_usd - float(
            snapshot["total"]["estimated_cost_usd"]
        )
        if remaining_cost + 1e-12 < reserved_cost:
            raise self._fail("max_cost_per_call_reservation_unavailable", options)

        bucket = self._usage[kind]
        bucket["call_count"] += 1
        remaining_wall = self._budgets.max_wall_time_seconds - (self._elapsed_seconds())
        try:
            response = await asyncio.wait_for(
                self._delegate.ainvoke(messages, effective_options),
                timeout=remaining_wall,
            )
        except TimeoutError as exc:
            raise self._timeout("max_wall_time_exceeded") from exc
        usage = response.usage
        if usage is None:
            raise self._fail("usage_missing", options)
        input_tokens = usage.input_tokens
        output_tokens = usage.output_tokens
        total_tokens = usage.total_tokens
        if input_tokens is None or output_tokens is None or total_tokens is None:
            raise self._fail("usage_incomplete", options)
        if min(input_tokens, output_tokens, total_tokens) < 0:
            raise self._fail("usage_invalid", options)
        if total_tokens != input_tokens + output_tokens:
            raise self._fail("usage_inconsistent", options)
        if output_tokens > self._budgets.max_output_tokens_per_call:
            raise self._fail("max_output_tokens_per_call_exceeded", options)

        cost = _estimate_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            budgets=self._budgets,
        )
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["total_tokens"] += total_tokens
        bucket["model_latency_ms"] += response.latency_ms
        bucket["estimated_cost_usd"] = round(
            float(bucket["estimated_cost_usd"]) + cost,
            9,
        )
        after = self.snapshot()["total"]
        if int(after["total_tokens"]) > self._budgets.max_total_tokens:
            raise self._fail("max_total_tokens_exceeded", options)
        if float(after["estimated_cost_usd"]) > self._budgets.max_estimated_cost_usd:
            raise self._fail("max_estimated_cost_exceeded", options)
        if self._elapsed_seconds() > self._budgets.max_wall_time_seconds:
            raise self._timeout("max_wall_time_exceeded")
        return response


def _fixed_inputs(
    *,
    suite: ValidatedModelBenchmarkSuite,
    case: ModelBenchmarkCase,
    reference_artifact_id: str,
    rendered_artifact_id: str,
    glsl_artifact_id: str | None,
    upstream_artifact_ids: Mapping[str, str],
    target_measurements: dict[str, Any],
    reference_sha256: str,
) -> dict[str, Any]:
    common: dict[str, Any] = {
        "reference_artifact_id": reference_artifact_id,
        "target_measurements": target_measurements,
        "render_contract": WEBGL1_STATIC_NO_TEXTURE_V1.to_dict(),
        "instruction": "复刻固定参考图中的粉色凝胶球。",
    }
    if case.node_id == "visual_analysis":
        return common

    if case.node_id == "author_initial":
        common["visual_analysis"] = _fixture_by_node(
            suite,
            case,
            "visual_analysis",
        )
        common["visual_analysis_artifact_id"] = upstream_artifact_ids["visual_analysis"]
        return common

    author = _fixture_by_node(suite, case, "author_initial")
    glsl = author.get("glsl")
    if not isinstance(glsl, str) or not glsl_artifact_id:
        raise ValueError(f"{case.case_id} 的 Author Fixture 缺少 GLSL。")
    if case.node_id == "author_compile_repair":
        return {
            **common,
            "previous_author_result": author,
            "previous_author_artifact_id": upstream_artifact_ids["author_initial"],
            "glsl_artifact_id": glsl_artifact_id,
            "static_validation": {
                "valid": False,
                "violations": [{"code": "fixture_compile_error"}],
            },
            "compile_result": {
                "success": False,
                "fragment_log": "fixture compile diagnostic",
            },
            "repair_budget": {"remaining": 1},
        }

    common["visual_analysis"] = _fixture_by_node(
        suite,
        case,
        "visual_analysis",
    )
    common["visual_analysis_artifact_id"] = upstream_artifact_ids["visual_analysis"]
    render_sha256 = reference_sha256
    candidate = {
        "candidate_id": "candidate-best",
        "parent_candidate_id": None,
        "glsl_sha256": sha256(glsl.encode("utf-8")).hexdigest(),
        "render_sha256": render_sha256,
        "prompt_version": "shader_author_initial_v1_1",
        "model_ref": "fixture:model",
        "iteration": 0,
    }
    candidate_inputs = {
        **common,
        "rendered_image_artifact_id": rendered_artifact_id,
        "glsl_artifact_id": glsl_artifact_id,
        "current_candidate": candidate,
        "current_best_candidate": candidate,
        "render_evidence_binding": {
            "candidate_id": "candidate-best",
            "glsl_sha256": candidate["glsl_sha256"],
            "image_sha256": render_sha256,
        },
        "score_breakdown": {
            "total_loss": 0.2,
            "roi_losses": {"highlight": 0.3, "subject": 0.1},
        },
        "residual_summary": {"highlight": "fixture_highlight_too_long"},
    }
    if case.node_id == "visual_critic":
        return candidate_inputs
    return {
        **candidate_inputs,
        "visual_review": _fixture_by_node(suite, case, "visual_critic"),
        "visual_review_artifact_id": upstream_artifact_ids["visual_critic"],
    }


def _fixture_usage(response: Mapping[str, Any]) -> dict[str, Any]:
    raw = response.get("usage")
    usage = raw if isinstance(raw, Mapping) else {}
    semantic = _usage_bucket()
    repair = _usage_bucket()
    semantic["call_count"] = int(usage.get("semantic_call_count", 0) or 0)
    repair["call_count"] = int(usage.get("json_repair_call_count", 0) or 0)
    semantic["input_tokens"] = int(usage.get("input_tokens", 0) or 0)
    semantic["output_tokens"] = int(usage.get("output_tokens", 0) or 0)
    semantic["total_tokens"] = int(usage.get("total_tokens", 0) or 0)
    semantic["model_latency_ms"] = int(usage.get("model_latency_ms", 0) or 0)
    buckets = {"semantic": semantic, "json_repair": repair}
    return {**buckets, "total": _sum_usage(buckets)}


def _copy_artifact(
    *,
    application: Any,
    store: RunArtifactStore,
    lab_run_id: str,
    descriptor: Any,
    attempt_root: str,
    role: Literal["input", "output"],
) -> dict[str, Any]:
    persisted, data = application.read_artifact(lab_run_id, descriptor.artifact_id)
    if persisted.to_dict() != descriptor.to_dict():
        raise ValueError("模型 benchmark Artifact descriptor 完整性校验失败。")
    relative_path = f"{attempt_root}/artifacts/{descriptor.artifact_id}/payload"
    ref = store.write_bytes(
        relative_path,
        data,
        content_type=descriptor.content_type,
    )
    if ref.sha256 != descriptor.sha256:
        raise ValueError("模型 benchmark Artifact payload 完整性校验失败。")
    return {
        "role": role,
        "relative_path": relative_path,
        "descriptor": descriptor.to_dict(),
    }


def _safe_run_id(value: str) -> str:
    if not _RUN_ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError("suite_run_id 包含非法字符。")
    return value


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _attempt_path(case_id: str, repetition: int) -> str:
    return f"cases/{case_id}/attempts/attempt-{repetition:03d}/execution.json"


def _percentiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p50": None, "p95": None, "max": None}
    return {
        "p50": round(float(statistics.median(values)), 6),
        "p95": (
            round(
                float(statistics.quantiles(values, n=100, method="inclusive")[94]),
                6,
            )
            if len(values) >= 20
            else None
        ),
        "max": round(max(values), 6),
    }


def _attempt_model_calls(attempt: Mapping[str, Any]) -> list[dict[str, Any]]:
    response = attempt.get("response")
    if not isinstance(response, Mapping):
        return []
    output = response.get("output")
    if not isinstance(output, Mapping):
        return []
    calls = output.get("model_calls")
    if not isinstance(calls, list):
        return []
    return [dict(call) for call in calls if isinstance(call, Mapping)]


def _attempt_timed_out(attempt: Mapping[str, Any]) -> bool:
    response = attempt.get("response")
    if not isinstance(response, Mapping):
        return False
    diagnostics = response.get("diagnostics")
    if isinstance(diagnostics, Mapping) and diagnostics.get("stop_reason") in {
        "wall_time_exhausted",
    }:
        return True
    output = response.get("output")
    events = output.get("events", []) if isinstance(output, Mapping) else []
    if not isinstance(events, list):
        return False
    return any(
        isinstance(event, Mapping)
        and isinstance(event.get("payload"), Mapping)
        and any(
            token in str(event["payload"].get("error_type", "")).lower()
            for token in ("timeout", "deadline")
        )
        for event in events
    )


def _aggregate_usage(entries: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    usage = {"semantic": _usage_bucket(), "json_repair": _usage_bucket()}
    for entry in entries:
        attempt_usage = entry.get("usage", {})
        if not isinstance(attempt_usage, Mapping):
            continue
        for kind in ("semantic", "json_repair"):
            bucket = attempt_usage.get(kind, {})
            if not isinstance(bucket, Mapping):
                continue
            for key in _usage_bucket():
                usage[kind][key] += bucket.get(key, 0)
    for kind in usage:
        usage[kind]["estimated_cost_usd"] = round(
            float(usage[kind]["estimated_cost_usd"]),
            9,
        )
    return {**usage, "total": _sum_usage(usage)}


def _aggregate_model_diagnostics(
    entries: Sequence[Mapping[str, Any]],
    usage: Mapping[str, Any],
    *,
    requested_model_ref: str,
) -> dict[str, Any]:
    calls = [call for entry in entries for call in _attempt_model_calls(entry)]
    parse_statuses = [
        str(call.get("parse_status"))
        for call in calls
        if call.get("parse_status") in {"valid", "invalid"}
    ]
    issue_codes = Counter(
        str(issue.get("code", "schema_validation"))
        for call in calls
        for issue in call.get("validation_issues", [])
        if isinstance(issue, Mapping)
    )
    error_codes = Counter(
        str(code)
        for call in calls
        for code in call.get("error_codes", [])
        if isinstance(code, str)
    )
    requested_refs = Counter(
        str(call["requested_model_ref"])
        for call in calls
        if isinstance(call.get("requested_model_ref"), str)
    )
    actual_refs = Counter(
        str(call["model_ref"])
        for call in calls
        if isinstance(call.get("model_ref"), str)
    )
    parse_total = len(parse_statuses)
    valid_count = parse_statuses.count("valid")
    total_usage = usage.get("total", {})
    total_calls = (
        int(total_usage.get("call_count", 0)) if isinstance(total_usage, Mapping) else 0
    )
    binding_codes = {
        "binding_mismatch",
        "compile_scope_violation",
        "role_violation",
    }
    return {
        "parse_status_counts": dict(sorted(Counter(parse_statuses).items())),
        "parse_pass_rate": (
            round(valid_count / parse_total, 6) if parse_total else None
        ),
        "schema_issue_counts": dict(sorted(issue_codes.items())),
        "error_code_counts": dict(sorted(error_codes.items())),
        "binding_failure_count": sum(
            count for code, count in error_codes.items() if code in binding_codes
        ),
        "timeout_attempt_count": sum(_attempt_timed_out(entry) for entry in entries),
        "model_latency_ms": (
            int(total_usage.get("model_latency_ms", 0))
            if isinstance(total_usage, Mapping)
            else 0
        ),
        "model_identity": {
            "manifest_requested_model_ref": requested_model_ref,
            "requested_model_ref_counts": dict(sorted(requested_refs.items())),
            "actual_model_ref_counts": dict(sorted(actual_refs.items())),
            "missing_actual_model_call_count": max(0, total_calls - len(calls)),
        },
    }


def _load_attempt_evidence(
    *,
    suite: ValidatedModelBenchmarkSuite,
    store: RunArtifactStore,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    interruptions: list[dict[str, Any]] = []
    for case in suite.manifest.cases:
        attempt_directory = store.root / f"cases/{case.case_id}/attempts"
        for path in sorted(attempt_directory.glob("*/execution.json")):
            evidence = json.loads(path.read_bytes())
            evidence["evidence_relative_path"] = path.relative_to(store.root).as_posix()
            attempts.append(evidence)
        for path in sorted(
            attempt_directory.glob("*/interruptions/interruption-*.json")
        ):
            evidence = json.loads(path.read_bytes())
            evidence["evidence_relative_path"] = path.relative_to(store.root).as_posix()
            interruptions.append(evidence)
    return attempts, interruptions


def _report_from_attempts(
    *,
    suite: ValidatedModelBenchmarkSuite,
    store: RunArtifactStore,
    suite_run_id: str,
    config_sha256: str,
    execution_mode: Literal["fixture", "real"],
    source_fingerprint: str,
    environment_fingerprint: str,
) -> dict[str, Any]:
    attempts, interruptions = _load_attempt_evidence(suite=suite, store=store)
    planned = len(suite.manifest.cases) * suite.manifest.repetitions
    passed = sum(item.get("correctness_passed") is True for item in attempts)
    completed_slots = {
        (item.get("case_id"), item.get("attempt_id")) for item in attempts
    }
    interrupted_slots = {
        (item.get("case_id"), item.get("attempt_id")) for item in interruptions
    }
    interrupted_only_slots = interrupted_slots - completed_slots
    unstarted = max(0, planned - len(completed_slots) - len(interrupted_only_slots))
    denominator = len(attempts) + len(interruptions) + unstarted
    entries: list[dict[str, Any]] = [*attempts, *interruptions]
    usage = _aggregate_usage(entries)
    diagnostics = _aggregate_model_diagnostics(
        entries,
        usage,
        requested_model_ref=suite.manifest.model_call_config.requested_model_ref,
    )
    failures = [
        {
            "case_id": item.get("case_id"),
            "attempt_id": item.get("attempt_id"),
            "attempt_status": item.get("attempt_status"),
            "correctness_failures": item.get("correctness_failures", []),
            "evidence_relative_path": item.get("evidence_relative_path"),
        }
        for item in entries
        if item.get("correctness_passed") is not True
    ]
    for case in suite.manifest.cases:
        for repetition in range(1, suite.manifest.repetitions + 1):
            slot = (case.case_id, f"attempt-{repetition:03d}")
            if slot not in completed_slots and slot not in interrupted_slots:
                failures.append(
                    {
                        "case_id": case.case_id,
                        "attempt_id": slot[1],
                        "attempt_status": "unstarted",
                        "correctness_failures": ["execution_not_started"],
                        "evidence_relative_path": None,
                    }
                )
    role_reports: dict[str, Any] = {}
    for case in suite.manifest.cases:
        case_attempts = [
            item for item in attempts if item.get("case_id") == case.case_id
        ]
        case_interruptions = [
            item for item in interruptions if item.get("case_id") == case.case_id
        ]
        case_entries: list[dict[str, Any]] = [*case_attempts, *case_interruptions]
        case_usage = _aggregate_usage(case_entries)
        case_completed_slots = {item.get("attempt_id") for item in case_attempts}
        case_interrupted_slots = {item.get("attempt_id") for item in case_interruptions}
        case_unstarted = max(
            0,
            suite.manifest.repetitions
            - len(case_completed_slots)
            - len(case_interrupted_slots - case_completed_slots),
        )
        case_denominator = len(case_attempts) + len(case_interruptions) + case_unstarted
        case_passed = sum(
            item.get("correctness_passed") is True for item in case_attempts
        )
        role_reports[case.node_id] = {
            "case_id": case.case_id,
            "planned_attempt_count": suite.manifest.repetitions,
            "completed_attempt_count": len(case_attempts),
            "interrupted_attempt_count": len(case_interruptions),
            "unstarted_attempt_count": case_unstarted,
            "denominator_attempt_count": case_denominator,
            "passed_attempt_count": case_passed,
            "failed_attempt_count": case_denominator - case_passed,
            "correctness_rate": round(case_passed / case_denominator, 6),
            "duration_ms": _percentiles(
                [float(item["duration_ms"]) for item in case_attempts]
            ),
            "usage": case_usage,
            "diagnostics": _aggregate_model_diagnostics(
                case_entries,
                case_usage,
                requested_model_ref=(
                    suite.manifest.model_call_config.requested_model_ref
                ),
            ),
        }
    evidence = [
        {
            "case_id": item.get("case_id"),
            "attempt_id": item.get("attempt_id"),
            "attempt_status": item.get("attempt_status"),
            "relative_path": item.get("evidence_relative_path"),
            "artifact_relative_paths": [
                artifact.get("relative_path")
                for artifact in item.get("artifact_evidence", [])
                if isinstance(artifact, Mapping)
            ],
        }
        for item in entries
    ]
    return {
        "schema_version": "node_lab_model_benchmark_report_v1",
        "suite_run_id": suite_run_id,
        "suite_id": suite.manifest.suite_id,
        "execution_mode": execution_mode,
        "manifest_sha256": suite.manifest_sha256,
        "config_sha256": config_sha256,
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "price_version": suite.manifest.budgets.price_version,
        "planned_attempt_count": planned,
        "denominator_attempt_count": denominator,
        "attempt_count": len(entries),
        "completed_attempt_count": len(attempts),
        "interrupted_attempt_count": len(interruptions),
        "unstarted_attempt_count": unstarted,
        "passed_attempt_count": passed,
        "failed_attempt_count": denominator - passed,
        "correctness_rate": round(passed / denominator, 6),
        "duration_ms": _percentiles(
            [
                float(item["duration_ms"])
                for item in attempts
                if isinstance(item.get("duration_ms"), (int, float))
            ]
        ),
        "usage": usage,
        "diagnostics": diagnostics,
        "roles": role_reports,
        "attempt_evidence": evidence,
        "failures": failures,
        "generated_at": _iso_now(),
    }


def _report_markdown(report: Mapping[str, Any]) -> str:
    usage = cast(Mapping[str, Any], report["usage"])
    total = cast(Mapping[str, Any], usage["total"])
    return "\n".join(
        (
            "# Node Lab 模型 Benchmark 报告",
            "",
            f"- Suite：`{report['suite_id']}`",
            f"- 执行模式：`{report['execution_mode']}`",
            f"- 通过：{report['passed_attempt_count']} / {report['denominator_attempt_count']}",
            f"- 正确率：{float(report['correctness_rate']) * 100:.2f}%",
            f"- 语义调用：{usage['semantic']['call_count']}",
            f"- JSON repair 调用：{usage['json_repair']['call_count']}",
            f"- 总 token：{total['total_tokens']}",
            f"- 模型延迟：{total['model_latency_ms']}ms",
            f"- 估算成本：${float(total['estimated_cost_usd']):.6f}",
            f"- 价格版本：`{report['price_version']}`",
            "",
            "失败和中断始终保留在 denominator_attempt_count 分母中。",
            "",
        )
    )


def _write_interruption(
    *,
    store: RunArtifactStore,
    suite_run_id: str,
    config_sha256: str,
    case: ModelBenchmarkCase,
    repetition: int,
    attempt_root: str,
    execution_mode: Literal["fixture", "real"],
    duration_ms: float,
    error_type: str,
    usage: Mapping[str, Any],
) -> str:
    interruption_root = store.path_for(f"{attempt_root}/interruptions")
    existing = (
        list(interruption_root.glob("interruption-*.json"))
        if interruption_root.exists()
        else []
    )
    interruption_id = f"interruption-{len(existing) + 1:03d}"
    relative_path = f"{attempt_root}/interruptions/{interruption_id}.json"
    store.write_json(
        relative_path,
        {
            "schema_version": "node_lab_model_benchmark_interruption_v1",
            "attempt_status": "interrupted",
            "suite_run_id": suite_run_id,
            "config_sha256": config_sha256,
            "case_id": case.case_id,
            "node_id": case.node_id,
            "attempt_id": f"attempt-{repetition:03d}",
            "interruption_id": interruption_id,
            "execution_mode": execution_mode,
            "duration_ms": duration_ms,
            "correctness_passed": False,
            "correctness_failures": ["execution_interrupted"],
            "error_type": error_type,
            "usage": dict(usage),
            "artifact_evidence": [],
            "response": None,
        },
    )
    return relative_path


async def run_model_benchmark(
    suite: ValidatedModelBenchmarkSuite,
    *,
    output_root: str | Path = DEFAULT_MODEL_BENCHMARK_OUTPUT_ROOT,
    lab_root: str | Path = DEFAULT_MODEL_BENCHMARK_LAB_ROOT,
    suite_run_id: str | None = None,
    execution_mode: Literal["fixture", "real"] = "fixture",
    allow_model_calls: bool = False,
    real_model_enabled: bool = False,
    gateway: LLMGateway | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """运行五个独立角色 attempt，并原子保存证据、JSON 与 Markdown 报告."""
    if execution_mode == "real" and (
        not allow_model_calls or not real_model_enabled or gateway is None
    ):
        raise ValueError("真实模型模式未同时满足 CLI、环境变量和 Gateway 门禁。")
    if execution_mode == "fixture" and gateway is not None:
        raise ValueError("fixture 模式不得注入真实 Gateway。")

    run_id = _safe_run_id(suite_run_id or f"node-lab-model-{uuid4().hex[:12]}")
    run_root = Path(output_root).resolve() / run_id
    store = RunArtifactStore(run_root)
    service_path = Path(__file__).resolve()
    prompt_paths = list((ROOT / "src/agent/app/prompts").glob("*.yaml"))
    v1_node_paths = list(
        (ROOT / "src/agent/app/nodes/png_to_shader_v1").rglob("*.py")
    )
    environment, source_fingerprint, environment_fingerprint = source_environment(
        extra_source_paths=(
            service_path,
            ROOT / "src/agent/app/parsers/png_to_shader_v1.py",
            suite.fixture_path,
            *v1_node_paths,
            *prompt_paths,
        )
    )
    config_base = {
        "schema_version": "node_lab_model_benchmark_config_v1",
        "suite_run_id": run_id,
        "execution_mode": execution_mode,
        "suite": suite.summary(),
        "budgets": suite.manifest.budgets.to_dict(),
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "fixture_hashes": suite.fixture_hashes,
        "prompt_hashes": {
            case.node_id: case.prompt_sha256 for case in suite.manifest.cases
        },
    }
    config_sha256 = sha256(_stable_json_bytes(config_base)).hexdigest()
    config = {**config_base, "config_sha256": config_sha256}
    config_path = run_root / "config.json"
    if config_path.is_file():
        existing = json.loads(config_path.read_bytes())
        if existing != config:
            raise ValueError("suite_run_id 已存在且 config hash 不一致，禁止覆盖。")
        report_path = run_root / "report.json"
        all_completed = all(
            store.path_for(_attempt_path(case.case_id, repetition)).is_file()
            for case in suite.manifest.cases
            for repetition in range(1, suite.manifest.repetitions + 1)
        )
        if report_path.is_file() and all_completed:
            existing_report = ensure_json_object(json.loads(report_path.read_bytes()))
            if (
                existing_report.get("config_sha256") == config_sha256
                and existing_report.get("completed_attempt_count")
                == len(suite.manifest.cases) * suite.manifest.repetitions
            ):
                return existing_report
    else:
        store.write_json("config.json", config)
        store.write_bytes(
            "manifest.snapshot.yaml",
            suite.manifest_path.read_bytes(),
            content_type="application/yaml",
        )
        store.write_json("environment.json", environment)

    existing_attempts, existing_interruptions = _load_attempt_evidence(
        suite=suite,
        store=store,
    )
    existing_entries: list[dict[str, Any]] = [
        *existing_attempts,
        *existing_interruptions,
    ]
    resumed_usage = _aggregate_usage(existing_entries)
    resumed_elapsed_seconds = (
        sum(float(entry.get("duration_ms", 0.0) or 0.0) for entry in existing_entries)
        / 1000.0
    )

    tracker = (
        BudgetedModelGateway(
            gateway,
            suite.manifest.budgets,
            suite.manifest.model_call_config.requested_model_ref,
            clock=clock,
            initial_usage=resumed_usage,
            elapsed_seconds=resumed_elapsed_seconds,
        )
        if execution_mode == "real" and gateway is not None
        else None
    )
    application = create_node_lab_application(
        root=lab_root,
        model_gateway=tracker,
        real_model_enabled=execution_mode == "real",
        model_fixture_path=suite.fixture_path,
    )
    reference = suite.reference_path.read_bytes()
    reference_sha256 = sha256(reference).hexdigest()
    target_measurements = measure_target(reference).to_dict()

    current_attempt: (
        tuple[
            ModelBenchmarkCase,
            int,
            str,
            float,
            dict[str, Any] | None,
        ]
        | None
    ) = None
    try:
        for case in suite.manifest.cases:
            for repetition in range(1, suite.manifest.repetitions + 1):
                if store.path_for(_attempt_path(case.case_id, repetition)).is_file():
                    continue
                attempt_id = f"attempt-{repetition:03d}"
                attempt_root = f"cases/{case.case_id}/attempts/{attempt_id}"
                started = time.perf_counter()
                before_usage = tracker.snapshot() if tracker is not None else None
                current_attempt = (
                    case,
                    repetition,
                    attempt_root,
                    started,
                    before_usage,
                )
                lab_run = application.create_run(
                    LabRunCreateRequest(
                        project_id="node-lab-model-benchmark",
                        initial_state={
                            "run_id": run_id,
                            "project_id": "node-lab-model-benchmark",
                            "budget_policy": {
                                "max_visual_refinements": 1,
                                "max_compile_repairs": 1,
                                "max_model_calls": 2,
                                "max_wall_time_seconds": (
                                    suite.manifest.budgets.max_wall_time_seconds
                                ),
                            },
                            "started_at": clock(),
                            "model_call_count": 0,
                            "model_calls": [],
                            "events": [],
                        },
                    )
                )
                reference_descriptor = application.upload_artifact(
                    lab_run_id=lab_run.lab_run_id,
                    kind="reference-png",
                    content_type="image/png",
                    data=reference,
                )
                rendered_descriptor = application.upload_artifact(
                    lab_run_id=lab_run.lab_run_id,
                    kind="render-png",
                    content_type="image/png",
                    data=reference,
                )
                upstream_descriptors: dict[str, Any] = {}
                for fixture_id in case.upstream_fixture_ids:
                    fixture = suite.fixtures[fixture_id]
                    upstream_node_id = str(fixture["node_id"])
                    upstream_descriptors[upstream_node_id] = (
                        application.upload_artifact(
                            lab_run_id=lab_run.lab_run_id,
                            kind=f"fixture-{upstream_node_id}",
                            content_type="application/json; charset=utf-8",
                            data=_stable_json_bytes(
                                _fixture_raw_output(suite, fixture_id)
                            ),
                        )
                    )
                author_source = None
                if case.node_id in {
                    "author_compile_repair",
                    "visual_critic",
                    "author_visual_refine",
                }:
                    author_source = _fixture_by_node(suite, case, "author_initial")
                glsl_descriptor = None
                if author_source is not None:
                    glsl = author_source.get("glsl")
                    if not isinstance(glsl, str):
                        raise ValueError("上游 Author Fixture 缺少 GLSL。")
                    glsl_descriptor = application.upload_artifact(
                        lab_run_id=lab_run.lab_run_id,
                        kind="shader-glsl",
                        content_type="text/plain; charset=utf-8",
                        data=glsl.encode("utf-8"),
                    )
                inputs = ensure_json_object(
                    json.loads(
                        _stable_json_bytes(
                            _fixed_inputs(
                                suite=suite,
                                case=case,
                                reference_artifact_id=reference_descriptor.artifact_id,
                                rendered_artifact_id=rendered_descriptor.artifact_id,
                                glsl_artifact_id=(
                                    glsl_descriptor.artifact_id
                                    if glsl_descriptor
                                    else None
                                ),
                                upstream_artifact_ids={
                                    node_id: descriptor.artifact_id
                                    for node_id, descriptor in (
                                        upstream_descriptors.items()
                                    )
                                },
                                target_measurements=target_measurements,
                                reference_sha256=reference_sha256,
                            )
                        )
                    )
                )
                response = await application.execute_step(
                    StepExecutionRequest(
                        lab_run_id=lab_run.lab_run_id,
                        node_id=case.node_id,
                        execution_mode=execution_mode,
                        allow_model_call=execution_mode == "real",
                        fixture_id=(
                            case.response_fixture_id
                            if execution_mode == "fixture"
                            else None
                        ),
                        inputs=inputs,
                    )
                )
                response_dict = response.to_dict()
                expected_field = {
                    "visual_analysis": "visual_analysis_artifact_id",
                    "author_initial": "author_artifact_id",
                    "author_compile_repair": "author_artifact_id",
                    "visual_critic": "visual_review_artifact_id",
                    "author_visual_refine": "author_artifact_id",
                }[case.node_id]
                failures: list[str] = []
                if response.execution_status != "completed":
                    failures.append("execution_status_not_completed")
                if response.outcome != "success":
                    failures.append("outcome_not_success")
                if expected_field not in response.output:
                    failures.append(f"missing_output:{expected_field}")
                input_descriptors = [
                    reference_descriptor,
                    rendered_descriptor,
                    *upstream_descriptors.values(),
                ]
                if glsl_descriptor is not None:
                    input_descriptors.append(glsl_descriptor)
                artifact_evidence = [
                    _copy_artifact(
                        application=application,
                        store=store,
                        lab_run_id=lab_run.lab_run_id,
                        descriptor=descriptor,
                        attempt_root=attempt_root,
                        role="input",
                    )
                    for descriptor in input_descriptors
                ]
                for descriptor in response.artifacts:
                    artifact_evidence.append(
                        _copy_artifact(
                            application=application,
                            store=store,
                            lab_run_id=lab_run.lab_run_id,
                            descriptor=descriptor,
                            attempt_root=attempt_root,
                            role="output",
                        )
                    )
                after_usage = tracker.snapshot() if tracker is not None else None
                usage = (
                    _usage_delta(after_usage, before_usage)
                    if after_usage is not None and before_usage is not None
                    else _fixture_usage(response_dict)
                )
                store.write_json(
                    _attempt_path(case.case_id, repetition),
                    {
                        "schema_version": "node_lab_model_benchmark_attempt_v1",
                        "attempt_status": "completed",
                        "suite_run_id": run_id,
                        "config_sha256": config_sha256,
                        "case_id": case.case_id,
                        "node_id": case.node_id,
                        "attempt_id": attempt_id,
                        "execution_mode": execution_mode,
                        "lab_run_id": lab_run.lab_run_id,
                        "fixture_hashes": {
                            fixture_id: suite.fixture_hashes[fixture_id]
                            for fixture_id in (
                                case.response_fixture_id,
                                *case.upstream_fixture_ids,
                            )
                        },
                        "prompt_sha256": case.prompt_sha256,
                        "input_sha256": sha256(_stable_json_bytes(inputs)).hexdigest(),
                        "duration_ms": (time.perf_counter() - started) * 1000.0,
                        "correctness_passed": not failures,
                        "correctness_failures": failures,
                        "usage": usage,
                        "artifact_evidence": artifact_evidence,
                        "response": response_dict,
                    },
                )
                current_attempt = None
    except (asyncio.CancelledError, KeyboardInterrupt) as exc:
        if current_attempt is not None:
            case, repetition, attempt_root, started, before_usage = current_attempt
            interruption_usage = (
                _usage_delta(tracker.snapshot(), before_usage)
                if tracker is not None and before_usage is not None
                else {
                    "semantic": _usage_bucket(),
                    "json_repair": _usage_bucket(),
                    "total": _usage_bucket(),
                }
            )
            _write_interruption(
                store=store,
                suite_run_id=run_id,
                config_sha256=config_sha256,
                case=case,
                repetition=repetition,
                attempt_root=attempt_root,
                execution_mode=execution_mode,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                error_type=type(exc).__name__,
                usage=interruption_usage,
            )
        partial = _report_from_attempts(
            suite=suite,
            store=store,
            suite_run_id=run_id,
            config_sha256=config_sha256,
            execution_mode=execution_mode,
            source_fingerprint=source_fingerprint,
            environment_fingerprint=environment_fingerprint,
        )
        store.write_json("report.json", partial)
        store.write_text("report.md", _report_markdown(partial))
        raise

    report = _report_from_attempts(
        suite=suite,
        store=store,
        suite_run_id=run_id,
        config_sha256=config_sha256,
        execution_mode=execution_mode,
        source_fingerprint=source_fingerprint,
        environment_fingerprint=environment_fingerprint,
    )
    store.write_json("report.json", report)
    store.write_text("report.md", _report_markdown(report))
    return ensure_json_object(report)


__all__ = [
    "BudgetedModelGateway",
    "DEFAULT_MODEL_BENCHMARK_LAB_ROOT",
    "DEFAULT_MODEL_BENCHMARK_MANIFEST",
    "DEFAULT_MODEL_BENCHMARK_OUTPUT_ROOT",
    "ModelBenchmarkBudgets",
    "ModelBenchmarkManifest",
    "ValidatedModelBenchmarkSuite",
    "load_model_benchmark_manifest",
    "run_model_benchmark",
]
