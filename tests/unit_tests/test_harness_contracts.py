from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import get_args, get_type_hints

from langgraph.channels import UntrackedValue

from agent.app.states.agent_state import PngToShaderV1State

ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _load_docs_check(module_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    return docs_check


def _feature_row(feature_id: str) -> str:
    for line in _read("docs/FEATURES.md").splitlines():
        if line.startswith(f"| {feature_id} |"):
            return line
    raise AssertionError(f"docs/FEATURES.md 缺少 {feature_id} 行。")


def test_feature_list_keeps_f03_as_the_only_active_pipeline() -> None:
    f02 = _feature_row("F02")
    f03 = _feature_row("F03")
    f09 = _feature_row("F09")

    assert "Intent IR" in f02
    assert "| passing |" in f02
    assert "DSL" in f03 and "Renderer" in f03
    assert "| active |" in f03
    assert "PNG" in f09
    assert "current_best" in f09
    assert "| blocked |" in f09
    assert "解除条件" in f09


def test_progress_is_bounded_current_handoff() -> None:
    progress = _read("PROGRESS.md")
    recent_changes = progress.split("## 最近重要变更", 1)[1].split("\n## ", 1)[0]

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
    assert "不是逐会话追加日志" in progress
    assert sum(line.startswith("- ") for line in recent_changes.splitlines()) <= 5

    archive = _read(
        "docs/progress/archive/PROGRESS-2026-07-07--2026-07-15.md"
    )
    assert "不代表当前" in archive

    stage_summary_path = "docs/progress/archive/STAGE-SUMMARY-2026-07-10.md"
    stage_summary = _read(stage_summary_path)
    stage_summary_preamble = "\n".join(stage_summary.splitlines()[:12])
    assert stage_summary_path in progress
    assert "截止日期：2026-07-10" in stage_summary_preamble
    assert "不代表当前" in stage_summary_preamble
    assert "`PROGRESS.md`" in stage_summary_preamble


def test_docs_check_rejects_unclassified_root_markdown(
    tmp_path, monkeypatch
) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_root_markdown_guard",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    (tmp_path / "旧阶段总结.md").write_text("# 历史总结\n", encoding="utf-8")
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_root_markdown_classification()

    assert any("旧阶段总结.md" in error for error in docs_check.ERRORS)


def test_docs_check_requires_archive_warning_in_preamble(
    tmp_path, monkeypatch
) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_archive_warning_guard",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    archive_dir = tmp_path / "docs/progress/archive"
    archive_dir.mkdir(parents=True)
    (archive_dir / "OLD.md").write_text(
        "# 旧记录\n" + ("\n占位" * 12) + "\n不代表当前事实\n",
        encoding="utf-8",
    )
    progress = "\n".join(
        (
            "# 进度",
            "不是逐会话追加日志，历史见 docs/progress/archive/。",
            *docs_check.PROGRESS_REQUIRED_HEADINGS,
        )
    )
    (tmp_path / "PROGRESS.md").write_text(progress, encoding="utf-8")
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_progress_handoff()

    assert any("必须在首屏" in error for error in docs_check.ERRORS)


def test_docs_check_detects_progress_growth_and_changelog_overflow(monkeypatch) -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_check_progress_guard",
        ROOT / "scripts/docs_check.py",
    )
    assert spec is not None
    assert spec.loader is not None
    docs_check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(docs_check)
    original_read = docs_check._read
    oversized_progress = _read("PROGRESS.md").replace(
        "\n## 历史索引",
        "\n- 2026-07-15：第六条不应留在主文件。\n\n## 历史索引",
        1,
    ) + ("x" * 20_000)

    def fake_read(path: str) -> str:
        if path == "PROGRESS.md":
            return oversized_progress
        return original_read(path)

    monkeypatch.setattr(docs_check, "_read", fake_read)
    docs_check.ERRORS.clear()

    docs_check._check_progress_handoff()

    assert any("20,000 bytes" in error for error in docs_check.ERRORS)
    assert any("最多保留 5 条" in error for error in docs_check.ERRORS)


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
        "visual_analysis -. continue .-> "
        "persist_visual_analysis[persist_visual_analysis]",
        "visual_analysis -. continue .-> "
        "missing_visual_analysis_node[missing_visual_analysis_node]",
    )

    def fake_read(path: str) -> str:
        if path == "src/agent/app/graphs/ARCHITECTURE.md":
            return architecture
        return _read(path)

    monkeypatch.setattr(docs_check, "_read", fake_read)
    docs_check.ERRORS.clear()

    docs_check._check_graph_visualizations()

    assert any(
        "Mermaid 条件边与代码不一致" in error
        and "('visual_analysis', 'continue', 'persist_visual_analysis')" in error
        for error in docs_check.ERRORS
    )


def test_docs_check_detects_extra_mermaid_node(tmp_path, monkeypatch) -> None:
    docs_check = _load_docs_check("docs_check_extra_mermaid_node")
    graph_root = tmp_path / "src/agent/app/graphs"
    graph_root.mkdir(parents=True)
    (graph_root / "sample_graph.py").write_text(
        "\n".join(
            (
                "# 图（测试）",
                'graph.add_node("alpha", object())',
                'graph.add_node("beta", object())',
                'graph.add_edge(START, "alpha")',
                'graph.add_edge("alpha", "beta")',
                'graph.add_edge("beta", END)',
            )
        ),
        encoding="utf-8",
    )
    (graph_root / "ARCHITECTURE.md").write_text(
        """<!-- graph-diagram:sample_graph:start -->
```mermaid
flowchart TD
    START([START])
    END([END])
    alpha[alpha]
    beta[beta]
    rogue[rogue]
    START --> alpha
    alpha --> beta
    beta --> END
```
<!-- graph-diagram:sample_graph:end -->
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_graph_visualizations()

    assert any(
        "Mermaid 节点与代码不一致" in error and "多余=rogue" in error
        for error in docs_check.ERRORS
    )


def test_docs_check_detects_development_builder_edge_drift(
    tmp_path: Path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_development_builder")
    graph_root = tmp_path / "src/agent/app/graphs"
    graph_root.mkdir(parents=True)
    (graph_root / "sample_builder.py").write_text(
        "\n".join(
            (
                "# 图（故意与 Mermaid 漂移）",
                'graph.add_node("alpha", fn)',
                'graph.add_node("beta", fn)',
                'graph.add_edge("alpha", "beta")',
            )
        ),
        encoding="utf-8",
    )
    (graph_root / "ARCHITECTURE.md").write_text(
        """<!-- graph-diagram:sample_builder:start -->
```mermaid
flowchart TD
alpha[alpha]
beta[beta]
```
<!-- graph-diagram:sample_builder:end -->
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_graph_visualizations()

    assert any(
        "sample_builder Mermaid 直接边与代码不一致" in error
        for error in docs_check.ERRORS
    )


def test_docs_check_enforces_bidirectional_langgraph_registration(
    tmp_path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_langgraph_registration")
    graph_root = tmp_path / "src/agent/app/graphs"
    graph_root.mkdir(parents=True)
    registered_path = graph_root / "registered_graph.py"
    unregistered_path = graph_root / "unregistered_graph.py"
    legacy_path = graph_root / "legacy.py"
    for path in (registered_path, unregistered_path, legacy_path):
        path.write_text("graph = object()\n", encoding="utf-8")
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)

    (tmp_path / "langgraph.json").write_text(
        json.dumps(
            {
                "graphs": {
                    "registered": (
                        "./src/agent/app/graphs/registered_graph.py:graph"
                    )
                }
            }
        ),
        encoding="utf-8",
    )
    docs_check.ERRORS.clear()
    docs_check._check_langgraph_registration()
    assert any(
        "存在未注册的 *_graph.py" in error
        and "unregistered_graph.py" in error
        for error in docs_check.ERRORS
    )

    (tmp_path / "langgraph.json").write_text(
        json.dumps(
            {
                "graphs": {
                    "registered": (
                        "./src/agent/app/graphs/registered_graph.py:graph"
                    ),
                    "unregistered": (
                        "./src/agent/app/graphs/unregistered_graph.py:graph"
                    ),
                    "legacy": "./src/agent/app/graphs/legacy.py:graph",
                }
            }
        ),
        encoding="utf-8",
    )
    docs_check.ERRORS.clear()
    docs_check._check_langgraph_registration()
    assert any(
        "langgraph.json 注册了非 *_graph.py 入口" in error
        and "legacy.py" in error
        for error in docs_check.ERRORS
    )


def test_docs_check_rejects_unknown_live_harness_commands(
    tmp_path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_documented_commands")
    (tmp_path / "frontend").mkdir()
    (tmp_path / "Makefile").write_text("check:\n\t@true\n", encoding="utf-8")
    (tmp_path / "frontend/package.json").write_text(
        json.dumps({"scripts": {"build": "vite build"}}),
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        """# Harness

- `make missing-target`
- `npm --prefix frontend run missing-script`
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_documented_commands()

    assert any(
        "不存在的 Make target：missing-target" in error
        for error in docs_check.ERRORS
    )
    assert any(
        "不存在的 frontend npm script：missing-script" in error
        for error in docs_check.ERRORS
    )


def test_docs_check_rejects_missing_live_repository_path(
    tmp_path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_documented_paths")
    (tmp_path / "AGENTS.md").write_text(
        "# Harness\n\n入口：`src/missing/ARCHITECTURE.md`。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_documented_repository_paths()

    assert any(
        "不存在的仓库路径：src/missing/ARCHITECTURE.md" in error
        for error in docs_check.ERRORS
    )


def test_docs_check_detects_local_evidence_size_and_hash_drift(
    tmp_path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_evidence_registry")
    evidence_root = tmp_path / "docs/evidence"
    evidence_root.mkdir(parents=True)
    payload = b"frozen-evidence"
    artifact_path = evidence_root / "proof.bin"
    artifact_path.write_bytes(payload)
    (evidence_root / "registry.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "evidence_id": "test-evidence",
                        "durability_status": "durable",
                        "artifacts": [
                            {
                                "path": "docs/evidence/proof.bin",
                                "availability": "git",
                                "size_bytes": len(payload) + 1,
                                "sha256": "0" * 64,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_evidence_registry()

    assert any("字节数与 registry 不一致" in error for error in docs_check.ERRORS)
    assert any("SHA-256 与 registry 不一致" in error for error in docs_check.ERRORS)


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


def test_h02_has_independent_offline_node_lab_gates() -> None:
    h02 = next(
        line
        for line in _read("docs/FEATURES.md").splitlines()
        if line.startswith("| H02 |")
    )

    assert "| passing |" in h02
    assert "make benchmark-node-lab-ai-off" in h02
    assert "make benchmark-node-lab-model" in h02
    assert "make test-node-lab-ui" in h02
    assert "未调用真实模型" in h02


def test_environment_examples_are_split_by_runtime() -> None:
    server_example = _read(".env.example")
    frontend_example = _read("frontend/.env.example")
    gitignore = _read(".gitignore")

    assert "VITE_" not in server_example
    assert "VITE_API_BASE_URL=" in frontend_example
    assert "VITE_GENERATION_REQUEST_TIMEOUT_MS=" in frontend_example
    assert "API_KEY=" not in frontend_example
    assert ".env.local" in gitignore
    assert ".env.*.local" in gitignore


def test_ci_harness_uses_locked_dependencies_and_disables_models() -> None:
    main_ci = _read(".github/workflows/unit-tests.yml")
    integration_ci = _read(".github/workflows/integration-tests.yml")

    assert "uv sync --locked" in main_ci
    assert 'UV_LOCKED: "1"' in main_ci
    assert "npm ci --prefix frontend" in main_ci
    assert "make check" in main_ci
    assert "uv run mypy --strict src backend" in main_ci
    assert '"3.10"' in main_ci
    assert "uv sync --locked" in integration_ci
    assert 'SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED: "false"' in integration_ci
    assert "API_KEY" not in integration_ci
    assert "--allow-model-calls" not in integration_ci


def test_docs_check_rejects_unlocked_or_model_enabled_ci(
    tmp_path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_ci_harness")
    workflow_root = tmp_path / ".github/workflows"
    workflow_root.mkdir(parents=True)
    (workflow_root / "unit-tests.yml").write_text(
        "run: uv pip install -r pyproject.toml\nrun: make check\n",
        encoding="utf-8",
    )
    (workflow_root / "integration-tests.yml").write_text(
        "env:\n  OPENAI_API_KEY: secret\nrun: uv run pytest\n",
        encoding="utf-8",
    )
    (workflow_root / "png-to-shader-benchmark.yml").write_text(
        "run: uv run python benchmark.py\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_ci_harness()

    assert any("uv sync --locked" in error for error in docs_check.ERRORS)
    assert any("OPENAI_API_KEY" in error for error in docs_check.ERRORS)
    assert any("pyproject.toml" in error for error in docs_check.ERRORS)


def test_docs_check_rejects_private_shaderforge_imports(
    tmp_path, monkeypatch
) -> None:
    docs_check = _load_docs_check("docs_check_shaderforge_public_boundary")
    agent_root = tmp_path / "src/agent"
    backend_root = tmp_path / "backend"
    agent_root.mkdir(parents=True)
    backend_root.mkdir()
    (agent_root / "bad.py").write_text(
        "from shaderforge.validation.shader_validator import validate_shader\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(docs_check, "ROOT", tmp_path)
    docs_check.ERRORS.clear()

    docs_check._check_shaderforge_public_boundary()

    assert any(
        "shaderforge.validation.shader_validator" in error
        for error in docs_check.ERRORS
    )


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
        "会话结束前原地更新 `PROGRESS.md`",
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


def test_agent_architecture_docs_capture_harness_safety_boundaries() -> None:
    graph_architecture = _read("src/agent/app/graphs/ARCHITECTURE.md")
    lab_architecture = _read("src/agent/app/lab/ARCHITECTURE.md")
    state_architecture = _read("src/agent/app/states/ARCHITECTURE.md")

    assert "失败的 deterministic measurement seed" in graph_architecture
    assert "不触发模型 compile repair" in graph_architecture
    assert "route_deciders() -> agent.app.lab.runner -> agent.app.lab.adapters" in (
        lab_architecture
    )
    assert "-X-> agent.app.graphs / production routing" in lab_architecture
    assert "模型 reasoning 原文不进入 State" in state_architecture
    for field_name in ("quality_preset", "measurement_seed_attempted", "cancelled"):
        assert f"`{field_name}`" in state_architecture


def test_agent_harness_docs_separate_product_diagnostics_and_store_boundaries() -> None:
    readme = _read("src/agent/README.md")
    agent_architecture = _read("src/agent/ARCHITECTURE.md")
    app_architecture = _read("src/agent/app/ARCHITECTURE.md")
    service_architecture = _read("src/agent/app/services/ARCHITECTURE.md")
    shaderforge_architecture = _read("src/shaderforge/ARCHITECTURE.md")

    assert "产品 service：`agent.app.services.png_to_shader_v1`" in readme
    assert "诊断 Harness service：`agent.app.services.node_lab`" in readme
    assert "SHADERGEN_NODE_LAB_ENABLED=true" in readme
    for architecture_path in (
        "src/agent/app/config/ARCHITECTURE.md",
        "src/agent/app/lab/ARCHITECTURE.md",
        "src/agent/app/nodes/png_to_shader_v1/ARCHITECTURE.md",
    ):
        assert architecture_path in readme
        assert architecture_path in agent_architecture
    assert "## Graph 可视化完成定义" not in readme
    assert "src/agent/app/graphs/ARCHITECTURE.md" in readme

    assert "20 个 descriptor" in service_architecture
    assert "15 个非模型节点" in service_architecture
    assert "五个模型节点" in service_architecture
    assert "`DeterministicNodeExecutor`" in service_architecture
    assert "`ModelRoleExecutor`" in service_architecture

    for architecture in (agent_architecture, app_architecture, service_architecture):
        assert "`LocalArtifactStore`" in architecture
        assert "`RunArtifactStore`" in architecture
        assert "`BaseStore`" in architecture
        assert "`NodeLabStore`" in architecture
    assert "`shaderforge.public`" in app_architecture
    assert "typed 子包公共根" in app_architecture
    assert "`shaderforge.public`" in shaderforge_architecture
    assert "typed 子包公共根" in shaderforge_architecture
