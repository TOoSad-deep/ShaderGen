#!/usr/bin/env python3
"""供独立保管人在封存环境中冻结并评估 V2 release package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from shaderforge.benchmark.v2_release_handoff import (
    ATTESTATION_SCHEMA_VERSION,
    FREEZE_SCHEMA_VERSION,
    create_signed_freeze,
    evaluate_signed_release_readiness,
    verify_release_readiness_attestation,
)


def _sha256(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise argparse.ArgumentTypeError("必须是 64 位小写 SHA-256。")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="V2 release-held-out 独立保管人冻结/readiness 工具。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze", help="验证 package 后写入签名冻结记录")
    _add_common(freeze)
    freeze.add_argument("--freeze-label", required=True)

    evaluate = subparsers.add_parser(
        "evaluate", help="复验冻结绑定并写出聚合 readiness attestation"
    )
    _add_common(evaluate)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--trusted-public-key", type=Path, required=True)
    evaluate.add_argument("--expected-public-key-sha256", type=_sha256, required=True)

    verify = subparsers.add_parser(
        "verify", help="开发侧用预先信任公钥验证 aggregate-only attestation"
    )
    verify.add_argument("--attestation", type=Path, required=True)
    verify.add_argument("--trusted-public-key", type=Path, required=True)
    verify.add_argument("--expected-public-key-sha256", type=_sha256, required=True)
    verify.add_argument("--expected-code-config-sha256", type=_sha256, required=True)
    verify.add_argument("--expected-freeze-label", required=True)
    verify.add_argument(
        "--expected-stage",
        choices=("v2_3_release_candidate",),
        required=True,
    )
    return parser


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument(
        "--manifest",
        type=Path,
        required=True,
        help="package root 内的三 split Manifest",
    )
    parser.add_argument("--freeze-manifest", type=Path, required=True)
    parser.add_argument(
        "--expected-code-config-sha256",
        type=_sha256,
        required=True,
    )
    parser.add_argument("--signing-private-key", type=Path, required=True)
    parser.add_argument("--signing-key-id", default="release-operator-key-v1")


def main() -> int:
    """执行独立保管人子命令，并保证 stdout 只含安全聚合."""
    args = _parser().parse_args()
    try:
        if args.command == "freeze":
            freeze = create_signed_freeze(
                package_root=args.package_root,
                manifest_path=args.manifest,
                freeze_path=args.freeze_manifest,
                expected_code_config_sha256=args.expected_code_config_sha256,
                freeze_label=args.freeze_label,
                signing_private_key_path=args.signing_private_key,
                signing_key_id=args.signing_key_id,
            )
            print(  # noqa: T201 - CLI 的 aggregate-only 标准输出
                json.dumps(
                    {
                        "frozen": True,
                        "schema_version": FREEZE_SCHEMA_VERSION,
                        "freeze_label": freeze.freeze_label,
                        "manifest_sha256": freeze.manifest_sha256,
                        "taxonomy_sha256": freeze.taxonomy_sha256,
                        "package_sha256": freeze.package_sha256,
                    },
                    sort_keys=True,
                )
            )
            return 0

        if args.command == "evaluate":
            attestation = evaluate_signed_release_readiness(
                package_root=args.package_root,
                manifest_path=args.manifest,
                freeze_path=args.freeze_manifest,
                output_path=args.output,
                expected_code_config_sha256=args.expected_code_config_sha256,
                signing_private_key_path=args.signing_private_key,
                trusted_public_key_path=args.trusted_public_key,
                expected_public_key_sha256=args.expected_public_key_sha256,
            )
        else:
            attestation = verify_release_readiness_attestation(
                attestation_path=args.attestation,
                trusted_public_key_path=args.trusted_public_key,
                expected_public_key_sha256=args.expected_public_key_sha256,
                expected_code_config_sha256=args.expected_code_config_sha256,
                expected_freeze_label=args.expected_freeze_label,
                expected_stage=args.expected_stage,
            )
        print(  # noqa: T201 - CLI 的 aggregate-only 标准输出
            json.dumps(
                {
                    "schema_version": ATTESTATION_SCHEMA_VERSION,
                    "ready": attestation.ready,
                    "package_verified": attestation.package_verified,
                    "critical_classes": [
                        item.model_dump(mode="json")
                        for item in attestation.critical_classes
                    ],
                    "blocker_category_counts": (
                        attestation.blocker_category_counts
                    ),
                },
                sort_keys=True,
            )
        )
        return 0 if attestation.ready else 2
    except Exception:  # noqa: BLE001 - CLI 不能把路径/逐例异常写到终端
        print(  # noqa: T201
            json.dumps(
                {
                    "ready": False,
                    "blocker_category_counts": {"invocation_or_freeze": 1},
                },
                sort_keys=True,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
