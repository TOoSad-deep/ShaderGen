"""读取并校验 PNG-to-Shader benchmark 与 M5 gate 配置."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

from shaderforge.benchmark.models import (
    BenchmarkCaseSpec,
    BenchmarkSuiteSpec,
    KeyRoiSpec,
    QualityGatePolicy,
)
from shaderforge.contracts import WEBGL1_STATIC_NO_TEXTURE_V1

_BENCHMARK_MANIFEST_SCHEMA_VERSION = 1
_QUALITY_GATE_SCHEMA_VERSION = 1
_MANIFEST_COORDINATE_SYSTEM = (
    f"shader_uv_{WEBGL1_STATIC_NO_TEXTURE_V1.uv_origin}"
)
_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "suite_id",
        "contract_id",
        "coordinate_system",
        "generator",
        "cases",
    }
)
_CASE_FIELDS = frozenset(
    {
        "id",
        "level",
        "image",
        "sha256",
        "resolution",
        "target_features",
        "expected_primitives",
        "expected_foreground_bbox_uv",
        "max_bbox_error_uv",
        "key_rois",
    }
)
_KEY_ROI_FIELDS = frozenset({"id", "bbox_uv", "purpose"})
_QUALITY_GATE_FIELDS = frozenset(
    {
        "schema_version",
        "policy_id",
        "suite_id",
        "calibration_basis",
        "thresholds",
        "pink_gel",
    }
)
_QUALITY_GATE_THRESHOLD_FIELDS = frozenset(
    {
        "required_case_count",
        "min_ai_off_compile_rate",
        "min_ai_off_static_pass_rate",
        "min_final_compile_rate",
        "min_final_static_pass_rate",
        "min_improvement_rate",
        "min_total_improvement",
        "max_final_current_best_mismatches",
        "max_non_monotonic_runs",
        "min_traceability_rate",
        "required_human_review_count",
        "min_human_final_preference_rate",
    }
)
_PINK_GEL_FIELDS = frozenset(
    {"max_bbox_error_uv", "max_global_rmse", "max_key_roi_losses"}
)


def _mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} 必须是 object。")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{field_name} 的字段名必须是 string。")
    return dict(value)


def _reject_unknown_fields(
    value: dict[str, Any],
    allowed_fields: frozenset[str],
    field_name: str,
) -> None:
    unknown = sorted(set(value) - allowed_fields)
    if unknown:
        raise ValueError(
            f"{field_name} 包含 schema v1 不支持字段：{', '.join(unknown)}。"
        )


def _sequence(value: Any, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是 array。")
    return value


def _non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} 必须是非空 string。")
    return value.strip()


def _non_empty_string_sequence(value: Any, field_name: str) -> list[str]:
    items = _sequence(value, field_name)
    if not items:
        raise ValueError(f"{field_name} 不能为空。")
    return [
        _non_empty_string(item, f"{field_name}[{index}]")
        for index, item in enumerate(items)
    ]


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


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} 必须是非负整数。")
    return int(value)


def _schema_version(value: Any, field_name: str, *, supported: int) -> int:
    version = _positive_int(value, field_name)
    if version != supported:
        raise ValueError(f"{field_name}={version} 不受支持；当前只支持 {supported}。")
    return version


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
    _reject_unknown_fields(root_value, _MANIFEST_FIELDS, "manifest")
    schema_version = _schema_version(
        root_value.get("schema_version"),
        "schema_version",
        supported=_BENCHMARK_MANIFEST_SCHEMA_VERSION,
    )
    suite_id = _non_empty_string(root_value.get("suite_id"), "suite_id")
    contract_id = _non_empty_string(root_value.get("contract_id"), "contract_id")
    if contract_id != WEBGL1_STATIC_NO_TEXTURE_V1.contract_id:
        raise ValueError(
            "manifest.contract_id 必须等于 canonical "
            f"{WEBGL1_STATIC_NO_TEXTURE_V1.contract_id}。"
        )
    coordinate_system = _non_empty_string(
        root_value.get("coordinate_system"),
        "coordinate_system",
    )
    if coordinate_system != _MANIFEST_COORDINATE_SYSTEM:
        raise ValueError(
            "manifest.coordinate_system 必须等于 "
            f"{_MANIFEST_COORDINATE_SYSTEM}。"
        )
    _non_empty_string(root_value.get("generator"), "generator")
    root = path.parent
    cases: list[BenchmarkCaseSpec] = []
    seen_ids: set[str] = set()
    raw_cases = _sequence(root_value.get("cases"), "cases")
    if not raw_cases:
        raise ValueError("cases 不能为空。")
    for index, raw_case in enumerate(raw_cases):
        value = _mapping(raw_case, f"cases[{index}]")
        _reject_unknown_fields(value, _CASE_FIELDS, f"cases[{index}]")
        case_id = _non_empty_string(value.get("id"), f"cases[{index}].id")
        if case_id in seen_ids:
            raise ValueError("benchmark case id 不得重复。")
        seen_ids.add(case_id)
        level = _non_empty_string(value.get("level"), f"cases[{index}].level")
        image_path = _safe_child(
            root,
            _non_empty_string(value.get("image"), f"cases[{index}].image"),
        )
        image_bytes = image_path.read_bytes()
        expected_sha256 = _non_empty_string(
            value.get("sha256"),
            f"cases[{index}].sha256",
        )
        if sha256(image_bytes).hexdigest() != expected_sha256:
            raise ValueError(f"{case_id} 图片 SHA-256 与 manifest 不一致。")
        resolution_value = _sequence(
            value.get("resolution"),
            f"cases[{index}].resolution",
        )
        if len(resolution_value) != 2:
            raise ValueError("resolution 必须包含 width/height。")
        resolution = (
            _positive_int(resolution_value[0], f"cases[{index}].resolution[0]"),
            _positive_int(resolution_value[1], f"cases[{index}].resolution[1]"),
        )
        with Image.open(image_path) as image:
            if image.size != resolution:
                raise ValueError(f"{case_id} 图片尺寸与 manifest 不一致。")
        _non_empty_string_sequence(
            value.get("target_features"),
            f"cases[{index}].target_features",
        )
        _non_empty_string_sequence(
            value.get("expected_primitives"),
            f"cases[{index}].expected_primitives",
        )
        raw_rois = _sequence(value.get("key_rois"), f"cases[{index}].key_rois")
        if not raw_rois:
            raise ValueError(f"{case_id} key_rois 不能为空。")
        key_rois: list[KeyRoiSpec] = []
        seen_roi_ids: set[str] = set()
        for roi_index, raw_roi in enumerate(raw_rois):
            roi_field = f"cases[{index}].key_rois[{roi_index}]"
            roi = _mapping(raw_roi, roi_field)
            _reject_unknown_fields(roi, _KEY_ROI_FIELDS, roi_field)
            region_id = _non_empty_string(roi.get("id"), f"{roi_field}.id")
            if region_id in seen_roi_ids:
                raise ValueError(f"{case_id} key ROI id 不得重复：{region_id}。")
            seen_roi_ids.add(region_id)
            key_rois.append(
                KeyRoiSpec(
                    region_id=region_id,
                    bbox_uv=_bbox(roi.get("bbox_uv"), f"{roi_field}.bbox_uv"),
                    purpose=_non_empty_string(
                        roi.get("purpose"),
                        f"{roi_field}.purpose",
                    ),
                )
            )
        cases.append(
            BenchmarkCaseSpec(
                case_id=case_id,
                level=level,
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
                key_rois=tuple(key_rois),
            )
        )
    return BenchmarkSuiteSpec(
        schema_version=schema_version,
        suite_id=suite_id,
        contract_id=contract_id,
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
    _reject_unknown_fields(value, _QUALITY_GATE_FIELDS, "quality_gate")
    schema_version = _schema_version(
        value.get("schema_version"),
        "schema_version",
        supported=_QUALITY_GATE_SCHEMA_VERSION,
    )
    policy_id = _non_empty_string(value.get("policy_id"), "policy_id")
    suite_id = _non_empty_string(value.get("suite_id"), "suite_id")
    _non_empty_string(value.get("calibration_basis"), "calibration_basis")
    thresholds = _mapping(value.get("thresholds"), "thresholds")
    _reject_unknown_fields(
        thresholds,
        _QUALITY_GATE_THRESHOLD_FIELDS,
        "thresholds",
    )
    pink = _mapping(value.get("pink_gel"), "pink_gel")
    _reject_unknown_fields(pink, _PINK_GEL_FIELDS, "pink_gel")
    roi_limits = _mapping(pink.get("max_key_roi_losses"), "max_key_roi_losses")
    if not roi_limits:
        raise ValueError("max_key_roi_losses 不能为空。")
    return QualityGatePolicy(
        schema_version=schema_version,
        policy_id=policy_id,
        suite_id=suite_id,
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
        max_final_current_best_mismatches=_non_negative_int(
            thresholds.get("max_final_current_best_mismatches"),
            "max_final_current_best_mismatches",
        ),
        max_non_monotonic_runs=_non_negative_int(
            thresholds.get("max_non_monotonic_runs"),
            "max_non_monotonic_runs",
        ),
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
                    _non_empty_string(region_id, "pink_gel.roi.id"),
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
