"""构建 wheel，并在排除仓库源码的解释器中验证 V2 公共导入."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """构建当前 wheel，并仅从 wheel 与已安装依赖导入 V2 API."""
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("wheel import 门禁需要 uv。")
    with tempfile.TemporaryDirectory(prefix="shadergen-wheel-") as temp_dir:
        output_dir = Path(temp_dir)
        subprocess.run(
            [
                uv,
                "build",
                "--wheel",
                "--no-build-isolation",
                "--no-build-logs",
                "--out-dir",
                str(output_dir),
                str(ROOT),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = tuple(output_dir.glob("*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"期望恰好一个 wheel，实际为 {len(wheels)}。")
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            if (
                "backend/app/core/png_to_shader_runtime_policy.v2.yaml"
                not in archive.namelist()
            ):
                raise RuntimeError("wheel 缺少 Backend V1 运行策略 YAML。")
        dependency_paths = [
            item for item in sys.path if item and "site-packages" in item
        ]
        probe = f"""
import sys
from pathlib import Path

root = Path({str(ROOT)!r}).resolve()
wheel = Path({str(wheel)!r}).resolve()
sys.path[:] = [str(wheel), *{dependency_paths!r}] + [
    item for item in sys.path if item and 'site-packages' not in item
]

from shaderforge.benchmark import (
    DatasetSourceRecord,
    LoadedV2Dataset,
    M6_2StructureDiagnosticReport,
    M6_2SelectorReplayReport,
    ReleaseFreezeManifest,
    ReleaseReadinessAttestation,
    StructureCapabilityAssessment,
    V2_1IntentCaseOutcome,
    V2_1IntentGateReport,
    V2_3RealBudgetV1,
    V2_3RealCaseOutcome,
    V2_3RealModelValidationReport,
    V2DatasetManifest,
    V2DatasetReadiness,
    V2DatasetStageGate,
    assess_generator_capability,
    build_m6_2_selector_replay_report,
    build_m6_2_structure_diagnostic_report,
    create_signed_freeze,
    evaluate_signed_release_readiness,
    verify_release_readiness_attestation,
    evaluate_v2_dataset_readiness,
    evaluate_v2_dataset_stage_gate,
    evaluate_v2_1_intent_gate,
    evaluate_v2_3_real_model_validation,
    load_v2_dataset_manifest,
)
from shaderforge.evaluation import (
    GeneratorAdmissionEvidence,
    MeasurementSeedAdmissionPolicy,
    RuntimeTargetStructureEvidence,
    RuntimeTargetStructureArtifactEnvelope,
    RuntimeTargetStructureVerification,
    TargetStructureFacts,
    select_current_best,
    verify_runtime_target_structure,
    load_runtime_target_structure_artifacts,
    materialize_runtime_target_structure_artifacts,
)
from shaderforge.analysis import (
    TargetMeasurementsV2ArtifactBundle,
    measure_target_v2,
)
from shaderforge.intent import (
    IntentBuildResult,
    VisualInterpretationV2,
    build_intent_build_context,
    build_intent_variants,
    build_request_constraint_set,
    merge_request_constraint_set,
    validate_intent_build_result,
    validate_intent_ir,
    validate_request_constraint_set_policy,
)
from shaderforge.compiler import CompilationBundle, compile_effect_genome
from shaderforge.genome import TypedEffectGenome
from shaderforge.seeding import SeedPlanV1, expand_seed_plans
from agent.app.services.png_to_shader_v2 import (
    PngToShaderV2ServiceConfig,
    create_png_to_shader_v2_development_service,
)
import shaderforge.benchmark.m6_2_diagnostics as diagnostic_module
import shaderforge.benchmark.m6_2_selector_replay as replay_module
import shaderforge.benchmark.v2_1_intent_gate as intent_gate_module
import shaderforge.benchmark.v2_3_real_model_validation as real_validation_module
import shaderforge.benchmark.v2_dataset as module
import shaderforge.benchmark.v2_release_handoff as release_handoff_module
import shaderforge.evaluation.admission as admission_module
import shaderforge.compiler as compiler_module
import shaderforge.seeding as seeding_module

assert DatasetSourceRecord.__module__ == module.__name__
assert LoadedV2Dataset.__module__ == module.__name__
assert M6_2StructureDiagnosticReport.__module__ == diagnostic_module.__name__
assert M6_2SelectorReplayReport.__module__ == replay_module.__name__
assert ReleaseFreezeManifest.__module__ == release_handoff_module.__name__
assert ReleaseReadinessAttestation.__module__ == release_handoff_module.__name__
assert StructureCapabilityAssessment.__module__ == admission_module.__name__
assert V2_1IntentCaseOutcome.__module__ == intent_gate_module.__name__
assert V2_1IntentGateReport.__module__ == intent_gate_module.__name__
assert V2_3RealBudgetV1.__module__ == real_validation_module.__name__
assert V2_3RealCaseOutcome.__module__ == real_validation_module.__name__
assert V2_3RealModelValidationReport.__module__ == real_validation_module.__name__
assert GeneratorAdmissionEvidence.__module__ == admission_module.__name__
assert MeasurementSeedAdmissionPolicy.__module__ == admission_module.__name__
assert RuntimeTargetStructureEvidence.__module__.endswith('runtime_structure')
assert RuntimeTargetStructureArtifactEnvelope.__module__.endswith('runtime_structure_artifacts')
assert RuntimeTargetStructureVerification.__module__.endswith('runtime_structure')
assert TargetStructureFacts.__module__ == admission_module.__name__
assert TargetMeasurementsV2ArtifactBundle.__module__.endswith('measurements_v2')
assert IntentBuildResult.__module__.endswith('intent.ir')
assert VisualInterpretationV2.__module__.endswith('intent.ir')
assert CompilationBundle.__module__.endswith('compiler.models')
assert TypedEffectGenome.__module__.endswith('genome.typed_nodes')
assert SeedPlanV1.__module__.endswith('seeding.models')
assert V2DatasetManifest.__module__ == module.__name__
assert V2DatasetReadiness.__module__ == module.__name__
assert V2DatasetStageGate.__module__ == module.__name__
assert callable(assess_generator_capability)
assert callable(build_m6_2_selector_replay_report)
assert callable(build_m6_2_structure_diagnostic_report)
assert callable(create_signed_freeze)
assert callable(evaluate_signed_release_readiness)
assert callable(verify_release_readiness_attestation)
assert callable(evaluate_v2_dataset_readiness)
assert callable(evaluate_v2_dataset_stage_gate)
assert callable(evaluate_v2_1_intent_gate)
assert callable(evaluate_v2_3_real_model_validation)
assert callable(load_v2_dataset_manifest)
assert callable(select_current_best)
assert callable(verify_runtime_target_structure)
assert callable(load_runtime_target_structure_artifacts)
assert callable(materialize_runtime_target_structure_artifacts)
assert callable(measure_target_v2)
assert callable(build_intent_variants)
assert callable(build_intent_build_context)
assert callable(build_request_constraint_set)
assert callable(merge_request_constraint_set)
assert callable(validate_intent_build_result)
assert callable(validate_intent_ir)
assert callable(validate_request_constraint_set_policy)
assert callable(compile_effect_genome)
assert callable(expand_seed_plans)
assert callable(create_png_to_shader_v2_development_service)
assert PngToShaderV2ServiceConfig.__module__.endswith('services.png_to_shader_v2.models')
assert '.whl/' in str(diagnostic_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(replay_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(intent_gate_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(real_validation_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(module.__file__).replace('\\\\', '/')
assert '.whl/' in str(release_handoff_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(admission_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(compiler_module.__file__).replace('\\\\', '/')
assert '.whl/' in str(seeding_module.__file__).replace('\\\\', '/')
"""
        subprocess.run(
            [sys.executable, "-I", "-c", probe],
            cwd=output_dir,
            check=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
