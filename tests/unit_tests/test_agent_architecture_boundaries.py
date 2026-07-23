from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _import_targets(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            targets.append(node.module)
    return targets


def _violations(package: str, forbidden: tuple[str, ...]) -> list[str]:
    violations: list[str] = []
    package_root = ROOT / "src/agent/app" / package
    for path in sorted(package_root.rglob("*.py")):
        for target in _import_targets(path):
            if target.startswith(forbidden):
                violations.append(f"{path.relative_to(package_root)}: {target}")
    return violations


def test_nodes_use_llm_contract_not_implementation() -> None:
    assert _violations("nodes", ("agent.app.llms", "agent.app.models")) == []


def test_v1_product_namespaces_are_removed() -> None:
    removed_files = (
        "src/agent/app/contracts/png_to_shader_v1.py",
        "src/agent/app/graphs/png_to_shader_v1_graph.py",
        "src/agent/app/graphs/png_to_shader_v1_routing.py",
        "src/agent/app/messages/png_to_shader_v1.py",
        "src/agent/app/parsers/png_to_shader_v1.py",
        "src/agent/app/services/png_to_shader_v1.py",
        "src/shaderforge/contracts/png_to_shader_v1.py",
    )
    assert [path for path in removed_files if (ROOT / path).is_file()] == []
    for directory in (
        ROOT / "src/agent/app/nodes/png_to_shader_v1",
        ROOT / "src/shaderforge/benchmark",
    ):
        assert list(directory.rglob("*.py")) == []


def test_typed_contract_import_does_not_load_heavy_runtime() -> None:
    code = """
import json
import sys
import shaderforge.contracts
import agent.app.contracts.llm
print(json.dumps({
    "playwright": "playwright.async_api" in sys.modules,
    "numpy": "numpy" in sys.modules,
    "pillow": "PIL.Image" in sys.modules,
}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(completed.stdout) == {
        "playwright": False,
        "numpy": False,
        "pillow": False,
    }


def test_agent_package_layout_keeps_only_min_node_package() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"agent.app.nodes.png_to_shader_min"' in pyproject
    assert "png_to_shader_v1" not in pyproject
    assert '"shaderforge.benchmark"' not in pyproject


def test_shader_generate_route_delegates_use_case() -> None:
    route = ROOT / "backend/app/api/routes/shader.py"
    source = route.read_text(encoding="utf-8")
    assert "backend.app.services.shader_generation" in _import_targets(route)
    assert "execute_shader_generation(command, dependencies)" in source
    assert "procedural_v1" not in source
