"""PNG-to-Shader V1 的确定性 benchmark、门禁与盲评工具."""

from shaderforge.benchmark.ai_off import (
    AI_OFF_BASELINE_VERSION,
    build_ai_off_shader,
)
from shaderforge.benchmark.blind_review import (
    BLIND_REVIEW_EVIDENCE_SCHEMA,
    build_blind_assignments,
    verify_blind_review_package,
    verify_legacy_blind_review_package,
    write_blind_review_package,
)
from shaderforge.benchmark.gate import evaluate_quality_gate
from shaderforge.benchmark.manifest import (
    load_benchmark_suite,
    load_quality_gate_policy,
)
from shaderforge.benchmark.models import (
    BenchmarkCaseSpec,
    BenchmarkSuiteSpec,
    GateCheck,
    GateStatus,
    KeyRoiSpec,
    QualityGatePolicy,
    QualityGateReport,
)

__all__ = [
    "AI_OFF_BASELINE_VERSION",
    "BLIND_REVIEW_EVIDENCE_SCHEMA",
    "BenchmarkCaseSpec",
    "BenchmarkSuiteSpec",
    "GateCheck",
    "GateStatus",
    "KeyRoiSpec",
    "QualityGatePolicy",
    "QualityGateReport",
    "build_ai_off_shader",
    "build_blind_assignments",
    "evaluate_quality_gate",
    "load_benchmark_suite",
    "load_quality_gate_policy",
    "verify_blind_review_package",
    "verify_legacy_blind_review_package",
    "write_blind_review_package",
]
