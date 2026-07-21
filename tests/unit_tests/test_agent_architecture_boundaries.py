from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
            targets.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return targets


def _relative_import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        "." * node.level + (node.module or "")
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.level
    ]


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    violations = []
    package_root = ROOT / "src/agent/app" / package
    for path in sorted(package_root.rglob("*.py")):
        for target in _import_targets(path):
            if target.startswith(forbidden):
                relative_path = path.relative_to(package_root).as_posix()
                violations.append(f"{relative_path}: {target}")
    return violations


def test_nodes_use_llm_contract_not_implementation() -> None:
    assert _violations(
        "nodes",
        ("agent.app.llms", "agent.app.models"),
    ) == []


def test_typed_submodule_imports_do_not_eager_load_heavy_runtime() -> None:
    code = """
import json
import sys
import shaderforge.contracts
import agent.app.contracts.llm
import nodelab.models
print(json.dumps({
    'playwright': 'playwright.async_api' in sys.modules,
    'numpy': 'numpy' in sys.modules,
    'pillow': 'PIL.Image' in sys.modules,
    'lab_runner': 'nodelab.runner' in sys.modules,
    'v1_contracts': 'agent.app.contracts.png_to_shader_v1' in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout) == {
        "playwright": False,
        "numpy": False,
        "pillow": False,
        "lab_runner": False,
        "v1_contracts": False,
    }


def test_states_do_not_depend_on_agent_implementation_layers() -> None:
    assert _violations(
        "states",
        (
            "agent.app.llms",
            "agent.app.models",
            "agent.app.nodes",
            "agent.app.graphs",
            "agent.app.services",
        ),
    ) == []


def test_node_lab_core_is_transport_free_and_does_not_reflect_nodes() -> None:
    core_root = ROOT / "src/nodelab"
    forbidden = (
        "backend",
        "fastapi",
        "agent.app.graphs",
        "agent.app.llms",
        "agent.app.nodes",
        "shaderforge.analysis",
        "shaderforge.evaluation",
        "shaderforge.public",
        "shaderforge.rendering",
        "shaderforge.validation",
    )
    violations = [
        f"{path.relative_to(core_root).as_posix()}: {target}"
        for path in sorted(core_root.rglob("*.py"))
        for target in _import_targets(path)
        if target.startswith(forbidden)
    ]
    assert violations == []
    assert all(
        "png_to_shader_v1" not in path.read_text(encoding="utf-8")
        for path in core_root.glob("*.py")
    )


def test_node_lab_node_execution_is_owned_by_production_provider() -> None:
    adapters = (
        ROOT
        / "src/agent/app/nodes/png_to_shader_v1/integrations/node_lab/capability_executor.py"
    )
    deterministic = (
        ROOT
        / "src/agent/app/nodes/png_to_shader_v1/integrations/node_lab/deterministic.py"
    )
    service = ROOT / "src/agent/app/services/node_lab.py"
    adapter_source = adapters.read_text(encoding="utf-8")
    deterministic_source = deterministic.read_text(encoding="utf-8")
    service_imports = _import_targets(service)

    assert not any(
        target.startswith(("agent.app.nodes", "agent.app.graphs"))
        for target in _import_targets(adapters)
    )
    assert "_render_and_evaluate" not in adapter_source
    assert "_select_current_best_node" not in adapter_source
    assert "class DeterministicNodeExecutor" in deterministic_source
    assert "StageCDeterministicExecutor" not in deterministic_source
    assert any(
        target.startswith("agent.app.nodes")
        for target in _import_targets(deterministic)
    )
    assert "agent.app.nodes.png_to_shader_v1.integrations.node_lab" in service_imports
    assert not any(
        target.startswith(
            (
                "agent.app.nodes.png_to_shader_v1.integrations.node_lab.deterministic",
                "agent.app.nodes.png_to_shader_v1.integrations.node_lab.model",
                "agent.app.graphs",
            )
        )
        for target in service_imports
    )


def test_m5_and_node_lab_benchmarks_keep_independent_evidence() -> None:
    m5_runner = ROOT / "scripts/run_png_to_shader_v1_benchmark.py"
    node_lab_runner = ROOT / "scripts/run_node_lab_benchmark.py"
    node_lab_benchmark = ROOT / "src/nodelab/benchmark.py"

    m5_imports = _import_targets(m5_runner)
    assert not any(
        target.startswith(("nodelab", "agent.app.services.node_lab"))
        for target in m5_imports
    )
    assert "agent.app.services.png_to_shader_v1" in m5_imports

    node_lab_imports = [
        *_import_targets(node_lab_runner),
        *_import_targets(node_lab_benchmark),
    ]
    assert not any(
        target.startswith("shaderforge.benchmark") for target in node_lab_imports
    )
    node_lab_source = node_lab_benchmark.read_text(encoding="utf-8")
    m5_runner_source = m5_runner.read_text(encoding="utf-8")
    node_lab_runner_source = node_lab_runner.read_text(encoding="utf-8")
    assert "evaluate_quality_gate" not in node_lab_source
    assert "output/benchmarks/png-to-shader-v1" in m5_runner_source
    assert "output/benchmarks/node-lab" in node_lab_runner_source


def test_agent_package_layout_uses_llms_gateway() -> None:
    assert not (ROOT / "src/agent/app/models").exists()
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"agent.app.models"' not in pyproject
    assert '"agent.app.nodes.integrations"' not in pyproject
    assert '"agent.app.nodes.integrations.node_lab"' not in pyproject
    for package in (
        "nodelab",
        "agent.app.contracts",
        "agent.app.llms",
        "agent.app.llms.families",
        "agent.app.messages",
        "agent.app.nodes.png_to_shader_v1",
        "agent.app.nodes.png_to_shader_v1.deterministic",
        "agent.app.nodes.png_to_shader_v1.integrations",
        "agent.app.nodes.png_to_shader_v1.integrations.node_lab",
        "agent.app.nodes.png_to_shader_v1.model",
    ):
        assert f'"{package}"' in pyproject


def test_nodes_directory_contains_no_cross_node_helper_modules() -> None:
    nodes = ROOT / "src/agent/app/nodes"
    assert not (nodes / "image_content.py").exists()
    assert not (nodes / "model_reasoning.py").exists()
    assert not (nodes / "model_runtime_options.py").exists()


def test_png_to_shader_v1_nodes_share_one_feature_namespace() -> None:
    nodes = ROOT / "src/agent/app/nodes"
    feature = nodes / "png_to_shader_v1"
    assert not (nodes / "integrations").exists()
    for legacy_name in (
        "bounded_model_node.py",
        "prepare_context_node.py",
        "promote_validated_strategy_node.py",
        "shader_author_node.py",
        "structured_output.py",
        "visual_analysis_node.py",
        "visual_critic_node.py",
    ):
        assert not (nodes / legacy_name).exists()
    for legacy_name in (
        "candidates.py",
        "finalization.py",
        "preparation.py",
        "render_evaluate.py",
        "render_evaluate_rendering.py",
        "render_evaluate_scoring.py",
        "render_evaluate_validation.py",
        "runtime.py",
        "selection.py",
    ):
        assert not (feature / legacy_name).exists()
    for package in (
        feature,
        feature / "deterministic",
        feature / "model",
        feature / "integrations",
        feature / "integrations/node_lab",
    ):
        assert (package / "__init__.py").is_file()


def test_png_to_shader_v1_node_layers_keep_dependency_direction() -> None:
    feature = ROOT / "src/agent/app/nodes/png_to_shader_v1"
    feature_prefix = "agent.app.nodes.png_to_shader_v1"

    assert _violations(
        "nodes/png_to_shader_v1/model",
        (
            "agent.app.graphs",
            f"{feature_prefix}.deterministic",
            f"{feature_prefix}.integrations",
        ),
    ) == []
    assert _violations(
        "nodes/png_to_shader_v1/deterministic",
        (
            "agent.app.graphs",
            f"{feature_prefix}.integrations",
            f"{feature_prefix}.model",
        ),
    ) == []

    model_relative = [
        target
        for path in (feature / "model").rglob("*.py")
        for target in _relative_import_targets(path)
    ]
    deterministic_relative = [
        target
        for path in (feature / "deterministic").rglob("*.py")
        for target in _relative_import_targets(path)
    ]
    assert not any(
        target.startswith(("..deterministic", "..integrations"))
        for target in model_relative
    )
    assert not any(
        target.startswith(("..integrations", "..model"))
        for target in deterministic_relative
    )
    assert not any(
        target.startswith(".integrations")
        for target in _relative_import_targets(feature / "__init__.py")
    )


def test_png_to_shader_v1_graph_uses_node_public_facade() -> None:
    graph = ROOT / "src/agent/app/graphs/png_to_shader_v1_graph.py"
    imports = _import_targets(graph)

    assert "agent.app.nodes.png_to_shader_v1" in imports
    assert not any(
        target.startswith(
            (
                "agent.app.nodes.png_to_shader_v1.deterministic",
                "agent.app.nodes.png_to_shader_v1.integrations",
                "agent.app.nodes.png_to_shader_v1.model",
            )
        )
        for target in imports
    )


def test_png_to_shader_parser_remains_pure() -> None:
    parser = ROOT / "src/agent/app/parsers/png_to_shader_v1.py"
    forbidden = (
        "agent.app.graphs",
        "agent.app.llms",
        "agent.app.nodes",
        "agent.app.prompts",
        "shaderforge.evaluation",
        "shaderforge.rendering",
        "shaderforge.store",
        "shaderforge.validation",
    )

    assert [
        target for target in _import_targets(parser) if target.startswith(forbidden)
    ] == []


def test_m2_role_nodes_do_not_run_m1_fact_layer_or_store() -> None:
    forbidden = (
        "agent.app.llms",
        "shaderforge.evaluation",
        "shaderforge.rendering",
        "shaderforge.store",
        "shaderforge.validation",
    )
    violations = []
    for name in ("visual_analysis.py", "shader_author.py", "visual_critic.py"):
        path = ROOT / "src/agent/app/nodes/png_to_shader_v1/model" / name
        violations.extend(
            f"{name}: {target}"
            for target in _import_targets(path)
            if target.startswith(forbidden)
        )

    assert violations == []


def test_shader_generate_route_delegates_backend_use_case_orchestration() -> None:
    route = ROOT / "backend/app/api/routes/shader.py"
    route_imports = _import_targets(route)
    route_source = route.read_text(encoding="utf-8")
    moved_generation_details = {
        "start_shader_generation_run",
        "record_shader_generation_success",
        "record_shader_generation_failure",
        "generate_shader_from_image",
        "generate_procedural_shader_from_image",
    }

    assert "backend.app.services.shader_generation" in route_imports
    assert moved_generation_details.isdisjoint(
        target.rsplit(".", maxsplit=1)[-1] for target in route_imports
    )
    assert "execute_shader_generation(command, dependencies)" in route_source
