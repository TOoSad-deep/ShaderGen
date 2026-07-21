from __future__ import annotations

import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from PIL import Image

from shaderforge.benchmark.v2_dataset import load_v2_dataset_manifest
from shaderforge.benchmark.v2_release_handoff import (
    FREEZE_SCHEMA_VERSION,
    PACKAGE_DIGEST_VERSION,
    SIGNATURE_ALGORITHM,
    TOOL_VERSION,
    _package_sha256,
    create_signed_freeze,
    evaluate_signed_release_readiness,
    verify_release_readiness_attestation,
)
from shaderforge.contracts.canonical import canonical_json_bytes

REPO_ROOT = Path(__file__).resolve().parents[2]
TAXONOMY = (
    REPO_ROOT / "benchmarks/png_to_shader_v2/expected_primitives_taxonomy.v1.json"
)
CODE_CONFIG_SHA256 = "a" * 64


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _png(path: Path, color: tuple[int, int, int, int]) -> str:
    Image.new("RGBA", (2, 2), color).save(path)
    return sha256(path.read_bytes()).hexdigest()


def _sample(
    *,
    split: str,
    index: int,
    topology: str,
    image: str,
    image_sha256: str,
    primitive_id: str,
) -> dict[str, Any]:
    return {
        "case_id": f"sealed-{split}-{index:02d}",
        "dataset_role": "evaluation",
        "source_suite_id": f"source-{split}",
        "image": image,
        "sha256": image_sha256,
        "resolution": [2, 2],
        "visual_family": f"family-{split}",
        "hash_group": f"hash-group-{split}",
        "topology": topology,
        "instance_count": 2,
        "hole_count": 1,
        "required_layers": ["highlight", "rim", "outline"],
        "expected_primitives": {
            "taxonomy_version": "png_to_shader_expected_primitives_v1",
            "items": [primitive_id],
        },
    }


@pytest.fixture
def sealed_package(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "external-sealed-package"
    root.mkdir()
    taxonomy_path = root / "taxonomy.json"
    taxonomy_path.write_bytes(TAXONOMY.read_bytes())
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    primitive_id = taxonomy["primitives"][0]["primitive_id"]

    source_records = []
    split_payloads = []
    colors = {
        "development": ((10, 20, 30, 255), (11, 21, 31, 255)),
        "validation": ((40, 50, 60, 255), (41, 51, 61, 255)),
        "release-held-out": ((70, 80, 90, 255), (71, 81, 91, 255)),
    }
    for split in ("development", "validation", "release-held-out"):
        provenance = root / f"provenance-{split}.txt"
        source_url = f"https://example.test/{split}/source"
        license_id = "CC0-1.0"
        license_url = "https://creativecommons.org/publicdomain/zero/1.0/"
        provenance.write_text(
            "\n".join((source_url, license_id, license_url, "selected_by: custodian")),
            encoding="utf-8",
        )
        source_records.append(
            {
                "source_suite_id": f"source-{split}",
                "provenance_path": provenance.name,
                "provenance_sha256": sha256(provenance.read_bytes()).hexdigest(),
                "source_url": source_url,
                "license_id": license_id,
                "license_url": license_url,
            }
        )
        ring_image = root / f"image-{split}-ring.png"
        hollow_image = root / f"image-{split}-hollow.png"
        ring_sha = _png(ring_image, colors[split][0])
        hollow_sha = _png(hollow_image, colors[split][1])
        count = 1 if split == "development" else 20
        samples = []
        for index in range(count):
            topology = "ring" if index < 10 else "hollow"
            image_path = ring_image if topology == "ring" else hollow_image
            image_sha = ring_sha if topology == "ring" else hollow_sha
            samples.append(
                _sample(
                    split=split,
                    index=index,
                    topology=topology,
                    image=image_path.name,
                    image_sha256=image_sha,
                    primitive_id=primitive_id,
                )
            )
        split_payloads.append(
            {
                "name": split,
                "status": "available",
                "access_policy": {
                    "development": "development",
                    "validation": "visible_validation",
                    "release-held-out": "sealed_release_test",
                }[split],
                "purpose": f"synthetic {split}",
                "samples": samples,
            }
        )

    manifest = {
        "schema_version": "png_to_shader_dataset_manifest_v1",
        "manifest_id": "synthetic-release-package",
        "dataset_version": "synthetic-v1",
        "contract_id": "webgl1_static_no_texture_v1",
        "coordinate_system": "shader_uv_bottom_left",
        "split_policy_version": "visual_family_hash_group_v1",
        "expected_primitives_taxonomy": {
            "path": taxonomy_path.name,
            "sha256": sha256(taxonomy_path.read_bytes()).hexdigest(),
            "taxonomy_version": "png_to_shader_expected_primitives_v1",
            "node_registry_version": "effect_node_registry_v0",
        },
        "source_records": source_records,
        "critical_class_minimums": {
            "multi_instance": 10,
            "ring": 10,
            "hollow": 10,
            "required_highlight": 10,
            "required_rim": 10,
            "required_outline": 10,
        },
        "splits": split_payloads,
    }
    manifest_path = root / "manifest.json"
    _write_json(manifest_path, manifest)
    private_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "operator-private.pem"
    key_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    public_key_path = tmp_path / "operator-public.pem"
    public_key = private_key.public_key()
    public_key_path.write_bytes(
        public_key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key_sha256 = sha256(
        public_key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).hexdigest()
    return {
        "root": root,
        "manifest": manifest_path,
        "key": key_path,
        "public_key": public_key_path,
        "public_key_sha256": Path(public_key_sha256),
        "freeze": tmp_path / "release.freeze.json",
        "output": tmp_path / "release.attestation.json",
    }


def _freeze(sealed_package: dict[str, Path]) -> None:
    create_signed_freeze(
        package_root=sealed_package["root"],
        manifest_path=sealed_package["manifest"],
        freeze_path=sealed_package["freeze"],
        expected_code_config_sha256=CODE_CONFIG_SHA256,
        freeze_label="v2.3-rc1",
        signing_private_key_path=sealed_package["key"],
        signing_key_id="custodian-test-key",
    )


def _write_manual_freeze(
    package: dict[str, Path],
    *,
    package_sha256: str = "0" * 64,
) -> None:
    raw = json.loads(package["manifest"].read_text(encoding="utf-8"))
    private_key = serialization.load_pem_private_key(
        package["key"].read_bytes(), password=None
    )
    assert isinstance(private_key, Ed25519PrivateKey)
    public_key_sha256 = str(package["public_key_sha256"])
    unsigned = {
        "schema_version": FREEZE_SCHEMA_VERSION,
        "tool_version": TOOL_VERSION,
        "package_digest_version": PACKAGE_DIGEST_VERSION,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "freeze_label": "v2.3-rc1",
        "dataset_version": raw["dataset_version"],
        "manifest_sha256": sha256(package["manifest"].read_bytes()).hexdigest(),
        "taxonomy_sha256": raw["expected_primitives_taxonomy"]["sha256"],
        "package_sha256": package_sha256,
        "code_config_sha256": CODE_CONFIG_SHA256,
        "signed_at_utc": "2026-07-20T00:00:00Z",
        "signing_key_id": "custodian-test-key",
        "signing_public_key_sha256": public_key_sha256,
    }
    signature = private_key.sign(canonical_json_bytes(unsigned)).hex()
    _write_json(package["freeze"], {**unsigned, "signature": signature})


def _evaluate(package: dict[str, Path]):
    return evaluate_signed_release_readiness(
        package_root=package["root"],
        manifest_path=package["manifest"],
        freeze_path=package["freeze"],
        output_path=package["output"],
        expected_code_config_sha256=CODE_CONFIG_SHA256,
        signing_private_key_path=package["key"],
        trusted_public_key_path=package["public_key"],
        expected_public_key_sha256=str(package["public_key_sha256"]),
    )


def test_ready_attestation_is_aggregate_only_and_exclusive_create(
    sealed_package: dict[str, Path],
) -> None:
    _freeze(sealed_package)
    attestation = _evaluate(sealed_package)

    assert attestation.ready is True
    assert attestation.package_verified is True
    assert attestation.release_sample_count == 20
    assert [item.numerator for item in attestation.critical_classes] == [20, 10, 10, 20, 20, 20]
    serialized = sealed_package["output"].read_text(encoding="utf-8")
    for forbidden in (
        "sealed-release-held-out-00",
        "image-release-held-out",
        str(sealed_package["root"]),
        "https://example.test/release-held-out/source",
        "required_layers",
        "case_id",
    ):
        assert forbidden not in serialized

    with pytest.raises(FileExistsError):
        _evaluate(sealed_package)


@pytest.mark.parametrize(
    ("tamper_target", "expected_category"),
    (("image", "package_integrity"), ("license", "source_license")),
)
def test_integrity_failures_keep_aggregate_denominators_and_are_sanitized(
    sealed_package: dict[str, Path],
    tamper_target: str,
    expected_category: str,
) -> None:
    if tamper_target == "image":
        _freeze(sealed_package)
        target = sealed_package["root"] / "image-release-held-out-ring.png"
        target.write_bytes(target.read_bytes() + b"tamper")
        forbidden = target.name
    else:
        raw = json.loads(sealed_package["manifest"].read_text(encoding="utf-8"))
        raw["source_records"][2]["license_id"] = ""
        _write_json(sealed_package["manifest"], raw)
        _write_manual_freeze(sealed_package)
        forbidden = "source-release-held-out"

    attestation = _evaluate(sealed_package)

    assert attestation.ready is False
    assert attestation.package_verified is False
    assert attestation.release_sample_count == 20
    assert attestation.critical_classes[1].numerator == 10
    assert attestation.blocker_category_counts[expected_category] == 1
    serialized = sealed_package["output"].read_text(encoding="utf-8")
    assert forbidden not in serialized
    assert str(sealed_package["root"]) not in serialized


def test_cross_split_pollution_is_fail_closed_without_identity_leak(
    sealed_package: dict[str, Path],
) -> None:
    raw = json.loads(sealed_package["manifest"].read_text(encoding="utf-8"))
    raw["splits"][2]["samples"][0]["visual_family"] = "family-validation"
    _write_json(sealed_package["manifest"], raw)
    _write_manual_freeze(sealed_package)

    attestation = _evaluate(sealed_package)

    assert attestation.ready is False
    assert attestation.package_verified is False
    assert attestation.release_sample_count == 20
    assert attestation.blocker_category_counts["cross_split_contamination"] == 1
    assert "family-validation" not in sealed_package["output"].read_text()


def test_critical_class_shortfall_remains_in_denominator(
    sealed_package: dict[str, Path],
) -> None:
    raw = json.loads(sealed_package["manifest"].read_text(encoding="utf-8"))
    raw["splits"][2]["samples"] = raw["splits"][2]["samples"][:19]
    _write_json(sealed_package["manifest"], raw)
    dataset = load_v2_dataset_manifest(
        sealed_package["manifest"],
        benchmark_root=sealed_package["root"],
        gate_stage="v2_3_release_candidate",
    )
    _write_manual_freeze(sealed_package, package_sha256=_package_sha256(dataset))

    attestation = _evaluate(sealed_package)

    assert attestation.ready is False
    assert attestation.release_sample_count == 19
    assert attestation.package_verified is True
    hollow = next(
        item for item in attestation.critical_classes if item.class_id == "hollow"
    )
    assert (hollow.numerator, hollow.denominator, hollow.sufficient) == (9, 10, False)
    assert attestation.blocker_category_counts["critical_class"] >= 1


def test_path_traversal_and_duplicate_manifest_key_do_not_leak(
    sealed_package: dict[str, Path],
) -> None:
    raw = json.loads(sealed_package["manifest"].read_text(encoding="utf-8"))
    raw["splits"][2]["samples"][0]["image"] = "../../private-secret.png"
    _write_json(sealed_package["manifest"], raw)
    _write_manual_freeze(sealed_package)
    attestation = _evaluate(sealed_package)
    assert attestation.ready is False
    assert "private-secret" not in sealed_package["output"].read_text()

    sealed_package["output"].unlink()
    sealed_package["freeze"].unlink()
    text = sealed_package["manifest"].read_text(encoding="utf-8")
    sealed_package["manifest"].write_text(
        text.replace(
            '"manifest_id": "synthetic-release-package",',
            '"manifest_id": "synthetic-release-package",\n  "manifest_id": "duplicate",',
            1,
        ),
        encoding="utf-8",
    )
    _write_manual_freeze(sealed_package)
    attestation = _evaluate(sealed_package)
    assert attestation.ready is False
    assert attestation.blocker_category_counts["manifest_schema"] == 1
    assert "duplicate" not in sealed_package["output"].read_text()


def test_unsigned_freeze_is_rejected(
    sealed_package: dict[str, Path],
) -> None:
    _freeze(sealed_package)
    raw = json.loads(sealed_package["freeze"].read_text(encoding="utf-8"))
    raw["signature"] = "0" * 128
    _write_json(sealed_package["freeze"], raw)
    attestation = _evaluate(sealed_package)
    assert attestation.ready is False
    assert attestation.blocker_category_counts["freeze_binding"] == 1


def test_code_config_hash_mismatch_is_rejected(
    sealed_package: dict[str, Path],
) -> None:
    _freeze(sealed_package)
    attestation = evaluate_signed_release_readiness(
        package_root=sealed_package["root"],
        manifest_path=sealed_package["manifest"],
        freeze_path=sealed_package["freeze"],
        output_path=sealed_package["output"],
        expected_code_config_sha256="b" * 64,
        signing_private_key_path=sealed_package["key"],
        trusted_public_key_path=sealed_package["public_key"],
        expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
    )
    assert attestation.ready is False
    assert attestation.package_verified is False
    assert attestation.blocker_category_counts["freeze_binding"] == 1


def test_duplicate_freeze_key_is_fail_closed_with_denominators(
    sealed_package: dict[str, Path],
) -> None:
    _freeze(sealed_package)
    text = sealed_package["freeze"].read_text(encoding="utf-8")
    sealed_package["freeze"].write_text(
        text.replace(
            '"freeze_label": "v2.3-rc1",',
            '"freeze_label": "v2.3-rc1",\n  "freeze_label": "duplicate",',
            1,
        ),
        encoding="utf-8",
    )

    attestation = _evaluate(sealed_package)

    assert attestation.ready is False
    assert attestation.release_sample_count == 20
    assert attestation.blocker_category_counts["freeze_binding"] == 1
    assert "duplicate" not in sealed_package["output"].read_text()


def test_cli_stdout_is_aggregate_only(sealed_package: dict[str, Path]) -> None:
    _freeze(sealed_package)
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/run_v2_release_operator_handoff.py"),
            "evaluate",
            "--package-root",
            str(sealed_package["root"]),
            "--manifest",
            "manifest.json",
            "--freeze-manifest",
            str(sealed_package["freeze"]),
            "--expected-code-config-sha256",
            CODE_CONFIG_SHA256,
            "--signing-private-key",
            str(sealed_package["key"]),
            "--trusted-public-key",
            str(sealed_package["public_key"]),
            "--expected-public-key-sha256",
            str(sealed_package["public_key_sha256"]),
            "--output",
            str(sealed_package["output"]),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    stdout = json.loads(result.stdout)
    assert stdout["ready"] is True
    assert "critical_classes" in stdout
    assert result.stderr == ""
    for forbidden in (
        "sealed-release-held-out",
        "image-release-held-out",
        str(sealed_package["root"]),
        "https://example.test/release-held-out/source",
        "case_id",
    ):
        assert forbidden not in result.stdout

    verify_result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts/run_v2_release_operator_handoff.py"),
            "verify",
            "--attestation",
            str(sealed_package["output"]),
            "--trusted-public-key",
            str(sealed_package["public_key"]),
            "--expected-public-key-sha256",
            str(sealed_package["public_key_sha256"]),
            "--expected-code-config-sha256",
            CODE_CONFIG_SHA256,
            "--expected-freeze-label",
            "v2.3-rc1",
            "--expected-stage",
            "v2_3_release_candidate",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert verify_result.returncode == 0
    assert json.loads(verify_result.stdout)["ready"] is True
    assert str(sealed_package["output"]) not in verify_result.stdout


def test_production_cli_cannot_lower_critical_class_minimum(
    sealed_package: dict[str, Path],
) -> None:
    raw = json.loads(sealed_package["manifest"].read_text(encoding="utf-8"))
    raw["critical_class_minimums"]["ring"] = 1
    _write_json(sealed_package["manifest"], raw)

    with pytest.raises(ValueError):
        _freeze(sealed_package)
    assert not sealed_package["freeze"].exists()


def test_public_verifier_rejects_wrong_key_tamper_and_trust_root_replacement(
    sealed_package: dict[str, Path],
    tmp_path: Path,
) -> None:
    _freeze(sealed_package)
    attestation = _evaluate(sealed_package)
    verified = verify_release_readiness_attestation(
        attestation_path=sealed_package["output"],
        trusted_public_key_path=sealed_package["public_key"],
        expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
        expected_code_config_sha256=CODE_CONFIG_SHA256,
        expected_freeze_label="v2.3-rc1",
        expected_stage="v2_3_release_candidate",
    )
    assert verified == attestation

    with pytest.raises(ValueError, match="code/config"):
        verify_release_readiness_attestation(
            attestation_path=sealed_package["output"],
            trusted_public_key_path=sealed_package["public_key"],
            expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
            expected_code_config_sha256="b" * 64,
            expected_freeze_label="v2.3-rc1",
            expected_stage="v2_3_release_candidate",
        )
    with pytest.raises(ValueError, match="freeze label"):
        verify_release_readiness_attestation(
            attestation_path=sealed_package["output"],
            trusted_public_key_path=sealed_package["public_key"],
            expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
            expected_code_config_sha256=CODE_CONFIG_SHA256,
            expected_freeze_label="v2.3-rc2",
            expected_stage="v2_3_release_candidate",
        )
    with pytest.raises(ValueError, match="expected stage"):
        verify_release_readiness_attestation(
            attestation_path=sealed_package["output"],
            trusted_public_key_path=sealed_package["public_key"],
            expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
            expected_code_config_sha256=CODE_CONFIG_SHA256,
            expected_freeze_label="v2.3-rc1",
            expected_stage="v2_3_graph_conformance",  # type: ignore[arg-type]
        )

    other_private = Ed25519PrivateKey.generate()
    other_public = other_private.public_key()
    other_public_path = tmp_path / "untrusted-public.pem"
    other_public_path.write_bytes(
        other_public.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    other_sha256 = sha256(
        other_public.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    ).hexdigest()
    with pytest.raises(ValueError, match="预期值"):
        verify_release_readiness_attestation(
            attestation_path=sealed_package["output"],
            trusted_public_key_path=other_public_path,
            expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
            expected_code_config_sha256=CODE_CONFIG_SHA256,
            expected_freeze_label="v2.3-rc1",
            expected_stage="v2_3_release_candidate",
        )

    raw = json.loads(sealed_package["output"].read_text(encoding="utf-8"))
    raw["release_sample_count"] = 21
    _write_json(sealed_package["output"], raw)
    with pytest.raises(ValueError, match="签名"):
        verify_release_readiness_attestation(
            attestation_path=sealed_package["output"],
            trusted_public_key_path=sealed_package["public_key"],
            expected_public_key_sha256=str(sealed_package["public_key_sha256"]),
            expected_code_config_sha256=CODE_CONFIG_SHA256,
            expected_freeze_label="v2.3-rc1",
            expected_stage="v2_3_release_candidate",
        )

    raw["signing_public_key_sha256"] = other_sha256
    _write_json(sealed_package["output"], raw)
    with pytest.raises(ValueError, match="签名"):
        verify_release_readiness_attestation(
            attestation_path=sealed_package["output"],
            trusted_public_key_path=other_public_path,
            expected_public_key_sha256=other_sha256,
            expected_code_config_sha256=CODE_CONFIG_SHA256,
            expected_freeze_label="v2.3-rc1",
            expected_stage="v2_3_release_candidate",
        )
