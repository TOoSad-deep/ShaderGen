"""基于项目自有 Playwright/Chromium 的 WebGL1 渲染器."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Mapping
from typing import Any

from playwright.async_api import (
    Browser,
    Page,
    Playwright,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)

from shaderforge.contracts.png_to_shader_v1 import (
    WEBGL1_STATIC_NO_TEXTURE_V1,
    RenderContract,
)
from shaderforge.rendering.models import (
    CompileResult,
    RendererMetadata,
    RendererUnavailableError,
    RenderResult,
)
from shaderforge.validation import ValidationResult, validate_shader

RENDERER_VERSION = "playwright_webgl1_v1"
PNG_DATA_URL_PREFIX = "data:image/png;base64,"

VERTEX_SHADER = """
attribute vec2 a_position;
varying vec2 v_uv;

void main() {
    v_uv = a_position * 0.5 + 0.5;
    gl_Position = vec4(a_position, 0.0, 1.0);
}
""".strip()

HOST_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="data:,">
  <title>ShaderForge WebGL1 Renderer</title>
  <style>
    html, body { margin: 0; min-height: 100%; background: #eceff3; }
    body { display: grid; place-items: center; font-family: ui-monospace, monospace; }
    canvas { display: block; background: white; }
    #shader-status {
      position: fixed; left: 12px; bottom: 12px; margin: 0; padding: 6px 9px;
      color: #102030; background: rgba(255,255,255,.9); border-radius: 5px;
      font-size: 12px; box-shadow: 0 1px 6px rgba(0,0,0,.15);
    }
  </style>
</head>
<body>
<script>
(() => {
  const vertexSource = `attribute vec2 a_position;
varying vec2 v_uv;
void main() {
  v_uv = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;

  function compileShader(gl, type, source) {
    const shader = gl.createShader(type);
    if (!shader) return { shader: null, success: false, log: "createShader failed" };
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    return {
      shader,
      success: Boolean(gl.getShaderParameter(shader, gl.COMPILE_STATUS)),
      log: gl.getShaderInfoLog(shader) || "",
    };
  }

  function metadata(gl) {
    const debugInfo = gl.getExtension("WEBGL_debug_renderer_info");
    return {
      glVersion: String(gl.getParameter(gl.VERSION) || ""),
      glslVersion: String(gl.getParameter(gl.SHADING_LANGUAGE_VERSION) || ""),
      glVendor: String(
        debugInfo ? gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL) : gl.getParameter(gl.VENDOR)
      ),
      glRenderer: String(
        debugInfo ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)
      ),
    };
  }

  function cleanup(gl, program, vertexShader, fragmentShader, buffer) {
    if (buffer) gl.deleteBuffer(buffer);
    if (program) gl.deleteProgram(program);
    if (vertexShader) gl.deleteShader(vertexShader);
    if (fragmentShader) gl.deleteShader(fragmentShader);
  }

  window.__renderShader = (payload) => {
    for (const oldCanvas of document.querySelectorAll("canvas")) oldCanvas.remove();

    const canvas = document.createElement("canvas");
    canvas.width = payload.width;
    canvas.height = payload.height;
    canvas.setAttribute("aria-label", "Shader render output");
    document.body.prepend(canvas);

    const gl = canvas.getContext("webgl", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: true,
      powerPreference: "low-power",
    });
    if (!gl) {
      return {
        success: false,
        vertexLog: "",
        fragmentLog: "",
        linkLog: "",
        drawError: "WebGL 1.0 unavailable",
        dataUrl: null,
        metadata: null,
      };
    }

    const environment = metadata(gl);
    const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
    const fragment = compileShader(gl, gl.FRAGMENT_SHADER, payload.fragmentSource);
    let program = null;
    let linkLog = "";
    let drawError = null;
    let dataUrl = null;

    if (vertex.success && fragment.success && vertex.shader && fragment.shader) {
      program = gl.createProgram();
      if (!program) {
        drawError = "createProgram failed";
      } else {
        gl.attachShader(program, vertex.shader);
        gl.attachShader(program, fragment.shader);
        gl.linkProgram(program);
        linkLog = gl.getProgramInfoLog(program) || "";
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
          drawError = "program link failed";
        }
      }
    }

    let buffer = null;
    if (program && !drawError) {
      gl.useProgram(program);
      buffer = gl.createBuffer();
      if (!buffer) {
        drawError = "createBuffer failed";
      } else {
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(
          gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 3, -1, -1, 3]),
          gl.STATIC_DRAW
        );
        const position = gl.getAttribLocation(program, "a_position");
        if (position < 0) {
          drawError = "a_position attribute unavailable";
        } else {
          gl.enableVertexAttribArray(position);
          gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

          const resolution = gl.getUniformLocation(program, "u_resolution");
          if (resolution !== null) gl.uniform2f(resolution, payload.width, payload.height);
          const time = gl.getUniformLocation(program, "u_time");
          if (time !== null) gl.uniform1f(time, 0.0);

          gl.viewport(0, 0, payload.width, payload.height);
          gl.clearColor(1.0, 1.0, 1.0, 1.0);
          gl.clear(gl.COLOR_BUFFER_BIT);
          gl.getError();
          gl.drawArrays(gl.TRIANGLES, 0, 3);
          gl.finish();
          const errorCode = gl.getError();
          if (errorCode !== gl.NO_ERROR) {
            drawError = `WebGL draw error 0x${errorCode.toString(16)}`;
          } else {
            dataUrl = canvas.toDataURL("image/png");
          }
        }
      }
    }

    const success = Boolean(
      vertex.success && fragment.success && program && !drawError && dataUrl
    );
    cleanup(gl, program, vertex.shader, fragment.shader, buffer);
    return {
      success,
      vertexLog: vertex.log,
      fragmentLog: fragment.log,
      linkLog,
      drawError,
      dataUrl: success ? dataUrl : null,
      metadata: environment,
    };
  };
})();
</script>
</body>
</html>
"""


def _decode_png_data_url(value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith(PNG_DATA_URL_PREFIX):
        raise RendererUnavailableError("浏览器返回了无效的 PNG data URL。")
    try:
        return base64.b64decode(value[len(PNG_DATA_URL_PREFIX) :], validate=True)
    except ValueError as exc:
        raise RendererUnavailableError("浏览器返回的 PNG base64 无法解码。") from exc


class PlaywrightWebGL1Renderer:
    """复用 browser/page、每帧新建 canvas 的 WebGL1 渲染 worker."""

    def __init__(
        self,
        *,
        contract: RenderContract = WEBGL1_STATIC_NO_TEXTURE_V1,
        replay_on_worker_failure: int = 1,
    ) -> None:
        """配置渲染契约和 worker 失败重放次数."""
        if contract != WEBGL1_STATIC_NO_TEXTURE_V1:
            raise ValueError(
                "PlaywrightWebGL1Renderer 当前只支持 canonical "
                f"{WEBGL1_STATIC_NO_TEXTURE_V1.contract_id} 契约。"
            )
        if replay_on_worker_failure < 0:
            raise ValueError("replay_on_worker_failure 不能小于 0。")
        self.contract = contract
        self.replay_on_worker_failure = replay_on_worker_failure
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._page: Page | None = None
        self._console_errors: list[str] = []

    async def __aenter__(self) -> PlaywrightWebGL1Renderer:
        """启动并返回可复用 renderer."""
        await self._ensure_started()
        return self

    async def __aexit__(self, *_: object) -> None:
        """释放 Playwright 进程资源."""
        await self.close()

    async def _ensure_started(self) -> None:
        if self._page is not None and not self._page.is_closed():
            return
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=(
                "--enable-webgl",
                "--ignore-gpu-blocklist",
                "--use-angle=swiftshader",
            ),
        )
        self._page = await self._browser.new_page()
        self._page.on("console", self._on_console)
        self._page.on("pageerror", self._on_page_error)
        await self._page.set_content(HOST_HTML, wait_until="load")

    def _on_console(self, message: Any) -> None:
        if message.type == "error":
            self._console_errors.append(message.text)

    def _on_page_error(self, error: Any) -> None:
        self._console_errors.append(str(error))

    async def close(self) -> None:
        """幂等关闭 page、browser 和 Playwright driver."""
        page, browser, playwright = self._page, self._browser, self._playwright
        first_error: Exception | None = None
        if page is not None:
            if page.is_closed():
                if self._page is page:
                    self._page = None
            else:
                try:
                    await page.close()
                except (PlaywrightError, OSError) as exc:
                    first_error = exc
                else:
                    if self._page is page:
                        self._page = None
        if browser is not None:
            try:
                await browser.close()
            except (PlaywrightError, OSError) as exc:
                first_error = first_error or exc
            else:
                if self._browser is browser:
                    self._browser = None
        if playwright is not None:
            try:
                await playwright.stop()
            except (PlaywrightError, OSError) as exc:
                first_error = first_error or exc
            else:
                if self._playwright is playwright:
                    self._playwright = None
        if first_error is not None:
            raise first_error

    def _validate_dimensions(self, width: int, height: int) -> None:
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, int)
            or not isinstance(height, int)
            or width <= 0
            or height <= 0
        ):
            raise ValueError("width 和 height 必须是正整数。")
        if max(width, height) > self.contract.max_long_side:
            raise ValueError(
                f"渲染尺寸超过契约长边上限 {self.contract.max_long_side}。"
            )

    async def render(self, fragment_source: str, width: int, height: int) -> RenderResult:
        """静态校验后编译并渲染 PNG；worker 异常时最多重放一次."""
        self._validate_dimensions(width, height)
        started = time.perf_counter()
        static_validation = validate_shader(fragment_source, contract=self.contract)
        if not static_validation.valid:
            return RenderResult(
                success=False,
                image_bytes=None,
                width=width,
                height=height,
                compile=CompileResult(
                    success=False,
                    vertex_log="",
                    fragment_log="",
                    link_log="",
                    draw_error="static_validation_failed",
                    static_validation=static_validation,
                ),
                console_errors=(),
                metadata=None,
                duration_ms=(time.perf_counter() - started) * 1000.0,
            )

        last_error: Exception | None = None
        for _attempt in range(self.replay_on_worker_failure + 1):
            try:
                return await self._render_once(
                    fragment_source,
                    width,
                    height,
                    static_validation,
                    started,
                )
            except (PlaywrightError, OSError, RendererUnavailableError) as exc:
                last_error = exc
                try:
                    await self.close()
                except (PlaywrightError, OSError):
                    pass
        raise RendererUnavailableError(
            "WebGL1 renderer 在 worker 重放后仍不可用。"
        ) from last_error

    async def _render_once(
        self,
        fragment_source: str,
        width: int,
        height: int,
        static_validation: ValidationResult,
        started: float,
    ) -> RenderResult:
        await self._ensure_started()
        if self._page is None or self._browser is None:
            raise RendererUnavailableError("Playwright page 未就绪。")
        self._console_errors.clear()
        payload = await self._page.evaluate(
            "payload => window.__renderShader(payload)",
            {
                "fragmentSource": fragment_source,
                "width": width,
                "height": height,
            },
        )
        await self._page.wait_for_timeout(0)
        if not isinstance(payload, Mapping):
            raise RendererUnavailableError("浏览器返回了无效渲染结果。")

        environment = payload.get("metadata")
        if environment is None and payload.get("drawError") == "WebGL 1.0 unavailable":
            raise RendererUnavailableError("Chromium worker 不支持 WebGL 1.0。")
        metadata = None
        if isinstance(environment, Mapping):
            metadata = RendererMetadata(
                renderer_version=RENDERER_VERSION,
                browser_version=self._browser.version,
                gl_version=str(environment.get("glVersion", "")),
                glsl_version=str(environment.get("glslVersion", "")),
                gl_vendor=str(environment.get("glVendor", "")),
                gl_renderer=str(environment.get("glRenderer", "")),
            )
        success = bool(payload.get("success"))
        image_bytes = _decode_png_data_url(payload.get("dataUrl")) if success else None
        compile_result = CompileResult(
            success=success,
            vertex_log=str(payload.get("vertexLog", "")),
            fragment_log=str(payload.get("fragmentLog", "")),
            link_log=str(payload.get("linkLog", "")),
            draw_error=(
                str(payload["drawError"])
                if payload.get("drawError") is not None
                else None
            ),
            static_validation=static_validation,
        )
        return RenderResult(
            success=success,
            image_bytes=image_bytes,
            width=width,
            height=height,
            compile=compile_result,
            console_errors=tuple(self._console_errors),
            metadata=metadata,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )


def build_standalone_html(fragment_source: str, width: int, height: int) -> str:
    """生成使用同一渲染 host 的可人工检查 HTML."""
    if width <= 0 or height <= 0:
        raise ValueError("width 和 height 必须大于 0。")
    if max(width, height) > WEBGL1_STATIC_NO_TEXTURE_V1.max_long_side:
        raise ValueError("standalone HTML 尺寸超过 V1 契约上限。")
    payload = json.dumps(
        {
            "fragmentSource": fragment_source,
            "width": width,
            "height": height,
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    boot_script = f"""
<pre id="shader-status">rendering</pre>
<script>
window.addEventListener("load", () => {{
  const result = window.__renderShader({payload});
  const publicResult = {{
    success: result.success,
    vertexLog: result.vertexLog,
    fragmentLog: result.fragmentLog,
    linkLog: result.linkLog,
    drawError: result.drawError,
    metadata: result.metadata,
  }};
  window.__shaderStatus = publicResult;
  document.body.dataset.shaderStatus = result.success ? "ready" : "failed";
  document.getElementById("shader-status").textContent =
    result.success ? "WebGL1 · ready" : `WebGL1 · failed · ${{result.drawError || result.fragmentLog}}`;
}});
</script>
"""
    return HOST_HTML.replace("</body>", boot_script + "</body>")
