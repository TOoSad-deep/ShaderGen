"""运行独立 Node Lab 模型角色 benchmark；默认完全离线."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from agent.app.benchmarks.model_roles import (
    DEFAULT_MODEL_BENCHMARK_LAB_ROOT,
    DEFAULT_MODEL_BENCHMARK_MANIFEST,
    DEFAULT_MODEL_BENCHMARK_OUTPUT_ROOT,
    load_model_benchmark_manifest,
    run_model_benchmark,
)

EXIT_OK = 0
EXIT_CASE_FAILED = 1
EXIT_CONFIGURATION_ERROR = 2
EXIT_INTERNAL_ERROR = 3
EXIT_INTERRUPTED = 130


def _write_stdout(value: dict[str, object]) -> None:
    """Stdout 只输出一行稳定机器摘要."""
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=DEFAULT_MODEL_BENCHMARK_MANIFEST
    )
    parser.add_argument(
        "--output-root", type=Path, default=DEFAULT_MODEL_BENCHMARK_OUTPUT_ROOT
    )
    parser.add_argument(
        "--lab-root", type=Path, default=DEFAULT_MODEL_BENCHMARK_LAB_ROOT
    )
    parser.add_argument("--suite-run-id")
    parser.add_argument(
        "--execution-mode",
        choices=("fixture", "real"),
        default="fixture",
    )
    parser.add_argument("--allow-model-calls", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--require-passed", action="store_true")
    return parser


async def _run(args: argparse.Namespace) -> int:
    suite = load_model_benchmark_manifest(args.manifest)
    if args.validate_only:
        _write_stdout(
            {
                "suite_id": suite.manifest.suite_id,
                "status": "valid",
                "manifest_sha256": suite.manifest_sha256,
            }
        )
        return EXIT_OK

    gateway = None
    real_enabled = False
    if args.execution_mode == "real":
        real_enabled = (
            os.getenv("SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED", "").strip().lower()
            == "true"
        )
        if not args.allow_model_calls or not real_enabled:
            raise ValueError(
                "real 模式必须同时提供 --allow-model-calls，且设置 "
                "SHADERGEN_NODE_LAB_REAL_MODEL_ENABLED=true。"
            )
        # 必须在 manifest 全预算校验和双开关之后才构造供应商 Gateway。
        from agent.app.llms.gateway import LangChainLLMGateway

        gateway = LangChainLLMGateway()

    report = await run_model_benchmark(
        suite,
        output_root=args.output_root,
        lab_root=args.lab_root,
        suite_run_id=args.suite_run_id,
        execution_mode=args.execution_mode,
        allow_model_calls=args.allow_model_calls,
        real_model_enabled=real_enabled,
        gateway=gateway,
    )
    failed = int(report["failed_attempt_count"])
    _write_stdout(
        {
            "suite_id": str(report["suite_id"]),
            "suite_run_id": str(report["suite_run_id"]),
            "status": "passed" if failed == 0 else "failed",
            "report_path": str(
                Path(args.output_root).resolve()
                / str(report["suite_run_id"])
                / "report.json"
            ),
        }
    )
    return EXIT_CASE_FAILED if failed else EXIT_OK


def main() -> int:
    """解析参数并以稳定退出码运行 benchmark."""
    try:
        return asyncio.run(_run(_parser().parse_args()))
    except KeyboardInterrupt:
        sys.stderr.write("node-lab model benchmark interrupted; evidence preserved.\n")
        return EXIT_INTERRUPTED
    except (OSError, ValueError) as exc:
        sys.stderr.write(f"node-lab model benchmark failed: {exc}\n")
        return EXIT_CONFIGURATION_ERROR
    except Exception as exc:  # noqa: BLE001 - CLI 必须稳定区分内部错误
        sys.stderr.write(
            "node-lab model benchmark internal error: "
            f"{type(exc).__name__}; evidence preserved when available.\n"
        )
        return EXIT_INTERNAL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
