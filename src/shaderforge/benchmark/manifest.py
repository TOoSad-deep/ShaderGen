"""读取并校验 PNG-to-Shader benchmark 与 M5 gate 配置."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]
from PIL import Image

from shaderforge.benchmark.models import (
    BenchmarkCaseSpec,
    BenchmarkSuiteSpec,
    KeyRoiSpec,
    QualityGatePolicy,
)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 object。")
    return {str(key): item for key, item in value.items()}


def _sequence(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是 array。")
    return value


def _finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} 必须是 number。")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{field_name} 必须位于 0 到 1。")
    return result


def _positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field_name} 必须是正整数。")
    return int(value)


def _bbox(value: Any, field_name: str) -> tuple[float, float, float, float]:
    parts = _sequence(value, field_name)
    if len(parts) != 4:
        raise ValueError(f"{field_name} 必须包含 4 个坐标。")
    result = tuple(
        _finite_float(item, f"{field_name}[{index}]")
        for index, item in enumerate(parts)
    )
    if result[0] >= result[2] or result[1] >= result[3]:
        raise ValueError(f"{field_name} 必须满足 min < max。")
    return result  # type: ignore[return-value]


def _safe_child(root: Path, relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("benchmark image 必须位于 suite 根目录。")
    candidate = (root / relative).resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("benchmark image 越过 suite 根目录。")
    return candidate


def load_benchmark_suite(manifest_path: str | Path) -> BenchmarkSuiteSpec:
    """加载固定 manifest，并验证图片 hash、尺寸和 ROI."""
    path = Path(manifest_path).resolve()
    raw_bytes = path.read_bytes()
    root_value = _mapping(yaml.safe_load(raw_bytes), "manifest")
    root = path.parent
    cases: list[BenchmarkCaseSpec] = []
    seen_ids: set[str] = set()
    for index, raw_case in enumerate(_sequence(root_value.get("cases"), "cases")):
        value = _mapping(raw_case, f"cases[{index}]")
        case_id = str(value.get("id", "")).strip()
        if not case_id or case_id in seen_ids:
            raise ValueError("benchmark case id 不能为空或重复。")
        seen_ids.add(case_id)
        image_path = _safe_child(root, str(value.get("image", "")))
        image_bytes = image_path.read_bytes()
        expected_sha256 = str(value.get("sha256", ""))
        if sha256(image_bytes).hexdigest() != expected_sha256:
            raise ValueError(f"{case_id} 图片 SHA-256 与 manifest 不一致。")
        resolution_value = _sequence(value.get("resolution"), "resolution")
        if len(resolution_value) != 2:
            raise ValueError("resolution 必须包含 width/height。")
        resolution = (
            _positive_int(resolution_value[0], "resolution[0]"),
            _positive_int(resolution_value[1], "resolution[1]"),
        )
        with Image.open(image_path) as image:
            if image.size != resolution:
                raise ValueError(f"{case_id} 图片尺寸与 manifest 不一致。")
        key_rois = tuple(
            KeyRoiSpec(
                region_id=str(roi.get("id", "")).strip(),
                bbox_uv=_bbox(roi.get("bbox_uv"), "key_rois.bbox_uv"),
                purpose=str(roi.get("purpose", "")).strip(),
            )
            for roi in (
                _mapping(item, "key_rois[]")
                for item in _sequence(value.get("key_rois"), "key_rois")
            )
        )
        if any(not roi.region_id or not roi.purpose for roi in key_rois):
            raise ValueError(f"{case_id} key ROI 缺少 id 或 purpose。")
        cases.append(
            BenchmarkCaseSpec(
                case_id=case_id,
                level=str(value.get("level", "")).strip(),
                image_path=image_path,
                image_sha256=expected_sha256,
                resolution=resolution,
                expected_foreground_bbox_uv=_bbox(
                    value.get("expected_foreground_bbox_uv"),
                    "expected_foreground_bbox_uv",
                ),
                max_bbox_error_uv=_finite_float(
                    value.get("max_bbox_error_uv"),
                    "max_bbox_error_uv",
                ),
                key_rois=key_rois,
            )
        )
    return BenchmarkSuiteSpec(
        schema_version=_positive_int(root_value.get("schema_version"), "schema_version"),
        suite_id=str(root_value.get("suite_id", "")).strip(),
        contract_id=str(root_value.get("contract_id", "")).strip(),
        manifest_path=path,
        manifest_sha256=sha256(raw_bytes).hexdigest(),
        cases=tuple(cases),
    )


def load_quality_gate_policy(policy_path: str | Path) -> QualityGatePolicy:
    """加载在运行前冻结的 M5 发布门禁."""
    value = _mapping(
        yaml.safe_load(Path(policy_path).read_bytes()),
        "quality_gate",
    )
    thresholds = _mapping(value.get("thresholds"), "thresholds")
    pink = _mapping(value.get("pink_gel"), "pink_gel")
    roi_limits = _mapping(pink.get("max_key_roi_losses"), "max_key_roi_losses")
    return QualityGatePolicy(
        schema_version=_positive_int(value.get("schema_version"), "schema_version"),
        policy_id=str(value.get("policy_id", "")).strip(),
        suite_id=str(value.get("suite_id", "")).strip(),
        required_case_count=_positive_int(
            thresholds.get("required_case_count"), "required_case_count"
        ),
        min_ai_off_compile_rate=_finite_float(
            thresholds.get("min_ai_off_compile_rate"), "min_ai_off_compile_rate"
        ),
        min_ai_off_static_pass_rate=_finite_float(
            thresholds.get("min_ai_off_static_pass_rate"),
            "min_ai_off_static_pass_rate",
        ),
        min_final_compile_rate=_finite_float(
            thresholds.get("min_final_compile_rate"), "min_final_compile_rate"
        ),
        min_final_static_pass_rate=_finite_float(
            thresholds.get("min_final_static_pass_rate"),
            "min_final_static_pass_rate",
        ),
        min_improvement_rate=_finite_float(
            thresholds.get("min_improvement_rate"), "min_improvement_rate"
        ),
        min_total_improvement=_finite_float(
            thresholds.get("min_total_improvement"), "min_total_improvement"
        ),
        max_final_current_best_mismatches=int(
            thresholds.get("max_final_current_best_mismatches", 0)
        ),
        max_non_monotonic_runs=int(thresholds.get("max_non_monotonic_runs", 0)),
        min_traceability_rate=_finite_float(
            thresholds.get("min_traceability_rate"), "min_traceability_rate"
        ),
        pink_gel_max_bbox_error_uv=_finite_float(
            pink.get("max_bbox_error_uv"), "pink_gel.max_bbox_error_uv"
        ),
        pink_gel_max_global_rmse=_finite_float(
            pink.get("max_global_rmse"), "pink_gel.max_global_rmse"
        ),
        pink_gel_max_key_roi_losses=tuple(
            sorted(
                (
                    str(region_id),
                    _finite_float(limit, f"pink_gel.roi.{region_id}"),
                )
                for region_id, limit in roi_limits.items()
            )
        ),
        required_human_review_count=_positive_int(
            thresholds.get("required_human_review_count"),
            "required_human_review_count",
        ),
        min_human_final_preference_rate=_finite_float(
            thresholds.get("min_human_final_preference_rate"),
            "min_human_final_preference_rate",
        ),
    )
