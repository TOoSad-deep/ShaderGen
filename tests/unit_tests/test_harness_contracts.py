from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import get_args, get_type_hints

from langgraph.channels import UntrackedValue

from agent.app.states.agent_state import PngToShaderV1State

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _feature_row(feature_id: str) -> str:
    for line in _read("docs/FEATURES.md").splitlines():
        if line.startswith(f"| {feature_id} |"):
            return line
    raise AssertionError(f"docs/FEATURES.md 缺少 {feature_id} 行。")


def test_feature_list_keeps_v1_as_the_only_active_pipeline() -> None:
    f09 = _feature_row("F09")

    assert "PNG" in f09
    assert "current_best" in f09
    assert "| active |" in f09


def test_h01_evidence_matches_current_harness_shape() -> None:
    h01 = _feature_row("H01")
    graph_count = len(json.loads(_read("langgraph.json"))["graphs"])

    assert "单元测试通过" in h01
    assert f"{graph_count} 个 graph" in h01
    assert "25 个单元测试" not in h01
    assert "20 个单元测试" not in h01
    assert "8 个单元测试" not in h01


def test_langgraph_registry_only_exposes_png_to_shader_v1() -> None:
    graphs = json.loads(_read("langgraph.json"))["graphs"]

    assert graphs == {
        "png_to_shader_v1": (
            "./src/agent/app/graphs/"
            "png_to_shader_v1_graph.py:png_to_shader_v1_graph"
        )
    }
    for deprecated_path in (
        "src/agent/app/graphs/main_graph.py",
        "src/agent/app/graphs/shader_generation_graph.py",
    ):
        assert not (ROOT / deprecated_path).exists()


def test_docs_check_derives_graph_count_from_langgraph_registry(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_graph_count",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    original_read = docs_check._read

    def fake_read(path: str) -> str:
        if path == "langgraph.json":
            return json.dumps(
                {
                    "graphs": {
                        "one": "one.py:graph",
                        "two": "two.py:graph",
                        "three": "three.py:graph",
                        "four": "four.py:graph",
                    }
                }
            )
        return original_read(path)

    monkeypatch.setattr(docs_check, "_read", fake_read)
    docs_check.ERRORS.clear()

    docs_check._check_feature_state_machine()

    assert any("4 个 graph" in error for error in docs_check.ERRORS)


def test_agent_service_does_not_import_node_or_llm_internals() -> None:
    path = ROOT / "src/agent/app/services/png_to_shader_v1.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_prefixes = ("agent.app.nodes", "agent.app.llms")
    violations = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(forbidden_prefixes):
                violations.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith(forbidden_prefixes):
                    violations.append(alias.name)

    assert violations == []


def test_docs_check_enforces_agent_llms_service_boundary(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_llms_boundary",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)

    monkeypatch.setattr(
        docs_check,
        "_imported_modules",
        lambda path: ["agent.app.llms.gateway"],
    )
    docs_check.ERRORS.clear()

    docs_check._check_agent_service_boundary()

    assert any("llms" in error for error in docs_check.ERRORS)


def test_docs_check_resolves_from_import_targets(tmp_path) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_import_targets",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    source = tmp_path / "boundary_probe.py"
    source.write_text("from agent.app import llms\n", encoding="utf-8")

    assert "agent.app.llms" in docs_check._imported_modules(source)


def test_docs_check_detects_graph_diagram_edge_drift(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_graph_diagrams",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    architecture = _read("src/agent/app/graphs/ARCHITECTURE.md").replace(
        "visual_analysis -. continue .-> persist_visual_analysis",
        "visual_analysis -. continue .-> missing_visual_analysis_node",
    )

    def fake_read(path: str) -> str:
        if path == "src/agent/app/graphs/ARCHITECTURE.md":
            return architecture
        return _read(path)

    monkeypatch.setattr(docs_check, "_read", fake_read)
    docs_check.ERRORS.clear()

    docs_check._check_graph_visualizations()

    assert any(
        "visual_analysis -. continue .-> persist_visual_analysis" in error
        for error in docs_check.ERRORS
    )


def test_png_to_shader_v1_run_summaries_are_untracked() -> None:
    hints = get_type_hints(PngToShaderV1State, include_extras=True)

    for field_name in (
        "image",
        "rendered_image",
        "glsl",
        "context_pack",
        "selected_memory_ids",
        "memory_status",
        "model_calls",
        "events",
        "logs",
        "run_id",
    ):
        field_type = hints[field_name]
        assert UntrackedValue in get_args(field_type)


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

    for required_text in (
        "当前 active 功能以 `docs/FEATURES.md` 为准",
        "当前进度和下一步以 `PROGRESS.md` 为准",
        "`make docs-check`",
        "`uv run pytest tests/unit_tests`",
        "`uv run langgraph validate`",
        "会话结束前更新 `PROGRESS.md`",
    ):
        assert required_text in readme


def test_agent_docs_describe_llms_gateway_boundary() -> None:
    app_architecture = _read("src/agent/app/ARCHITECTURE.md")
    agent_architecture = _read("src/agent/ARCHITECTURE.md")

    assert "agent.app.contracts" in app_architecture
    assert "agent.app.llms" in app_architecture
    assert "Node 不得直接依赖 `agent.app.llms`" in app_architecture
    assert "LLM Gateway" in agent_architecture
    assert "agent.app.models" not in app_architecture


def test_docs_check_enforces_agent_readme_harness_router(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_under_test",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)

    def fake_read(path: str) -> str:
        if path == "src/agent/README.md":
            return "# Agent\n\n只有链接，没有 harness 接手规则。\n"
        return _read(path)

    monkeypatch.setattr(docs_check, "_read", fake_read)
    docs_check.ERRORS.clear()

    docs_check._check_agent_readme_harness_router()

    assert any("src/agent/README.md" in error for error in docs_check.ERRORS)
