"""生成不暴露 initial/final 左右位置的人工盲评包."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

BLIND_REVIEW_EVIDENCE_SCHEMA = 1
BLIND_REVIEW_EVIDENCE_MANIFEST = "blind-review/evidence-manifest.json"
BLIND_REVIEW_ASSIGNMENTS = "blind-review/assignments.private.json"
BLIND_REVIEW_REVIEWER_ROOT = "blind-review/reviewer"
_HASH_ALGORITHM = "sha256"


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _write_frozen(path: Path, data: bytes) -> None:
    """只补齐缺失文件；已有冻结证据不一致时拒绝覆盖."""
    if path.is_file():
        if path.read_bytes() != data:
            raise ValueError(f"冻结的盲评证据已存在且内容不一致：{path.name}")
        return
    if path.exists():
        raise ValueError(f"盲评证据路径不是普通文件：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _relative_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("盲评证据路径越过 suite 输出目录。") from exc


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} 不是可读 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} 必须是 JSON 对象。")
    return value


def build_blind_assignments(
    suite_run_id: str,
    case_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """用稳定 hash 随机化 A/B 身份，并把映射留在私有证据文件."""
    items: list[dict[str, str]] = []
    for case in case_results:
        case_id = str(case.get("case_id", "")).strip()
        ai_on = case.get("ai_on")
        if not case_id or not isinstance(ai_on, Mapping):
            continue
        initial_path = str(ai_on.get("initial_render_path", ""))
        final_path = str(ai_on.get("final_render_path", ""))
        if not initial_path or not final_path:
            continue
        final_on_a = sha256(f"{suite_run_id}:{case_id}".encode()).digest()[0] % 2 == 0
        items.append(
            {
                "case_id": case_id,
                "a_role": "final" if final_on_a else "initial",
                "b_role": "initial" if final_on_a else "final",
                "initial_render_path": initial_path,
                "final_render_path": final_path,
            }
        )
    return {
        "schema_version": 1,
        "suite_run_id": suite_run_id,
        "items": items,
    }


def _source_path(suite_root: Path, relative_path: str) -> Path:
    candidate = (suite_root / relative_path).resolve()
    if not candidate.is_relative_to(suite_root.resolve()):
        raise ValueError("盲评图片路径越过 suite 输出目录。")
    return candidate


def _safe_case_id(value: Any) -> str:
    case_id = str(value).strip()
    if not case_id or case_id in {".", ".."} or "/" in case_id or "\\" in case_id:
        raise ValueError("盲评 case_id 不能用于安全文件名。")
    return case_id


def _public_items(assignments: Mapping[str, Any]) -> list[dict[str, str]]:
    raw_items = assignments.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("assignments.items 必须为数组。")
    public_items: list[dict[str, str]] = []
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("assignments.items 的每一项都必须是对象。")
        case_id = _safe_case_id(raw.get("case_id"))
        public_items.append(
            {
                "case_id": case_id,
                "reference_image": f"assets/{case_id}-reference.png",
                "a_image": f"assets/{case_id}-a.png",
                "b_image": f"assets/{case_id}-b.png",
            }
        )
    return public_items


def _review_template_v1(
    suite_run_id: str,
    public_items: Sequence[Mapping[str, str]],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "suite_run_id": suite_run_id,
        "reviewer": "human-reviewer",
        "items": [
            {"case_id": item["case_id"], "choice": "A|B|TIE"} for item in public_items
        ],
    }


def _expected_evidence_paths(
    assignments: Mapping[str, Any],
) -> dict[str, str]:
    """从已校验 assignment 推导 manifest 必须完整覆盖的文件集合."""
    expected = {
        BLIND_REVIEW_ASSIGNMENTS: "private_assignment",
        f"{BLIND_REVIEW_REVIEWER_ROOT}/index.html": "reviewer_index",
        f"{BLIND_REVIEW_REVIEWER_ROOT}/human-review.template.json": (
            "reviewer_template"
        ),
    }
    raw_items = assignments.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("assignments.items 必须为数组。")
    for raw in raw_items:
        if not isinstance(raw, Mapping):
            raise ValueError("assignments.items 的每一项都必须是对象。")
        case_id = _safe_case_id(raw.get("case_id"))
        for key in ("initial_render_path", "final_render_path"):
            value = raw.get(key)
            if not isinstance(value, str) or not value:
                raise ValueError(f"assignments 缺少 {key}：{case_id}")
            expected[value] = "source"
        expected[f"cases/{case_id}/reference.png"] = "source"
        for suffix in ("a", "b", "reference"):
            expected[f"{BLIND_REVIEW_REVIEWER_ROOT}/assets/{case_id}-{suffix}.png"] = (
                "reviewer_asset"
            )
    return expected


def _manifest_entry(root: Path, relative_path: str, kind: str) -> dict[str, Any]:
    path = _source_path(root, relative_path)
    if not path.is_file():
        raise ValueError(f"盲评证据文件不存在：{relative_path}")
    data = path.read_bytes()
    return {
        "path": relative_path,
        "kind": kind,
        "byte_size": len(data),
        "sha256": sha256(data).hexdigest(),
    }


def _review_html_v1(items: list[dict[str, str]], suite_run_id: str) -> str:
    payload = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>PNG-to-Shader 人工盲评</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f3f6fb; color: #172033; }}
    main {{ width: min(1160px, calc(100% - 32px)); margin: 28px auto 80px; }}
    header, article {{ background: #fff; border: 1px solid #dbe2ef; border-radius: 14px; box-shadow: 0 8px 24px #1f3b6d12; }}
    header {{ padding: 22px 24px; position: sticky; top: 12px; z-index: 2; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; }}
    p {{ color: #536179; }}
    .meta {{ display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }}
    input {{ border: 1px solid #bcc8db; border-radius: 8px; padding: 9px 11px; }}
    #progress {{ font-weight: 700; color: #2456d6; }}
    article {{ margin-top: 18px; padding: 20px; }}
    .reference {{ width: min(520px, 100%); margin: 0 auto 16px; }}
    .pair {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    figure {{ margin: 0; border: 1px solid #dbe2ef; border-radius: 10px; overflow: hidden; background: #fafcff; }}
    figcaption {{ padding: 10px; font-weight: 800; text-align: center; }}
    img {{ display: block; width: 100%; aspect-ratio: 1; object-fit: contain; background: white; }}
    .choices {{ display: flex; gap: 10px; margin-top: 16px; flex-wrap: wrap; }}
    button {{ border: 1px solid #9eb0ca; background: white; border-radius: 9px; padding: 10px 16px; cursor: pointer; font-weight: 700; }}
    button.selected {{ color: white; background: #2456d6; border-color: #2456d6; }}
    #download {{ margin-left: auto; color: white; background: #16233a; }}
    #download:disabled {{ opacity: .45; cursor: not-allowed; }}
    @media (max-width: 720px) {{ .pair {{ grid-template-columns: 1fr; }} header {{ position: static; }} }}
  </style>
</head>
<body>
<main>
  <header>
    <h1>PNG-to-Shader 人工盲评</h1>
    <p>只比较视觉接近度，不检查源码。A/B 身份已随机化；请选择更接近参考目标的候选，相同则选择“平局”。</p>
    <div class="meta">
      <label>评审人 <input id="reviewer" placeholder="请输入姓名或代号"></label>
      <span id="progress">0 / {len(items)}</span>
      <button id="download" disabled>下载评审 JSON</button>
    </div>
  </header>
  <section id="cases"></section>
</main>
<script>
const suiteRunId = {json.dumps(suite_run_id)};
const items = {payload};
const choices = JSON.parse(localStorage.getItem(`shadergen-blind-${{suiteRunId}}`) || '{{}}');
const root = document.getElementById('cases');
function render() {{
  root.innerHTML = '';
  for (const item of items) {{
    const article = document.createElement('article');
    article.dataset.caseId = item.case_id;
    article.innerHTML = `<h2>${{item.case_id}}</h2><figure class="reference"><figcaption>参考目标</figcaption><img src="${{item.reference_image}}" alt="${{item.case_id}} reference"></figure><div class="pair"><figure><figcaption>候选 A</figcaption><img src="${{item.a_image}}" alt="${{item.case_id}} candidate A"></figure><figure><figcaption>候选 B</figcaption><img src="${{item.b_image}}" alt="${{item.case_id}} candidate B"></figure></div><div class="choices"><button data-choice="A">A 更接近</button><button data-choice="B">B 更接近</button><button data-choice="TIE">平局</button></div>`;
    for (const button of article.querySelectorAll('[data-choice]')) {{
      if (choices[item.case_id] === button.dataset.choice) button.classList.add('selected');
      button.addEventListener('click', () => {{ choices[item.case_id] = button.dataset.choice; localStorage.setItem(`shadergen-blind-${{suiteRunId}}`, JSON.stringify(choices)); render(); }});
    }}
    root.appendChild(article);
  }}
  const done = items.filter(item => choices[item.case_id]).length;
  document.getElementById('progress').textContent = `${{done}} / ${{items.length}}`;
  document.getElementById('download').disabled = done !== items.length;
}}
document.getElementById('download').addEventListener('click', () => {{
  const reviewer = document.getElementById('reviewer').value.trim() || 'anonymous-human';
  const result = {{schema_version: 1, suite_run_id: suiteRunId, reviewer, items: items.map(item => ({{case_id: item.case_id, choice: choices[item.case_id]}}))}};
  const link = document.createElement('a');
  link.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2) + '\\n'], {{type: 'application/json'}}));
  link.download = `human-review-${{suiteRunId}}.json`;
  link.click();
  URL.revokeObjectURL(link.href);
}});
render();
</script>
</body>
</html>
"""


def write_blind_review_package(
    suite_root: str | Path,
    suite_run_id: str,
    case_results: Sequence[Mapping[str, Any]],
) -> Path:
    """写入隔离的评审者包、私有映射及其冻结 SHA-256 manifest."""
    root = Path(suite_root).resolve()
    evidence_root = root / "blind-review"
    reviewer_root = root / BLIND_REVIEW_REVIEWER_ROOT
    assets = reviewer_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    assignments = build_blind_assignments(suite_run_id, case_results)
    public_items = _public_items(assignments)
    raw_items = assignments.get("items")
    assert isinstance(raw_items, list)
    for item in raw_items:
        assert isinstance(item, Mapping)
        case_id = _safe_case_id(item.get("case_id"))
        role_to_source = {
            "initial": item["initial_render_path"],
            "final": item["final_render_path"],
        }
        a_name = f"{case_id}-a.png"
        b_name = f"{case_id}-b.png"
        reference_name = f"{case_id}-reference.png"
        _write_frozen(
            assets / reference_name,
            _source_path(root, f"cases/{case_id}/reference.png").read_bytes(),
        )
        a_role = item.get("a_role")
        b_role = item.get("b_role")
        if a_role not in role_to_source or b_role not in role_to_source:
            raise ValueError(f"assignments A/B 角色非法：{case_id}")
        _write_frozen(
            assets / a_name,
            _source_path(root, str(role_to_source[a_role])).read_bytes(),
        )
        _write_frozen(
            assets / b_name,
            _source_path(root, str(role_to_source[b_role])).read_bytes(),
        )
    _write_frozen(
        root / BLIND_REVIEW_ASSIGNMENTS,
        _json_bytes(assignments),
    )
    template = _review_template_v1(suite_run_id, public_items)
    _write_frozen(
        reviewer_root / "human-review.template.json",
        _json_bytes(template),
    )
    index_path = reviewer_root / "index.html"
    _write_frozen(
        index_path,
        _review_html_v1(public_items, suite_run_id).encode("utf-8"),
    )

    leaked_assignments = tuple(
        path
        for path in reviewer_root.rglob("assignments.private.json")
        if path.is_file()
    )
    if leaked_assignments:
        raise ValueError("评审者目录不得包含 assignments.private.json。")

    expected = _expected_evidence_paths(assignments)
    entries = [
        _manifest_entry(root, relative_path, expected[relative_path])
        for relative_path in sorted(expected)
    ]
    manifest = {
        "schema_version": BLIND_REVIEW_EVIDENCE_SCHEMA,
        "suite_run_id": suite_run_id,
        "hash_algorithm": _HASH_ALGORITHM,
        "reviewer_root": BLIND_REVIEW_REVIEWER_ROOT,
        "assignment_path": BLIND_REVIEW_ASSIGNMENTS,
        "entries": entries,
    }
    _write_frozen(
        evidence_root / "evidence-manifest.json",
        _json_bytes(manifest),
    )
    verify_blind_review_package(root, expected_suite_run_id=suite_run_id)
    return index_path


def verify_blind_review_package(
    suite_root: str | Path,
    *,
    expected_suite_run_id: str,
) -> dict[str, Any]:
    """在读取人工选择前，严格复验新式盲评证据 manifest."""
    root = Path(suite_root).resolve()
    manifest_path = root / BLIND_REVIEW_EVIDENCE_MANIFEST
    if not manifest_path.is_file():
        raise ValueError("evaluate 找不到冻结的盲评 evidence manifest。")
    manifest = _load_json_object(manifest_path, label="blind review evidence manifest")
    if manifest.get("schema_version") != BLIND_REVIEW_EVIDENCE_SCHEMA:
        raise ValueError("blind review evidence manifest schema_version 不受支持。")
    if manifest.get("suite_run_id") != expected_suite_run_id:
        raise ValueError("blind review evidence manifest suite_run_id 不一致。")
    if manifest.get("hash_algorithm") != _HASH_ALGORITHM:
        raise ValueError("blind review evidence manifest hash_algorithm 非法。")
    if manifest.get("reviewer_root") != BLIND_REVIEW_REVIEWER_ROOT:
        raise ValueError("blind review evidence manifest reviewer_root 非法。")
    if manifest.get("assignment_path") != BLIND_REVIEW_ASSIGNMENTS:
        raise ValueError("blind review evidence manifest assignment_path 非法。")

    raw_entries = manifest.get("entries")
    if not isinstance(raw_entries, list):
        raise ValueError("blind review evidence manifest entries 必须为数组。")
    entries_by_path: dict[str, Mapping[str, Any]] = {}
    for raw in raw_entries:
        if not isinstance(raw, Mapping):
            raise ValueError("blind review evidence manifest entry 必须是对象。")
        relative_path = raw.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("blind review evidence manifest entry.path 非法。")
        if relative_path in entries_by_path:
            raise ValueError(
                f"blind review evidence manifest 路径重复：{relative_path}"
            )
        path = _source_path(root, relative_path)
        if _relative_path(root, path) != relative_path:
            raise ValueError(
                f"blind review evidence manifest 路径未规范化：{relative_path}"
            )
        digest = raw.get("sha256")
        byte_size = raw.get("byte_size")
        kind = raw.get("kind")
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError(f"blind review evidence SHA-256 非法：{relative_path}")
        if type(byte_size) is not int or byte_size < 0:
            raise ValueError(f"blind review evidence byte_size 非法：{relative_path}")
        if not isinstance(kind, str) or not kind:
            raise ValueError(f"blind review evidence kind 非法：{relative_path}")
        if not path.is_file():
            raise ValueError(f"冻结的盲评证据缺失：{relative_path}")
        data = path.read_bytes()
        if len(data) != byte_size or sha256(data).hexdigest() != digest:
            raise ValueError(f"冻结的盲评证据已漂移：{relative_path}")
        entries_by_path[relative_path] = raw

    reviewer_root = root / BLIND_REVIEW_REVIEWER_ROOT
    if not reviewer_root.is_dir():
        raise ValueError("评审者目录不存在。")
    reviewer_files = {
        _relative_path(root, path)
        for path in reviewer_root.rglob("*")
        if path.is_file()
    }
    if any(path.endswith("/assignments.private.json") for path in reviewer_files):
        raise ValueError("评审者目录不得包含 assignments.private.json。")

    assignment_path = root / BLIND_REVIEW_ASSIGNMENTS
    assignments = _load_json_object(assignment_path, label="blind review assignments")
    if assignments.get("schema_version") != 1:
        raise ValueError("blind review assignments schema_version 非法。")
    if assignments.get("suite_run_id") != expected_suite_run_id:
        raise ValueError("blind review assignments suite_run_id 不一致。")
    expected = _expected_evidence_paths(assignments)
    if set(entries_by_path) != set(expected):
        missing = sorted(set(expected) - set(entries_by_path))
        extra = sorted(set(entries_by_path) - set(expected))
        raise ValueError(
            f"blind review evidence manifest 文件集合不一致：missing={missing}, extra={extra}"
        )
    for relative_path, kind in expected.items():
        if entries_by_path[relative_path].get("kind") != kind:
            raise ValueError(f"blind review evidence kind 不一致：{relative_path}")
    expected_reviewer_files = {
        path for path in expected if path.startswith(f"{BLIND_REVIEW_REVIEWER_ROOT}/")
    }
    if reviewer_files != expected_reviewer_files:
        missing = sorted(expected_reviewer_files - reviewer_files)
        extra = sorted(reviewer_files - expected_reviewer_files)
        raise ValueError(f"评审者目录文件集合已漂移：missing={missing}, extra={extra}")
    return manifest


def verify_legacy_blind_review_package(
    suite_root: str | Path,
    suite_run_id: str,
    case_results: Sequence[Mapping[str, Any]],
) -> None:
    """兼容只读旧 run：重建稳定映射并逐字节复验旧式公开包."""
    root = Path(suite_root).resolve()
    review_root = root / "blind-review"
    assignment_path = root / BLIND_REVIEW_ASSIGNMENTS
    assignments = _load_json_object(assignment_path, label="legacy assignments")
    expected_assignments = build_blind_assignments(suite_run_id, case_results)
    if assignments != expected_assignments:
        raise ValueError("旧式盲评 assignments 与冻结 case 证据不一致。")
    public_items = _public_items(expected_assignments)
    raw_items = expected_assignments.get("items")
    assert isinstance(raw_items, list)
    expected_public_files = {"index.html", "human-review.template.json"}
    for item in raw_items:
        assert isinstance(item, Mapping)
        case_id = _safe_case_id(item.get("case_id"))
        role_to_source = {
            "initial": str(item.get("initial_render_path", "")),
            "final": str(item.get("final_render_path", "")),
        }
        for suffix, source in (
            ("reference", f"cases/{case_id}/reference.png"),
            ("a", role_to_source[str(item.get("a_role"))]),
            ("b", role_to_source[str(item.get("b_role"))]),
        ):
            relative_asset = f"assets/{case_id}-{suffix}.png"
            expected_public_files.add(relative_asset)
            asset_path = review_root / relative_asset
            if (
                not asset_path.is_file()
                or asset_path.read_bytes() != _source_path(root, source).read_bytes()
            ):
                raise ValueError(f"旧式盲评 asset 已漂移：{relative_asset}")
    expected_index = _review_html_v1(public_items, suite_run_id).encode("utf-8")
    index_path = review_root / "index.html"
    if not index_path.is_file() or index_path.read_bytes() != expected_index:
        raise ValueError("旧式盲评 index.html 已漂移。")
    template_path = review_root / "human-review.template.json"
    expected_template = _json_bytes(_review_template_v1(suite_run_id, public_items))
    if not template_path.is_file() or template_path.read_bytes() != expected_template:
        raise ValueError("旧式盲评 human-review.template.json 已漂移。")
    expected_asset_files = {
        path for path in expected_public_files if path.startswith("assets/")
    }
    actual_asset_files = {
        path.relative_to(review_root).as_posix()
        for path in (review_root / "assets").rglob("*")
        if path.is_file()
    }
    if actual_asset_files != expected_asset_files:
        raise ValueError("旧式盲评 assets 文件集合已漂移。")
