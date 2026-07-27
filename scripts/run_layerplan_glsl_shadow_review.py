"""构建、复验和评价 LayerPlan shadow suite v2 匿名盲评包."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from agent.app.services.layerplan_glsl_shadow_review import (
    evaluate_blind_review,
    verify_blind_review_package,
    write_blind_review_package,
)
from agent.app.services.layerplan_glsl_shadow_suite import (
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
)
from shaderforge.program_spec import canonical_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/layerplan_glsl_shadow/manifest_v2.yaml"
DEFAULT_GATE = ROOT / "benchmarks/layerplan_glsl_shadow/gate_v2.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LayerPlan shadow suite v2 匿名盲评。")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--gate", default=str(DEFAULT_GATE))
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="从已复验 suite 创建 write-once 包。")
    build.add_argument("--suite-dir", required=True)
    build.add_argument("--output-root", required=True)

    verify = subparsers.add_parser("verify", help="递归复验 suite 与盲评包。")
    verify.add_argument("--suite-dir", required=True)
    verify.add_argument("--package-dir", required=True)

    evaluate = subparsers.add_parser("evaluate", help="读取人工 A/B/tie JSON。")
    evaluate.add_argument("--suite-dir", required=True)
    evaluate.add_argument("--package-dir", required=True)
    evaluate.add_argument("--human-review", required=True)
    evaluate.add_argument(
        "--output",
        help="可选：写入 canonical evaluation JSON（0600，拒绝覆盖）。",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """执行盲评 CLI；任何契约错误均 fail-closed 返回 2."""
    args = _parse_args(argv)
    try:
        manifest = load_shadow_suite_manifest(Path(args.manifest))
        gate = load_shadow_suite_gate(Path(args.gate), manifest=manifest)
        suite_dir = Path(args.suite_dir)
        if args.command == "build":
            package_dir = write_blind_review_package(
                suite_dir,
                manifest=manifest,
                gate=gate,
                output_root=Path(args.output_root),
            )
            print(f"review package: {package_dir}")  # noqa: T201
        elif args.command == "verify":
            payload = verify_blind_review_package(
                Path(args.package_dir),
                suite_dir=suite_dir,
                manifest=manifest,
                gate=gate,
            )
            print(f"verify ok: {payload['package_id']}")  # noqa: T201
        else:
            payload = evaluate_blind_review(
                Path(args.package_dir),
                suite_dir=suite_dir,
                human_review_path=Path(args.human_review),
                manifest=manifest,
                gate=gate,
            )
            serialized = canonical_json(payload) + "\n"
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with output.open("x", encoding="utf-8") as stream:
                    stream.write(serialized)
                output.chmod(0o600)
                print(f"human evaluation: {output}")  # noqa: T201
            else:
                print(serialized, end="")  # noqa: T201
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
