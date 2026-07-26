"""LayerPlan/direct GLSL shadow A/B 离线运行入口（D084 第二阶段）.

显式 opt-in：缺省不运行真实模型，必须给出 ``--allow-live-model`` 才会构造
真实 ``LangChainLLMGateway`` 与 Playwright WebGL1 Renderer。详细证据
（LayerPlan、Spec、render、metric、ledger、arm identity、执行顺序与内容
hash）只写 ``--output-root`` 下的私有 run 目录；不调用
``LocalArtifactStore.register_run``，不接产品 API/manifest，不登记
durable evidence，也不触碰生产 ``png_to_shader_min`` Graph/runtime。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from agent.app.llms.gateway import LangChainLLMGateway
from agent.app.services.layerplan_glsl_shadow import (
    LayerPlanGlslShadowRunner,
    ShadowABConfig,
    shadow_run_id,
    verify_shadow_run,
    write_shadow_run,
)
from shaderforge.rendering import PlaywrightWebGL1Renderer


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LayerPlan/direct GLSL shadow A/B 离线 harness（私有证据）。",
    )
    parser.add_argument(
        "--verify",
        default=None,
        help="校验已有私有 run 目录的全部文件/报告哈希与权限后退出。",
    )
    parser.add_argument("--reference", help="参考图路径（PNG）。")
    parser.add_argument("--instruction", default="", help="用户意图文本。")
    parser.add_argument(
        "--output-root",
        default=None,
        help="显式私有输出根目录；run 证据只写该目录下。",
    )
    parser.add_argument(
        "--allow-live-model",
        action="store_true",
        help="显式 opt-in：允许真实模型调用与真实 WebGL1 渲染。",
    )
    parser.add_argument(
        "--direct-author-llm-budget",
        type=int,
        default=8,
        help="每臂 direct GLSL Author（Initial/Refine/repair）LLM 调用预算。",
    )
    parser.add_argument(
        "--compile-budget", type=int, default=8, help="每臂 compile 预算。"
    )
    parser.add_argument("--draw-budget", type=int, default=8, help="每臂 draw 预算。")
    parser.add_argument(
        "--refine-budget", type=int, default=2, help="每臂 Refine 次数预算。"
    )
    parser.add_argument(
        "--plan-llm-budget",
        type=int,
        default=2,
        help="VisualAnalysis/LayerPlan 的独立 LLM 预算（不占用任一臂 Author 预算）。",
    )
    parser.add_argument(
        "--arm-order",
        choices=("AB", "BA"),
        default="AB",
        help="两臂执行顺序，查看结果前冻结并写入报告。",
    )
    parser.add_argument(
        "--canvas",
        default=None,
        help="可选固定画布 WIDTHxHEIGHT；缺省按 scene_mvp 规则从参考图推导。",
    )
    parser.add_argument(
        "--content-type", default="image/png", help="参考图 content type。"
    )
    return parser.parse_args(argv)


def _parse_canvas(value: str | None) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    try:
        width_text, height_text = value.lower().split("x", 1)
        return int(width_text), int(height_text)
    except ValueError as exc:
        raise SystemExit(f"--canvas 必须是 WIDTHxHEIGHT 形式：{value}") from exc


async def _run(args: argparse.Namespace) -> Path:
    reference_path = Path(args.reference)
    if not reference_path.is_file():
        raise SystemExit(f"参考图不存在：{reference_path}")
    output_root = Path(args.output_root)
    canvas_width, canvas_height = _parse_canvas(args.canvas)
    config = ShadowABConfig(
        direct_author_llm_budget=args.direct_author_llm_budget,
        compile_budget_per_arm=args.compile_budget,
        draw_budget_per_arm=args.draw_budget,
        refine_budget_per_arm=args.refine_budget,
        plan_llm_budget=args.plan_llm_budget,
        arm_order=(args.arm_order[0], args.arm_order[1]),
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )
    gateway = LangChainLLMGateway()
    async with PlaywrightWebGL1Renderer() as renderer:
        runner = LayerPlanGlslShadowRunner(
            gateway=gateway,
            renderer=renderer,
            config=config,
        )
        result = await runner.run(
            reference_path.read_bytes(),
            content_type=args.content_type,
            instruction=args.instruction,
        )
    run_dir = write_shadow_run(result, output_root)
    arm_summary = {
        arm.arm_id: (arm.status, arm.inconclusive_code) for arm in result.arms
    }
    print(f"run_id: {shadow_run_id(result)}")  # noqa: T201
    print(f"status: {result.status} arms={arm_summary}")  # noqa: T201
    print(f"report: {run_dir / 'report.json'}")  # noqa: T201
    return run_dir


def main(argv: list[str] | None = None) -> int:
    """CLI 入口；未显式 opt-in 时拒绝运行真实模型."""
    args = _parse_args(argv)
    if args.verify is not None:
        payload = verify_shadow_run(Path(args.verify))
        print(f"verify ok: {args.verify} status={payload['status']}")  # noqa: T201
        return 0
    if not args.reference:
        print("缺少 --reference。", file=sys.stderr)  # noqa: T201
        return 2
    if not args.output_root:
        print("缺少 --output-root。", file=sys.stderr)  # noqa: T201
        return 2
    if not args.allow_live_model:
        print(  # noqa: T201
            "拒绝运行：shadow A/B 默认不运行真实模型。"
            "确认实验配置后显式追加 --allow-live-model。",
            file=sys.stderr,
        )
        return 2
    asyncio.run(_run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
