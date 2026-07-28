# 静态粉色玻璃圆片 Shader Implementation Plan

> 归档状态：历史一次性样例，不得按下方 worker 指令重新生成产物。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成一个兼容当前 ShaderGen 前端、完全静态且不采样贴图的 WebGL1 fragment shader，并保留一张 505×527 的验证渲染图。

**Architecture:** 单个 GLSL 文件使用椭圆 SDF、Gaussian 色团、解析椭圆弧高光和各向异性阴影分层合成画面。临时 HTML 只负责在真实 WebGL1 上编译和离屏渲染，验证后删除，不进入最终运行时。

**Tech Stack:** WebGL1、GLSL ES 1.00、当前 `ShaderPreview` uniform 契约、Playwright CLI、Pillow/NumPy（仅验证）

## Global Constraints

- 最终 Shader 必须从 `precision mediump float;` 开始，不使用 `#version`。
- 必须声明 `v_uv`、`u_image`、`u_resolution`、`u_time`，入口为 `void main()`，输出为 `gl_FragColor`。
- 不调用 `texture2D`，不读取 `u_image`，不使用 `u_time`，不包含动画。
- 505×527 是像素对照基准；其他分辨率保持相对布局。
- 不修改前端、后端、Agent Prompt、API、架构或功能状态。
- 当前目录没有 Git 元数据，因此本计划不包含无法执行的 commit 步骤。

---

### Task 1: 创建并静态检查 Shader

**Files:**
- Create: `output/static_pink_glass_orb.glsl`

**Interfaces:**
- Consumes: `v_uv`、`u_resolution`；为前端兼容声明 `u_image`、`u_time`
- Produces: WebGL1 fragment shader，输出不透明 RGBA

- [x] **Step 1: 验证产物尚不存在**

Run: `test ! -e output/static_pink_glass_orb.glsl`

Expected: exit 0，证明后续产物检查不是读取旧文件。

- [x] **Step 2: 写入最小完整 Shader**

```glsl
precision mediump float;

varying vec2 v_uv;
uniform sampler2D u_image;
uniform vec2 u_resolution;
uniform float u_time;

float gaussian(vec2 point, vec2 center, vec2 sigma) {
  vec2 q = (point - center) / sigma;
  return exp(-0.5 * dot(q, q));
}

vec2 axisCoordinates(vec2 point, vec2 center, vec2 axis) {
  vec2 delta = point - center;
  return vec2(dot(delta, axis), dot(delta, vec2(-axis.y, axis.x)));
}

void main() {
  vec2 referenceSize = vec2(505.0, 527.0);
  vec2 uv = vec2(v_uv.x, 1.0 - v_uv.y);
  vec2 pixel = uv * referenceSize;

  vec2 center = vec2(251.5, 243.3);
  vec2 radius = vec2(213.5, 211.5);
  vec2 ellipse = (pixel - center) / radius;
  float radial = length(ellipse);
  vec2 safeResolution = max(u_resolution, vec2(1.0));
  float referencePixel = 0.5 * (
    referenceSize.x / safeResolution.x +
    referenceSize.y / safeResolution.y
  );
  float aa = 1.2 * referencePixel / min(radius.x, radius.y);
  float bodyMask = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, radial);

  vec3 background = vec3(0.996);
  float shadow =
    0.23 * gaussian(pixel, vec2(277.0, 467.0), vec2(105.0, 34.0)) +
    0.08 * gaussian(pixel, vec2(287.0, 486.0), vec2(138.0, 56.0));
  vec3 color = mix(
    background,
    vec3(1.0, 0.38, 0.65),
    clamp(shadow * (1.0 - bodyMask), 0.0, 0.32)
  );

  float gradient = clamp(
    0.58 + 0.18 * ellipse.x + 0.35 * ellipse.y,
    0.0,
    1.0
  );
  vec3 deepPink = vec3(0.93, 0.005, 0.18);
  vec3 hotPink = vec3(1.0, 0.42, 0.62);
  vec3 palePink = vec3(1.0, 0.96, 0.98);
  vec3 body = mix(deepPink, hotPink, smoothstep(0.05, 0.68, gradient));
  body = mix(body, palePink, smoothstep(0.48, 1.02, gradient));

  float darkLobe = gaussian(pixel, vec2(78.0, 178.0), vec2(92.0, 132.0));
  float rightLobe = gaussian(pixel, vec2(405.0, 250.0), vec2(175.0, 175.0));
  float milkLobe = gaussian(pixel, vec2(252.0, 423.0), vec2(178.0, 78.0));
  body = mix(body, vec3(0.86, 0.0, 0.15), 0.30 * darkLobe);
  body = mix(body, vec3(1.0, 0.56, 0.72), 0.20 * rightLobe);
  body = mix(body, vec3(1.0, 0.975, 0.987), 0.68 * milkLobe);

  float softRim = smoothstep(0.79, 0.995, radial);
  float outerStroke = smoothstep(0.958, 0.993, radial);
  float edgeLight = clamp(
    0.55 + 0.25 * ellipse.x + 0.40 * ellipse.y,
    0.0,
    1.0
  );
  vec3 edgeColor = mix(
    vec3(0.86, 0.0, 0.18),
    vec3(1.0, 0.63, 0.77),
    edgeLight
  );
  body = mix(body, edgeColor, clamp(0.24 * softRim + 0.58 * outerStroke, 0.0, 0.78));

  vec2 highlightAxis = vec2(0.819, -0.574);
  vec2 left = axisCoordinates(pixel, vec2(153.0, 91.0), highlightAxis);
  vec2 leftGlowDistance = left / vec2(82.0, 38.0);
  vec2 leftCoreDistance = left / vec2(61.0, 17.0);
  float leftGlowRadial = (radial - 0.87) / 0.075;
  float leftCoreRadial = (radial - 0.875) / 0.027;
  float leftGlow =
    exp(-0.5 * dot(leftGlowDistance, leftGlowDistance)) *
    exp(-0.5 * leftGlowRadial * leftGlowRadial);
  float leftCore =
    exp(-0.5 * dot(leftCoreDistance, leftCoreDistance)) *
    exp(-0.5 * leftCoreRadial * leftCoreRadial);
  body = mix(body, vec3(1.0, 0.89, 0.95), 0.38 * leftGlow);
  body = mix(body, vec3(1.0), clamp(0.92 * leftCore, 0.0, 0.96));

  vec2 right = axisCoordinates(pixel, vec2(368.0, 382.0), highlightAxis);
  vec2 rightGlowDistance = right / vec2(88.0, 43.0);
  vec2 rightCoreDistance = right / vec2(65.0, 20.0);
  float rightGlowRadial = (radial - 0.88) / 0.090;
  float rightCoreRadial = (radial - 0.89) / 0.033;
  float rightGlow =
    exp(-0.5 * dot(rightGlowDistance, rightGlowDistance)) *
    exp(-0.5 * rightGlowRadial * rightGlowRadial);
  float rightCore =
    exp(-0.5 * dot(rightCoreDistance, rightCoreDistance)) *
    exp(-0.5 * rightCoreRadial * rightCoreRadial);
  body = mix(body, vec3(1.0, 0.93, 0.97), 0.48 * rightGlow);
  body = mix(body, vec3(1.0), clamp(0.95 * rightCore, 0.0, 0.98));

  float topRimRadial = (radial - 0.985) / 0.018;
  float topRim =
    gaussian(pixel, vec2(252.0, 45.0), vec2(145.0, 25.0)) *
    exp(-0.5 * topRimRadial * topRimRadial);
  body = mix(body, vec3(1.0, 0.68, 0.80), 0.42 * topRim);

  color = mix(color, body, bodyMask);
  gl_FragColor = vec4(clamp(color, 0.0, 1.0), 1.0);
}
```

- [x] **Step 3: 静态检查契约**

Run:

```bash
head -n 1 output/static_pink_glass_orb.glsl
rg -n 'texture2D|#version|mainImage' output/static_pink_glass_orb.glsl
rg -n '^void main\(\)|gl_FragColor|varying vec2 v_uv|uniform sampler2D u_image|uniform vec2 u_resolution|uniform float u_time' output/static_pink_glass_orb.glsl
```

Expected: 第一条命令输出 `precision mediump float;`；禁止项扫描无结果；必需声明和入口全部命中。

### Task 2: 在真实 WebGL1 中渲染并对照参考图

**Files:**
- Create temporarily: `output/static_pink_glass_orb.preview.html`
- Create: `output/static_pink_glass_orb.png`
- Modify: `PROGRESS.md`
- Remove after verification: `output/static_pink_glass_orb.preview.html`

**Interfaces:**
- Consumes: `output/static_pink_glass_orb.glsl`
- Produces: 505×527 PNG 与验证证据

- [x] **Step 1: 创建临时 WebGL1 预览页**

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>Static Pink Glass Orb Validation</title>
    <style>
      html, body { margin: 0; background: white; }
      canvas { display: block; width: 505px; height: 527px; }
      pre { color: #b00020; white-space: pre-wrap; }
    </style>
  </head>
  <body data-status="loading">
    <canvas width="505" height="527"></canvas>
    <pre></pre>
    <script type="module">
      const canvas = document.querySelector("canvas");
      const errorOutput = document.querySelector("pre");
      const vertexSource = `
        attribute vec2 a_position;
        varying vec2 v_uv;
        void main() {
          v_uv = a_position * 0.5 + 0.5;
          gl_Position = vec4(a_position, 0.0, 1.0);
        }
      `;

      function compile(gl, type, source) {
        const shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
          throw new Error(gl.getShaderInfoLog(shader) || "Shader compile failed");
        }
        return shader;
      }

      try {
        const fragmentSource = await fetch("./static_pink_glass_orb.glsl").then((response) => response.text());
        const gl = canvas.getContext("webgl", { preserveDrawingBuffer: true });
        if (!gl) throw new Error("WebGL1 unavailable");

        const program = gl.createProgram();
        gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertexSource));
        gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragmentSource));
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
          throw new Error(gl.getProgramInfoLog(program) || "Program link failed");
        }

        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        gl.bufferData(
          gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
          gl.STATIC_DRAW,
        );
        gl.viewport(0, 0, 505, 527);
        gl.useProgram(program);
        const position = gl.getAttribLocation(program, "a_position");
        gl.enableVertexAttribArray(position);
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
        gl.uniform2f(gl.getUniformLocation(program, "u_resolution"), 505, 527);
        gl.drawArrays(gl.TRIANGLES, 0, 6);
        document.body.dataset.status = "ready";
      } catch (error) {
        document.body.dataset.status = "error";
        errorOutput.textContent = error instanceof Error ? error.stack : String(error);
        console.error(error);
      }
    </script>
  </body>
</html>
```

- [x] **Step 2: 启动静态服务器并用 Playwright 渲染**

Run:

```bash
python3 -m http.server 4178 --directory output
"$PWCLI" --session static-pink-orb open http://127.0.0.1:4178/static_pink_glass_orb.preview.html
"$PWCLI" --session static-pink-orb snapshot
"$PWCLI" --session static-pink-orb console warning
```

Expected: 页面状态为 `ready`，控制台无 Shader 编译或链接错误。

- [x] **Step 3: 保存 canvas 截图并检查尺寸**

Run:

```bash
"$PWCLI" --session static-pink-orb run-code "await page.waitForFunction(() => document.body.dataset.status !== 'loading'); if (await page.locator('body').getAttribute('data-status') !== 'ready') throw new Error(await page.locator('pre').textContent()); await page.locator('canvas').screenshot({path: '/Users/douwen/Documents/HUAWEl/Shader-Agent/ShaderGen/output/static_pink_glass_orb.png'})"
```

然后运行：

```bash
sips -g pixelWidth -g pixelHeight output/static_pink_glass_orb.png
```

Expected: `pixelWidth: 505`、`pixelHeight: 527`。

- [x] **Step 4: 对照参考图并迭代参数**

Run:

```bash
uv run python - <<'PY'
from pathlib import Path

import numpy as np
from PIL import Image

reference = np.asarray(Image.open("/Users/douwen/Desktop/p2s-test/参考图.png").convert("RGB"), dtype=np.float32)
rendered = np.asarray(Image.open("output/static_pink_glass_orb.png").convert("RGB"), dtype=np.float32)
assert reference.shape == rendered.shape == (527, 505, 3)

def subject_bbox(image: np.ndarray) -> tuple[int, int, int, int]:
    red, green, blue = image[..., 0], image[..., 1], image[..., 2]
    mask = ((red - green) > 15.0) & ((red - blue) > 4.0)
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())

points = {
    "background": (0, 0),
    "deep_left": (65, 200),
    "center": (252, 243),
    "bottom_milk": (252, 430),
    "left_highlight": (153, 91),
    "right_highlight": (368, 382),
    "shadow": (278, 461),
}

print("reference_bbox", subject_bbox(reference))
print("rendered_bbox", subject_bbox(rendered))
print("rmse", float(np.sqrt(np.mean((reference - rendered) ** 2))))
for name, (x, y) in points.items():
    print(name, "reference", reference[y, x].astype(int), "rendered", rendered[y, x].astype(int))
PY
```

Expected: 主体 bbox 各边与参考值相差不超过 2 px；两处高光像素接近白色；四角背景接近 `#FEFEFE`。根据打印的代表性像素，只调整 Task 1 中已有的颜色、中心、半径、Gaussian 中心/宽度和混合强度，然后重新执行 WebGL1 渲染，直到边界与主要视觉层匹配。

- [x] **Step 5: 清理临时页并记录进度**

删除 `output/static_pink_glass_orb.preview.html`；在 `PROGRESS.md` 记录产物、无贴图约束、WebGL1 编译/渲染结果、视觉验证方式以及当前目录无 Git 元数据。

- [x] **Step 6: 完整文档检查**

Run: `make docs-check`

Expected: `docs-check passed`。
