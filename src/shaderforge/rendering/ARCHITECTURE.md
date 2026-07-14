# Rendering 模块架构

## 职责

`rendering` 是 PNG 转无贴图 Shader 闭环的确定性 WebGL1 执行层。它把通过静态契约校验的 Fragment Shader 放进项目自有的 Playwright/Chromium 页面，在目标分辨率、`u_time = 0`、无输入纹理的条件下编译、链接、绘制并导出 PNG。

## 边界

- 上游只传 Fragment Shader 字符串和目标宽高；本模块不调用模型，也不修改 Shader。
- 每次渲染都新建 canvas 和 WebGL context，避免编译失败时误用上一帧。
- 同一个 `PlaywrightWebGL1Renderer` 生命周期内复用 browser/page；worker 异常时关闭并最多重放一次。
- WebGL context 固定 `antialias: false`、`preserveDrawingBuffer: true`，且不创建、不绑定输入纹理。
- 静态校验错误、GLSL 编译错误和 renderer 不可用是三个不同失败面；前两者返回结构化结果，最后一个抛出 `RendererUnavailableError`。
- PNG 本体通过 `RenderResult.image_bytes` 交给 Artifact Store；结构化日志通过 `RenderResult.to_dict()` 持久化。

## 确定性约束

固定全屏三角形顶点 Shader、viewport、透明/深度/模板配置、白色 clear color、`u_resolution` 和 `u_time`。渲染元数据记录 Chromium、WebGL、GLSL、vendor 与 renderer 版本，以便解释跨机器像素漂移。
