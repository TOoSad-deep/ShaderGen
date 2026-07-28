"""复验历史 LayerPlan/direct GLSL suite，或显式运行当前冻结协议."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.services.layerplan_glsl_shadow_suite import (
    load_shadow_suite_gate,
    load_shadow_suite_manifest,
    require_current_protocol_for_live,
    run_shadow_suite,
    verify_shadow_suite_report,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer

ROOT = Path(__file__).resolve().parents[1]
HISTORICAL_V2_MANIFEST = ROOT / "benchmarks/layerplan_glsl_shadow/manifest_v2.yaml"
HISTORICAL_V2_GATE = ROOT / "benchmarks/layerplan_glsl_shadow/gate_v2.yaml"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LayerPlan/direct GLSL 四样本 × AB/BA shadow suite。",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="live 必须显式提供；--verify 缺省使用历史 v2。",
    )
    parser.add_argument(
        "--gate",
        default=None,
        help="必须与 --manifest 成对提供；--verify 缺省使用历史 v2。",
    )
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


def _resolve_protocol_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    manifest = Path(args.manifest) if args.manifest is not None else None
    gate = Path(args.gate) if args.gate is not None else None
    if (manifest is None) != (gate is None):
        raise ValueError("--manifest 与 --gate 必须成对提供。")
    if manifest is not None and gate is not None:
        return manifest, gate
    if args.verify is not None:
        return HISTORICAL_V2_MANIFEST, HISTORICAL_V2_GATE
    raise ValueError(
        "live 模式没有默认协议：仓库 v2 仅供历史 --verify；"
        "请显式提供当前 --manifest 与 --gate。"
    )


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
    try:
        manifest_path, gate_path = _resolve_protocol_paths(args)
        manifest = load_shadow_suite_manifest(manifest_path)
        gate = load_shadow_suite_gate(gate_path, manifest=manifest)
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
    args.manifest = str(manifest_path)
    args.gate = str(gate_path)
    if args.verify is not None:
        payload = verify_shadow_suite_report(
            Path(args.verify), manifest=manifest, gate=gate
        )
        print(  # noqa: T201
            f"verify ok: {args.verify} "
            f"outcome={payload['aggregate']['automatic_gate']['outcome']}"
        )
        return 0
    try:
        require_current_protocol_for_live(manifest, gate)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)  # noqa: T201
        return 2
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
