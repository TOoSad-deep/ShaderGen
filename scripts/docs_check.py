"""检查仓库 harness 文档和架构边界."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _require(condition: bool, message: str) -> None:
    if not condition:
        ERRORS.append(message)


def _feature_row(feature_id: str) -> str:
    for line in _read("docs/FEATURES.md").splitlines():
        if line.startswith(f"| {feature_id} |"):
            return line
    ERRORS.append(f"docs/FEATURES.md 缺少 {feature_id} 行。")
    return ""


def _check_feature_state_machine() -> None:
    rows = [
        line
        for line in _read("docs/FEATURES.md").splitlines()
        if line.startswith("| ") and not line.startswith("|---")
    ]
    active_rows = [line for line in rows if "| active |" in line]
    _require(
        len(active_rows) <= 1, "docs/FEATURES.md 同一时间最多只能有一个 active 功能。"
    )

    h01 = _feature_row("H01")
    _require("单元测试通过" in h01, "H01 evidence 需要记录单元测试通过。")
    _require("2 个 graph" in h01, "H01 evidence 需要反映当前 LangGraph 图数量。")
    _require(
        "25 个单元测试" not in h01, "H01 evidence 不应硬编码易过期的 25 个单元测试。"
    )
    _require("20 个单元测试" not in h01, "H01 evidence 仍包含过时的 20 个单元测试。")
    _require("8 个单元测试" not in h01, "H01 evidence 仍包含过时的 8 个单元测试。")
    _require("1 个 graph" not in h01, "H01 evidence 仍包含过时的 1 个 graph。")

    f06 = _feature_row("F06")
    f07 = _feature_row("F07")
    _require("Agent/后端在线 Review" in f06, "F06 需要限定为 Agent/后端 Review 能力。")
    _require("| passing |" in f06, "F06 当前应保持 passing。")
    _require("单元测试通过" in f06, "F06 evidence 需要记录单元测试通过。")
    _require(
        "单元测试 25 个通过" not in f06,
        "F06 evidence 不应硬编码易过期的 25 个单元测试。",
    )
    _require(
        "单元测试 20 个通过" not in f06, "F06 evidence 仍包含过时的 20 个单元测试。"
    )
    _require("浏览器端 Review 闭环" in f07, "F07 需要承载浏览器端 Review 闭环。")
    _require(
        "canvas 截图 -> review API -> UI 展示" in f07,
        "F07 需要写明 canvas 截图 -> review API -> UI 展示。",
    )
    _require("Playwright" in f07, "F07 evidence 需要说明 Playwright 或等价浏览器检查。")
    _require("| not_started |" in f07, "F07 当前应是 not_started。")


def _check_agent_architecture_docs() -> None:
    app_dir = ROOT / "src/agent/app"
    for child in sorted(app_dir.iterdir()):
        if child.is_dir() and not child.name.startswith("__"):
            _require(
                (child / "ARCHITECTURE.md").exists(),
                f"{child.relative_to(ROOT)} 缺少 ARCHITECTURE.md。",
            )


def _graph_endpoint(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in {"START", "END"}:
        return node.id
    return None


def _graph_requirements(
    source: str,
) -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    tree = ast.parse(source)
    nodes: set[str] = set()
    direct_edges: set[tuple[str, str]] = set()
    conditional_edges: set[tuple[str, str, str]] = set()

    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Attribute):
            continue
        if call.func.attr == "add_node" and call.args:
            name = _graph_endpoint(call.args[0])
            if name:
                nodes.add(name)
        elif call.func.attr == "add_edge" and len(call.args) >= 2:
            source_name = _graph_endpoint(call.args[0])
            target_name = _graph_endpoint(call.args[1])
            if source_name and target_name:
                direct_edges.add((source_name, target_name))
        elif call.func.attr == "add_conditional_edges" and len(call.args) >= 3:
            source_name = _graph_endpoint(call.args[0])
            path_map = call.args[2]
            if not source_name or not isinstance(path_map, ast.Dict):
                continue
            for key, value in zip(path_map.keys, path_map.values, strict=True):
                if key is None:
                    continue
                route = _graph_endpoint(key)
                target_name = _graph_endpoint(value)
                if route and target_name:
                    conditional_edges.add((source_name, route, target_name))

    return nodes, direct_edges, conditional_edges


def _graph_diagram_section(architecture: str, stem: str) -> str:
    start = f"<!-- graph-diagram:{stem}:start -->"
    end = f"<!-- graph-diagram:{stem}:end -->"
    if start not in architecture or end not in architecture:
        return ""
    return architecture.split(start, 1)[1].split(end, 1)[0]


def _check_graph_visualizations() -> None:
    architecture = _read("src/agent/app/graphs/ARCHITECTURE.md")
    graph_root = ROOT / "src/agent/app/graphs"

    for path in sorted(graph_root.glob("*_graph.py")):
        relative_path = str(path.relative_to(ROOT))
        source = _read(relative_path)
        _require("# 图（" in source, f"{relative_path} 缺少 Builder 上方的 ASCII 图。")

        diagram = _graph_diagram_section(architecture, path.stem)
        _require(
            bool(diagram),
            f"graphs/ARCHITECTURE.md 缺少 {path.stem} 的 Mermaid 区块。",
        )
        if not diagram:
            continue

        nodes, direct_edges, conditional_edges = _graph_requirements(source)
        for node in sorted(nodes):
            _require(
                node in diagram,
                f"{path.stem} Mermaid 缺少节点 {node}。",
            )
        for source_name, target_name in sorted(direct_edges):
            edge = f"{source_name} --> {target_name}"
            _require(edge in diagram, f"{path.stem} Mermaid 缺少直接边 {edge}。")
        for source_name, route, target_name in sorted(conditional_edges):
            edge = f"{source_name} -. {route} .-> {target_name}"
            _require(edge in diagram, f"{path.stem} Mermaid 缺少条件边 {edge}。")


def _check_agent_readme_harness_router() -> None:
    readme = _read("src/agent/README.md")
    for heading in (
        "## 当前状态",
        "## 开始前",
        "## Agent 改动门禁",
        "## 完成交接",
        "## 按需阅读",
    ):
        _require(heading in readme, f"src/agent/README.md 缺少 {heading}。")

    for required_text in (
        "当前 active 功能以 `docs/FEATURES.md` 为准",
        "当前进度和下一步以 `PROGRESS.md` 为准",
        "`make docs-check`",
        "`uv run pytest tests/unit_tests`",
        "`uv run langgraph validate`",
        "会话结束前更新 `PROGRESS.md`",
    ):
        _require(
            required_text in readme,
            f"src/agent/README.md 缺少 harness 入口内容：{required_text}",
        )


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
            modules.extend(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
    return modules


def _check_agent_service_boundary() -> None:
    modules = _imported_modules(ROOT / "src/agent/app/services/shader_generation.py")
    forbidden = ("agent.app.nodes", "agent.app.llms")
    violations = [module for module in modules if module.startswith(forbidden)]
    _require(
        not violations,
        "agent.app.services.shader_generation 不应 import nodes/llms 内部模块："
        + ", ".join(violations),
    )


def _check_backend_agent_boundary() -> None:
    forbidden = (
        "agent.app.contracts",
        "agent.app.graphs",
        "agent.app.llms",
        "agent.app.messages",
        "agent.app.nodes",
        "agent.app.prompts",
        "langchain_core",
    )
    violations: list[str] = []
    for path in sorted((ROOT / "backend").rglob("*.py")):
        for module in _imported_modules(path):
            if module.startswith(forbidden):
                violations.append(f"{path.relative_to(ROOT)} imports {module}")
    _require(
        not violations,
        "后端只能通过 agent.app.services.* 调用 Agent：\n" + "\n".join(violations),
    )


def _main() -> int:
    _check_feature_state_machine()
    _check_agent_architecture_docs()
    _check_graph_visualizations()
    _check_agent_readme_harness_router()
    _check_agent_service_boundary()
    _check_backend_agent_boundary()

    if ERRORS:
        sys.stdout.write("docs-check failed:\n")
        for error in ERRORS:
            sys.stdout.write(f"- {error}\n")
        return 1

    sys.stdout.write("docs-check passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
