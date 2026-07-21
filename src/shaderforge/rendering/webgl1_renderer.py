"""基于项目自有 Playwright/Chromium 的 WebGL1 渲染器."""

from __future__ import annotations

import base64
import json
import math
import re
import time
from collections.abc import Mapping
from typing import Any, Literal, cast

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
    PreparedRenderResult,
    RendererMetadata,
    RendererUnavailableError,
    RenderResult,
    ShaderPreparationError,
)
from shaderforge.validation import ValidationResult, validate_shader

RENDERER_VERSION = "playwright_webgl1_v1"
PREPARED_RENDERER_PATH = "prepared_uniforms_v1"
PNG_DATA_URL_PREFIX = "data:image/png;base64,"
_UNIFORM_NAME_PATTERN = re.compile(r"^u_[A-Za-z0-9_]+$")
_UNIFORM_TYPES = frozenset({"float", "vec2", "vec3"})
_RESERVED_UNIFORMS = frozenset({"u_image", "u_resolution", "u_time"})

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

  const preparedPrograms = new Map();
  let nextPreparedId = 1;

  window.__prepareShader = (payload) => {
    const canvas = document.createElement("canvas");
    canvas.width = payload.width;
    canvas.height = payload.height;
    canvas.hidden = true;
    canvas.dataset.prepared = "true";
    canvas.setAttribute("aria-label", "Prepared shader render output");
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
      canvas.remove();
      return {
        success: false,
        vertexLog: "",
        fragmentLog: "",
        linkLog: "",
        drawError: "WebGL 1.0 unavailable",
        metadata: null,
        preparedId: null,
      };
    }

    const environment = metadata(gl);
    const vertex = compileShader(gl, gl.VERTEX_SHADER, vertexSource);
    const fragment = compileShader(gl, gl.FRAGMENT_SHADER, payload.fragmentSource);
    let program = null;
    let buffer = null;
    let linkLog = "";
    let drawError = null;
    const uniformLocations = {};

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
        }
      }
    }

    if (program && !drawError) {
      const expectedTypes = {float: gl.FLOAT, vec2: gl.FLOAT_VEC2, vec3: gl.FLOAT_VEC3};
      const activeTypes = {};
      const activeCount = gl.getProgramParameter(program, gl.ACTIVE_UNIFORMS);
      for (let index = 0; index < activeCount; index += 1) {
        const info = gl.getActiveUniform(program, index);
        if (info) activeTypes[info.name.replace(/\[0\]$/, "")] = info.type;
      }
      for (const [name, type] of Object.entries(payload.uniformSchema)) {
        const location = gl.getUniformLocation(program, name);
        if (location === null || activeTypes[name] !== expectedTypes[type]) {
          drawError = `uniform schema mismatch: ${name}:${type}`;
          break;
        }
        uniformLocations[name] = location;
      }
    }

    if (program && !drawError) {
      const resolution = gl.getUniformLocation(program, "u_resolution");
      if (resolution !== null) gl.uniform2f(resolution, payload.width, payload.height);
      const time = gl.getUniformLocation(program, "u_time");
      if (time !== null) gl.uniform1f(time, 0.0);
      const preparedId = String(nextPreparedId++);
      preparedPrograms.set(preparedId, {
        canvas,
        gl,
        program,
        vertexShader: vertex.shader,
        fragmentShader: fragment.shader,
        buffer,
        uniformLocations,
        uniformSchema: payload.uniformSchema,
        width: payload.width,
        height: payload.height,
      });
      return {
        success: true,
        vertexLog: vertex.log,
        fragmentLog: fragment.log,
        linkLog,
        drawError: null,
        metadata: environment,
        preparedId,
      };
    }

    cleanup(gl, program, vertex.shader, fragment.shader, buffer);
    canvas.remove();
    return {
      success: false,
      vertexLog: vertex.log,
      fragmentLog: fragment.log,
      linkLog,
      drawError,
      metadata: environment,
      preparedId: null,
    };
  };

  window.__renderPrepared = (payload) => {
    const prepared = preparedPrograms.get(payload.preparedId);
    if (!prepared) {
      return {success: false, drawError: "prepared program unavailable", rgb: null, dataUrl: null};
    }
    const {canvas, gl, program, uniformLocations, uniformSchema, width, height} = prepared;
    gl.useProgram(program);
    for (const [name, type] of Object.entries(uniformSchema)) {
      const value = payload.uniformValues[name];
      const location = uniformLocations[name];
      if (type === "float") gl.uniform1f(location, value);
      if (type === "vec2") gl.uniform2f(location, value[0], value[1]);
      if (type === "vec3") gl.uniform3f(location, value[0], value[1], value[2]);
    }
    gl.viewport(0, 0, width, height);
    gl.clearColor(1.0, 1.0, 1.0, 1.0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.getError();
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.finish();
    const errorCode = gl.getError();
    if (errorCode !== gl.NO_ERROR) {
      return {
        success: false,
        drawError: `WebGL draw error 0x${errorCode.toString(16)}`,
        rgb: null,
        dataUrl: null,
      };
    }

    const rgba = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, rgba);
    const readError = gl.getError();
    if (readError !== gl.NO_ERROR) {
      return {
        success: false,
        drawError: `WebGL readPixels error 0x${readError.toString(16)}`,
        rgb: null,
        dataUrl: null,
      };
    }
    const rgb = new Uint8Array(width * height * 3);
    for (let targetY = 0; targetY < height; targetY += 1) {
      const sourceY = height - targetY - 1;
      for (let x = 0; x < width; x += 1) {
        const source = (sourceY * width + x) * 4;
        const target = (targetY * width + x) * 3;
        rgb[target] = rgba[source];
        rgb[target + 1] = rgba[source + 1];
        rgb[target + 2] = rgba[source + 2];
      }
    }
    return {
      success: true,
      drawError: null,
      rgb: Array.from(rgb),
      dataUrl: payload.capturePng ? canvas.toDataURL("image/png") : null,
    };
  };

  window.__closePrepared = (preparedId) => {
    const prepared = preparedPrograms.get(preparedId);
    if (!prepared) return false;
    preparedPrograms.delete(preparedId);
    cleanup(
      prepared.gl,
      prepared.program,
      prepared.vertexShader,
      prepared.fragmentShader,
      prepared.buffer
    );
    prepared.canvas.remove();
    return true;
  };

  window.__renderShader = (payload) => {
    for (const oldCanvas of document.querySelectorAll("canvas:not([data-prepared])")) {
      oldCanvas.remove();
    }

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


UniformType = Literal["float", "vec2", "vec3"]


def _normalize_uniform_schema(uniform_schema: Mapping[str, Any]) -> dict[str, UniformType]:
    """规范化 uniform 白名单，拒绝保留名、非法名和未支持类型."""
    normalized: dict[str, UniformType] = {}
    for name, raw_spec in uniform_schema.items():
        if not isinstance(name, str) or not _UNIFORM_NAME_PATTERN.fullmatch(name):
            raise ValueError("uniform 名必须是 u_ 开头的 ASCII 标识符。")
        if name in _RESERVED_UNIFORMS:
            raise ValueError(f"uniform {name} 由 Renderer 保留并自动上传。")
        raw_type = raw_spec if isinstance(raw_spec, str) else getattr(raw_spec, "type", None)
        if raw_type not in _UNIFORM_TYPES:
            raise ValueError(f"uniform {name} 只支持 float、vec2 或 vec3。")
        normalized[name] = cast(UniformType, raw_type)
    return normalized


def _finite_number(value: Any, *, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"uniform {name} 必须是有限数值。")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"uniform {name} 必须是有限数值。")
    return result


def _validate_uniform_values(
    schema: Mapping[str, UniformType], uniform_values: Mapping[str, Any]
) -> dict[str, float | list[float]]:
    """校验值集与白名单完全一致，避免未设置 uniform 沿用旧帧状态."""
    if set(uniform_values) != set(schema):
        missing = sorted(set(schema) - set(uniform_values))
        extra = sorted(set(uniform_values) - set(schema))
        raise ValueError(f"uniform 值集必须与白名单完全一致；missing={missing}，extra={extra}。")
    normalized: dict[str, float | list[float]] = {}
    lengths = {"vec2": 2, "vec3": 3}
    for name, uniform_type in schema.items():
        value = uniform_values[name]
        if uniform_type == "float":
            normalized[name] = _finite_number(value, name=name)
            continue
        if not isinstance(value, (list, tuple)) or len(value) != lengths[uniform_type]:
            raise ValueError(
                f"uniform {name} 必须是长度 {lengths[uniform_type]} 的 {uniform_type}。"
            )
        normalized[name] = [
            _finite_number(item, name=f"{name}[{index}]")
            for index, item in enumerate(value)
        ]
    return normalized


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
        self._prepared: set[PreparedWebGL1Renderer] = set()

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
        for prepared in tuple(self._prepared):
            try:
                await prepared.close()
            except (PlaywrightError, OSError) as exc:
                first_error = first_error or exc
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

    async def prepare(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: Mapping[str, Any],
    ) -> PreparedWebGL1Renderer:
        """静态校验并一次性编译/链接固定 program."""
        self._validate_dimensions(width, height)
        schema = _normalize_uniform_schema(uniform_schema)
        started = time.perf_counter()
        static_validation = validate_shader(fragment_source, contract=self.contract)
        if not static_validation.valid:
            raise ShaderPreparationError(
                CompileResult(
                    success=False,
                    vertex_log="",
                    fragment_log="",
                    link_log="",
                    draw_error="static_validation_failed",
                    static_validation=static_validation,
                )
            )

        last_error: Exception | None = None
        for _attempt in range(self.replay_on_worker_failure + 1):
            try:
                return await self._prepare_once(
                    fragment_source,
                    width,
                    height,
                    schema,
                    static_validation,
                    started,
                )
            except ShaderPreparationError:
                raise
            except (PlaywrightError, OSError, RendererUnavailableError) as exc:
                last_error = exc
                try:
                    await self.close()
                except (PlaywrightError, OSError):
                    pass
        raise RendererUnavailableError(
            "WebGL1 prepared renderer 在 worker 重放后仍不可用。"
        ) from last_error

    async def _prepare_once(
        self,
        fragment_source: str,
        width: int,
        height: int,
        uniform_schema: dict[str, UniformType],
        static_validation: ValidationResult,
        started: float,
    ) -> PreparedWebGL1Renderer:
        await self._ensure_started()
        if self._page is None or self._browser is None:
            raise RendererUnavailableError("Playwright page 未就绪。")
        self._console_errors.clear()
        payload = await self._page.evaluate(
            "payload => window.__prepareShader(payload)",
            {
                "fragmentSource": fragment_source,
                "width": width,
                "height": height,
                "uniformSchema": uniform_schema,
            },
        )
        await self._page.wait_for_timeout(0)
        if not isinstance(payload, Mapping):
            raise RendererUnavailableError("浏览器返回了无效 prepared 结果。")
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
        prepared_id = payload.get("preparedId")
        if not success or not isinstance(prepared_id, str):
            raise ShaderPreparationError(compile_result)
        prepared = PreparedWebGL1Renderer(
            owner=self,
            prepared_id=prepared_id,
            width=width,
            height=height,
            uniform_schema=uniform_schema,
            compile_result=compile_result,
            metadata=metadata,
            prepare_duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        self._prepared.add(prepared)
        return prepared

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


class PreparedWebGL1Renderer:
    """持有一次编译/链接的 WebGL1 program，只允许热更新白名单 uniform."""

    def __init__(
        self,
        *,
        owner: PlaywrightWebGL1Renderer,
        prepared_id: str,
        width: int,
        height: int,
        uniform_schema: dict[str, UniformType],
        compile_result: CompileResult,
        metadata: RendererMetadata | None,
        prepare_duration_ms: float,
    ) -> None:
        """绑定所属 page、program id、类型白名单与准备诊断."""
        self._owner = owner
        self._prepared_id = prepared_id
        self.width = width
        self.height = height
        self.uniform_schema = dict(uniform_schema)
        self.compile = compile_result
        self.metadata = metadata
        self.prepare_duration_ms = prepare_duration_ms
        self._closed = False
        self._render_durations_ms: list[float] = []

    @property
    def render_count(self) -> int:
        """返回已完成的 uniform draw 数."""
        return len(self._render_durations_ms)

    @property
    def render_durations_ms(self) -> tuple[float, ...]:
        """返回每次 uniform draw 耗时快照."""
        return tuple(self._render_durations_ms)

    async def render_uniforms(
        self,
        uniform_values: Mapping[str, Any],
        *,
        capture_png: bool = False,
    ) -> PreparedRenderResult:
        """上传完整 typed uniform 值集并绘制；默认只返回原始 RGB."""
        if self._closed:
            raise RendererUnavailableError("prepared renderer 已关闭。")
        if not isinstance(capture_png, bool):
            raise ValueError("capture_png 必须是 bool。")
        if not isinstance(uniform_values, Mapping):
            raise ValueError("uniform_values 必须是 mapping。")
        values = _validate_uniform_values(self.uniform_schema, uniform_values)
        page = self._owner._page
        if page is None or page.is_closed():
            raise RendererUnavailableError("prepared renderer 所属 page 不可用。")
        started = time.perf_counter()
        self._owner._console_errors.clear()
        try:
            payload = await page.evaluate(
                "payload => window.__renderPrepared(payload)",
                {
                    "preparedId": self._prepared_id,
                    "uniformValues": values,
                    "capturePng": capture_png,
                },
            )
            await page.wait_for_timeout(0)
        except (PlaywrightError, OSError) as exc:
            raise RendererUnavailableError("prepared uniform 绘制失败。") from exc
        duration_ms = (time.perf_counter() - started) * 1000.0
        self._render_durations_ms.append(duration_ms)
        if not isinstance(payload, Mapping):
            raise RendererUnavailableError("浏览器返回了无效 uniform 绘制结果。")
        success = bool(payload.get("success"))
        rgb_value = payload.get("rgb")
        rgb_bytes: bytes | None = None
        if success:
            if not isinstance(rgb_value, list) or len(rgb_value) != self.width * self.height * 3:
                raise RendererUnavailableError("浏览器返回了无效 RGB 像素。")
            try:
                rgb_bytes = bytes(rgb_value)
            except (TypeError, ValueError) as exc:
                raise RendererUnavailableError("浏览器返回的 RGB 像素超出 uint8。") from exc
        image_bytes = (
            _decode_png_data_url(payload.get("dataUrl"))
            if success and capture_png
            else None
        )
        return PreparedRenderResult(
            success=success,
            rgb_bytes=rgb_bytes,
            image_bytes=image_bytes,
            width=self.width,
            height=self.height,
            console_errors=tuple(self._owner._console_errors),
            duration_ms=duration_ms,
            draw_error=(
                str(payload["drawError"])
                if payload.get("drawError") is not None
                else None
            ),
        )

    async def close(self) -> None:
        """幂等释放当前 program/context，并从所属 Renderer 注册表移除."""
        if self._closed:
            return
        self._closed = True
        self._owner._prepared.discard(self)
        page = self._owner._page
        if page is None or page.is_closed():
            return
        await page.evaluate("preparedId => window.__closePrepared(preparedId)", self._prepared_id)


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
