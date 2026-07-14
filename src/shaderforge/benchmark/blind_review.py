"""生成不暴露 initial/final 左右位置的人工盲评包."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any


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


def _review_html(items: list[dict[str, str]], suite_run_id: str) -> str:
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
    """复制 A/B 图片、写入私有映射和可下载结果的静态页面."""
    root = Path(suite_root).resolve()
    review_root = root / "blind-review"
    assets = review_root / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    assignments = build_blind_assignments(suite_run_id, case_results)
    public_items: list[dict[str, str]] = []
    for item in assignments["items"]:
        case_id = item["case_id"]
        role_to_source = {
            "initial": item["initial_render_path"],
            "final": item["final_render_path"],
        }
        a_name = f"{case_id}-a.png"
        b_name = f"{case_id}-b.png"
        reference_name = f"{case_id}-reference.png"
        shutil.copyfile(
            _source_path(root, f"cases/{case_id}/reference.png"),
            assets / reference_name,
        )
        shutil.copyfile(
            _source_path(root, role_to_source[item["a_role"]]),
            assets / a_name,
        )
        shutil.copyfile(
            _source_path(root, role_to_source[item["b_role"]]),
            assets / b_name,
        )
        public_items.append(
            {
                "case_id": case_id,
                "reference_image": f"assets/{reference_name}",
                "a_image": f"assets/{a_name}",
                "b_image": f"assets/{b_name}",
            }
        )
    (review_root / "assignments.private.json").write_text(
        json.dumps(assignments, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    template = {
        "schema_version": 1,
        "suite_run_id": suite_run_id,
        "reviewer": "human-reviewer",
        "items": [
            {"case_id": item["case_id"], "choice": "A|B|TIE"}
            for item in public_items
        ],
    }
    (review_root / "human-review.template.json").write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    index_path = review_root / "index.html"
    index_path.write_text(
        _review_html(public_items, suite_run_id),
        encoding="utf-8",
    )
    return index_path
