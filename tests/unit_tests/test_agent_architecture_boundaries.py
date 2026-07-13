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
