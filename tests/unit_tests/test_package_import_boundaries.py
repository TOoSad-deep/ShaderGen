import json
import subprocess
import sys
from importlib.resources import files
from pathlib import Path

import agent.app.config
import backend.sql

ROOT = Path(__file__).resolve().parents[2]


def _run_probe(source: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_shaderforge_typed_contract_import_does_not_load_renderer() -> None:
    result = _run_probe(
        """
import json
import sys
import shaderforge.contracts

print(json.dumps({
    "rendering_package": "shaderforge.rendering" in sys.modules,
    "renderer_module": "shaderforge.rendering.webgl1_renderer" in sys.modules,
    "playwright": "playwright.async_api" in sys.modules,
}))
"""
    )

    assert result == {
        "rendering_package": False,
        "renderer_module": False,
        "playwright": False,
    }


def test_shaderforge_root_keeps_min_api_with_per_domain_lazy_loading() -> None:
    result = _run_probe(
        """
import json
import sys
import shaderforge
from shaderforge.contracts import RenderContract

contract_identity = shaderforge.RenderContract is RenderContract
renderer_after_contract = "shaderforge.rendering" in sys.modules
exports_complete = set(shaderforge.__all__) == set(shaderforge._EXPORT_MODULES)

from shaderforge.rendering import PlaywrightWebGL1Renderer
renderer_identity = shaderforge.PlaywrightWebGL1Renderer is PlaywrightWebGL1Renderer

print(json.dumps({
    "contract_identity": contract_identity,
    "renderer_after_contract": renderer_after_contract,
    "exports_complete": exports_complete,
    "renderer_identity": renderer_identity,
    "renderer_loaded": "shaderforge.rendering.webgl1_renderer" in sys.modules,
}))
"""
    )

    assert result == {
        "contract_identity": True,
        "renderer_after_contract": False,
        "exports_complete": True,
        "renderer_identity": True,
        "renderer_loaded": True,
    }


def test_backend_sql_is_an_explicit_packaged_resource_boundary() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '    "backend.sql",' in pyproject
    assert '"backend.sql" = ["*.sql"]' in pyproject
    assert backend.sql.__file__ is not None
    assert backend.sql.__file__.endswith("backend/sql/__init__.py")

    schema = files(backend.sql).joinpath("001_agent_process.sql")
    assert schema.is_file()
    assert "CREATE TABLE IF NOT EXISTS agent_runs" in schema.read_text(encoding="utf-8")


def test_scene_mvp_yaml_is_an_explicit_packaged_resource() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '"agent.app.config" = ["*.yaml"]' in pyproject
    policy = files(agent.app.config).joinpath("png_to_shader_min.yaml")
    assert policy.is_file()
    assert "scene_mvp_runtime_policy_v1" in policy.read_text(encoding="utf-8")
