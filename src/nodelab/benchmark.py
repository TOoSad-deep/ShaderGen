"""Node Lab AI-off benchmark 的冻结 manifest、逐 attempt 证据与报告."""

from __future__ import annotations

import asyncio
import json
import platform
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import yaml  # type: ignore[import-untyped]
from pydantic import Field, field_validator, model_validator
from typing_extensions import Self

from nodelab.file_store import AtomicFileStore
from nodelab.models import (
    CapabilityExecutionRequest,
    Identifier,
    LabRunCreateRequest,
    NodeLabModel,
    StepExecutionRequest,
    ensure_json_object,
)

_SUITE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_CASES = 100
_MAX_REPETITIONS = 20
_MAX_WARMUPS = 5


class BenchmarkArtifactInput(NodeLabModel):
    """一个 manifest 文件到 capability Artifact 字段的绑定."""

    field: Identifier
    path: str = Field(min_length=1, max_length=500)
    kind: Identifier
    content_type: str = Field(min_length=3, max_length=100)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    step_id: Identifier | None = None


class BenchmarkExpectation(NodeLabModel):
    """单 case 的稳定 outcome 与 JSON pointer 期望."""

    outcome: Literal["success", "rejected", "stopped"]
    values: dict[str, Any] = Field(default_factory=dict)

    @field_validator("values")
    @classmethod
    def validate_values(cls, value: dict[str, Any]) -> dict[str, Any]:
        """期望值必须可稳定冻结到 config."""
        return ensure_json_object(value)


class BenchmarkStepBinding(NodeLabModel):
    """把前序 scenario 响应 JSON pointer 绑定到当前输入字段."""

    field: Identifier
    source_step_id: Identifier
    json_pointer: str = Field(min_length=1, max_length=500, pattern=r"^/")


class BenchmarkScenarioStep(NodeLabModel):
    """一个 multi-step scenario 中的确定性 capability 调用."""

    step_id: Identifier
    capability_id: Identifier
    inputs: dict[str, Any] = Field(default_factory=dict)
    bindings: list[BenchmarkStepBinding] = Field(default_factory=list)
    expect: BenchmarkExpectation

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Scenario inline 输入必须是 JSON-safe object."""
        return ensure_json_object(value)

    @model_validator(mode="after")
    def validate_binding_fields(self) -> Self:
        """同一步不能重复或同时以内联值和绑定设置字段."""
        fields = [binding.field for binding in self.bindings]
        if len(fields) != len(set(fields)):
            raise ValueError("scenario step binding field 不能重复。")
        if set(fields) & set(self.inputs):
            raise ValueError("scenario binding field 不能同时出现在 inputs。")
        return self


class BenchmarkPipelineStep(NodeLabModel):
    """一个 pipeline 中通过不可变父快照执行的确定性 production node."""

    step_id: Identifier
    node_id: Identifier
    base_step_id: Identifier | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    bindings: list[BenchmarkStepBinding] = Field(default_factory=list)
    expect: BenchmarkExpectation

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Pipeline inline 输入必须是 JSON-safe object."""
        return ensure_json_object(value)

    @model_validator(mode="after")
    def validate_binding_fields(self) -> Self:
        """同一步不能重复或同时以内联值和绑定设置字段."""
        fields = [binding.field for binding in self.bindings]
        if len(fields) != len(set(fields)):
            raise ValueError("pipeline step binding field 不能重复。")
        if set(fields) & set(self.inputs):
            raise ValueError("pipeline binding field 不能同时出现在 inputs。")
        return self


class BenchmarkCase(NodeLabModel):
    """一个显式 capability、node、scenario 或 pipeline benchmark case."""

    case_id: Identifier
    target_type: Literal["capability", "node", "scenario", "pipeline"]
    capability_id: Identifier | None = None
    node_id: Identifier | None = None
    profile: Identifier
    inputs: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[BenchmarkArtifactInput] = Field(default_factory=list)
    expect: BenchmarkExpectation | None = None
    steps: list[BenchmarkScenarioStep | BenchmarkPipelineStep] = Field(
        default_factory=list
    )

    @field_validator("inputs")
    @classmethod
    def validate_inputs(cls, value: dict[str, Any]) -> dict[str, Any]:
        """非文件输入必须是 JSON-safe object."""
        return ensure_json_object(value)

    @model_validator(mode="after")
    def validate_shape_and_artifact_fields(self) -> Self:
        """按 target_type 区分执行入口，并禁止 Artifact 字段冲突."""
        if self.target_type == "scenario":
            if self.profile != "scenario":
                raise ValueError("scenario target 必须使用 scenario profile。")
            if (
                self.capability_id is not None
                or self.node_id is not None
                or self.expect is not None
            ):
                raise ValueError("scenario case 不得设置顶层 target id 或 expect。")
            if len(self.steps) < 2:
                raise ValueError("scenario case 至少需要两个 steps。")
            if not all(isinstance(step, BenchmarkScenarioStep) for step in self.steps):
                raise ValueError("scenario steps 必须全部使用 capability_id。")
            if self.inputs:
                raise ValueError("scenario case 的 inputs 必须放在具体 step。")
        elif self.target_type == "pipeline":
            if self.profile != "pipeline":
                raise ValueError("pipeline target 必须使用 pipeline profile。")
            if (
                self.capability_id is not None
                or self.node_id is not None
                or self.expect is not None
            ):
                raise ValueError("pipeline case 不得设置顶层 target id 或 expect。")
            if len(self.steps) < 2:
                raise ValueError("pipeline case 至少需要两个 steps。")
            if not all(isinstance(step, BenchmarkPipelineStep) for step in self.steps):
                raise ValueError("pipeline steps 必须全部使用 node_id。")
            if self.inputs:
                raise ValueError("pipeline case 的 inputs 必须放在具体 step。")
        elif self.target_type == "node":
            if self.profile != "node":
                raise ValueError("node target 必须使用 node profile。")
            if (
                self.node_id is None
                or self.capability_id is not None
                or self.expect is None
                or self.steps
            ):
                raise ValueError("node case 必须设置 node_id/expect 且不能有 steps。")
        elif (
            self.capability_id is None
            or self.node_id is not None
            or self.expect is None
            or self.steps
            or self.profile in {"node", "scenario", "pipeline"}
        ):
            raise ValueError(
                "capability case 必须设置 capability_id/expect，并使用 capability profile。"
            )
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("多步骤 step_id 不能重复。")
        step_id_set = set(step_ids)
        positions = {step_id: index for index, step_id in enumerate(step_ids)}
        for index, step in enumerate(self.steps):
            for binding in step.bindings:
                if binding.source_step_id not in step_id_set:
                    raise ValueError("binding 引用了未知 step_id。")
                if positions[binding.source_step_id] >= index:
                    raise ValueError("binding 只能引用前序 step。")
            if (
                isinstance(step, BenchmarkPipelineStep)
                and step.base_step_id is not None
            ):
                if step.base_step_id not in step_id_set:
                    raise ValueError("pipeline base_step_id 引用了未知 step_id。")
                if positions[step.base_step_id] >= index:
                    raise ValueError("pipeline base_step_id 只能引用前序 step。")
        fields = [(artifact.step_id, artifact.field) for artifact in self.artifacts]
        if len(fields) != len(set(fields)):
            raise ValueError("case Artifact 的 step_id/field 组合不能重复。")
        if self.target_type in {"scenario", "pipeline"}:
            if any(artifact.step_id not in step_id_set for artifact in self.artifacts):
                raise ValueError("多步骤 Artifact 必须绑定已登记 step_id。")
            step_inputs = {step.step_id: set(step.inputs) for step in self.steps}
            if any(
                artifact.field in step_inputs[str(artifact.step_id)]
                for artifact in self.artifacts
            ):
                raise ValueError("多步骤 Artifact field 不能同时出现在 step.inputs。")
        elif any(artifact.step_id is not None for artifact in self.artifacts):
            raise ValueError("单目标 case 的 Artifact 不得设置 step_id。")
        elif {field for _step_id, field in fields} & set(self.inputs):
            raise ValueError("Artifact field 不能同时出现在 inputs。")
        return self


class BenchmarkManifest(NodeLabModel):
    """Node Lab 版本化 AI-off suite manifest."""

    schema_version: Literal["node_lab_benchmark_manifest_v1"]
    pipeline_id: Identifier | None = None
    suite_id: Identifier
    repetitions: int = Field(default=1, ge=1, le=_MAX_REPETITIONS)
    warmups: int = Field(default=0, ge=0, le=_MAX_WARMUPS)
    resource_lifecycle: Literal["cold_per_attempt", "warm_per_suite"] = (
        "cold_per_attempt"
    )
    renderer_lifecycle: Literal["cold_per_attempt", "warm_per_suite"] | None = None
    cases: list[BenchmarkCase] = Field(min_length=1, max_length=_MAX_CASES)

    @model_validator(mode="before")
    @classmethod
    def migrate_renderer_lifecycle(cls, value: Any) -> Any:
        """只读兼容旧 manifest，并收敛到通用资源生命周期字段."""
        if not isinstance(value, Mapping):
            return value
        normalized = dict(value)
        legacy = normalized.get("renderer_lifecycle")
        current = normalized.get("resource_lifecycle")
        if legacy is not None and current is not None and legacy != current:
            raise ValueError("resource_lifecycle 与旧 renderer_lifecycle 不一致。")
        if legacy is not None:
            normalized["resource_lifecycle"] = legacy
        return normalized

    @model_validator(mode="after")
    def validate_case_ids(self) -> Self:
        """Case id 必须在 suite 内唯一."""
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark case_id 不能重复。")
        profiles = {case.profile for case in self.cases}
        if self.resource_lifecycle == "warm_per_suite":
            if self.warmups < 1:
                raise ValueError(
                    "warm_per_suite 必须至少执行一次 warmup。"
                )
            if len(profiles) != 1:
                raise ValueError(
                    "warm_per_suite 必须使用单一独立 profile。"
                )
            if any(case.target_type not in {"capability", "scenario"} for case in self.cases):
                raise ValueError("warm_per_suite 当前只支持 capability/scenario case。")
        return self


@dataclass(frozen=True)
class ValidatedBenchmarkSuite:
    """已校验路径、hash 和 capability id 的冻结 suite."""

    manifest: BenchmarkManifest
    manifest_path: Path
    manifest_sha256: str
    artifact_paths: dict[tuple[str, str | None, str], Path]

    def summary(self) -> dict[str, Any]:
        """返回不暴露绝对路径的校验摘要."""
        return {
            "schema_version": self.manifest.schema_version,
            "pipeline_id": self.manifest.pipeline_id,
            "suite_id": self.manifest.suite_id,
            "manifest_sha256": self.manifest_sha256,
            "case_count": len(self.manifest.cases),
            "repetitions": self.manifest.repetitions,
            "warmups": self.manifest.warmups,
            "resource_lifecycle": self.manifest.resource_lifecycle,
            "renderer_lifecycle": self.manifest.resource_lifecycle,
            "profiles": sorted({case.profile for case in self.manifest.cases}),
        }


def _stable_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _safe_child(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute():
        raise ValueError("benchmark Artifact 必须使用相对路径。")
    candidate = (root / relative).resolve(strict=False)
    if not candidate.is_relative_to(root.resolve().parent.parent):
        raise ValueError("benchmark Artifact 路径越过 benchmarks 根目录。")
    if not candidate.is_file():
        raise ValueError("benchmark Artifact 文件不存在。")
    return candidate


def load_benchmark_manifest(
    manifest_path: str | Path,
    *,
    capability_ids: set[str],
    node_ids: set[str],
) -> ValidatedBenchmarkSuite:
    """加载 manifest，并在运行前验证文件 hash 及两类 target allowlist."""
    path = Path(manifest_path).resolve()
    raw_bytes = path.read_bytes()
    raw = yaml.safe_load(raw_bytes)
    manifest = BenchmarkManifest.model_validate(raw)
    allowed_node_ids = node_ids
    artifact_paths: dict[tuple[str, str | None, str], Path] = {}
    for case in manifest.cases:
        case_capability_ids: list[str] = []
        case_node_ids: list[str] = []
        if case.target_type == "scenario":
            case_capability_ids = [
                step.capability_id
                for step in case.steps
                if isinstance(step, BenchmarkScenarioStep)
            ]
        elif case.target_type == "pipeline":
            case_node_ids = [
                step.node_id
                for step in case.steps
                if isinstance(step, BenchmarkPipelineStep)
            ]
        elif case.target_type == "capability":
            if case.capability_id is None:
                raise ValueError("capability case 缺少 capability_id。")
            case_capability_ids = [case.capability_id]
        else:
            if case.node_id is None:
                raise ValueError("node case 缺少 node_id。")
            case_node_ids = [case.node_id]
        for capability_id in case_capability_ids:
            if capability_id not in capability_ids:
                raise ValueError(f"未知 capability_id：{capability_id}。")
        for node_id in case_node_ids:
            if node_id not in allowed_node_ids:
                raise ValueError(f"未知 node_id：{node_id}。")
        for artifact in case.artifacts:
            resolved = _safe_child(path.parent, artifact.path)
            data = resolved.read_bytes()
            if sha256(data).hexdigest() != artifact.sha256:
                raise ValueError(
                    f"{case.case_id}.{artifact.field} 的 SHA-256 与 manifest 不一致。"
                )
            artifact_paths[(case.case_id, artifact.step_id, artifact.field)] = resolved
    return ValidatedBenchmarkSuite(
        manifest=manifest,
        manifest_path=path,
        manifest_sha256=sha256(raw_bytes).hexdigest(),
        artifact_paths=artifact_paths,
    )


def source_environment(
    *,
    workspace_root: str | Path | None = None,
    extra_source_paths: Iterable[Path] = (),
    dependency_names: Iterable[str] = ("jsonschema", "pydantic", "PyYAML"),
) -> tuple[dict[str, Any], str, str]:
    """冻结影响 Node Lab deterministic 行为的源码与执行环境摘要."""
    root = Path(workspace_root or Path.cwd()).resolve()
    core_root = Path(__file__).resolve().parent
    source_paths = sorted(
        {
            *core_root.glob("*.py"),
            *(
                path if path.is_absolute() else root / path
                for path in extra_source_paths
            ),
        }
    )

    def source_key(path: Path) -> str:
        resolved = path.resolve()
        if resolved.is_relative_to(root):
            return resolved.relative_to(root).as_posix()
        if resolved.is_relative_to(core_root):
            return f"nodelab/{resolved.relative_to(core_root).as_posix()}"
        opaque_parent = sha256(str(resolved.parent).encode("utf-8")).hexdigest()[:12]
        return f"external/{opaque_parent}/{resolved.name}"

    source_hashes = {
        source_key(path): sha256(path.read_bytes()).hexdigest()
        for path in source_paths
        if path.is_file()
    }
    source_fingerprint = sha256(_stable_json_bytes(source_hashes)).hexdigest()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    environment = {
        "schema_version": "node_lab_environment_v1",
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "dependency_versions": _dependency_versions(dependency_names),
        "worktree_dirty": bool(status.stdout.strip())
        if status.returncode == 0
        else None,
        "source_hashes": source_hashes,
    }
    environment_fingerprint = sha256(_stable_json_bytes(environment)).hexdigest()
    return environment, source_fingerprint, environment_fingerprint


def _dependency_versions(
    dependency_names: Iterable[str],
) -> dict[str, str | None]:
    """记录调用方声明的依赖版本，不内置领域包清单."""
    versions: dict[str, str | None] = {}
    for package in sorted(set(dependency_names)):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    return versions


def _copy_attempt_artifact(
    *,
    application: Any,
    store: AtomicFileStore,
    lab_run_id: str,
    descriptor: dict[str, Any],
    attempt_root: str,
    role: Literal["input", "output"],
    field: str | None,
    step_id: str | None,
) -> dict[str, Any]:
    """把 Lab Artifact 复制进 suite，使 attempt 证据可独立审计."""
    artifact_id = str(descriptor["artifact_id"])
    persisted, data = application.read_artifact(lab_run_id, artifact_id)
    persisted_dict = persisted.to_dict()
    if persisted_dict != descriptor or sha256(data).hexdigest() != descriptor["sha256"]:
        raise ValueError("复制 benchmark Artifact 时完整性校验失败。")
    relative_path = f"{attempt_root}/artifacts/{artifact_id}/payload"
    store.write_bytes(
        relative_path,
        data,
        content_type=str(descriptor["content_type"]),
    )
    return {
        "role": role,
        "field": field,
        "step_id": step_id,
        "relative_path": relative_path,
        "descriptor": descriptor,
    }


def _safe_suite_id(value: str) -> str:
    if not _SUITE_ID_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError("suite_run_id 包含非法字符。")
    return value


def _json_pointer(value: dict[str, Any], pointer: str) -> Any:
    if not pointer.startswith("/"):
        raise ValueError("benchmark JSON pointer 必须以 / 开头。")
    current: Any = value
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            raise KeyError(pointer)
    return current


def _correctness(
    response: dict[str, Any],
    expect: BenchmarkExpectation,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if response.get("execution_status") != "completed":
        failures.append("execution_status_not_completed")
    if response.get("outcome") != expect.outcome:
        failures.append("outcome_mismatch")
    for pointer, expected in expect.values.items():
        try:
            actual = _json_pointer(response, pointer)
        except KeyError:
            failures.append(f"missing:{pointer}")
            continue
        if actual != expected:
            failures.append(f"mismatch:{pointer}")
    return not failures, failures


def _percentiles(durations: list[float]) -> dict[str, float | None]:
    if not durations:
        return {"p50": None, "p95": None, "max": None}
    return {
        "p50": round(float(statistics.median(durations)), 6),
        "p95": (
            round(
                float(statistics.quantiles(durations, n=100, method="inclusive")[94]), 6
            )
            if len(durations) >= 20
            else None
        ),
        "max": round(max(durations), 6),
    }


@dataclass
class _AttemptContext:
    """在取消发生时也能持久化的 attempt 现场."""

    lab_run_id: str
    input_artifacts: list[dict[str, Any]]
    artifact_evidence: list[dict[str, Any]]
    responses: list[dict[str, Any]]


async def _execute_for_attempt(
    application: Any,
    request: CapabilityExecutionRequest,
    *,
    resource: Any | None,
    warm_resource_calls: list[int],
) -> Any:
    """按 suite 生命周期选择 cold 或 warm Application API 入口."""
    if resource is None:
        return await application.execute_capability(request)
    usage_count = 1 if warm_resource_calls[0] == 0 else 0
    response = await application.execute_capability_with_resource(
        request,
        resource=resource,
        resource_usage_count=usage_count,
    )
    warm_resource_calls[0] += 1
    return response


async def _execute_node_for_attempt(
    application: Any,
    request: StepExecutionRequest,
) -> Any:
    """Node benchmark 只走正式 Application step API，保持 AI-off."""
    return await application.execute_step(request)


def _upload_attempt_artifact(
    *,
    application: Any,
    suite: ValidatedBenchmarkSuite,
    store: AtomicFileStore,
    context: _AttemptContext,
    case: BenchmarkCase,
    artifact_spec: BenchmarkArtifactInput,
    attempt_root: str,
) -> str:
    """上传 manifest Artifact，并同步复制到 suite attempt 目录."""
    source_path = suite.artifact_paths[
        (case.case_id, artifact_spec.step_id, artifact_spec.field)
    ]
    descriptor = application.upload_artifact(
        lab_run_id=context.lab_run_id,
        kind=artifact_spec.kind,
        content_type=artifact_spec.content_type,
        data=source_path.read_bytes(),
    )
    descriptor_dict = descriptor.to_dict()
    context.input_artifacts.append(descriptor_dict)
    context.artifact_evidence.append(
        _copy_attempt_artifact(
            application=application,
            store=store,
            lab_run_id=context.lab_run_id,
            descriptor=descriptor_dict,
            attempt_root=attempt_root,
            role="input",
            field=artifact_spec.field,
            step_id=artifact_spec.step_id,
        )
    )
    return str(descriptor.artifact_id)


def _copy_response_artifacts(
    *,
    application: Any,
    store: AtomicFileStore,
    context: _AttemptContext,
    response: Any,
    attempt_root: str,
    step_id: str | None,
) -> None:
    """把 capability 输出 Artifact 纳入自包含 attempt 证据."""
    for descriptor in response.artifacts:
        context.artifact_evidence.append(
            _copy_attempt_artifact(
                application=application,
                store=store,
                lab_run_id=context.lab_run_id,
                descriptor=descriptor.to_dict(),
                attempt_root=attempt_root,
                role="output",
                field=None,
                step_id=step_id,
            )
        )


async def _execute_attempt(
    *,
    application: Any,
    suite: ValidatedBenchmarkSuite,
    store: AtomicFileStore,
    context: _AttemptContext,
    case: BenchmarkCase,
    attempt_root: str,
    resource: Any | None,
    warm_resource_calls: list[int],
) -> tuple[dict[str, Any], bool, list[str], float]:
    """按显式 target_type 执行单目标或多步骤目标，并返回最终响应."""
    started = time.perf_counter()
    failures: list[str] = []
    if case.target_type in {"capability", "node"}:
        if case.expect is None:
            raise ValueError("单目标 case 缺少 expect。")
        if case.capability_id is None and case.target_type == "capability":
            raise ValueError("capability case 缺少 capability_id。")
        if case.node_id is None and case.target_type == "node":
            raise ValueError("node case 缺少 node_id。")
        inputs = dict(case.inputs)
        for artifact_spec in case.artifacts:
            inputs[artifact_spec.field] = _upload_attempt_artifact(
                application=application,
                suite=suite,
                store=store,
                context=context,
                case=case,
                artifact_spec=artifact_spec,
                attempt_root=attempt_root,
            )
        if case.target_type == "capability":
            assert case.capability_id is not None
            response = await _execute_for_attempt(
                application,
                CapabilityExecutionRequest(
                    lab_run_id=context.lab_run_id,
                    capability_id=case.capability_id,
                    inputs=inputs,
                ),
                resource=resource,
                warm_resource_calls=warm_resource_calls,
            )
        else:
            assert case.node_id is not None
            response = await _execute_node_for_attempt(
                application,
                StepExecutionRequest(
                    lab_run_id=context.lab_run_id,
                    node_id=case.node_id,
                    execution_mode="deterministic",
                    inputs=inputs,
                ),
            )
        response_dict = response.to_dict()
        context.responses.append(
            {
                "step_id": None,
                "target_type": case.target_type,
                "capability_id": case.capability_id,
                "node_id": case.node_id,
                "response": response_dict,
            }
        )
        _copy_response_artifacts(
            application=application,
            store=store,
            context=context,
            response=response,
            attempt_root=attempt_root,
            step_id=None,
        )
        passed, failures = _correctness(response_dict, case.expect)
        return (
            response_dict,
            passed,
            failures,
            (time.perf_counter() - started) * 1000.0,
        )

    responses_by_step: dict[str, dict[str, Any]] = {}
    actual_step_ids: dict[str, str] = {}
    final_response: dict[str, Any] | None = None
    for step in case.steps:
        inputs = dict(step.inputs)
        for artifact_spec in case.artifacts:
            if artifact_spec.step_id == step.step_id:
                inputs[artifact_spec.field] = _upload_attempt_artifact(
                    application=application,
                    suite=suite,
                    store=store,
                    context=context,
                    case=case,
                    artifact_spec=artifact_spec,
                    attempt_root=attempt_root,
                )
        for binding in step.bindings:
            try:
                source_response = responses_by_step[binding.source_step_id]
                inputs[binding.field] = _json_pointer(
                    source_response,
                    binding.json_pointer,
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"{case.case_id}.{step.step_id} 的 binding 无法解析。"
                ) from exc
        if isinstance(step, BenchmarkScenarioStep):
            response = await _execute_for_attempt(
                application,
                CapabilityExecutionRequest(
                    lab_run_id=context.lab_run_id,
                    capability_id=step.capability_id,
                    inputs=inputs,
                ),
                resource=resource,
                warm_resource_calls=warm_resource_calls,
            )
            response_target = {
                "target_type": "capability",
                "capability_id": step.capability_id,
                "node_id": None,
                "manifest_base_step_id": None,
            }
        else:
            actual_base_step_id = (
                actual_step_ids[step.base_step_id]
                if step.base_step_id is not None
                else None
            )
            response = await _execute_node_for_attempt(
                application,
                StepExecutionRequest(
                    lab_run_id=context.lab_run_id,
                    node_id=step.node_id,
                    execution_mode="deterministic",
                    base_step_id=actual_base_step_id,
                    inputs=inputs,
                ),
            )
            actual_step_ids[step.step_id] = str(response.step_id)
            response_target = {
                "target_type": "node",
                "capability_id": None,
                "node_id": step.node_id,
                "manifest_base_step_id": step.base_step_id,
            }
        response_dict = response.to_dict()
        final_response = response_dict
        responses_by_step[step.step_id] = response_dict
        context.responses.append(
            {
                "step_id": step.step_id,
                **response_target,
                "response": response_dict,
            }
        )
        _copy_response_artifacts(
            application=application,
            store=store,
            context=context,
            response=response,
            attempt_root=attempt_root,
            step_id=step.step_id,
        )
        step_passed, step_failures = _correctness(response_dict, step.expect)
        failures.extend(f"{step.step_id}:{failure}" for failure in step_failures)
        if not step_passed:
            break
    if final_response is None:
        raise ValueError("多步骤 target 没有产生任何 step 响应。")
    return (
        final_response,
        not failures and len(context.responses) == len(case.steps),
        failures,
        (time.perf_counter() - started) * 1000.0,
    )


def _write_interruption(
    *,
    store: AtomicFileStore,
    context: _AttemptContext,
    config_sha256: str,
    run_id: str,
    case: BenchmarkCase,
    attempt_id: str,
    attempt_root: str,
    warmup: bool,
    error_type: str,
) -> None:
    """保留中断现场但不占用 execution.json，使恢复能重跑该 attempt."""
    interruption_root = store.path_for(f"{attempt_root}/interruptions")
    existing = (
        list(interruption_root.glob("interruption-*.json"))
        if interruption_root.exists()
        else []
    )
    interruption_id = f"interruption-{len(existing) + 1:03d}"
    store.write_json(
        f"{attempt_root}/interruptions/{interruption_id}.json",
        {
            "schema_version": "node_lab_benchmark_interruption_v1",
            "suite_run_id": run_id,
            "config_sha256": config_sha256,
            "case_id": case.case_id,
            "target_type": case.target_type,
            "profile": case.profile,
            "attempt_id": attempt_id,
            "interruption_id": interruption_id,
            "warmup": warmup,
            "error_code": "execution_interrupted",
            "error_type": error_type,
            "lab_run_id": context.lab_run_id,
            "input_artifacts": context.input_artifacts,
            "artifact_evidence": context.artifact_evidence,
            "responses": context.responses,
        },
    )


def _planned_attempts(
    suite: ValidatedBenchmarkSuite,
) -> list[tuple[BenchmarkCase, str, bool]]:
    """生成冻结的 warmup/measurement attempt 列表."""
    planned: list[tuple[BenchmarkCase, str, bool]] = []
    for case in suite.manifest.cases:
        total = suite.manifest.warmups + suite.manifest.repetitions
        for index in range(total):
            warmup = index < suite.manifest.warmups
            ordinal = index + 1 if warmup else index - suite.manifest.warmups + 1
            attempt_id = f"warmup-{ordinal:03d}" if warmup else f"attempt-{ordinal:03d}"
            planned.append((case, attempt_id, warmup))
    return planned


async def run_benchmark_suite(
    application: Any,
    suite: ValidatedBenchmarkSuite,
    *,
    output_root: str | Path,
    suite_run_id: str | None = None,
) -> dict[str, Any]:
    """运行 AI-off suite，原子保存逐 attempt 证据和聚合报告."""
    run_id = _safe_suite_id(suite_run_id or f"node-lab-{uuid4().hex[:12]}")
    root = Path(output_root).resolve() / run_id
    store = AtomicFileStore(root)
    environment, source_fingerprint, environment_fingerprint = source_environment(
        workspace_root=application.benchmark_workspace_root,
        extra_source_paths=application.benchmark_source_paths(),
        dependency_names=application.benchmark_dependency_names,
    )
    config_base = {
        "schema_version": "node_lab_benchmark_config_v1",
        "suite_run_id": run_id,
        "suite": suite.summary(),
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
    }
    config_sha256 = sha256(_stable_json_bytes(config_base)).hexdigest()
    config = {**config_base, "config_sha256": config_sha256}
    config_path = root / "config.json"
    if config_path.is_file():
        existing = json.loads(config_path.read_bytes())
        if existing != config:
            raise ValueError("suite_run_id 已存在且 config hash 不一致，禁止恢复。")
    else:
        store.write_json("config.json", config)
        store.write_bytes(
            "manifest.snapshot.yaml",
            suite.manifest_path.read_bytes(),
            content_type="application/yaml",
        )
        store.write_json("environment.json", environment)

    planned = _planned_attempts(suite)
    report_path = store.path_for("report.json")
    if report_path.is_file() and all(
        store.path_for(
            f"cases/{case.case_id}/attempts/{attempt_id}/execution.json"
        ).is_file()
        for case, attempt_id, _warmup in planned
    ):
        return ensure_json_object(json.loads(report_path.read_bytes()))

    async with AsyncExitStack() as stack:
        resource = None
        warm_resource_calls = [0]
        if suite.manifest.resource_lifecycle == "warm_per_suite":
            resource = await stack.enter_async_context(
                application.benchmark_resource_session()
            )
            completed_warmups = [
                (case, attempt_id)
                for case, attempt_id, warmup in planned
                if warmup
                and store.path_for(
                    f"cases/{case.case_id}/attempts/{attempt_id}/execution.json"
                ).is_file()
            ]
            if completed_warmups:
                case = completed_warmups[0][0]
                existing_rewarmups = list(
                    (root / f"cases/{case.case_id}/attempts").glob(
                        "rewarmup-*/execution.json"
                    )
                )
                rewarm_id = f"rewarmup-{len(existing_rewarmups) + 1:03d}"
                planned.insert(0, (case, rewarm_id, True))

        for case, attempt_id, warmup in planned:
            attempt_root = f"cases/{case.case_id}/attempts/{attempt_id}"
            relative_path = f"{attempt_root}/execution.json"
            if store.path_for(relative_path).is_file():
                continue
            lab_run = application.create_run(LabRunCreateRequest())
            context = _AttemptContext(
                lab_run_id=lab_run.lab_run_id,
                input_artifacts=[],
                artifact_evidence=[],
                responses=[],
            )
            try:
                response_dict, passed, failures, duration_ms = await _execute_attempt(
                    application=application,
                    suite=suite,
                    store=store,
                    context=context,
                    case=case,
                    attempt_root=attempt_root,
                    resource=resource,
                    warm_resource_calls=warm_resource_calls,
                )
            except (asyncio.CancelledError, KeyboardInterrupt) as exc:
                _write_interruption(
                    store=store,
                    context=context,
                    config_sha256=config_sha256,
                    run_id=run_id,
                    case=case,
                    attempt_id=attempt_id,
                    attempt_root=attempt_root,
                    warmup=warmup,
                    error_type=type(exc).__name__,
                )
                raise
            store.write_json(
                relative_path,
                {
                    "schema_version": "node_lab_benchmark_attempt_v1",
                    "attempt_status": "completed",
                    "suite_run_id": run_id,
                    "config_sha256": config_sha256,
                    "case_id": case.case_id,
                    "target_type": case.target_type,
                    "capability_id": case.capability_id,
                    "node_id": case.node_id,
                    "profile": case.profile,
                    "resource_lifecycle": suite.manifest.resource_lifecycle,
                    "renderer_lifecycle": suite.manifest.resource_lifecycle,
                    "attempt_id": attempt_id,
                    "warmup": warmup,
                    "duration_ms": duration_ms,
                    "input_artifacts": context.input_artifacts,
                    "artifact_evidence": context.artifact_evidence,
                    "correctness_passed": passed,
                    "correctness_failures": failures,
                    "responses": context.responses,
                    "response": response_dict,
                },
            )

    attempts: list[dict[str, Any]] = []
    interruptions: list[dict[str, Any]] = []
    for case in suite.manifest.cases:
        for path in sorted(
            (root / f"cases/{case.case_id}/attempts").glob("*/execution.json")
        ):
            evidence = json.loads(path.read_bytes())
            if not evidence.get("warmup"):
                attempts.append(evidence)
        for path in sorted(
            (root / f"cases/{case.case_id}/attempts").glob(
                "*/interruptions/interruption-*.json"
            )
        ):
            evidence = json.loads(path.read_bytes())
            if not evidence.get("warmup"):
                interruptions.append(evidence)
    durations = [float(item["duration_ms"]) for item in attempts]
    failed_completed = [
        f"{item['case_id']}:{item['attempt_id']}"
        for item in attempts
        if not item.get("correctness_passed")
    ]
    interrupted = [
        f"{item['case_id']}:{item['attempt_id']}:{item['interruption_id']}"
        for item in interruptions
    ]
    failed = [*failed_completed, *interrupted]
    total_attempt_count = len(attempts) + len(interruptions)
    passed_attempt_count = sum(1 for item in attempts if item.get("correctness_passed"))
    report = {
        "schema_version": "node_lab_benchmark_report_v1",
        "suite_run_id": run_id,
        "pipeline_id": suite.manifest.pipeline_id,
        "suite_id": suite.manifest.suite_id,
        "manifest_sha256": suite.manifest_sha256,
        "config_sha256": config_sha256,
        "source_fingerprint": source_fingerprint,
        "environment_fingerprint": environment_fingerprint,
        "attempt_count": total_attempt_count,
        "completed_attempt_count": len(attempts),
        "interrupted_attempt_count": len(interruptions),
        "passed_attempt_count": passed_attempt_count,
        "failed_attempt_count": len(failed),
        "correctness_rate": (
            passed_attempt_count / total_attempt_count if total_attempt_count else 0.0
        ),
        "duration_ms": _percentiles(durations),
        "failed_attempts": failed,
        "profiles": sorted(
            {str(item["profile"]) for item in [*attempts, *interruptions]}
        ),
        "resource_lifecycle": suite.manifest.resource_lifecycle,
        "renderer_lifecycle": suite.manifest.resource_lifecycle,
    }
    store.write_json("report.json", report)
    store.write_text("report.md", _report_markdown(report))
    return report


def _report_markdown(report: dict[str, Any]) -> str:
    """生成可人工阅读但不作为机器真相源的报告."""
    duration = dict(report["duration_ms"])
    failed = list(report["failed_attempts"])
    lines = [
        f"# Node Lab Benchmark · {report['suite_run_id']}",
        "",
        f"- suite: `{report['suite_id']}`",
        f"- attempts: `{report['passed_attempt_count']}/{report['attempt_count']}` passed",
        f"- completed/interrupted: `{report['completed_attempt_count']}` / `{report['interrupted_attempt_count']}`",
        f"- correctness rate: `{report['correctness_rate']:.3f}`",
        f"- resource lifecycle: `{report['resource_lifecycle']}`",
        f"- duration p50/p95/max ms: `{duration['p50']}` / `{duration['p95']}` / `{duration['max']}`",
        f"- source fingerprint: `{report['source_fingerprint']}`",
        f"- environment fingerprint: `{report['environment_fingerprint']}`",
        "",
        "## Failed attempts",
        "",
    ]
    lines.extend(f"- `{item}`" for item in failed)
    if not failed:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def compare_benchmark_reports(
    baseline_path: str | Path,
    candidate_path: str | Path,
) -> dict[str, Any]:
    """按独立报告 SHA 比较；环境或源码不同则明确不可严格比较."""
    baseline_bytes = Path(baseline_path).read_bytes()
    candidate_bytes = Path(candidate_path).read_bytes()
    baseline = ensure_json_object(json.loads(baseline_bytes))
    candidate = ensure_json_object(json.loads(candidate_bytes))
    comparable = (
        baseline.get("source_fingerprint") == candidate.get("source_fingerprint")
        and baseline.get("environment_fingerprint")
        == candidate.get("environment_fingerprint")
        and baseline.get("pipeline_id") == candidate.get("pipeline_id")
        and baseline.get("suite_id") == candidate.get("suite_id")
    )
    result: dict[str, Any] = {
        "schema_version": "node_lab_benchmark_comparison_v1",
        "status": "comparable" if comparable else "non_comparable",
        "baseline_report_sha256": sha256(baseline_bytes).hexdigest(),
        "candidate_report_sha256": sha256(candidate_bytes).hexdigest(),
        "baseline_suite_run_id": baseline.get("suite_run_id"),
        "candidate_suite_run_id": candidate.get("suite_run_id"),
    }
    if comparable:
        result["correctness_rate_delta"] = float(candidate["correctness_rate"]) - float(
            baseline["correctness_rate"]
        )
        baseline_p50 = dict(baseline["duration_ms"]).get("p50")
        candidate_p50 = dict(candidate["duration_ms"]).get("p50")
        result["duration_p50_delta_ms"] = (
            None
            if baseline_p50 is None or candidate_p50 is None
            else float(candidate_p50) - float(baseline_p50)
        )
    return result
