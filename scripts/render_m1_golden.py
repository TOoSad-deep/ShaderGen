"""渲染并落盘 M1 粉色凝胶 golden smoke 产物."""

from __future__ import annotations

import asyncio
from pathlib import Path

from shaderforge.evaluation import evaluate_render
from shaderforge.rendering import PlaywrightWebGL1Renderer, build_standalone_html
from shaderforge.store import RunArtifactStore

ROOT = Path(__file__).resolve().parents[1]
SHADER_PATH = ROOT / "benchmarks/png_to_shader_v1/golden/pink_gel.frag"
REFERENCE_PATH = ROOT / "benchmarks/png_to_shader_v1/images/pink_gel.png"
OUTPUT_PATH = ROOT / "output/playwright/m1-golden"


async def _main() -> None:
    source = SHADER_PATH.read_text(encoding="utf-8")
    async with PlaywrightWebGL1Renderer() as renderer:
        result = await renderer.render(source, 192, 192)
    if not result.success or result.image_bytes is None:
        raise RuntimeError(f"M1 golden 渲染失败：{result.compile.to_dict()}")

    score = evaluate_render(REFERENCE_PATH.read_bytes(), result.image_bytes)
    store = RunArtifactStore(OUTPUT_PATH)
    image_ref = store.write_bytes(
        "render.png", result.image_bytes, content_type="image/png"
    )
    store.write_text("shader.frag", source, content_type="text/plain; charset=utf-8")
    store.write_text(
        "pink-gel.html",
        build_standalone_html(source, 192, 192),
        content_type="text/html; charset=utf-8",
    )
    store.write_json(
        "smoke.json",
        {
            "renderer": result.to_dict(),
            "oracle": score.to_dict(),
            "image_artifact": image_ref,
        },
    )
    print(OUTPUT_PATH)  # noqa: T201


if __name__ == "__main__":
    asyncio.run(_main())
