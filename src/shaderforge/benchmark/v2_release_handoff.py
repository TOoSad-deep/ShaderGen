"""V2 release-held-out 独立保管人 readiness 交接."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, model_validator

from shaderforge.benchmark.v2_dataset import (
    CRITICAL_CLASS_IDS,
    evaluate_v2_dataset_stage_gate,
    load_v2_dataset_manifest,
)
from shaderforge.contracts.canonical import canonical_json_bytes

TOOL_VERSION = "v2_release_operator_handoff_v1"
FREEZE_SCHEMA_VERSION = "png_to_shader_v2_release_freeze_v1"
ATTESTATION_SCHEMA_VERSION = "png_to_shader_v2_release_readiness_attestation_v1"
PACKAGE_DIGEST_VERSION = "v2_release_referenced_package_sha256_v1"
SIGNATURE_ALGORITHM = "ed25519_v1"
PRODUCTION_CRITICAL_CLASS_MINIMUM = 10

_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_ED25519_SIGNATURE_PATTERN = r"^[0-9a-f]{128}$"
_SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
_BLOCKER_CATEGORIES = frozenset(
    {
        "critical_class",
        "cross_split_contamination",
        "freeze_binding",
        "manifest_schema",
        "package_integrity",
        "package_validation",
        "source_license",
        "split_status",
        "stage_gate",
    }
)


class _StrictModel(BaseModel):
    """冻结交接 JSON 的严格基类."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ReleaseFreezeManifest(_StrictModel):
    """由独立保管人签名的 release package 冻结绑定."""

    schema_version: Literal["png_to_shader_v2_release_freeze_v1"]
    tool_version: Literal["v2_release_operator_handoff_v1"]
    package_digest_version: Literal["v2_release_referenced_package_sha256_v1"]
    signature_algorithm: Literal["ed25519_v1"]
    freeze_label: str = Field(min_length=1, pattern=_SAFE_ID_PATTERN)
    dataset_version: str = Field(min_length=1, pattern=_SAFE_ID_PATTERN)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    taxonomy_sha256: str = Field(pattern=_SHA256_PATTERN)
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    code_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    signed_at_utc: str = Field(min_length=1)
    signing_key_id: str = Field(min_length=1, pattern=_SAFE_ID_PATTERN)
    signing_public_key_sha256: str = Field(pattern=_SHA256_PATTERN)
    signature: str = Field(pattern=_ED25519_SIGNATURE_PATTERN)


class CriticalClassAggregate(_StrictModel):
    """不会暴露逐例信息的关键类聚合分母."""

    class_id: Literal[
        "multi_instance",
        "ring",
        "hollow",
        "required_highlight",
        "required_rim",
        "required_outline",
    ]
    numerator: int = Field(ge=0)
    denominator: Literal[10] = 10
    sufficient: bool

    @model_validator(mode="after")
    def _validate_sufficient(self) -> CriticalClassAggregate:
        if self.sufficient != (self.numerator >= self.denominator):
            raise ValueError("关键类 sufficient 与 numerator/denominator 不一致。")
        return self


class ReleaseReadinessAttestation(_StrictModel):
    """开发侧可接收的聚合-only、版本化 readiness 证明."""

    schema_version: Literal[
        "png_to_shader_v2_release_readiness_attestation_v1"
    ]
    tool_version: Literal["v2_release_operator_handoff_v1"]
    package_digest_version: Literal["v2_release_referenced_package_sha256_v1"]
    signature_algorithm: Literal["ed25519_v1"]
    stage: Literal["v2_3_release_candidate"]
    ready: bool
    package_verified: bool
    freeze_label: str = Field(min_length=1, pattern=_SAFE_ID_PATTERN)
    dataset_version: str = Field(min_length=1, pattern=_SAFE_ID_PATTERN)
    release_sample_count: int = Field(ge=0)
    critical_classes: tuple[CriticalClassAggregate, ...]
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    taxonomy_sha256: str = Field(pattern=_SHA256_PATTERN)
    package_sha256: str = Field(pattern=_SHA256_PATTERN)
    code_config_sha256: str = Field(pattern=_SHA256_PATTERN)
    executed_at_utc: str = Field(min_length=1)
    signing_key_id: str = Field(min_length=1, pattern=_SAFE_ID_PATTERN)
    signing_public_key_sha256: str = Field(pattern=_SHA256_PATTERN)
    blocker_category_counts: dict[str, int]
    signature: str = Field(pattern=_ED25519_SIGNATURE_PATTERN)

    @model_validator(mode="after")
    def _validate_aggregate_contract(self) -> ReleaseReadinessAttestation:
        if tuple(item.class_id for item in self.critical_classes) != CRITICAL_CLASS_IDS:
            raise ValueError("关键类必须完整且顺序固定。")
        if any(item.numerator > self.release_sample_count for item in self.critical_classes):
            raise ValueError("关键类 numerator 不得超过 release 样本总数。")
        if not set(self.blocker_category_counts).issubset(_BLOCKER_CATEGORIES):
            raise ValueError("blocker 类别不在脱敏白名单内。")
        if any(value < 1 for value in self.blocker_category_counts.values()):
            raise ValueError("blocker 类别计数必须为正整数。")
        expected_ready = (
            self.package_verified
            and not self.blocker_category_counts
            and all(item.sufficient for item in self.critical_classes)
        )
        if self.ready != expected_ready:
            raise ValueError("ready 与 package/class/blocker 聚合不一致。")
        return self


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("JSON 包含重复字段。")
        value[key] = item
    return value


def _parse_strict_json_bytes(raw_bytes: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw_bytes,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("JSON 包含非有限数值。")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("JSON 不可读取或格式非法。") from exc
    if not isinstance(value, dict):
        raise ValueError("JSON 根必须是 object。")
    return value


def _read_strict_json(path: Path) -> dict[str, Any]:
    return _parse_strict_json_bytes(path.read_bytes())


def _safe_path(root: Path, path: str | Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("输入路径越过 release package 根目录。")
    return resolved


def _load_private_key(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("签名私钥必须是 Ed25519 PEM。")
    return key


def _load_public_key(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("受信公钥必须是 Ed25519 PEM。")
    return key


def _public_key_sha256(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sha256(raw).hexdigest()


def _unsigned_payload(model: BaseModel | dict[str, Any]) -> dict[str, Any]:
    payload = (
        model.model_dump(mode="python") if isinstance(model, BaseModel) else dict(model)
    )
    payload.pop("signature", None)
    return payload


def _sign(payload: dict[str, Any], key: Ed25519PrivateKey) -> str:
    return key.sign(canonical_json_bytes(payload)).hex()


def _verify_signature(model: BaseModel, key: Ed25519PublicKey) -> None:
    signature = getattr(model, "signature")
    try:
        key.verify(bytes.fromhex(signature), canonical_json_bytes(_unsigned_payload(model)))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("Ed25519 签名无效。") from exc


def _declared_release_aggregate(
    raw_manifest: dict[str, Any] | None,
) -> tuple[int, dict[str, int]]:
    counts = {class_id: 0 for class_id in CRITICAL_CLASS_IDS}
    if raw_manifest is None:
        return 0, counts
    splits = raw_manifest.get("splits")
    if not isinstance(splits, list):
        return 0, counts
    release = next(
        (
            item
            for item in splits
            if isinstance(item, dict) and item.get("name") == "release-held-out"
        ),
        None,
    )
    if not isinstance(release, dict) or not isinstance(release.get("samples"), list):
        return 0, counts
    samples = [item for item in release["samples"] if isinstance(item, dict)]
    for sample in samples:
        instance_count = sample.get("instance_count")
        if isinstance(instance_count, int) and not isinstance(instance_count, bool):
            counts["multi_instance"] += int(instance_count > 1)
        topology = sample.get("topology")
        counts["ring"] += int(topology == "ring")
        counts["hollow"] += int(topology == "hollow")
        layers = sample.get("required_layers")
        if isinstance(layers, list):
            counts["required_highlight"] += int("highlight" in layers)
            counts["required_rim"] += int("rim" in layers)
            counts["required_outline"] += int("outline" in layers)
    return len(samples), counts


def _validate_source_license_records(dataset: Any) -> None:
    release_source_ids = {
        sample.source_suite_id
        for sample in dataset.manifest.split("release-held-out").samples
    }
    records = {
        record.source_suite_id: record for record in dataset.manifest.source_records
    }
    placeholders = {"unknown", "tbd", "todo", "none", "n/a", "unlicensed"}
    for source_id in release_source_ids:
        record = records[source_id]
        source_scheme = urlparse(record.source_url).scheme.lower()
        license_scheme = urlparse(record.license_url).scheme.lower()
        if source_scheme not in {"http", "https"}:
            raise ValueError("release 来源 URL 必须是 http(s)。")
        if license_scheme not in {"http", "https"}:
            raise ValueError("release 许可 URL 必须是 http(s)。")
        if record.license_id.strip().lower() in placeholders:
            raise ValueError("release license_id 不得是占位值。")
        provenance = _safe_path(
            dataset.benchmark_root,
            record.provenance_path,
        ).read_text(encoding="utf-8")
        required_evidence = (
            record.source_url,
            record.license_id,
            record.license_url,
        )
        if not all(value in provenance for value in required_evidence):
            raise ValueError("release 来源/许可文档缺少 Manifest 绑定字段。")


def _package_sha256(dataset: Any) -> str:
    """计算不含路径/逐例标签的 referenced-package 聚合内容身份."""
    payload = {
        "digest_version": PACKAGE_DIGEST_VERSION,
        "manifest_sha256": dataset.manifest_sha256,
        "taxonomy_sha256": dataset.taxonomy_sha256,
        "provenance_sha256": sorted(
            record.provenance_sha256 for record in dataset.manifest.source_records
        ),
        "image_sha256": sorted(
            sample.sha256
            for split in dataset.manifest.splits
            for sample in split.samples
        ),
    }
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _category_for_exception(exc: Exception) -> str:
    message = str(exc).lower()
    if (
        "签名" in message
        or "signature" in message
        or "冻结" in message
        or "code/config" in message
    ):
        return "freeze_binding"
    if "license" in message or "许可" in message or "来源" in message:
        return "source_license"
    if (
        "跨 split" in message
        or "cross" in message
        or "v1 样本" in message
        or "regression" in message
    ):
        return "cross_split_contamination"
    if (
        "sha-256" in message
        or "图片尺寸" in message
        or "taxonomy" in message
        or "文件" in message
        or "路径" in message
        or "path" in message
    ):
        return "package_integrity"
    if "json" in message or "manifest" in message or "validation error" in message:
        return "manifest_schema"
    return "package_validation"


def _critical_aggregates(
    counts: dict[str, int],
) -> tuple[CriticalClassAggregate, ...]:
    return tuple(
        CriticalClassAggregate(
            class_id=class_id,  # type: ignore[arg-type]
            numerator=counts[class_id],
            sufficient=counts[class_id] >= PRODUCTION_CRITICAL_CLASS_MINIMUM,
        )
        for class_id in CRITICAL_CLASS_IDS
    )


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def create_signed_freeze(
    *,
    package_root: Path,
    manifest_path: Path,
    freeze_path: Path,
    expected_code_config_sha256: str,
    freeze_label: str,
    signing_private_key_path: Path,
    signing_key_id: str,
) -> ReleaseFreezeManifest:
    """完整验证外部 package 后，以 exclusive-create 写入签名冻结记录."""
    root = package_root.resolve()
    manifest = _safe_path(root, manifest_path)
    private_key = _load_private_key(signing_private_key_path)
    public_key_sha256 = _public_key_sha256(private_key.public_key())
    dataset = load_v2_dataset_manifest(
        manifest,
        benchmark_root=root,
        gate_stage="v2_3_release_candidate",
    )
    _validate_source_license_records(dataset)
    gate = evaluate_v2_dataset_stage_gate(dataset, stage="v2_3_release_candidate")
    if not gate.ready:
        raise ValueError("package readiness 未通过，拒绝冻结。")
    if any(
        value != PRODUCTION_CRITICAL_CLASS_MINIMUM
        for value in dataset.manifest.critical_class_minimums.as_dict().values()
    ):
        raise ValueError("production 关键类门槛必须固定为 10。")
    unsigned = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "package_digest_version": PACKAGE_DIGEST_VERSION,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "freeze_label": freeze_label,
        "dataset_version": dataset.manifest.dataset_version,
        "manifest_sha256": dataset.manifest_sha256,
        "taxonomy_sha256": dataset.taxonomy_sha256,
        "package_sha256": _package_sha256(dataset),
        "code_config_sha256": expected_code_config_sha256,
        "signed_at_utc": _utc_now(),
        "signing_key_id": signing_key_id,
        "signing_public_key_sha256": public_key_sha256,
    }
    freeze = ReleaseFreezeManifest.model_validate(
        {**unsigned, "signature": _sign(unsigned, private_key)}, strict=True
    )
    _write_json_exclusive(freeze_path, freeze.model_dump(mode="json"))
    return freeze


def evaluate_signed_release_readiness(
    *,
    package_root: Path,
    manifest_path: Path,
    freeze_path: Path,
    output_path: Path,
    expected_code_config_sha256: str,
    signing_private_key_path: Path,
    trusted_public_key_path: Path,
    expected_public_key_sha256: str,
) -> ReleaseReadinessAttestation:
    """验证冻结 release package，并只写出 aggregate-only attestation."""
    root = package_root.resolve()
    manifest = _safe_path(root, manifest_path)
    private_key = _load_private_key(signing_private_key_path)
    trusted_public_key = _load_public_key(trusted_public_key_path)
    trusted_public_key_sha256 = _public_key_sha256(trusted_public_key)
    private_public_key_sha256 = _public_key_sha256(private_key.public_key())
    if trusted_public_key_sha256 != expected_public_key_sha256:
        raise ValueError("受信公钥 SHA-256 与发布方预期值不匹配。")
    if private_public_key_sha256 != expected_public_key_sha256:
        raise ValueError("attestation 签名私钥与受信公钥不匹配。")
    raw_manifest: dict[str, Any] | None = None
    sample_count = 0
    counts = {class_id: 0 for class_id in CRITICAL_CLASS_IDS}
    blockers: Counter[str] = Counter()
    dataset_version = "unavailable"
    manifest_sha = "0" * 64
    taxonomy_sha = "0" * 64
    package_sha = "0" * 64
    freeze_label = "unavailable"
    signing_key_id = "unavailable"
    ready = False
    package_verified = False

    try:
        try:
            raw_manifest = _read_strict_json(manifest)
        finally:
            sample_count, counts = _declared_release_aggregate(raw_manifest)
        manifest_sha = sha256(manifest.read_bytes()).hexdigest()
        try:
            raw_freeze = _read_strict_json(freeze_path)
        except Exception as exc:
            raise ValueError("冻结记录缺失或非法。") from exc
        freeze = ReleaseFreezeManifest.model_validate(raw_freeze, strict=True)
        freeze_label = freeze.freeze_label
        signing_key_id = freeze.signing_key_id
        dataset_version = freeze.dataset_version
        taxonomy_sha = freeze.taxonomy_sha256
        package_sha = freeze.package_sha256
        _verify_signature(freeze, trusted_public_key)
        if freeze.signing_public_key_sha256 != expected_public_key_sha256:
            raise ValueError("冻结签名公钥身份不匹配。")
        if freeze.code_config_sha256 != expected_code_config_sha256:
            raise ValueError("冻结绑定的 code/config SHA-256 不匹配。")
        if freeze.manifest_sha256 != manifest_sha:
            raise ValueError("冻结绑定的 Manifest SHA-256 不匹配。")

        dataset = load_v2_dataset_manifest(
            manifest,
            benchmark_root=root,
            gate_stage="v2_3_release_candidate",
        )
        dataset_version = dataset.manifest.dataset_version
        if any(
            value != PRODUCTION_CRITICAL_CLASS_MINIMUM
            for value in dataset.manifest.critical_class_minimums.as_dict().values()
        ):
            raise ValueError("production 关键类门槛必须固定为 10。")
        _validate_source_license_records(dataset)
        gate = evaluate_v2_dataset_stage_gate(
            dataset,
            stage="v2_3_release_candidate",
        )
        package_sha = _package_sha256(dataset)
        taxonomy_sha = dataset.taxonomy_sha256
        if freeze.dataset_version != dataset.manifest.dataset_version:
            raise ValueError("冻结绑定的 dataset version 不匹配。")
        if freeze.taxonomy_sha256 != taxonomy_sha:
            raise ValueError("冻结绑定的 taxonomy SHA-256 不匹配。")
        if freeze.package_sha256 != package_sha:
            raise ValueError("冻结绑定的 package SHA-256 不匹配。")
        package_verified = True
        if not gate.ready:
            for blocker in gate.blockers:
                if "insufficient_denominator" in blocker:
                    blockers["critical_class"] += 1
                elif "split_status" in blocker:
                    blockers["split_status"] += 1
                else:
                    blockers["stage_gate"] += 1
        ready = gate.ready and not blockers
    except Exception as exc:  # noqa: BLE001 - 对外必须统一脱敏
        blockers[_category_for_exception(exc)] += 1

    critical_classes = _critical_aggregates(counts)
    insufficient_count = sum(not item.sufficient for item in critical_classes)
    if insufficient_count:
        if not blockers["critical_class"]:
            blockers["critical_class"] = insufficient_count
        ready = False

    unsigned = {
        "schema_version": ATTESTATION_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "package_digest_version": PACKAGE_DIGEST_VERSION,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "stage": "v2_3_release_candidate",
        "ready": ready,
        "package_verified": package_verified,
        "freeze_label": freeze_label,
        "dataset_version": dataset_version,
        "release_sample_count": sample_count,
        "critical_classes": tuple(
            item.model_dump(mode="python") for item in critical_classes
        ),
        "manifest_sha256": manifest_sha,
        "taxonomy_sha256": taxonomy_sha,
        "package_sha256": package_sha,
        "code_config_sha256": expected_code_config_sha256,
        "executed_at_utc": _utc_now(),
        "signing_key_id": signing_key_id,
        "signing_public_key_sha256": expected_public_key_sha256,
        "blocker_category_counts": dict(sorted(blockers.items())),
    }
    attestation = ReleaseReadinessAttestation.model_validate(
        {**unsigned, "signature": _sign(unsigned, private_key)}, strict=True
    )
    _write_json_exclusive(output_path, attestation.model_dump(mode="json"))
    return attestation


def verify_release_readiness_attestation(
    *,
    attestation_path: Path,
    trusted_public_key_path: Path,
    expected_public_key_sha256: str,
    expected_code_config_sha256: str,
    expected_freeze_label: str,
    expected_stage: Literal["v2_3_release_candidate"],
) -> ReleaseReadinessAttestation:
    """仅以预先信任的 Ed25519 公钥验证 aggregate-only attestation."""
    if not re.fullmatch(_SHA256_PATTERN, expected_public_key_sha256):
        raise ValueError("consumer expected public key SHA-256 格式非法。")
    if not re.fullmatch(_SHA256_PATTERN, expected_code_config_sha256):
        raise ValueError("consumer expected code/config SHA-256 格式非法。")
    if not re.fullmatch(_SAFE_ID_PATTERN, expected_freeze_label):
        raise ValueError("consumer expected freeze label 格式非法。")
    if expected_stage != "v2_3_release_candidate":
        raise ValueError("consumer expected stage 必须是 v2_3_release_candidate。")
    attestation_bytes = attestation_path.read_bytes()
    _parse_strict_json_bytes(attestation_bytes)
    attestation = ReleaseReadinessAttestation.model_validate_json(
        attestation_bytes, strict=True
    )
    public_key = _load_public_key(trusted_public_key_path)
    actual_public_key_sha256 = _public_key_sha256(public_key)
    if actual_public_key_sha256 != expected_public_key_sha256:
        raise ValueError("受信公钥 SHA-256 与发布方预期值不匹配。")
    if attestation.signing_public_key_sha256 != expected_public_key_sha256:
        raise ValueError("attestation 声明的公钥身份不匹配。")
    _verify_signature(attestation, public_key)
    if attestation.code_config_sha256 != expected_code_config_sha256:
        raise ValueError("attestation code/config SHA-256 与 consumer 预期不匹配。")
    if attestation.freeze_label != expected_freeze_label:
        raise ValueError("attestation freeze label 与 consumer 预期不匹配。")
    if attestation.stage != expected_stage:
        raise ValueError("attestation stage 与 consumer 预期不匹配。")
    return attestation


__all__ = [
    "ATTESTATION_SCHEMA_VERSION",
    "FREEZE_SCHEMA_VERSION",
    "PACKAGE_DIGEST_VERSION",
    "PRODUCTION_CRITICAL_CLASS_MINIMUM",
    "TOOL_VERSION",
    "CriticalClassAggregate",
    "ReleaseFreezeManifest",
    "ReleaseReadinessAttestation",
    "create_signed_freeze",
    "evaluate_signed_release_readiness",
    "verify_release_readiness_attestation",
]
