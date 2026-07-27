"""构建或离线复验 LayerPlan shadow promotion 私有证据包."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.app.services.layerplan_glsl_promotion_evidence import (
    build_promotion_evidence_bundle,
    verify_promotion_evidence_bundle,
)
from agent.app.services.layerplan_glsl_shadow_suite import (
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/layerplan_glsl_shadow/manifest_v2.yaml"
DEFAULT_GATE = ROOT / "benchmarks/layerplan_glsl_shadow/gate_v2.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LayerPlan shadow promotion 私有 evidence bundle。"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build", help="严格复验后构建 write-once bundle。")
    build.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    build.add_argument("--gate", default=str(DEFAULT_GATE))
    build.add_argument("--suite-dir", required=True)
    build.add_argument("--package-dir", required=True)
    build.add_argument("--human-review", required=True)
    build.add_argument("--human-evaluation", required=True)
    build.add_argument("--output-root", required=True)
    verify = subparsers.add_parser("verify", help="完全离线递归复验 bundle。")
    verify.add_argument("--bundle-dir", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行私有 bundle CLI；只输出路径、hash 和 gate outcome."""
    args = _parse_args(argv)
    try:
        if args.command == "build":
            manifest = load_shadow_suite_manifest(Path(args.manifest))
            gate = load_shadow_suite_gate(Path(args.gate), manifest=manifest)
            bundle_dir = build_promotion_evidence_bundle(
                Path(args.suite_dir),
                package_dir=Path(args.package_dir),
                human_review_path=Path(args.human_review),
                human_evaluation_path=Path(args.human_evaluation),
                manifest=manifest,
                gate=gate,
                output_root=Path(args.output_root),
            )
            payload = verify_promotion_evidence_bundle(bundle_dir)
        else:
            bundle_dir = Path(args.bundle_dir)
            payload = verify_promotion_evidence_bundle(bundle_dir)
        print(  # noqa: T201
            f"path={bundle_dir} "
            f"sha256={payload['bundle_manifest_sha256']} "
            f"outcome={payload['human']['gate_outcome']}"
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
