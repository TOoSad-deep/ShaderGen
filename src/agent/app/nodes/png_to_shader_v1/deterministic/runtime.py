"""PNG 转 Shader V1 确定性 Node 共用的运行时协议与辅助函数."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any, Protocol

from agent.app.contracts.png_to_shader_v1 import (
    VisualAnalysis,
)
from shaderforge.analysis import (
    RegionOfInterest,
    TargetMeasurements,
)
from shaderforge.contracts import (
    AcceptancePolicy,
    BudgetPolicy,
)
from shaderforge.evaluation import (
    CandidateRecord,
    ScoreBreakdownV1,
)
from shaderforge.rendering import (
    RenderResult,
)
from shaderforge.store import LocalArtifactStore, RunArtifactStore
from shaderforge.validation import (
    ShaderRepairResult,
    ValidationResult,
)

Clock = Callable[[], float]
RendererFactory = Callable[[int], "ShaderRenderer"]
RunNode = Callable[[Mapping[str, Any]], Awaitable[dict[str, Any]]]
logger = logging.getLogger("agent.png_to_shader")
MAX_FINALIZE_RESERVE_SECONDS = 30.0
FINALIZE_RESERVE_RATIO = 0.10
RENDERER_CLOSE_TIMEOUT_SECONDS = 3.0


class NodeEvidenceError(RuntimeError):
    """生产 Node 发现输入 Artifact/摘要之间的证据绑定不一致."""


class ShaderRenderer(Protocol):
    """M3 编排依赖的最小异步渲染接口."""

    async def render(
        self, fragment_source: str, width: int, height: int
    ) -> RenderResult:
        """编译并渲染一个 Fragment Shader."""
        ...

    async def close(self) -> None:
        """释放当前 run 的渲染资源."""
        ...


class RenderEvaluator(Protocol):
    """M3 编排依赖的确定性、无外部副作用评分接口."""

    def __call__(
        self,
        reference_image: bytes,
        candidate_image: bytes,
        *,
        measurements: TargetMeasurements,
    ) -> ScoreBreakdownV1:
        """返回候选相对参考图的评分向量."""
        ...


class RunRendererRegistry:
    """按 project/run 隔离并复用 Renderer，finalize 时释放."""

    def __init__(self, factory: RendererFactory) -> None:
        """保存工厂并初始化空的 run 级资源表."""
        self._factory = factory
        self._renderers: dict[tuple[str, str], ShaderRenderer] = {}
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    async def render(
        self,
        key: tuple[str, str],
        *,
        replay_on_worker_failure: int,
        fragment_source: str,
        width: int,
        height: int,
    ) -> RenderResult:
        """在当前 run 的串行锁内复用 Renderer."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            renderer = self._renderers.get(key)
            if renderer is None:
                renderer = self._factory(replay_on_worker_failure)
                self._renderers[key] = renderer
            return await renderer.render(fragment_source, width, height)

    async def close(self, key: tuple[str, str]) -> None:
        """幂等移除并关闭一个 run 的 Renderer."""
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            renderer = self._renderers.get(key)
            if renderer is not None:
                await renderer.close()
                # 只有关闭成功后才能移除；超时或异常时保留引用，供外层重试。
                self._renderers.pop(key, None)
        self._locks.pop(key, None)


def _run_key(state: Mapping[str, Any]) -> tuple[str, str]:
    return str(state["project_id"]), str(state["run_id"])


def _run_store(
    artifact_store: LocalArtifactStore,
    state: Mapping[str, Any],
) -> RunArtifactStore:
    return artifact_store.start_run(str(state["project_id"]), str(state["run_id"]))


def _budget(state: Mapping[str, Any]) -> BudgetPolicy:
    value = state["budget_policy"]
    if isinstance(value, BudgetPolicy):
        return value
    return BudgetPolicy(**dict(value))


def _acceptance(state: Mapping[str, Any]) -> AcceptancePolicy:
    value = state["acceptance_policy"]
    if isinstance(value, AcceptancePolicy):
        return value
    return AcceptancePolicy(**dict(value))


def _record(value: Any) -> CandidateRecord:
    if isinstance(value, CandidateRecord):
        return value
    return CandidateRecord.from_dict(dict(value))


def _replace_record(
    records: tuple[Any, ...],
    replacement: CandidateRecord,
) -> tuple[CandidateRecord, ...]:
    normalized = tuple(_record(item) for item in records)
    return tuple(
        replacement if item.candidate_id == replacement.candidate_id else item
        for item in normalized
    )


def _read_json(store: RunArtifactStore, relative_path: str) -> dict[str, Any]:
    value = json.loads(store.read_bytes(relative_path))
    if not isinstance(value, dict):
        raise ValueError("Artifact JSON 根节点必须是 object。")
    return value


def _candidate_manifest_path(candidate_id: str) -> str:
    return f"candidates/{candidate_id}/manifest.json"


def _write_candidate_manifest(
    store: RunArtifactStore,
    record: CandidateRecord,
) -> None:
    store.write_json(_candidate_manifest_path(record.candidate_id), record.to_dict())


def _wall_remaining(state: Mapping[str, Any], clock: Clock) -> float:
    return _budget(state).max_wall_time_seconds - (clock() - float(state["started_at"]))


def _elapsed_seconds(state: Mapping[str, Any], clock: Clock) -> float:
    return max(0.0, clock() - float(state["started_at"]))


def _finalize_reserve_seconds(state: Mapping[str, Any]) -> float:
    budget = _budget(state)
    return min(
        MAX_FINALIZE_RESERVE_SECONDS,
        budget.max_wall_time_seconds * FINALIZE_RESERVE_RATIO,
    )


def _work_seconds_before_finalize(
    state: Mapping[str, Any],
    clock: Clock,
) -> float:
    return _wall_remaining(state, clock) - _finalize_reserve_seconds(state)


def _evaluation_measurements(
    state: Mapping[str, Any],
    measurements: TargetMeasurements,
) -> TargetMeasurements:
    """把模型确认的语义 ROI 合并进确定性评分目标.

    基础测量 ROI 始终保留；VisualAnalysis 只能追加不同 id 的区域，不能覆盖
    `subject`、`background_border` 等确定性证据。这样 Critic 和 Selector 能看到
    高光、阴影、颜色与保护区残差，同时仍以同一张规范化参考图为事实来源。
    """
    raw_analysis = state.get("visual_analysis")
    if raw_analysis is None:
        return measurements
    analysis = (
        raw_analysis
        if isinstance(raw_analysis, VisualAnalysis)
        else VisualAnalysis.model_validate(raw_analysis)
    )
    region_ids = {region.region_id for region in measurements.roi_candidates}
    semantic_regions: list[RegionOfInterest] = []
    for region in analysis.regions_of_interest:
        if region.region_id in region_ids:
            continue
        region_ids.add(region.region_id)
        semantic_regions.append(
            RegionOfInterest(
                region_id=region.region_id,
                bbox_uv=region.bbox_uv,
                purpose=region.purpose,
                confidence=region.confidence,
            )
        )
    if not semantic_regions:
        return measurements
    return replace(
        measurements,
        roi_candidates=(*measurements.roi_candidates, *semantic_regions),
    )


def _validation_diagnostics(validation: ValidationResult) -> dict[str, Any]:
    violations = [
        {
            "code": item.code,
            "severity": item.severity,
            "line": item.line,
        }
        for item in validation.violations
    ]
    return {
        "violation_codes": list(dict.fromkeys(item["code"] for item in violations)),
        "violations": violations,
    }


def _persist_deterministic_shader_repair(
    store: RunArtifactStore,
    state: Mapping[str, Any],
    record: CandidateRecord,
    repair: ShaderRepairResult,
) -> tuple[CandidateRecord, dict[str, Any], dict[str, Any], dict[str, Any]]:
    """写入新的修复产物后切换候选绑定，并返回安全审计摘要."""
    original_sha256 = record.glsl_sha256
    repair_prefix = f"candidates/{record.candidate_id}/deterministic-repair"
    glsl_ref = store.write_text(
        f"{repair_prefix}/shader.frag",
        repair.source,
        content_type="text/x-glsl; charset=utf-8",
    )
    author = dict(state["author_result"])
    author["glsl"] = repair.source
    provenance = dict(state["candidate_provenance"])
    provenance["glsl_sha256"] = glsl_ref.sha256
    author_ref = store.write_json(f"{repair_prefix}/author.json", author)
    provenance_ref = store.write_json(f"{repair_prefix}/provenance.json", provenance)
    repaired_record = replace(
        record,
        glsl_sha256=glsl_ref.sha256,
        glsl_ref=glsl_ref.relative_path,
        author_ref=author_ref.relative_path,
        provenance_ref=provenance_ref.relative_path,
    )
    _write_candidate_manifest(store, repaired_record)
    audit = {
        **repair.safe_audit_dict(),
        "before_glsl_sha256": original_sha256,
        "after_glsl_sha256": glsl_ref.sha256,
        "repaired_glsl_ref": glsl_ref.relative_path,
    }
    return repaired_record, author, provenance, audit
