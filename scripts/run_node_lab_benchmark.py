"""运行 Node Lab AI-off 模块 benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from agent.app.nodes.png_to_shader_v1.integrations.node_lab.suites import (
    build_png_to_shader_v1_suite_registry,
)
from agent.app.services.node_lab import create_node_lab_application
from nodelab.benchmark import compare_benchmark_reports

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/node_lab/png_to_shader_v1/manifest.yaml"
DEFAULT_OUTPUT_ROOT = ROOT / "output/benchmarks/node-lab"
DEFAULT_LAB_ROOT = ROOT / "output/node-lab/benchmark-runs"
EXIT_OK = 0
EXIT_CASE_FAILED = 1
EXIT_CONFIGURATION_ERROR = 2
EXIT_INTERNAL_ERROR = 3
EXIT_INTERRUPTED = 130
SUITES = build_png_to_shader_v1_suite_registry()


def _write_stdout(value: dict[str, object]) -> None:
    """Stdout 只输出一行稳定机器摘要."""
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--manifest", type=Path)
    source.add_argument(
        "--suite-id",
        choices=SUITES.describe(),
        help="选择仓库内版本化 AI-off suite；与 --manifest 互斥。",
    )
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--lab-root", type=Path, default=DEFAULT_LAB_ROOT)
    parser.add_argument("--suite-run-id")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-passed", action="store_true")
    parser.add_argument("--compare-baseline", type=Path)
    parser.add_argument("--compare-candidate", type=Path)
    return parser


async def _run(args: argparse.Namespace) -> int:
    if bool(args.compare_baseline) != bool(args.compare_candidate):
        raise ValueError("比较报告时必须同时提供 baseline 和 candidate。")
    if args.compare_baseline and args.compare_candidate:
        comparison = compare_benchmark_reports(
            args.compare_baseline,
            args.compare_candidate,
        )
        _write_stdout(comparison)
        return EXIT_OK

    manifest = (
        SUITES.resolve(args.suite_id)
        if args.suite_id is not None
        else args.manifest or DEFAULT_MANIFEST
    )
    application = create_node_lab_application(root=args.lab_root)
    summary = application.validate_suite(manifest)
    if args.validate_only:
        _write_stdout(
            {
                "suite_id": str(summary["suite_id"]),
                "status": "valid",
                "manifest_sha256": str(summary["manifest_sha256"]),
            }
        )
        return EXIT_OK
    report = await application.run_suite(
        manifest,
        output_root=args.output_root,
        suite_run_id=args.suite_run_id,
    )
    failed_count = int(report["failed_attempt_count"])
    suite_run_id = str(report["suite_run_id"])
    _write_stdout(
        {
            "suite_id": str(report["suite_id"]),
            "suite_run_id": suite_run_id,
            "status": "passed" if failed_count == 0 else "failed",
            "report_path": str(
                Path(args.output_root).resolve() / suite_run_id / "report.json"
            ),
        }
    )
    return EXIT_CASE_FAILED if failed_count else EXIT_OK


def main() -> int:
    """解析参数并运行异步 benchmark."""
    try:
        return asyncio.run(_run(_parser().parse_args()))
    except KeyboardInterrupt:
        sys.stderr.write("node-lab benchmark interrupted; evidence preserved.\n")
        return EXIT_INTERRUPTED
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"node-lab benchmark failed: {exc}\n")
        return EXIT_CONFIGURATION_ERROR
    except Exception as exc:  # noqa: BLE001 - CLI 必须稳定区分内部错误
        sys.stderr.write(
            "node-lab benchmark internal error: "
            f"{type(exc).__name__}; evidence preserved when available.\n"
        )
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
