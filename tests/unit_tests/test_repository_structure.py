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
