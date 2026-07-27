"""执行 D086 冻结的 LayerPlan/direct GLSL shadow suite."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.services.layerplan_glsl_shadow_suite import (
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
    run_shadow_suite,
    verify_shadow_suite_report,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "benchmarks/layerplan_glsl_shadow/manifest_v1.yaml"
DEFAULT_GATE = ROOT / "benchmarks/layerplan_glsl_shadow/gate_v1.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LayerPlan/direct GLSL 四样本 × AB/BA shadow suite。",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--gate", default=str(DEFAULT_GATE))
    parser.add_argument("--output-root", default=None)
    parser.add_argument(
        "--allow-live-model",
        action="store_true",
        help="显式允许冻结 suite 的真实模型与 WebGL1 调用。",
    )
    parser.add_argument(
        "--verify",
        default=None,
        help="只复验已有 shadow-suite-* 目录及其引用的全部单 run。",
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> Path:
    manifest = load_shadow_suite_manifest(Path(args.manifest))
    gate = load_shadow_suite_gate(Path(args.gate), manifest=manifest)
    gateway = LangChainLLMGateway()
    async with PlaywrightWebGL1Renderer() as renderer:
        return await run_shadow_suite(
            gateway=gateway,
            renderer=renderer,
            manifest=manifest,
            gate=gate,
            output_root=Path(args.output_root),
        )


def main(argv: list[str] | None = None) -> int:
    """CLI 入口；默认拒绝触发真实模型."""
    args = _parse_args(argv)
    manifest = load_shadow_suite_manifest(Path(args.manifest))
    gate = load_shadow_suite_gate(Path(args.gate), manifest=manifest)
    if args.verify is not None:
        payload = verify_shadow_suite_report(
            Path(args.verify), manifest=manifest, gate=gate
        )
        print(  # noqa: T201
            f"verify ok: {args.verify} "
            f"outcome={payload['aggregate']['automatic_gate']['outcome']}"
        )
        return 0
    if not args.output_root:
        print("缺少 --output-root。", file=sys.stderr)  # noqa: T201
        return 2
    if not args.allow_live_model:
        print(  # noqa: T201
            "拒绝运行：suite 默认不调用真实模型。"
            "确认冻结协议后显式追加 --allow-live-model。",
            file=sys.stderr,
        )
        return 2
    suite_dir = asyncio.run(_run(args))
    print(f"suite report: {suite_dir / 'suite_report.json'}")  # noqa: T201
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
