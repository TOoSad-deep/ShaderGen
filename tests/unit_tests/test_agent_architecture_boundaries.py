from __future__ import annotations

import ast
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


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    violations = []
    for path in sorted((ROOT / "src/agent/app" / package).glob("*.py")):
        for target in _import_targets(path):
            if target.startswith(forbidden):
                violations.append(f"{path.name}: {target}")
    return violations


def test_nodes_use_llm_contract_not_implementation() -> None:
    assert _violations(
        "nodes",
        ("agent.app.llms", "agent.app.models"),
    ) == []


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
    violations = _violations(
        "lab",
        (
            "backend",
            "fastapi",
            "agent.app.graphs",
            "agent.app.llms",
            "agent.app.nodes",
        ),
    )
    assert violations == []


def test_node_lab_node_execution_is_owned_by_production_provider() -> None:
    adapters = ROOT / "src/agent/app/lab/adapters.py"
    deterministic = (
        ROOT
        / "src/agent/app/nodes/integrations/node_lab/deterministic.py"
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
    assert "agent.app.nodes.integrations.node_lab" in service_imports
    assert not any(
        target.startswith(
            (
                "agent.app.nodes.integrations.node_lab.deterministic",
                "agent.app.nodes.integrations.node_lab.model",
                "agent.app.graphs",
            )
        )
        for target in service_imports
    )


def test_m5_and_node_lab_benchmarks_keep_independent_evidence() -> None:
    m5_runner = ROOT / "scripts/run_png_to_shader_v1_benchmark.py"
    node_lab_runner = ROOT / "scripts/run_node_lab_benchmark.py"
    node_lab_benchmark = ROOT / "src/agent/app/lab/benchmark.py"

    m5_imports = _import_targets(m5_runner)
    assert not any(
        target.startswith(("agent.app.lab", "agent.app.services.node_lab"))
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
    for package in (
        "agent.app.contracts",
        "agent.app.llms",
        "agent.app.llms.families",
        "agent.app.messages",
    ):
        assert f'"{package}"' in pyproject


def test_nodes_directory_contains_no_cross_node_helper_modules() -> None:
    nodes = ROOT / "src/agent/app/nodes"
    assert not (nodes / "image_content.py").exists()
    assert not (nodes / "model_reasoning.py").exists()
    assert not (nodes / "model_runtime_options.py").exists()


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
    for name in (
        "visual_analysis_node.py",
        "shader_author_node.py",
        "visual_critic_node.py",
    ):
        path = ROOT / "src/agent/app/nodes" / name
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
