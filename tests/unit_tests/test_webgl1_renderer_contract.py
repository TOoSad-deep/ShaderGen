import asyncio
from hashlib import sha256
from unittest.mock import AsyncMock

import pytest

from shaderforge.rendering import (
    CompileResult,
    PlaywrightWebGL1Renderer,
    RendererUnavailableError,
    RenderResult,
    build_standalone_html,
)
from shaderforge.validation import validate_shader

VALID_SHADER = """precision mediump float;
varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;
void main() {
    gl_FragColor = vec4(v_uv, 0.5, 1.0);
}
"""


@pytest.mark.anyio
async def test_static_rejection_does_not_start_browser_or_return_png():
    renderer = PlaywrightWebGL1Renderer()
    invalid = VALID_SHADER.replace(
        "gl_FragColor = vec4(v_uv, 0.5, 1.0);",
        "gl_FragColor = texture2D(u_image, v_uv);",
    )

    result = await renderer.render(invalid, 64, 64)

    assert not result.success
    assert result.image_bytes is None
    assert result.image_sha256 is None
    assert result.compile.draw_error == "static_validation_failed"
    assert {item.code for item in result.compile.static_validation.errors} == {
        "texture_sampling"
    }
    assert renderer._page is None


@pytest.mark.anyio
@pytest.mark.parametrize("width,height", [(0, 64), (64, -1), (1025, 64)])
async def test_renderer_rejects_dimensions_outside_contract(width: int, height: int):
    renderer = PlaywrightWebGL1Renderer()
    with pytest.raises(ValueError):
        await renderer.render(VALID_SHADER, width, height)


def test_standalone_html_uses_same_host_and_escapes_script_terminator():
    source = VALID_SHADER + "\n// </script><script>window.bad = true;</script>"

    html = build_standalone_html(source, 80, 72)

    assert "window.__renderShader" in html
    assert 'canvas.getContext("webgl"' in html
    assert 'antialias: false' in html
    assert 'preserveDrawingBuffer: true' in html
    assert "createTexture" not in html
    assert "bindTexture" not in html
    assert '"width": 80' in html
    assert "<\\/script>" in html
    assert "window.bad = true;</script>" not in html


def test_result_hash_is_content_addressed():
    image = b"png-bytes"
    result = RenderResult(
        success=True,
        image_bytes=image,
        width=1,
        height=1,
        compile=CompileResult(
            success=True,
            vertex_log="",
            fragment_log="",
            link_log="",
            draw_error=None,
            static_validation=validate_shader(VALID_SHADER),
        ),
        console_errors=(),
        metadata=None,
        duration_ms=1.0,
    )

    assert result.image_sha256 == sha256(image).hexdigest()
    assert result.to_dict()["image_size_bytes"] == len(image)


@pytest.mark.anyio
async def test_renderer_close_keeps_resources_for_retry_after_timeout() -> None:
    class TimeoutOncePage:
        def __init__(self) -> None:
            self.close_calls = 0
            self.closed = False

        def is_closed(self) -> bool:
            return self.closed

        async def close(self) -> None:
            self.close_calls += 1
            if self.close_calls == 1:
                await asyncio.Event().wait()
            self.closed = True

    class RecordingClosable:
        def __init__(self) -> None:
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

        async def stop(self) -> None:
            self.close_calls += 1

    renderer = PlaywrightWebGL1Renderer()
    page = TimeoutOncePage()
    browser = RecordingClosable()
    playwright = RecordingClosable()
    renderer._page = page  # type: ignore[assignment]
    renderer._browser = browser  # type: ignore[assignment]
    renderer._playwright = playwright  # type: ignore[assignment]

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(renderer.close(), timeout=0.01)

    assert renderer._page is page
    assert renderer._browser is browser
    assert renderer._playwright is playwright

    await renderer.close()

    assert page.close_calls == 2
    assert browser.close_calls == 1
    assert playwright.close_calls == 1
    assert renderer._page is None
    assert renderer._browser is None
    assert renderer._playwright is None


@pytest.mark.anyio
async def test_renderer_replays_worker_failure_once(monkeypatch):
    renderer = PlaywrightWebGL1Renderer(replay_on_worker_failure=1)
    validation = validate_shader(VALID_SHADER)
    recovered = RenderResult(
        success=True,
        image_bytes=b"png",
        width=4,
        height=4,
        compile=CompileResult(
            success=True,
            vertex_log="",
            fragment_log="",
            link_log="",
            draw_error=None,
            static_validation=validation,
        ),
        console_errors=(),
        metadata=None,
        duration_ms=2.0,
    )
    render_once = AsyncMock(
        side_effect=(RendererUnavailableError("worker crashed"), recovered)
    )
    monkeypatch.setattr(renderer, "_render_once", render_once)

    result = await renderer.render(VALID_SHADER, 4, 4)

    assert result is recovered
    assert render_once.await_count == 2
