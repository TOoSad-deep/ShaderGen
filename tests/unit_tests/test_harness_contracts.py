from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import get_args, get_type_hints

from langgraph.channels import UntrackedValue

from agent.app.states.agent_state import ShaderPipelineState

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _feature_row(feature_id: str) -> str:
    for line in _read("docs/FEATURES.md").splitlines():
        if line.startswith(f"| {feature_id} |"):
            return line
    raise AssertionError(f"docs/FEATURES.md 缺少 {feature_id} 行。")


def test_feature_list_separates_agent_review_from_browser_e2e_gap() -> None:
    f06 = _feature_row("F06")
    f07 = _feature_row("F07")

    assert "Agent/后端在线 Review" in f06
    assert "| passing |" in f06
    assert "单元测试通过" in f06
    assert "单元测试 25 个通过" not in f06
    assert "单元测试 20 个通过" not in f06
    assert "浏览器端 Review 闭环" in f07
    assert "canvas 截图 -> review API -> UI 展示" in f07
    assert "Playwright" in f07
    assert "| not_started |" in f07


def test_h01_evidence_matches_current_harness_shape() -> None:
    h01 = _feature_row("H01")

    assert "单元测试通过" in h01
    assert "2 个 graph" in h01
    assert "25 个单元测试" not in h01
    assert "20 个单元测试" not in h01
    assert "8 个单元测试" not in h01
    assert "1 个 graph" not in h01


def test_agent_service_does_not_import_node_or_llm_internals() -> None:
    tree = ast.parse(_read("src/agent/app/services/shader_generation.py"))
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


def test_shader_pipeline_run_summaries_are_untracked() -> None:
    hints = get_type_hints(ShaderPipelineState, include_extras=True)

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
