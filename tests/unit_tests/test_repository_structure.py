from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return tuple(imports)


def test_integration_tests_do_not_depend_on_unit_test_modules() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in (ROOT / "tests/integration_tests").glob("*.py")
        for import_name in _imports(path)
        if import_name.startswith("tests.unit_tests")
    }

    assert violations == {}


def test_backend_database_layer_does_not_construct_agent_services() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in (ROOT / "backend/app/database").glob("*.py")
        for import_name in _imports(path)
        if import_name.startswith("agent.app.services")
    }

    assert violations == {}


def test_online_agent_services_do_not_depend_on_offline_benchmarks() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in (ROOT / "src/agent/app/services").glob("*.py")
        for import_name in _imports(path)
        if import_name.startswith("agent.app.benchmarks")
    }

    assert violations == {}


def test_frontend_backend_fetches_are_centralized_in_api_client() -> None:
    sources = tuple((ROOT / "frontend/src").rglob("*.ts")) + tuple(
        (ROOT / "frontend/src").rglob("*.tsx")
    )
    direct_fetches = {
        path.relative_to(ROOT).as_posix()
        for path in sources
        if "fetch(" in path.read_text(encoding="utf-8")
    }

    assert direct_fetches == {"frontend/src/api/client.ts"}


def test_node_lab_core_does_not_depend_on_transport_or_product_packages() -> None:
    node_lab_root = ROOT / "src/nodelab"
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in node_lab_root.rglob("*.py")
        if "http" not in path.relative_to(node_lab_root).parts
        for import_name in _imports(path)
        if import_name == "fastapi"
        or import_name.startswith("fastapi.")
        or import_name == "backend"
        or import_name.startswith("backend.")
        or import_name == "agent"
        or import_name.startswith("agent.")
        or import_name == "shaderforge"
        or import_name.startswith("shaderforge.")
        or import_name == "nodelab.http"
        or import_name.startswith("nodelab.http.")
    }

    assert violations == {}


def test_node_lab_http_does_not_depend_on_product_packages() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in (ROOT / "src/nodelab/http").rglob("*.py")
        for import_name in _imports(path)
        if import_name == "backend"
        or import_name.startswith("backend.")
        or import_name == "agent"
        or import_name.startswith("agent.")
        or import_name == "shaderforge"
        or import_name.startswith("shaderforge.")
    }

    assert violations == {}


def test_backend_does_not_register_node_lab_http_transport() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in (ROOT / "backend").rglob("*.py")
        for import_name in _imports(path)
        if import_name == "nodelab.http"
        or import_name.startswith("nodelab.http.")
    }

    assert violations == {}


def test_node_lab_schema_leaf_does_not_depend_on_http_runtime() -> None:
    violations = {
        path.relative_to(ROOT).as_posix(): import_name
        for path in (ROOT / "src/nodelab/http/schemas").rglob("*.py")
        for import_name in _imports(path)
        if import_name == "nodelab.http.main"
        or import_name.startswith("nodelab.http.main.")
        or import_name == "nodelab.http.routes"
        or import_name.startswith("nodelab.http.routes.")
        or import_name == "nodelab.http.service"
        or import_name.startswith("nodelab.http.service.")
        or import_name == "nodelab.http.factory"
        or import_name.startswith("nodelab.http.factory.")
    }

    assert violations == {}
