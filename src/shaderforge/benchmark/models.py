"""PNG-to-Shader benchmark 的不可变数据契约."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

GateStatus = Literal["passed", "failed", "pending_human_review"]


@dataclass(frozen=True)
class KeyRoiSpec:
    """单个 benchmark 关键区域定义."""

    region_id: str
    bbox_uv: tuple[float, float, float, float]
    purpose: str


@dataclass(frozen=True)
class BenchmarkCaseSpec:
    """一个固定 PNG benchmark 样例."""

    case_id: str
    level: str
    image_path: Path
    image_sha256: str
    resolution: tuple[int, int]
    expected_foreground_bbox_uv: tuple[float, float, float, float]
    max_bbox_error_uv: float
    key_rois: tuple[KeyRoiSpec, ...]


@dataclass(frozen=True)
class BenchmarkSuiteSpec:
    """已校验的 benchmark manifest."""

    schema_version: int
    suite_id: str
    contract_id: str
    manifest_path: Path
    manifest_sha256: str
    cases: tuple[BenchmarkCaseSpec, ...]


@dataclass(frozen=True)
class QualityGatePolicy:
    """依据固定 benchmark 校准的 M5 发布门禁."""

    schema_version: int
    policy_id: str
    suite_id: str
    required_case_count: int
    min_ai_off_compile_rate: float
    min_ai_off_static_pass_rate: float
    min_final_compile_rate: float
    min_final_static_pass_rate: float
    min_improvement_rate: float
    min_total_improvement: float
    max_final_current_best_mismatches: int
    max_non_monotonic_runs: int
    min_traceability_rate: float
    pink_gel_max_bbox_error_uv: float
    pink_gel_max_global_rmse: float
    pink_gel_max_key_roi_losses: tuple[tuple[str, float], ...]
    required_human_review_count: int
    min_human_final_preference_rate: float

    @property
    def pink_gel_key_roi_limit_map(self) -> dict[str, float]:
        """以字典形式返回粉色凝胶 ROI 阈值."""
        return dict(self.pink_gel_max_key_roi_losses)


@dataclass(frozen=True)
class GateCheck:
    """一个可审计的质量门禁判断."""

    check_id: str
    passed: bool
    actual: Any
    expected: str
    failed_case_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容的检查记录."""
        return asdict(self)


@dataclass(frozen=True)
class QualityGateReport:
    """完整 M5 gate 结果与聚合指标."""

    policy_id: str
    status: GateStatus
    checks: tuple[GateCheck, ...]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 兼容的 gate 报告."""
        return {
            "policy_id": self.policy_id,
            "status": self.status,
            "checks": [check.to_dict() for check in self.checks],
            "summary": self.summary,
        }
