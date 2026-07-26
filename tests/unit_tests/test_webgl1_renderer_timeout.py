"""Playwright WebGL1 renderer 的 prepare/draw 有界超时回归.

用挂起的 fake page 证明：模型 GLSL 导致 page/GPU 长期阻塞时，prepare 与
draw 都在有界超时内收敛为 ``RendererUnavailableError`` 并重置 worker，
绝不无限等待。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from shaderforge.rendering import (
    PlaywrightWebGL1Renderer,
    RendererUnavailableError,
)
from shaderforge.rendering.webgl1_renderer import (
    RENDERER_VERSION,
    PreparedWebGL1Renderer,
)

_VALID_SOURCE = (
    "precision mediump float;\n"
    "varying vec2 v_uv;\n"
    "uniform sampler2D u_image;\n"
    "uniform vec2 u_resolution;\n"
    "uniform float u_time;\n"
    "void main(){gl_FragColor=vec4(1.0);}\n"
)


class _HangingPage:
    """evaluate 永不返回的 fake page，用于触发有界超时."""

    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def evaluate(self, *_: Any, **__: Any) -> Any:
        await asyncio.sleep(3600)
        return None

    async def close(self) -> None:
        self.closed = True

    def on(self, *_: Any, **__: Any) -> None:
        return None


class _FakeBrowser:
    """占位 browser，仅满足 prepare 的存在性检查与 close 调用."""

    async def close(self) -> None:
        return None


def _renderer_with_hanging_page() -> PlaywrightWebGL1Renderer:
    renderer = PlaywrightWebGL1Renderer(
        replay_on_worker_failure=0,
        prepare_timeout_ms=50,
        draw_timeout_ms=50,
    )
    renderer._page = _HangingPage()  # type: ignore[assignment]
    renderer._browser = _FakeBrowser()  # type: ignore[assignment]
    return renderer


@pytest.mark.anyio
async def test_prepare_timeout_resets_worker_and_fails_closed() -> None:
    renderer = _renderer_with_hanging_page()

    with pytest.raises(RendererUnavailableError) as excinfo:
        await renderer.prepare(_VALID_SOURCE, 64, 64, {})

    cause = excinfo.value.__cause__
    assert cause is not None and "超时" in str(cause)
    assert renderer._page is None, "超时后 worker 必须重置以便下次重建"


@pytest.mark.anyio
async def test_draw_timeout_resets_worker_and_fails_closed() -> None:
    renderer = _renderer_with_hanging_page()
    prepared = PreparedWebGL1Renderer(
        owner=renderer,
        prepared_id="fake-prepared",
        width=64,
        height=64,
        uniform_schema={},
        compile_result=None,  # type: ignore[arg-type]
        metadata=None,
        prepare_duration_ms=1.0,
        source_sha256="0" * 64,
    )
    renderer._prepared.add(prepared)

    with pytest.raises(RendererUnavailableError, match="超时"):
        await prepared.render_uniforms({})

    assert renderer._page is None, "draw 超时后 worker 必须重置以便下次重建"
    assert prepared._closed, "重置时必须在 Python 侧失效 prepared handle"
    assert not renderer._prepared, "重置不得再执行已挂起 page 上的 closePrepared"


@pytest.mark.anyio
async def test_legacy_render_timeout_also_resets_worker() -> None:
    renderer = _renderer_with_hanging_page()

    with pytest.raises(RendererUnavailableError):
        await renderer.render(_VALID_SOURCE, 64, 64)

    assert renderer._page is None


@pytest.mark.anyio
async def test_timeout_config_must_be_positive_finite() -> None:
    with pytest.raises(ValueError):
        PlaywrightWebGL1Renderer(prepare_timeout_ms=0)
    with pytest.raises(ValueError):
        PlaywrightWebGL1Renderer(draw_timeout_ms=float("inf"))


def test_renderer_version_unchanged() -> None:
    assert RENDERER_VERSION == "playwright_webgl1_v1"
