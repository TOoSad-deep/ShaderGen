# Rendering 模块架构

## 职责

`rendering` 是 PNG 转无贴图 Shader 闭环的确定性 WebGL1 执行层。它把通过静态契约校验的 Fragment Shader 放进项目自有的 Playwright/Chromium 页面，在目标分辨率、`u_time = 0`、无输入纹理的条件下编译、链接和绘制。旧 `render()` 仍返回 PNG；新 `prepare()` / `render_uniforms()` 热路径可直接返回原始 RGB。

## 边界

- 上游只传 Fragment Shader 字符串和目标宽高；本模块不调用模型，也不修改 Shader。
- `PlaywrightWebGL1Renderer` 只接受 canonical `WEBGL1_STATIC_NO_TEXTURE_V1`；不等价的 `RenderContract` 在启动浏览器前拒绝。
- `render()` 继续每次新建非 prepared canvas/context，不删除已准备的 program，与 V1 行为兼容。
- `prepare(fragment_source, width, height, uniform_schema)` 只做一次静态校验、编译和链接；`render_uniforms()` 每帧完整上传白名单值集、清屏、draw、`gl.finish()` 和 `readPixels`。
- uniform 白名单只支持 `float`/`vec2`/`vec3`；缺失、额外名称、非有限数值、错误类型或错误向量长度都在绘制前拒绝，因此不会沿用上一帧 uniform。`u_resolution`/`u_time` 由 Renderer 保留并自动上传。
- `capture_png=False` 只返回按左上角行序排列的 RGB bytes，不调用 `canvas.toDataURL()`；`capture_png=True` 同时返回 PNG，仅用于接受候选或最终结果。
- 同一个 `PlaywrightWebGL1Renderer` 生命周期内复用 browser/page；worker 异常时关闭并最多重放一次。
- WebGL context 固定 `antialias: false`、`preserveDrawingBuffer: true`，且不创建、不绑定输入纹理。
- 静态校验错误、GLSL 编译错误和 renderer 不可用是三个不同失败面；前两者返回结构化结果，最后一个抛出 `RendererUnavailableError`。
- PNG 本体通过 `RenderResult.image_bytes` 或 `PreparedRenderResult.image_bytes` 交给 Artifact Store；热路径 RGB 只在运行时消费，不进入 LangGraph State。prepared 对象由 run registry 持有，`close()` 幂等。

## 确定性约束

固定全屏三角形顶点 Shader、viewport、透明/深度/模板配置、白色 clear color、`u_resolution` 和 `u_time`。渲染元数据记录 Chromium、WebGL、GLSL、vendor 与 renderer 版本，以便解释跨机器像素漂移。

## 性能门禁

192x192 粉球固定模板的 100 draw 探针只在显式开关下运行，不进入普通 `make check`：

`SHADERGEN_RUN_RENDERER_PERFORMANCE_PROBE=true uv run pytest -q tests/integration_tests/test_prepared_webgl1_renderer.py::test_prepared_renderer_100_draw_performance_probe`

门槛固定为总耗时不超过 45 秒、P95 不超过 450 ms，并通过交替背景帧哈希验证无陈旧帧。
