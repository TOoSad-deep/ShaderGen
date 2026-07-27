"""构建并离线复验 LayerPlan shadow promotion 私有证据包.

本模块不调用模型、浏览器或 evidence registry。构建前先递归复验 v2 suite
和匿名盲评包，再从原始 human review 重新计算评价；只有调用方提供的
human evaluation 与规范化重算结果逐字节一致且人工 gate 为 supported，
才会创建内容寻址、write-once 的私有 bundle。
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

from agent.app.services.layerplan_glsl_shadow_review import (
    HUMAN_EVALUATION_SCHEMA_VERSION,
    evaluate_blind_review,
    verify_blind_review_package,
)
from agent.app.services.layerplan_glsl_shadow_suite import (
    CURRENT_GATE_SCHEMA_VERSION,
    CURRENT_MANIFEST_SCHEMA_VERSION,
    ShadowSuiteGate,
    ShadowSuiteManifest,
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
    verify_shadow_suite_report,
)
from shaderforge.program_spec import canonical_json

PROMOTION_EVIDENCE_SCHEMA_VERSION = "layerplan_glsl_promotion_evidence_v1"
DURABILITY_STATUS = "local_private_not_registered"

_BUNDLE_MANIFEST = "promotion-evidence-manifest.json"
_SHA256 = frozenset("0123456789abcdef")


class PromotionEvidenceError(ValueError):
    """promotion evidence 输入或私有 bundle 违反 fail-closed 契约."""


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    encoded = canonical_json(dict(value))
    return (encoded + "\n").encode("utf-8")


def _digest(data: bytes) -> dict[str, Any]:
    return {"sha256": sha256(data).hexdigest(), "size_bytes": len(data)}


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise PromotionEvidenceError("bundle 文件路径必须是规范 POSIX 相对路径。")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in {"", ".", ".."} for part in pure.parts)
    ):
        raise PromotionEvidenceError("bundle 文件路径必须是规范 POSIX 相对路径。")
    return value


def _load_json_object_with_bytes(
    path: Path, *, label: str
) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise PromotionEvidenceError(f"{label} 缺失、不是普通文件或是 symlink。")
    try:
        data = path.read_bytes()
        value = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionEvidenceError(f"{label} 不是可读 JSON object。") from exc
    if not isinstance(value, dict):
        raise PromotionEvidenceError(f"{label} 必须是 JSON object。")
    return cast(dict[str, Any], value), data


def _require_v2(manifest: ShadowSuiteManifest, gate: ShadowSuiteGate) -> None:
    if (
        manifest.schema_version != CURRENT_MANIFEST_SCHEMA_VERSION
        or gate.schema_version != CURRENT_GATE_SCHEMA_VERSION
        or manifest.implementation_identity_sha256 is None
        or gate.implementation_identity_sha256
        != manifest.implementation_identity_sha256
    ):
        raise PromotionEvidenceError(
            "promotion evidence 只接受身份绑定完整的 v2 协议。"
        )


def _require_supported(suite: Mapping[str, Any], evaluation: Mapping[str, Any]) -> None:
    aggregate = suite.get("aggregate")
    automatic = aggregate.get("automatic_gate") if isinstance(aggregate, dict) else None
    if (
        not isinstance(automatic, dict)
        or automatic.get("passed") is not True
        or automatic.get("outcome") != "supported"
    ):
        raise PromotionEvidenceError("automatic gate 未达到 supported，拒绝构建。")
    human_gate = evaluation.get("gate")
    if (
        evaluation.get("schema_version") != HUMAN_EVALUATION_SCHEMA_VERSION
        or not isinstance(human_gate, dict)
        or human_gate.get("passed") is not True
        or human_gate.get("outcome") != "supported"
    ):
        raise PromotionEvidenceError("human gate 未达到 supported，拒绝构建。")


def _source_files(root: Path, *, label: str) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise PromotionEvidenceError(f"{label} 不是有效私有目录或是 symlink。")
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PromotionEvidenceError(f"{label} 递归禁止 symlink：{path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise PromotionEvidenceError(f"{label} 包含非普通文件：{path}")
    relative_files = {path.relative_to(root).as_posix() for path in files}
    actual_dirs = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    }
    if actual_dirs != _expected_dirs(relative_files):
        raise PromotionEvidenceError(f"{label} 包含额外空目录或目录改名。")
    return sorted(files)


def _write_file(root: Path, relative: str, data: bytes) -> dict[str, Any]:
    relative = _safe_relative(relative)
    target = root.joinpath(*PurePosixPath(relative).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    os.chmod(target, 0o600)
    return {"path": relative, **_digest(data)}


def _copy_file(root: Path, relative: str, source: Path) -> dict[str, Any]:
    if source.is_symlink() or not source.is_file():
        raise PromotionEvidenceError(f"复制源缺失、不是普通文件或是 symlink：{source}")
    return _write_file(root, relative, source.read_bytes())


def _copy_tree(
    staging: Path, *, source: Path, destination: str
) -> list[dict[str, Any]]:
    files = _source_files(source, label=source.name)
    copied = []
    for path in files:
        relative = path.relative_to(source).as_posix()
        copied.append(_copy_file(staging, f"{destination}/{relative}", path))
    return copied


def _expected_dirs(files: set[str]) -> set[str]:
    direct = {
        PurePosixPath(relative).parent.as_posix()
        for relative in files
        if PurePosixPath(relative).parent.as_posix() != "."
    }
    return direct | {
        parent.as_posix()
        for relative in tuple(direct)
        for parent in PurePosixPath(relative).parents
        if parent.as_posix() != "."
    }


def _verify_bundle_tree(
    bundle_dir: Path, payload: Mapping[str, Any]
) -> dict[str, tuple[str, int]]:
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise PromotionEvidenceError("promotion bundle 目录无效或是 symlink。")
    for path in [bundle_dir, *bundle_dir.rglob("*")]:
        if path.is_symlink():
            raise PromotionEvidenceError(f"promotion bundle 递归禁止 symlink：{path}")
        expected_mode = 0o700 if path.is_dir() else 0o600
        if stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise PromotionEvidenceError(
                f"promotion bundle 权限非法：{path}，要求 {oct(expected_mode)}。"
            )
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise PromotionEvidenceError("bundle manifest files 必须是数组。")
    declared: dict[str, tuple[str, int]] = {}
    for entry in raw_files:
        if not isinstance(entry, dict):
            raise PromotionEvidenceError("bundle manifest file entry 非法。")
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
            raise PromotionEvidenceError(f"bundle 文件声明非法：{relative}")
        declared[relative] = (digest, size)
    expected_files = set(declared) | {_BUNDLE_MANIFEST}
    actual_files = {
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise PromotionEvidenceError(
            "bundle 文件集合漂移（缺失、改名或包含额外文件）。"
        )
    actual_dirs = {
        path.relative_to(bundle_dir).as_posix()
        for path in bundle_dir.rglob("*")
        if path.is_dir()
    }
    if actual_dirs != _expected_dirs(expected_files):
        raise PromotionEvidenceError("bundle 目录集合漂移（含额外空目录或改名）。")
    for relative, (digest, size) in declared.items():
        data = bundle_dir.joinpath(*PurePosixPath(relative).parts).read_bytes()
        if len(data) != size or sha256(data).hexdigest() != digest:
            raise PromotionEvidenceError(f"bundle 内容 hash/size 漂移：{relative}")
    return declared


def _manifest_path(bundle_dir: Path, value: Any, *, label: str) -> Path:
    relative = _safe_relative(value)
    path = bundle_dir.joinpath(*PurePosixPath(relative).parts)
    if path.is_symlink() or not path.is_file():
        raise PromotionEvidenceError(f"{label} 路径无效。")
    return path


def verify_promotion_evidence_bundle(bundle_dir: Path) -> dict[str, Any]:
    """在无模型、无浏览器环境递归复验整个 promotion evidence bundle."""
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise PromotionEvidenceError("promotion bundle 目录无效或是 symlink。")
    payload, manifest_bytes = _load_json_object_with_bytes(
        bundle_dir / _BUNDLE_MANIFEST, label="promotion evidence manifest"
    )
    if payload.get("schema_version") != PROMOTION_EVIDENCE_SCHEMA_VERSION:
        raise PromotionEvidenceError("promotion evidence schema_version 非法。")
    claimed = payload.pop("bundle_manifest_sha256", None)
    actual = sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    payload["bundle_manifest_sha256"] = claimed
    if claimed != actual or bundle_dir.name != f"promotion-evidence-{actual[:12]}":
        raise PromotionEvidenceError("bundle manifest 或内容寻址目录名不匹配。")
    manifest_size = payload.get("bundle_manifest_size_bytes")
    if (
        isinstance(manifest_size, bool)
        or not isinstance(manifest_size, int)
        or manifest_size != len(manifest_bytes)
    ):
        raise PromotionEvidenceError("bundle manifest size_bytes 不匹配。")
    if payload.get("durability_status") != DURABILITY_STATUS:
        raise PromotionEvidenceError("bundle durability_status 非法。")
    if payload.get("registry_status") != "not_registered":
        raise PromotionEvidenceError("bundle 不得自称已注册。")
    declared = _verify_bundle_tree(bundle_dir, payload)

    protocol = payload.get("protocol")
    source = payload.get("source")
    human = payload.get("human")
    if not all(isinstance(value, dict) for value in (protocol, source, human)):
        raise PromotionEvidenceError("bundle 缺少 protocol/source/human 绑定。")
    protocol = cast(dict[str, Any], protocol)
    source = cast(dict[str, Any], source)
    human = cast(dict[str, Any], human)
    manifest_path = _manifest_path(
        bundle_dir, protocol.get("manifest_path"), label="manifest"
    )
    gate_path = _manifest_path(bundle_dir, protocol.get("gate_path"), label="gate")
    manifest = load_shadow_suite_manifest(manifest_path)
    gate = load_shadow_suite_gate(gate_path, manifest=manifest)
    _require_v2(manifest, gate)
    if (
        protocol.get("manifest_path") != f"protocol/{manifest_path.name}"
        or protocol.get("gate_path") != f"protocol/{gate_path.name}"
        or manifest_path.name == gate_path.name
        or protocol.get("manifest_sha256") != manifest.manifest_sha256
        or protocol.get("gate_sha256") != gate.gate_sha256
        or payload.get("implementation_identity_sha256")
        != manifest.implementation_identity_sha256
    ):
        raise PromotionEvidenceError("冻结协议或 implementation identity 绑定漂移。")

    suite_relative = _safe_relative(source.get("suite_path"))
    package_relative = _safe_relative(source.get("review_package_path"))
    suite_dir = bundle_dir.joinpath(*PurePosixPath(suite_relative).parts)
    package_dir = bundle_dir.joinpath(*PurePosixPath(package_relative).parts)
    if (
        suite_relative != f"evidence/{suite_dir.name}"
        or package_relative != f"review/{package_dir.name}"
    ):
        raise PromotionEvidenceError("suite/package 在 bundle 内的固定路径已漂移。")
    suite = verify_shadow_suite_report(suite_dir, manifest=manifest, gate=gate)
    package = verify_blind_review_package(
        package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
    )
    if source.get("suite_report_sha256") != suite.get(
        "suite_report_sha256"
    ) or source.get("review_package_sha256") != package.get("package_manifest_sha256"):
        raise PromotionEvidenceError("suite/package identity 绑定漂移。")

    run_ids = source.get("run_ids")
    suite_runs = suite.get("runs")
    expected_run_ids = (
        [item.get("run_id") for item in suite_runs]
        if isinstance(suite_runs, list)
        and all(isinstance(item, dict) for item in suite_runs)
        else None
    )
    if (
        not isinstance(run_ids, list)
        or len(run_ids) != 8
        or not all(isinstance(run_id, str) for run_id in run_ids)
        or len(set(run_ids)) != 8
        or run_ids != expected_run_ids
    ):
        raise PromotionEvidenceError("bundle 必须绑定 suite 的 8 个唯一 run。")

    review_relative = _safe_relative(human.get("review_path"))
    evaluation_relative = _safe_relative(human.get("evaluation_path"))
    if (
        review_relative != "human/human-review.json"
        or evaluation_relative != "human/human-evaluation.json"
    ):
        raise PromotionEvidenceError("human review/evaluation 固定路径已漂移。")
    review_path = _manifest_path(bundle_dir, review_relative, label="human review")
    evaluation_path = _manifest_path(
        bundle_dir, evaluation_relative, label="human evaluation"
    )
    exact_files = {
        cast(str, protocol.get("manifest_path")),
        cast(str, protocol.get("gate_path")),
        review_relative,
        evaluation_relative,
    }
    allowed_prefixes = [
        f"{suite_relative}/",
        f"{package_relative}/",
        *(f"evidence/{run_id}/" for run_id in run_ids),
    ]
    if any(
        relative not in exact_files
        and not any(relative.startswith(prefix) for prefix in allowed_prefixes)
        for relative in declared
    ):
        raise PromotionEvidenceError("bundle manifest 声明了语义边界外的额外文件。")
    recalculated = evaluate_blind_review(
        package_dir,
        suite_dir=suite_dir,
        human_review_path=review_path,
        manifest=manifest,
        gate=gate,
    )
    evaluation, evaluation_bytes = _load_json_object_with_bytes(
        evaluation_path, label="human evaluation"
    )
    expected_evaluation_bytes = _canonical_bytes(recalculated)
    if (
        evaluation != recalculated
        or evaluation_bytes != expected_evaluation_bytes
        or declared[evaluation_relative]
        != (
            sha256(expected_evaluation_bytes).hexdigest(),
            len(expected_evaluation_bytes),
        )
    ):
        raise PromotionEvidenceError("human evaluation 与重新评价结果不一致。")
    review_bytes = review_path.read_bytes()
    evaluation_review = recalculated.get("human_review")
    if (
        not isinstance(evaluation_review, dict)
        or human.get("review_sha256") != sha256(review_bytes).hexdigest()
        or human.get("evaluation_sha256") != sha256(evaluation_bytes).hexdigest()
        or evaluation_review.get("review_sha256") != sha256(review_bytes).hexdigest()
    ):
        raise PromotionEvidenceError("human review/evaluation hash 绑定漂移。")
    _require_supported(suite, recalculated)
    return payload


def build_promotion_evidence_bundle(
    suite_dir: Path,
    *,
    package_dir: Path,
    human_review_path: Path,
    human_evaluation_path: Path,
    manifest: ShadowSuiteManifest,
    gate: ShadowSuiteGate,
    output_root: Path,
) -> Path:
    """严格复验输入并原子创建内容寻址、write-once 私有 bundle."""
    _require_v2(manifest, gate)
    # 安全顺序：先递归复验 suite/package，再评价；之后才读取 evaluation。
    suite = verify_shadow_suite_report(suite_dir, manifest=manifest, gate=gate)
    package = verify_blind_review_package(
        package_dir, suite_dir=suite_dir, manifest=manifest, gate=gate
    )
    recalculated = evaluate_blind_review(
        package_dir,
        suite_dir=suite_dir,
        human_review_path=human_review_path,
        manifest=manifest,
        gate=gate,
    )
    _require_supported(suite, recalculated)
    evaluation, evaluation_bytes = _load_json_object_with_bytes(
        human_evaluation_path, label="human evaluation"
    )
    expected_evaluation_bytes = _canonical_bytes(recalculated)
    if evaluation != recalculated or evaluation_bytes != expected_evaluation_bytes:
        raise PromotionEvidenceError(
            "human evaluation 必须与重新评价的 canonical JSON 字节完全一致。"
        )
    review_bytes = human_review_path.read_bytes()

    raw_runs = suite.get("runs")
    if not isinstance(raw_runs, list) or not all(
        isinstance(item, dict) for item in raw_runs
    ):
        raise PromotionEvidenceError("suite runs 非法。")
    run_ids = [cast(dict[str, Any], item).get("run_id") for item in raw_runs]
    if (
        len(run_ids) != 8
        or not all(isinstance(run_id, str) for run_id in run_ids)
        or len(set(run_ids)) != 8
    ):
        raise PromotionEvidenceError("promotion bundle 必须包含 8 个唯一 suite run。")
    typed_run_ids = cast(list[str], run_ids)

    if output_root.is_symlink():
        raise PromotionEvidenceError("bundle output_root 不得是 symlink。")
    output_root.mkdir(parents=True, exist_ok=True)
    protocol_names = {manifest.path.name, gate.path.name}
    if len(protocol_names) != 2:
        raise PromotionEvidenceError("manifest 与 gate 文件名不得冲突。")

    staging = output_root / (
        f".promotion-evidence.staging-{os.getpid()}-{uuid4().hex[:8]}"
    )
    staging.mkdir(mode=0o700)
    final_dir: Path | None = None
    renamed = False
    try:
        files: list[dict[str, Any]] = []
        evidence_root = "evidence"
        suite_relative = f"{evidence_root}/{suite_dir.name}"
        files.extend(_copy_tree(staging, source=suite_dir, destination=suite_relative))
        for run_id in typed_run_ids:
            files.extend(
                _copy_tree(
                    staging,
                    source=suite_dir.parent / run_id,
                    destination=f"{evidence_root}/{run_id}",
                )
            )
        package_relative = f"review/{package_dir.name}"
        files.extend(
            _copy_tree(staging, source=package_dir, destination=package_relative)
        )
        manifest_relative = f"protocol/{manifest.path.name}"
        gate_relative = f"protocol/{gate.path.name}"
        files.append(_copy_file(staging, manifest_relative, manifest.path))
        files.append(_copy_file(staging, gate_relative, gate.path))
        review_relative = "human/human-review.json"
        evaluation_relative = "human/human-evaluation.json"
        files.append(_write_file(staging, review_relative, review_bytes))
        files.append(_write_file(staging, evaluation_relative, evaluation_bytes))

        body: dict[str, Any] = {
            "schema_version": PROMOTION_EVIDENCE_SCHEMA_VERSION,
            "durability_status": DURABILITY_STATUS,
            "registry_status": "not_registered",
            "implementation_identity_sha256": (manifest.implementation_identity_sha256),
            "protocol": {
                "manifest_path": manifest_relative,
                "manifest_sha256": manifest.manifest_sha256,
                "gate_path": gate_relative,
                "gate_sha256": gate.gate_sha256,
            },
            "source": {
                "suite_path": suite_relative,
                "suite_report_sha256": suite["suite_report_sha256"],
                "run_ids": typed_run_ids,
                "review_package_path": package_relative,
                "review_package_sha256": package["package_manifest_sha256"],
            },
            "human": {
                "review_path": review_relative,
                "review_sha256": sha256(review_bytes).hexdigest(),
                "evaluation_path": evaluation_relative,
                "evaluation_sha256": sha256(evaluation_bytes).hexdigest(),
                "gate_outcome": "supported",
            },
            "files": sorted(files, key=lambda item: item["path"]),
            "bundle_manifest_size_bytes": 0,
        }
        while True:
            payload = dict(body)
            payload["bundle_manifest_sha256"] = sha256(
                canonical_json(body).encode("utf-8")
            ).hexdigest()
            manifest_data = _canonical_bytes(payload)
            if body["bundle_manifest_size_bytes"] == len(manifest_data):
                break
            body["bundle_manifest_size_bytes"] = len(manifest_data)
        _write_file(staging, _BUNDLE_MANIFEST, manifest_data)
        for path in [staging, *staging.rglob("*")]:
            if path.is_dir():
                os.chmod(path, 0o700)
        bundle_hash = cast(str, payload["bundle_manifest_sha256"])
        final_dir = output_root / f"promotion-evidence-{bundle_hash[:12]}"
        if final_dir.exists() or final_dir.is_symlink():
            raise FileExistsError(f"promotion bundle 已存在，拒绝覆盖：{final_dir}")
        os.rename(staging, final_dir)
        renamed = True
        verify_promotion_evidence_bundle(final_dir)
        return final_dir
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        if renamed and final_dir is not None and final_dir.exists():
            shutil.rmtree(final_dir, ignore_errors=True)
        raise


__all__ = [
    "DURABILITY_STATUS",
    "PROMOTION_EVIDENCE_SCHEMA_VERSION",
    "PromotionEvidenceError",
    "build_promotion_evidence_bundle",
    "verify_promotion_evidence_bundle",
]
