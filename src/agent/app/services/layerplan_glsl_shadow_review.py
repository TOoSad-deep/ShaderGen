"""LayerPlan shadow suite v2 的匿名人工盲评包与人工 gate.

公开 ``reviewer/`` 目录只含参考图、匿名 A/B render、``index.html`` 和
``review-template.json``。真实 sample/round/Arm 映射只存在于父目录的
``mapping.private.json``。创建、复验和评价都先复验原 suite；评价还必须
先完整复验盲评包，之后才读取人工提交，绝不自动生成偏好票。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any, cast
from uuid import uuid4

from agent.app.services.layerplan_glsl_shadow import verify_shadow_run
from agent.app.services.layerplan_glsl_shadow_suite import (
    CURRENT_GATE_SCHEMA_VERSION,
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ShadowSuiteGate,
    ShadowSuiteManifest,
    verify_shadow_suite_report,
)
from shaderforge.program_spec import canonical_json

REVIEW_PACKAGE_SCHEMA_VERSION = "layerplan_glsl_shadow_review_package_v1"
PRIVATE_MAPPING_SCHEMA_VERSION = "layerplan_glsl_shadow_review_mapping_v1"
REVIEW_TEMPLATE_SCHEMA_VERSION = "layerplan_glsl_shadow_review_template_v1"
HUMAN_REVIEW_SCHEMA_VERSION = "layerplan_glsl_shadow_human_review_v1"
HUMAN_EVALUATION_SCHEMA_VERSION = "layerplan_glsl_shadow_human_evaluation_v1"

_PACKAGE_MANIFEST = "package-manifest.json"
_PRIVATE_MAPPING = "mapping.private.json"
_REVIEWER_ROOT = "reviewer"
_SHA256 = frozenset("0123456789abcdef")


class ShadowReviewError(ValueError):
    """盲评证据、人工选择或 gate 违反 fail-closed 契约."""


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (canonical_json(dict(value)) + "\n").encode("utf-8")


def _digest(data: bytes) -> dict[str, Any]:
    return {"sha256": sha256(data).hexdigest(), "size_bytes": len(data)}


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ShadowReviewError("盲评文件路径必须是规范 POSIX 相对路径。")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise ShadowReviewError("盲评文件路径必须是规范 POSIX 相对路径。")
    return value


def _load_json_object_with_bytes(
    path: Path, *, label: str
) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise ShadowReviewError(f"{label} 缺失、不是普通文件或是 symlink。")
    try:
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShadowReviewError(f"{label} 不是可读 JSON object。") from exc
    if not isinstance(value, dict):
        raise ShadowReviewError(f"{label} 必须是 JSON object。")
    return cast(dict[str, Any], value), data


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    return _load_json_object_with_bytes(path, label=label)[0]


def _require_current_v2(
    manifest: ShadowSuiteManifest, gate: ShadowSuiteGate
) -> None:
    if (
        manifest.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION
        or gate.schema_version != CURRENT_GATE_SCHEMA_VERSION
    ):
        raise ShadowReviewError("人工盲评包只接受当前 LayerPlan shadow suite v2。")


def _suite_report_hash(payload: Mapping[str, Any]) -> str:
    value = payload.get("suite_report_sha256")
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise ShadowReviewError("suite_report_sha256 非法。")
    return value


def _current_best_render(
    run_dir: Path, run_payload: Mapping[str, Any], arm_id: str
) -> Path | None:
    arms = run_payload.get("arms")
    if not isinstance(arms, list):
        raise ShadowReviewError("shadow run 缺少 arms。")
    arm_matches = [
        arm
        for arm in arms
        if isinstance(arm, dict) and arm.get("arm_id") == arm_id
    ]
    if len(arm_matches) != 1:
        raise ShadowReviewError(f"Arm {arm_id} 摘要不唯一。")
    current_best = arm_matches[0].get("current_best")
    if current_best is None:
        return None
    if not isinstance(current_best, dict):
        raise ShadowReviewError(f"Arm {arm_id} current_best 摘要非法。")
    spec_sha256 = current_best.get("spec_sha256")
    candidates = arm_matches[0].get("candidates")
    if not isinstance(spec_sha256, str) or not isinstance(candidates, list):
        raise ShadowReviewError(f"Arm {arm_id} current_best 摘要非法。")
    selected = [
        item
        for item in candidates
        if isinstance(item, dict)
        and item.get("spec_sha256") == spec_sha256
        and item.get("is_current_best") is True
    ]
    if len(selected) != 1:
        raise ShadowReviewError(f"Arm {arm_id} current_best 候选不唯一。")
    files = run_payload.get("files")
    if not isinstance(files, dict):
        raise ShadowReviewError("shadow run 缺少已复验 files 映射。")
    spec_paths = [
        relative
        for relative in files
        if isinstance(relative, str)
        and relative.startswith(f"arms/{arm_id}/candidates/")
        and relative.endswith("/spec.json")
    ]
    spec_matches: list[Path] = []
    for relative in spec_paths:
        spec = _load_json_object(run_dir / relative, label="current_best spec")
        if spec.get("spec_sha256") == spec_sha256:
            spec_matches.append(run_dir / relative)
    if len(spec_matches) != 1:
        raise ShadowReviewError(f"Arm {arm_id} current_best render 目录不唯一。")
    render = spec_matches[0].with_name("render.png")
    if render.is_symlink() or not render.is_file():
        raise ShadowReviewError(f"Arm {arm_id} current_best render 缺失。")
    return render


def _source_items(
    suite_dir: Path,
    suite_payload: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    runs = suite_payload.get("runs")
    if not isinstance(runs, list):
        raise ShadowReviewError("suite 报告缺少 runs。")
    items: list[dict[str, Any]] = []
    unreviewable: list[dict[str, Any]] = []
    suite_hash = _suite_report_hash(suite_payload)
    for index, summary in enumerate(runs, start=1):
        if not isinstance(summary, dict):
            raise ShadowReviewError("suite run 摘要非法。")
        sample_id = summary.get("sample_id")
        round_index = summary.get("round_index")
        run_id = summary.get("run_id")
        if (
            not isinstance(sample_id, str)
            or isinstance(round_index, bool)
            or not isinstance(round_index, int)
            or not isinstance(run_id, str)
        ):
            raise ShadowReviewError("suite run 身份非法。")
        run_dir = suite_dir.parent / run_id
        run_payload = verify_shadow_run(run_dir)
        if run_payload.get("report_sha256") != summary.get("report_sha256"):
            raise ShadowReviewError("盲评读取的 run 与 suite report 绑定已漂移。")
        assignment_digest = sha256(
            f"{suite_hash}:{sample_id}:{round_index}".encode()
        ).digest()
        a_arm = "A" if assignment_digest[0] % 2 == 0 else "B"
        b_arm = "B" if a_arm == "A" else "A"
        render_a = _current_best_render(run_dir, run_payload, a_arm)
        render_b = _current_best_render(run_dir, run_payload, b_arm)
        if render_a is None or render_b is None:
            unreviewable.append(
                {
                    "schedule_index": index,
                    "status": "unreviewable",
                    "reason_code": "missing_paired_current_best",
                }
            )
            continue
        items.append(
            {
                "item_id": f"item-{len(items) + 1:03d}",
                "sample_id": sample_id,
                "round_index": round_index,
                "run_id": run_id,
                "reference": run_dir / "input" / "reference",
                "A": render_a,
                "B": render_b,
                "a_arm": a_arm,
                "b_arm": b_arm,
            }
        )
    return items, unreviewable


def _public_template(package_id: str, item_count: int) -> dict[str, Any]:
    return {
        "schema_version": REVIEW_TEMPLATE_SCHEMA_VERSION,
        "package_id": package_id,
        "reviewer": "",
        "items": [
            {"item_id": f"item-{index:03d}", "choice": "A|B|tie"}
            for index in range(1, item_count + 1)
        ],
    }


def _review_html(package_id: str, item_count: int) -> str:
    items = [
        {
            "item_id": f"item-{index:03d}",
            "reference": f"items/item-{index:03d}/reference.png",
            "a": f"items/item-{index:03d}/a.png",
            "b": f"items/item-{index:03d}/b.png",
        }
        for index in range(1, item_count + 1)
    ]
    encoded = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><link rel="icon" href="data:,">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ShaderGen 匿名 A/B 盲评</title>
<style>
body{{font-family:system-ui,sans-serif;margin:24px;background:#f4f6fa;color:#172033}}
main{{max-width:1100px;margin:auto}}article{{background:white;padding:18px;margin:16px 0;border-radius:12px}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}img{{width:100%;object-fit:contain;background:white}}
.ref{{max-width:480px;margin:auto}}button{{margin:12px 8px 0 0;padding:9px 16px}}
button.selected{{background:#2456d6;color:white}}@media(max-width:700px){{.pair{{grid-template-columns:1fr}}}}
</style></head><body><main><h1>ShaderGen 匿名 A/B 盲评</h1>
<p>只按视觉接近度选择 A、B 或平局；页面不包含候选来源。</p>
<label>评审人 <input id="reviewer"></label> <span id="progress"></span>
<button id="download" disabled>下载 JSON</button><section id="items"></section></main>
<script>
const packageId={json.dumps(package_id)},items={encoded},choices={{}};
const root=document.getElementById("items");
function render(){{root.innerHTML="";for(const item of items){{const el=document.createElement("article");
el.innerHTML=`<h2>${{item.item_id}}</h2><div class="ref"><img src="${{item.reference}}" alt="reference"></div><div class="pair"><figure><figcaption>A</figcaption><img src="${{item.a}}"></figure><figure><figcaption>B</figcaption><img src="${{item.b}}"></figure></div><div><button data-v="A">A</button><button data-v="B">B</button><button data-v="tie">平局</button></div>`;
for(const button of el.querySelectorAll("button")){{if(choices[item.item_id]===button.dataset.v)button.classList.add("selected");button.onclick=()=>{{choices[item.item_id]=button.dataset.v;render();}}}}root.appendChild(el);}}
const done=items.filter(item=>choices[item.item_id]).length;document.getElementById("progress").textContent=`${{done}} / ${{items.length}}`;document.getElementById("download").disabled=done!==items.length;}}
document.getElementById("download").onclick=()=>{{const payload={{schema_version:{json.dumps(HUMAN_REVIEW_SCHEMA_VERSION)},package_id:packageId,reviewer:document.getElementById("reviewer").value.trim(),items:items.map(item=>({{item_id:item.item_id,choice:choices[item.item_id]}}))}};const link=document.createElement("a");link.href=URL.createObjectURL(new Blob([JSON.stringify(payload,null,2)+"\\n"],{{type:"application/json"}}));link.download="human-review.json";link.click();URL.revokeObjectURL(link.href);}};render();
</script></body></html>
"""


def _write_file(root: Path, relative: str, data: bytes) -> dict[str, Any]:
    path = root.joinpath(*PurePosixPath(relative).parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return {"path": relative, **_digest(data)}


def write_blind_review_package(
    suite_dir: Path,
    *,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
    output_root: Path,
) -> Path:
    """从已复验 suite 原子写入 write-once 匿名盲评包."""
    _require_current_v2(manifest, gate)
    suite_payload = verify_shadow_suite_report(
        suite_dir, manifest=manifest, gate=gate
    )
    items, unreviewable = _source_items(suite_dir, suite_payload)
    scheduled_count = len(manifest.samples) * manifest.rounds
    if len(items) + len(unreviewable) != scheduled_count:
        raise ShadowReviewError("盲评状态必须完整覆盖每个 sample×round。")
    if output_root.is_symlink():
        raise ShadowReviewError("盲评 output_root 不得是 symlink。")
    output_root.mkdir(parents=True, exist_ok=True)
    suite_hash = _suite_report_hash(suite_payload)
    # package id 显式携带 review protocol major；未来修改匿名分配、页面/template
    # 或证据边界时必须升级 schema/id，避免新 verifier 与同名历史包发生碰撞。
    package_id = f"shadow-review-v1-{suite_hash[:12]}"
    package_dir = output_root / package_id
    staging = output_root / f".{package_id}.staging-{os.getpid()}-{uuid4().hex[:8]}"
    staging.mkdir(mode=0o700)
    try:
        mapping = {
            "schema_version": PRIVATE_MAPPING_SCHEMA_VERSION,
            "package_id": package_id,
            "suite_report_sha256": suite_hash,
            "assignment_algorithm": "sha256(suite_report_sha256:sample_id:round_index)-byte0-parity-v1",
            "items": [
                {
                    "item_id": item["item_id"],
                    "sample_id": item["sample_id"],
                    "round_index": item["round_index"],
                    "run_id": item["run_id"],
                    "a_arm": item["a_arm"],
                    "b_arm": item["b_arm"],
                }
                for item in items
            ],
        }
        files = [_write_file(staging, _PRIVATE_MAPPING, _json_bytes(mapping))]
        for item in items:
            prefix = f"{_REVIEWER_ROOT}/items/{item['item_id']}"
            for name, key in (("reference.png", "reference"), ("a.png", "A"), ("b.png", "B")):
                files.append(
                    _write_file(staging, f"{prefix}/{name}", item[key].read_bytes())
                )
        files.append(
            _write_file(
                staging,
                f"{_REVIEWER_ROOT}/review-template.json",
                _json_bytes(_public_template(package_id, len(items))),
            )
        )
        files.append(
            _write_file(
                staging,
                f"{_REVIEWER_ROOT}/index.html",
                _review_html(package_id, len(items)).encode("utf-8"),
            )
        )
        package_body: dict[str, Any] = {
            "schema_version": REVIEW_PACKAGE_SCHEMA_VERSION,
            "package_id": package_id,
            "suite_report_sha256": suite_hash,
            "reviewer_root": _REVIEWER_ROOT,
            "private_mapping_path": _PRIVATE_MAPPING,
            "scheduled_count": scheduled_count,
            "item_count": len(items),
            "unreviewable": unreviewable,
            "files": sorted(files, key=lambda item: item["path"]),
            "package_manifest_size_bytes": 0,
        }
        while True:
            package_payload = dict(package_body)
            package_payload["package_manifest_sha256"] = sha256(
                canonical_json(package_body).encode("utf-8")
            ).hexdigest()
            package_data = _json_bytes(package_payload)
            if package_body["package_manifest_size_bytes"] == len(package_data):
                break
            package_body["package_manifest_size_bytes"] = len(package_data)
        _write_file(staging, _PACKAGE_MANIFEST, package_data)
        for path in [staging, *staging.rglob("*")]:
            if path.is_dir():
                os.chmod(path, 0o700)
        if package_dir.exists() or package_dir.is_symlink():
            raise FileExistsError(f"盲评包已存在，拒绝覆盖：{package_dir}")
        os.rename(staging, package_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    verify_blind_review_package(
        package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
    )
    return package_dir


def _verify_tree(package_dir: Path, package: Mapping[str, Any]) -> None:
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise ShadowReviewError("盲评包目录无效。")
    for path in [package_dir, *package_dir.rglob("*")]:
        if path.is_symlink():
            raise ShadowReviewError(f"盲评包递归禁止 symlink：{path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise ShadowReviewError(f"盲评包权限过宽：{path}")
    raw_files = package.get("files")
    if not isinstance(raw_files, list):
        raise ShadowReviewError("package manifest files 必须是数组。")
    declared: dict[str, tuple[str, int]] = {}
    for entry in raw_files:
        if not isinstance(entry, dict):
            raise ShadowReviewError("package manifest file entry 非法。")
        relative = _safe_relative(entry.get("path"))
        digest = entry.get("sha256")
        size = entry.get("size_bytes")
        if (
            relative in declared
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in _SHA256 for character in digest)
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise ShadowReviewError(f"package manifest 文件声明非法：{relative}")
        declared[relative] = (digest, size)
    actual_files = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_file()
    }
    expected_files = set(declared) | {_PACKAGE_MANIFEST}
    if actual_files != expected_files:
        raise ShadowReviewError(
            "盲评包文件集合漂移（缺失、改名或包含额外文件）。"
        )
    expected_dirs = {
        PurePosixPath(relative).parent.as_posix()
        for relative in expected_files
        if PurePosixPath(relative).parent.as_posix() != "."
    }
    expected_dirs |= {
        parent.as_posix()
        for relative in tuple(expected_dirs)
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }
    actual_dirs = {
        path.relative_to(package_dir).as_posix()
        for path in package_dir.rglob("*")
        if path.is_dir()
    }
    if actual_dirs != expected_dirs:
        raise ShadowReviewError("盲评包目录集合漂移（含额外空目录或改名）。")
    for relative, (digest, size) in declared.items():
        data = package_dir.joinpath(*PurePosixPath(relative).parts).read_bytes()
        if len(data) != size or sha256(data).hexdigest() != digest:
            raise ShadowReviewError(f"盲评包内容 hash/size 漂移：{relative}")


def verify_blind_review_package(
    package_dir: Path,
    *,
    suite_dir: Path,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
) -> dict[str, Any]:
    """递归复验 suite、盲评包、私有映射和全部公开内容."""
    _require_current_v2(manifest, gate)
    suite_payload = verify_shadow_suite_report(
        suite_dir, manifest=manifest, gate=gate
    )
    if package_dir.is_symlink() or not package_dir.is_dir():
        raise ShadowReviewError("盲评包目录无效或是 symlink。")
    package = _load_json_object(
        package_dir / _PACKAGE_MANIFEST, label="package manifest"
    )
    if package.get("schema_version") != REVIEW_PACKAGE_SCHEMA_VERSION:
        raise ShadowReviewError("package manifest schema_version 非法。")
    suite_hash = _suite_report_hash(suite_payload)
    expected_package_id = f"shadow-review-v1-{suite_hash[:12]}"
    if (
        package.get("package_id") != expected_package_id
        or package_dir.name != expected_package_id
        or package.get("suite_report_sha256") != suite_hash
        or package.get("reviewer_root") != _REVIEWER_ROOT
        or package.get("private_mapping_path") != _PRIVATE_MAPPING
    ):
        raise ShadowReviewError("盲评包与 suite 的内容寻址绑定已漂移。")
    claimed = package.pop("package_manifest_sha256", None)
    actual = sha256(canonical_json(package).encode("utf-8")).hexdigest()
    package["package_manifest_sha256"] = claimed
    if claimed != actual:
        raise ShadowReviewError("package_manifest_sha256 不匹配。")
    manifest_size = package.get("package_manifest_size_bytes")
    if (
        isinstance(manifest_size, bool)
        or not isinstance(manifest_size, int)
        or manifest_size != (package_dir / _PACKAGE_MANIFEST).stat().st_size
    ):
        raise ShadowReviewError("package manifest size_bytes 不匹配。")
    _verify_tree(package_dir, package)

    items, unreviewable = _source_items(suite_dir, suite_payload)
    item_count = package.get("item_count")
    if (
        isinstance(item_count, bool)
        or not isinstance(item_count, int)
        or item_count < 0
        or item_count != len(items)
    ):
        raise ShadowReviewError("盲评包 item_count 与 suite 不一致。")
    scheduled_count = len(manifest.samples) * manifest.rounds
    if (
        package.get("scheduled_count") != scheduled_count
        or package.get("unreviewable") != unreviewable
        or len(items) + len(unreviewable) != scheduled_count
    ):
        raise ShadowReviewError("盲评包 unreviewable 状态与 suite 不一致。")
    mapping = _load_json_object(
        package_dir / _PRIVATE_MAPPING, label="private mapping"
    )
    expected_mapping = {
        "schema_version": PRIVATE_MAPPING_SCHEMA_VERSION,
        "package_id": expected_package_id,
        "suite_report_sha256": suite_hash,
        "assignment_algorithm": "sha256(suite_report_sha256:sample_id:round_index)-byte0-parity-v1",
        "items": [
            {
                "item_id": item["item_id"],
                "sample_id": item["sample_id"],
                "round_index": item["round_index"],
                "run_id": item["run_id"],
                "a_arm": item["a_arm"],
                "b_arm": item["b_arm"],
            }
            for item in items
        ],
    }
    if mapping != expected_mapping:
        raise ShadowReviewError("私有 A/B mapping 与 suite 确定性分配不一致。")
    expected_public: dict[str, bytes] = {
        f"{_REVIEWER_ROOT}/review-template.json": _json_bytes(
            _public_template(expected_package_id, len(items))
        ),
        f"{_REVIEWER_ROOT}/index.html": _review_html(
            expected_package_id, len(items)
        ).encode("utf-8"),
    }
    for item in items:
        prefix = f"{_REVIEWER_ROOT}/items/{item['item_id']}"
        expected_public[f"{prefix}/reference.png"] = item["reference"].read_bytes()
        expected_public[f"{prefix}/a.png"] = item["A"].read_bytes()
        expected_public[f"{prefix}/b.png"] = item["B"].read_bytes()
    declared = {
        entry["path"]: (entry["sha256"], entry["size_bytes"])
        for entry in cast(list[dict[str, Any]], package["files"])
    }
    if set(declared) != set(expected_public) | {_PRIVATE_MAPPING}:
        raise ShadowReviewError("盲评包公开/私有文件边界已漂移。")
    for relative, data in expected_public.items():
        if declared[relative] != (_digest(data)["sha256"], len(data)):
            raise ShadowReviewError(f"盲评公开内容与原 suite 不一致：{relative}")
    return package


def evaluate_blind_review(
    package_dir: Path,
    *,
    suite_dir: Path,
    human_review_path: Path,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
) -> dict[str, Any]:
    """复验 suite/package 后读取完整人工 A/B/tie JSON 并计算 v2 gate."""
    package = verify_blind_review_package(
        package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
    )
    # 安全顺序：人工文件直到上述 suite/package 全部复验成功后才读取。
    review, review_bytes = _load_json_object_with_bytes(
        human_review_path, label="human review"
    )
    if (
        review.get("schema_version") != HUMAN_REVIEW_SCHEMA_VERSION
        or review.get("package_id") != package.get("package_id")
        or set(review) != {"schema_version", "package_id", "reviewer", "items"}
    ):
        raise ShadowReviewError("human review schema/package 绑定不一致。")
    reviewer = review.get("reviewer")
    raw_items = review.get("items")
    if (
        not isinstance(reviewer, str)
        or not reviewer.strip()
        or len(reviewer.strip()) > 128
    ):
        raise ShadowReviewError("human review reviewer 必须是 1..128 字符的代号。")
    if not isinstance(raw_items, list):
        raise ShadowReviewError("human review items 必须是数组。")
    choices: dict[str, str] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise ShadowReviewError("human review item 必须是 object。")
        item_id = raw.get("item_id")
        choice = raw.get("choice")
        if (
            not isinstance(item_id, str)
            or item_id in choices
            or choice not in {"A", "B", "tie"}
            or set(raw) != {"item_id", "choice"}
        ):
            raise ShadowReviewError("human review item 重复、字段或 choice 非法。")
        choices[item_id] = choice
    item_count = package.get("item_count")
    if (
        isinstance(item_count, bool)
        or not isinstance(item_count, int)
        or item_count < 0
    ):
        raise ShadowReviewError("package item_count 非法。")
    expected_ids = {
        f"item-{index:03d}"
        for index in range(1, item_count + 1)
    }
    if set(choices) != expected_ids:
        raise ShadowReviewError("human review 必须完整覆盖全部 sample×round 项。")
    mapping, mapping_bytes = _load_json_object_with_bytes(
        package_dir / _PRIVATE_MAPPING, label="private mapping"
    )
    mapping_entry = next(
        (
            entry
            for entry in cast(list[dict[str, Any]], package["files"])
            if entry.get("path") == _PRIVATE_MAPPING
        ),
        None,
    )
    if (
        mapping_entry is None
        or mapping_entry.get("sha256") != sha256(mapping_bytes).hexdigest()
        or mapping_entry.get("size_bytes") != len(mapping_bytes)
    ):
        raise ShadowReviewError("private mapping 在评价前发生漂移。")
    mapping_items = mapping.get("items")
    if not isinstance(mapping_items, list):
        raise ShadowReviewError("private mapping items 非法。")
    arm_b_wins = 0
    arm_a_wins = 0
    ties = 0
    for item in mapping_items:
        if not isinstance(item, dict) or not isinstance(item.get("item_id"), str):
            raise ShadowReviewError("private mapping item 非法。")
        choice = choices[item["item_id"]]
        if choice == "tie":
            ties += 1
        elif item.get("a_arm" if choice == "A" else "b_arm") == "B":
            arm_b_wins += 1
        else:
            arm_a_wins += 1
    review_count = len(expected_ids)
    scheduled_count = package.get("scheduled_count")
    if (
        isinstance(scheduled_count, bool)
        or not isinstance(scheduled_count, int)
        or scheduled_count < review_count
    ):
        raise ShadowReviewError("package scheduled_count 非法。")
    rate = arm_b_wins / scheduled_count
    passed = rate >= gate.min_arm_b_preference_rate
    return {
        "schema_version": HUMAN_EVALUATION_SCHEMA_VERSION,
        "package_id": package["package_id"],
        "suite_report_sha256": package["suite_report_sha256"],
        "human_review": {
            "reviewer_alias_sha256": sha256(
                reviewer.strip().encode("utf-8")
            ).hexdigest(),
            "review_sha256": sha256(review_bytes).hexdigest(),
            "review_size_bytes": len(review_bytes),
            "review_count": review_count,
            "scheduled_count": scheduled_count,
            "unreviewable_count": scheduled_count - review_count,
            "arm_b_preference_count": arm_b_wins,
            "arm_a_preference_count": arm_a_wins,
            "tie_count": ties,
            "arm_b_preference_rate": rate,
        },
        "gate": {
            "schema_version": gate.schema_version,
            "min_arm_b_preference_rate": gate.min_arm_b_preference_rate,
            "ties_not_counted_as_b_win": True,
            "passed": passed,
            "outcome": "supported" if passed else "not_supported",
        },
        "promotion_decision": (
            "no_go_pending_durable" if passed else "no_go_human_gate_failed"
        ),
    }


__all__ = [
    "HUMAN_EVALUATION_SCHEMA_VERSION",
    "HUMAN_REVIEW_SCHEMA_VERSION",
    "PRIVATE_MAPPING_SCHEMA_VERSION",
    "REVIEW_PACKAGE_SCHEMA_VERSION",
    "REVIEW_TEMPLATE_SCHEMA_VERSION",
    "ShadowReviewError",
    "evaluate_blind_review",
    "verify_blind_review_package",
    "write_blind_review_package",
]
