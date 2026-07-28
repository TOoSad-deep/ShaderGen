"""检查仓库 harness 文档和架构边界."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ERRORS: list[str] = []
ROOT_MARKDOWN_ALLOWLIST = frozenset({"AGENTS.md", "PROGRESS.md", "README.md"})
PROGRESS_MAX_BYTES = 20_000
PROGRESS_REQUIRED_HEADINGS = (
    "## 当前状态",
    "## 当前 active 功能",
    "## 下一步",
    "## 未解决缺口",
    "## 当前验证基线",
)
MERMAID_DIRECT_EDGE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+-->\s+([A-Za-z_][A-Za-z0-9_]*)"
)
MERMAID_CONDITIONAL_EDGE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+-\.\s+([^\s]+)\s+\.->\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
MERMAID_NODE_DECLARATION = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[|\()")
SHADERFORGE_PUBLIC_IMPORT_ROOTS = frozenset(
    {
        "shaderforge",
        "shaderforge.public",
        "shaderforge.contracts",
        "shaderforge.dsl",
        "shaderforge.evaluation",
        "shaderforge.generation",
        "shaderforge.optimization",
        "shaderforge.program_spec",
        "shaderforge.rendering",
        "shaderforge.store",
        "shaderforge.validation",
    }
)
REPOSITORY_PATH_PREFIXES = (
    "backend/",
    "benchmarks/",
    "docs/",
    "frontend/",
    "human_doc/",
    "scripts/",
    "src/",
    "tests/",
)
REPOSITORY_ROOT_FILES = frozenset(
    {
        ".env.example",
        ".gitignore",
        "AGENTS.md",
        "Makefile",
        "PROGRESS.md",
        "README.md",
        "langgraph.json",
        "pyproject.toml",
    }
)
ARCHIVED_DOCUMENT_GLOB = "docs/archive/**/*.md"
RETIRED_LIVE_DOCUMENT_DIRS = (
    "docs/superpowers",
    "docs/analysis",
    "docs/progress/archive",
    "human_doc",
)


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


def _check_root_markdown_classification() -> None:
    root_markdown = {path.name for path in ROOT.glob("*.md") if path.is_file()}
    unclassified = sorted(root_markdown - ROOT_MARKDOWN_ALLOWLIST)
    _require(
        not unclassified,
        "根目录出现未分类 Markdown："
        + ", ".join(unclassified)
        + "。实时入口需加入显式白名单；历史总结需移入 "
        "docs/archive/ 并在首屏标注归档状态。",
    )


def _registered_graphs() -> dict[str, str]:
    try:
        value = json.loads(_read("langgraph.json"))
    except (json.JSONDecodeError, OSError):
        return {}
    graphs = value.get("graphs") if isinstance(value, dict) else None
    if not isinstance(graphs, dict):
        return {}
    return {
        str(name): str(target)
        for name, target in graphs.items()
        if isinstance(name, str) and isinstance(target, str)
    }


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

    f09 = _feature_row("F09")
    _require(
        "PNG" in f09 and "scene_mvp" in f09,
        "F09 需要描述 scene_mvp 当前主链路。",
    )
    _require("| active |" in f09, "F09 在质量门禁通过前必须保持 active。")


def _check_progress_handoff() -> None:
    progress = _read("PROGRESS.md")
    progress_bytes = len(progress.encode("utf-8"))
    _require(
        progress_bytes <= PROGRESS_MAX_BYTES,
        "PROGRESS.md 必须保持有界当前交接页，UTF-8 体量不得超过 "
        f"{PROGRESS_MAX_BYTES:,} bytes；当前为 {progress_bytes:,} bytes。",
    )

    for heading in PROGRESS_REQUIRED_HEADINGS:
        _require(heading in progress, f"PROGRESS.md 缺少当前交接区块：{heading}。")

    _require(
        "不是逐会话追加日志" in progress,
        "PROGRESS.md 必须明确不是逐会话追加日志。",
    )
    _require(
        "docs/archive/" in progress,
        "PROGRESS.md 必须提供 docs/archive/ 统一历史索引。",
    )


def _check_document_authority() -> None:
    """确保历史材料退出实时文档面，并保持当前决策有界."""
    archive_paths = sorted(ROOT.glob(ARCHIVED_DOCUMENT_GLOB))
    _require(bool(archive_paths), "docs/archive/ 至少需要一个归档 Markdown。")
    for path in archive_paths:
        preamble = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
        _require(
            "> 归档状态：" in preamble,
            f"{path.relative_to(ROOT)} 必须在首屏声明“> 归档状态：”。",
        )

    for relative_dir in RETIRED_LIVE_DOCUMENT_DIRS:
        live_markdown = list((ROOT / relative_dir).rglob("*.md"))
        _require(
            not live_markdown,
            f"{relative_dir}/ 已退出实时文档面，Markdown 必须移入 docs/archive/。",
        )

    decisions = _read("docs/DECISIONS.md")
    index_rows = re.findall(
        r"^\| (D[0-9]{3}) \| (accepted|updated) \|",
        decisions,
        re.MULTILINE,
    )
    index_ids = [decision_id for decision_id, _status in index_rows]
    _require(
        len(index_ids) == len(set(index_ids)),
        "docs/DECISIONS.md 当前决策表存在重复编号。",
    )
    _require(
        len(index_ids) <= 25,
        f"docs/DECISIONS.md 当前决策最多 25 条；当前 {len(index_ids)} 条。",
    )
    required_current_decisions = {"D095", "D097", "D098", "D099", "D100", "D101"}
    _require(
        required_current_decisions.issubset(index_ids),
        "docs/DECISIONS.md 缺少当前 engine/process/归档决策："
        + ", ".join(sorted(required_current_decisions - set(index_ids)))
        + "。",
    )
    _require(
        "archive/2026-07/decisions/DECISIONS-through-D099.md" in decisions,
        "docs/DECISIONS.md 必须链接完整历史决策归档。",
    )

    root_readme = _read("README.md")
    for required_text in (
        "direct_glsl_layerplan_v1（默认）",
        "fresh shader_graph_v1 fallback",
        "`docs/archive/` 不参与默认开发上下文",
    ):
        _require(
            required_text in root_readme,
            f"README.md 缺少当前 direct-first/风险分级事实：{required_text}。",
        )

    architecture = _read("docs/ARCHITECTURE.md")
    for required_text in (
        "direct_default（无 policy 文件时的默认值）",
        "fresh ShaderGraph fallback child",
        "休眠能力只在用户明确发起对应任务时读取",
    ):
        _require(
            required_text in architecture,
            f"docs/ARCHITECTURE.md 缺少当前运行事实：{required_text}。",
        )

    backend_readme = _read("backend/README.md")
    for required_text in (
        "scene=null",
        "无授权 `direct_default`",
        "测试按根 `AGENTS.md` 选择",
    ):
        _require(
            required_text in backend_readme,
            f"backend/README.md 缺少当前响应或验证边界：{required_text}。",
        )


def _check_agent_architecture_docs() -> None:
    app_dir = ROOT / "src/agent/app"
    for child in sorted(app_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        has_substantive_content = any(
            path.is_file() and path.name not in {"__init__.py", "ARCHITECTURE.md"}
            for path in child.iterdir()
        )
        if has_substantive_content:
            _require(
                (child / "ARCHITECTURE.md").exists(),
                f"{child.relative_to(ROOT)} 缺少 ARCHITECTURE.md。",
            )

    shaderforge_dir = ROOT / "src/shaderforge"
    for child in sorted(shaderforge_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("__"):
            continue
        if any(path.suffix == ".py" for path in child.rglob("*.py")):
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


def _mermaid_requirements(
    diagram: str,
) -> tuple[set[str], set[tuple[str, str]], set[tuple[str, str, str]]]:
    nodes: set[str] = set()
    direct_edges: set[tuple[str, str]] = set()
    conditional_edges: set[tuple[str, str, str]] = set()
    for line in diagram.splitlines():
        if match := MERMAID_CONDITIONAL_EDGE.match(line):
            source, route, target = match.groups()
            nodes.update((source, target))
            conditional_edges.add((source, route, target))
            continue
        if match := MERMAID_DIRECT_EDGE.match(line):
            source, target = match.groups()
            nodes.update((source, target))
            direct_edges.add((source, target))
            continue
        if match := MERMAID_NODE_DECLARATION.match(line):
            nodes.add(match.group(1))
    return nodes, direct_edges, conditional_edges


def _format_items(items: set[object]) -> str:
    return ", ".join(str(item) for item in sorted(items, key=str))


def _check_langgraph_registration() -> None:
    graph_root = ROOT / "src/agent/app/graphs"
    source_paths = {
        str(path.relative_to(ROOT)) for path in graph_root.glob("*_graph.py")
    }
    registered = _registered_graphs()
    registered_paths: set[str] = set()
    for graph_name, target in registered.items():
        source_path, separator, export_name = target.partition(":")
        normalized = source_path.removeprefix("./")
        _require(
            bool(separator and export_name),
            f"langgraph.json 的 {graph_name} 必须使用 path.py:export 格式。",
        )
        _require(
            (ROOT / normalized).is_file(),
            f"langgraph.json 的 {graph_name} 指向不存在文件 {normalized}。",
        )
        registered_paths.add(normalized)

    missing = source_paths - registered_paths
    extra = registered_paths - source_paths
    _require(
        not missing,
        "存在未注册的 *_graph.py：" + _format_items(set(missing)) + "。",
    )
    _require(
        not extra,
        "langgraph.json 注册了非 *_graph.py 入口：" + _format_items(set(extra)) + "。",
    )


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
        diagram_nodes, diagram_direct, diagram_conditional = _mermaid_requirements(
            diagram
        )
        expected_nodes = (
            nodes
            | {endpoint for edge in direct_edges for endpoint in edge}
            | {
                endpoint
                for source_name, _route, target_name in conditional_edges
                for endpoint in (source_name, target_name)
            }
        )
        _require(
            diagram_nodes == expected_nodes,
            f"{path.stem} Mermaid 节点与代码不一致；"
            f"缺少={_format_items(set(expected_nodes - diagram_nodes)) or '无'}；"
            f"多余={_format_items(set(diagram_nodes - expected_nodes)) or '无'}。",
        )
        _require(
            diagram_direct == direct_edges,
            f"{path.stem} Mermaid 直接边与代码不一致；"
            f"缺少={_format_items(set(direct_edges - diagram_direct)) or '无'}；"
            f"多余={_format_items(set(diagram_direct - direct_edges)) or '无'}。",
        )
        _require(
            diagram_conditional == conditional_edges,
            f"{path.stem} Mermaid 条件边与代码不一致；"
            "缺少="
            f"{_format_items(set(conditional_edges - diagram_conditional)) or '无'}；"
            "多余="
            f"{_format_items(set(diagram_conditional - conditional_edges)) or '无'}。",
        )


def _check_agent_readme_harness_router() -> None:
    readme = _read("src/agent/README.md")
    for heading in (
        "## 开始前",
        "## 边界",
        "## 验证",
    ):
        _require(heading in readme, f"src/agent/README.md 缺少 {heading}。")

    for required_text in (
        "本次修改目录最近的 `ARCHITECTURE.md`",
        "不要预先遍历全部子模块文档",
        "make docs-check",
        "uv run langgraph validate",
        "全量检查和真实模型调用遵循根 `AGENTS.md`",
    ):
        _require(
            required_text in readme,
            f"src/agent/README.md 缺少 harness 入口内容：{required_text}",
        )


def _live_harness_markdown_paths() -> list[Path]:
    paths = [ROOT / name for name in sorted(ROOT_MARKDOWN_ALLOWLIST)]
    paths.extend((ROOT / "backend").rglob("*.md"))
    paths.extend((ROOT / "benchmarks").rglob("*.md"))
    paths.extend(
        path for path in (ROOT / "docs").glob("*.md") if path.name != "DECISIONS.md"
    )
    paths.append(ROOT / "frontend/README.md")
    paths.extend((ROOT / "src/agent").rglob("ARCHITECTURE.md"))
    paths.append(ROOT / "src/agent/README.md")
    paths.extend((ROOT / "src/shaderforge").rglob("ARCHITECTURE.md"))
    return sorted({path for path in paths if path.is_file()})


def _check_documented_commands() -> None:
    make_targets = {
        match.group(1)
        for match in re.finditer(
            r"^([A-Za-z0-9_.-]+)\s*:", _read("Makefile"), re.MULTILINE
        )
    }
    package = json.loads(_read("frontend/package.json"))
    scripts = package.get("scripts", {}) if isinstance(package, dict) else {}
    frontend_scripts = set(scripts) if isinstance(scripts, dict) else set()

    for path in _live_harness_markdown_paths():
        relative_path = path.relative_to(ROOT)
        content = path.read_text(encoding="utf-8")
        for target in set(re.findall(r"\bmake\s+([A-Za-z0-9_.-]+)", content)):
            _require(
                target in make_targets,
                f"{relative_path} 引用了不存在的 Make target：{target}。",
            )
        for script in set(
            re.findall(
                r"npm\s+--prefix\s+frontend\s+run\s+([A-Za-z0-9_:-]+)",
                content,
            )
        ):
            _require(
                script in frontend_scripts,
                f"{relative_path} 引用了不存在的 frontend npm script：{script}。",
            )


def _documented_repository_paths(content: str) -> set[str]:
    result: set[str] = set()
    for raw in re.findall(r"`([^`\n]+)`", content):
        value = raw.strip().removeprefix("./")
        if (
            not value
            or value.startswith(("http://", "https://", "/", "output/"))
            or value in {".env", ".env.local", "frontend/.env.local"}
            or any(character in value for character in ("<", ">", "{", "}", "|"))
        ):
            continue
        if ".py:" in value:
            value = value.split(":", 1)[0]
        if value in REPOSITORY_ROOT_FILES or value.startswith(REPOSITORY_PATH_PREFIXES):
            result.add(value)
    return result


def _check_documented_repository_paths() -> None:
    for markdown_path in _live_harness_markdown_paths():
        relative_markdown = markdown_path.relative_to(ROOT)
        content = markdown_path.read_text(encoding="utf-8")
        for documented_path in sorted(_documented_repository_paths(content)):
            if any(character in documented_path for character in ("*", "?", "[")):
                exists = any(ROOT.glob(documented_path)) or any(
                    markdown_path.parent.glob(documented_path)
                )
            else:
                relative_value = documented_path.rstrip("/")
                exists = (ROOT / relative_value).exists() or (
                    markdown_path.parent / relative_value
                ).exists()
            _require(
                exists,
                f"{relative_markdown} 引用了不存在的仓库路径：{documented_path}。",
            )


def _env_example_keys(path: str) -> set[str]:
    keys: set[str] = set()
    for line in _read(path).splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _value = stripped.split("=", 1)
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            keys.add(key)
    return keys


def _check_environment_documentation() -> None:
    server_keys = _env_example_keys(".env.example")
    frontend_keys = _env_example_keys("frontend/.env.example")
    root_readme = _read("README.md")
    frontend_readme = _read("frontend/README.md")

    _require(
        server_keys and not any(key.startswith("VITE_") for key in server_keys),
        "根 .env.example 只能保存服务端变量，不能包含 VITE_*。",
    )
    _require(
        frontend_keys and all(key.startswith("VITE_") for key in frontend_keys),
        "frontend/.env.example 只能包含 VITE_* 公开变量。",
    )
    _require(
        "[.env.example](.env.example)" in root_readme,
        "README.md 必须链接服务端 .env.example。",
    )
    for key in sorted(frontend_keys):
        _require(key in frontend_readme, f"frontend/README.md 缺少 {key}。")

    for line in _read(".env.example").splitlines():
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("#"):
            continue
        key, value = stripped.split("=", 1)
        if key.endswith("API_KEY"):
            _require(not value, f".env.example 的 {key} 必须留空，不能放伪密钥。")

    gitignore = _read(".gitignore").splitlines()
    _require(".env.local" in gitignore, ".gitignore 必须忽略 .env.local。")
    _require(".env.*.local" in gitignore, ".gitignore 必须忽略 .env.*.local。")


def _check_ci_harness() -> None:
    workflow_root = ROOT / ".github/workflows"
    workflow_paths = sorted(workflow_root.glob("*.yml"))
    _require(bool(workflow_paths), ".github/workflows/ 至少需要一个 CI workflow。")

    for path in workflow_paths:
        source = path.read_text(encoding="utf-8")
        relative_path = path.relative_to(ROOT)
        if "uv run" in source or "make " in source:
            _require(
                "uv sync --locked" in source,
                f"{relative_path} 必须从 uv.lock 执行 uv sync --locked。",
            )
            _require(
                'UV_LOCKED: "1"' in source,
                f"{relative_path} 必须让后续 uv run 拒绝隐式改写锁文件。",
            )
        _require(
            "uv pip install -r pyproject.toml" not in source,
            f"{relative_path} 不得绕过 uv.lock 从 pyproject.toml 临时解析依赖。",
        )

    main_ci = _read(".github/workflows/unit-tests.yml")
    for required_text in (
        'python-version: "3.12"',
        'node-version: "22"',
        "npm ci --prefix frontend",
        "make check",
        "uv run mypy --strict src backend",
        '"3.10"',
    ):
        _require(
            required_text in main_ci,
            "主 CI 必须覆盖最低 Python、锁定前端安装和完整 make check；"
            f"缺少 {required_text}。",
        )

    integration_ci = _read(".github/workflows/integration-tests.yml")
    for forbidden_text in (
        "ANTHROPIC_API_KEY",
        "DASHSCOPE_API_KEY",
        "OPENAI_API_KEY",
        "LANGSMITH_API_KEY",
        "--allow-model-calls",
    ):
        _require(
            forbidden_text not in integration_ci,
            "普通 Integration workflow 不得获得真实模型凭据或调用开关；"
            f"发现 {forbidden_text}。",
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
    forbidden = ("agent.app.llms",)
    path = ROOT / "src/agent/app/services/png_to_shader_min.py"
    violations = [
        module for module in _imported_modules(path) if module.startswith(forbidden)
    ]
    _require(
        not violations,
        "agent.app.services.png_to_shader_min 不应 import nodes/llms 内部模块："
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


def _check_shaderforge_public_boundary() -> None:
    violations: list[str] = []
    for source_root in (ROOT / "src/agent", ROOT / "backend"):
        for path in sorted(source_root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            modules: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    modules.append(node.module)
                elif isinstance(node, ast.Import):
                    modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith("shaderforge.") and module not in (
                    SHADERFORGE_PUBLIC_IMPORT_ROOTS
                ):
                    violations.append(f"{path.relative_to(ROOT)} imports {module}")
    _require(
        not violations,
        "Agent/Backend 只能从 shaderforge.public 或 typed 子包公共根导入：\n"
        + "\n".join(violations),
    )


def _main() -> int:
    _check_root_markdown_classification()
    _check_feature_state_machine()
    _check_progress_handoff()
    _check_document_authority()
    _check_agent_architecture_docs()
    _check_langgraph_registration()
    _check_graph_visualizations()
    _check_agent_readme_harness_router()
    _check_documented_commands()
    _check_documented_repository_paths()
    _check_environment_documentation()
    _check_ci_harness()
    _check_agent_service_boundary()
    _check_backend_agent_boundary()
    _check_shaderforge_public_boundary()

    if ERRORS:
        sys.stdout.write("docs-check failed:\n")
        for error in ERRORS:
            sys.stdout.write(f"- {error}\n")
        return 1

    sys.stdout.write("docs-check passed\n")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
