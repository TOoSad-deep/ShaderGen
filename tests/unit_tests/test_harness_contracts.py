from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import get_args, get_type_hints

from langgraph.channels import UntrackedValue

from agent.app.states.agent_state import PngToShaderMinState

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _feature_row(feature_id: str) -> str:
    for line in _read("docs/FEATURES.md").splitlines():
        if line.startswith(f"| {feature_id} |"):
            return line
    raise AssertionError(f"docs/FEATURES.md 缺少 {feature_id} 行。")


def test_progress_is_bounded_current_handoff() -> None:
    progress = _read("PROGRESS.md")
    assert len(progress.encode("utf-8")) <= 20_000
    for heading in (
        "## 当前状态",
        "## 当前 active 功能",
        "## 下一步",
        "## 未解决缺口",
        "## 当前验证基线",
        "## 最近重要变更",
        "## 历史索引",
        "## 维护规则",
    ):
        assert heading in progress


def test_h01_evidence_matches_graph_registry() -> None:
    h01 = _feature_row("H01")
    graph_count = len(json.loads(_read("langgraph.json"))["graphs"])
    assert f"{graph_count} 个 graph" in h01


def test_langgraph_registry_exposes_only_scene_mvp() -> None:
    graphs = json.loads(_read("langgraph.json"))["graphs"]
    assert graphs == {
        "png_to_shader_min": (
            "./src/agent/app/graphs/png_to_shader_min_graph.py:png_to_shader_min_graph"
        )
    }


def test_min_run_large_fields_are_untracked() -> None:
    hints = get_type_hints(PngToShaderMinState, include_extras=True)
    for field_name in (
        "run_id",
        "image",
        "target_rgb",
        "scene",
        "current_glsl",
        "current_render",
        "current_best",
        "trace",
        "final_result",
    ):
        assert UntrackedValue in get_args(hints[field_name])


def test_environment_examples_are_split_by_runtime() -> None:
    server_example = _read(".env.example")
    frontend_example = _read("frontend/.env.example")
    assert "VITE_" not in server_example
    assert "VITE_API_BASE_URL=" in frontend_example
    assert "API_KEY=" not in frontend_example


def test_ci_harness_uses_locked_dependencies_and_no_model_credentials() -> None:
    main_ci = _read(".github/workflows/unit-tests.yml")
    integration_ci = _read(".github/workflows/integration-tests.yml")
    assert "uv sync --locked" in main_ci
    assert "make check" in main_ci
    assert "uv run mypy --strict src backend" in main_ci
    assert "uv sync --locked" in integration_ci
    assert "API_KEY" not in integration_ci
    assert "--allow-model-calls" not in integration_ci


def test_docs_check_script_passes() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/docs_check.py"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_agent_readme_is_harness_router() -> None:
    readme = _read("src/agent/README.md")
    for heading in (
        "## 当前状态",
        "## 开始前",
        "## Agent 改动门禁",
        "## 完成交接",
        "## 按需阅读",
    ):
        assert heading in readme
    assert "`make docs-check`" in readme
    assert "`uv run langgraph validate`" in readme
