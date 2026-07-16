"""检查仓库 harness 文档和架构边界."""

from __future__ import annotations

import ast
import json
import re
import sys
from hashlib import sha256
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
    "## 最近重要变更",
    "## 历史索引",
    "## 维护规则",
)
MERMAID_DIRECT_EDGE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+-->\s+([A-Za-z_][A-Za-z0-9_]*)"
)
MERMAID_CONDITIONAL_EDGE = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+-\.\s+([^\s]+)\s+\.->\s+"
    r"([A-Za-z_][A-Za-z0-9_]*)"
)
MERMAID_NODE_DECLARATION = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\[|\()"
)
SHADERFORGE_PUBLIC_IMPORT_ROOTS = frozenset(
    {
        "shaderforge",
        "shaderforge.public",
        "shaderforge.analysis",
        "shaderforge.benchmark",
        "shaderforge.contracts",
        "shaderforge.evaluation",
        "shaderforge.generation",
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
    root_markdown = {
        path.name for path in ROOT.glob("*.md") if path.is_file()
    }
    unclassified = sorted(root_markdown - ROOT_MARKDOWN_ALLOWLIST)
    _require(
        not unclassified,
        "根目录出现未分类 Markdown："
        + ", ".join(unclassified)
        + "。实时入口需加入显式白名单；历史总结需移入 "
        "docs/progress/archive/ 并在首屏标注非当前事实。",
    )


def _registered_graph_count() -> int:
    """从 LangGraph 注册表读取当前对外图数量，避免文档检查固化旧数字."""
    try:
        value = json.loads(_read("langgraph.json"))
    except (json.JSONDecodeError, OSError) as exc:
        ERRORS.append(f"无法读取 langgraph.json：{type(exc).__name__}。")
        return 0
    graphs = value.get("graphs") if isinstance(value, dict) else None
    if not isinstance(graphs, dict) or not graphs:
        ERRORS.append("langgraph.json 的 graphs 必须是非空 object。")
        return 0
    return len(graphs)


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

    h01 = _feature_row("H01")
    _require("单元测试通过" in h01, "H01 evidence 需要记录单元测试通过。")
    graph_count = _registered_graph_count()
    if graph_count:
        expected_graph_evidence = f"{graph_count} 个 graph"
        _require(
            expected_graph_evidence in h01,
            "H01 evidence 需要反映 langgraph.json 当前注册的 "
            f"{expected_graph_evidence}。",
        )
    _require(
        "25 个单元测试" not in h01, "H01 evidence 不应硬编码易过期的 25 个单元测试。"
    )
    _require("20 个单元测试" not in h01, "H01 evidence 仍包含过时的 20 个单元测试。")
    _require("8 个单元测试" not in h01, "H01 evidence 仍包含过时的 8 个单元测试。")

    h02 = _feature_row("H02")
    for command in (
        "make benchmark-node-lab-ai-off",
        "make benchmark-node-lab-model",
        "make test-node-lab-ui",
    ):
        _require(command in h02, f"H02 验证缺少 {command}。")
    _require("| passing |" in h02, "H02 三项 Harness 门禁通过后必须保持 passing。")
    _require("未调用真实模型" in h02, "H02 evidence 必须明确未调用真实模型。")

    f09 = _feature_row("F09")
    _require("PNG" in f09 and "current_best" in f09, "F09 需要描述 V1 主链路。")
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
        "docs/progress/archive/" in progress,
        "PROGRESS.md 必须提供 docs/progress/archive/ 历史索引。",
    )
    _require(
        "docs/evidence/registry.json" in progress,
        "PROGRESS.md 必须链接版本化验收证据注册表。",
    )

    recent_changes = ""
    if "## 最近重要变更" in progress:
        recent_changes = progress.split("## 最近重要变更", 1)[1].split("\n## ", 1)[0]
    change_entries = [
        line for line in recent_changes.splitlines() if line.startswith("- ")
    ]
    _require(
        len(change_entries) <= 5,
        "PROGRESS.md 的最近重要变更最多保留 5 条；"
        f"当前为 {len(change_entries)} 条。",
    )

    archive_paths = sorted((ROOT / "docs/progress/archive").glob("*.md"))
    _require(bool(archive_paths), "docs/progress/archive/ 至少需要一个历史快照。")
    for path in archive_paths:
        archive = _read(str(path.relative_to(ROOT)))
        archive_preamble = "\n".join(archive.splitlines()[:12])
        _require(
            "历史" in archive_preamble
            and (
                "不代表当前" in archive_preamble
                or "非当前事实" in archive_preamble
            ),
            f"{path.relative_to(ROOT)} 必须在首屏明确标注为历史且非当前事实。",
        )


def _check_evidence_registry() -> None:
    registry_path = ROOT / "docs/evidence/registry.json"
    _require(registry_path.is_file(), "缺少 docs/evidence/registry.json。")
    if not registry_path.is_file():
        return
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        ERRORS.append(f"docs/evidence/registry.json 不是合法 JSON：{exc.msg}。")
        return

    _require(
        registry.get("schema_version") == 1,
        "docs/evidence/registry.json 只接受 schema_version=1。",
    )
    entries = registry.get("entries")
    _require(isinstance(entries, list) and bool(entries), "证据注册表必须非空。")
    if not isinstance(entries, list):
        return

    evidence_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            ERRORS.append("证据注册表 entry 必须是 object。")
            continue
        evidence_id = entry.get("evidence_id")
        _require(
            isinstance(evidence_id, str) and bool(evidence_id.strip()),
            "证据注册表 entry 缺少 evidence_id。",
        )
        if isinstance(evidence_id, str):
            _require(
                evidence_id not in evidence_ids,
                f"证据注册表 evidence_id 重复：{evidence_id}。",
            )
            evidence_ids.add(evidence_id)
        durability = entry.get("durability_status")
        _require(
            durability in {"durable", "partial", "missing"},
            f"{evidence_id} 的 durability_status 无效。",
        )
        artifacts = entry.get("artifacts")
        _require(isinstance(artifacts, list), f"{evidence_id} 缺少 artifacts。")
        if not isinstance(artifacts, list):
            continue
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                ERRORS.append(f"{evidence_id} 的 artifact 必须是 object。")
                continue
            path_value = artifact.get("path")
            availability = artifact.get("availability")
            expected_size = artifact.get("size_bytes")
            expected_sha = artifact.get("sha256")
            _require(
                isinstance(path_value, str) and bool(path_value),
                f"{evidence_id} 的 artifact 缺少 path。",
            )
            _require(
                availability in {"git", "git_lfs", "release", "object_store", "local_ignored"},
                f"{evidence_id} 的 artifact availability 无效。",
            )
            _require(
                isinstance(expected_size, int)
                and not isinstance(expected_size, bool)
                and expected_size > 0,
                f"{evidence_id} 的 artifact size_bytes 必须是正整数。",
            )
            _require(
                isinstance(expected_sha, str)
                and bool(re.fullmatch(r"[0-9a-f]{64}", expected_sha)),
                f"{evidence_id} 的 artifact sha256 无效。",
            )
            if not isinstance(path_value, str):
                continue
            artifact_path = ROOT / path_value
            must_exist = availability in {"git", "git_lfs"}
            _require(
                artifact_path.is_file() or not must_exist,
                f"{evidence_id} 的持久 Artifact 不存在：{path_value}。",
            )
            if not artifact_path.is_file():
                continue
            payload = artifact_path.read_bytes()
            _require(
                len(payload) == expected_size,
                f"{evidence_id} 的 {path_value} 字节数与 registry 不一致。",
            )
            _require(
                sha256(payload).hexdigest() == expected_sha,
                f"{evidence_id} 的 {path_value} SHA-256 与 registry 不一致。",
            )


def _check_agent_architecture_docs() -> None:
    app_dir = ROOT / "src/agent/app"
    for child in sorted(app_dir.iterdir()):
        if child.is_dir() and not child.name.startswith("__"):
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
        "langgraph.json 注册了非 *_graph.py 入口："
        + _format_items(set(extra))
        + "。",
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
        expected_nodes = nodes | {
            endpoint for edge in direct_edges for endpoint in edge
        } | {
            endpoint
            for source_name, _route, target_name in conditional_edges
            for endpoint in (source_name, target_name)
        }
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
    architecture = _read("src/agent/ARCHITECTURE.md")
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
        "会话结束前原地更新 `PROGRESS.md`",
    ):
        _require(
            required_text in readme,
            f"src/agent/README.md 缺少 harness 入口内容：{required_text}",
        )

    app_architectures = sorted(
        str(path.relative_to(ROOT))
        for path in (ROOT / "src/agent/app").glob("*/ARCHITECTURE.md")
    )
    for relative_path in app_architectures:
        _require(
            relative_path in readme,
            f"src/agent/README.md 导航缺少 {relative_path}。",
        )
        _require(
            relative_path in architecture,
            f"src/agent/ARCHITECTURE.md 索引缺少 {relative_path}。",
        )
    v1_nodes_architecture = (
        "src/agent/app/nodes/png_to_shader_v1/ARCHITECTURE.md"
    )
    _require(
        v1_nodes_architecture in readme,
        "src/agent/README.md 导航缺少 V1 Node 子架构。",
    )


def _live_harness_markdown_paths() -> list[Path]:
    paths = [ROOT / name for name in sorted(ROOT_MARKDOWN_ALLOWLIST)]
    paths.extend((ROOT / "backend").rglob("*.md"))
    paths.extend((ROOT / "benchmarks").rglob("*.md"))
    paths.extend(
        path
        for path in (ROOT / "docs").glob("*.md")
        if path.name != "DECISIONS.md"
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
        if value in REPOSITORY_ROOT_FILES or value.startswith(
            REPOSITORY_PATH_PREFIXES
        ):
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
                f"{relative_markdown} 引用了不存在的仓库路径："
                f"{documented_path}。",
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
    for key in sorted(server_keys):
        _require(f"{key}=" in root_readme, f"README.md 配置清单缺少 {key}。")
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
    _require(
        'SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED: "false"' in integration_ci,
        "普通 Integration workflow 必须显式关闭 Node Lab 真实模型路径。",
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
    forbidden = ("agent.app.nodes", "agent.app.llms")
    path = ROOT / "src/agent/app/services/png_to_shader_v1.py"
    violations = [
        module for module in _imported_modules(path) if module.startswith(forbidden)
    ]
    _require(
        not violations,
        "agent.app.services.png_to_shader_v1 不应 import nodes/llms 内部模块："
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
    _check_evidence_registry()
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
