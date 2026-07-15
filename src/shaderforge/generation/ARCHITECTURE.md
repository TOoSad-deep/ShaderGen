# ShaderForge Generation 架构

`generation/` 把已经规范化的参考图和确定性测量转换为可进入 Validator、Renderer 与 Oracle 的静态 Shader seed，不调用模型。

## 当前能力

- `measurement_affine_seed_v1`：使用背景色差最大连通前景 component 的像素，在测量 bbox 的局部 UV 中拟合紧凑 RGB affine plane；
- 使用测量 bbox 构造抗锯齿 ellipse mask，并把 affine 系数固化进 WebGL1 GLSL；
- 前景低置信、bbox 缺失、component 不足或拟合病态时，回退到 palette solid ellipse；
- 输出稳定 generator version、输入/测量/GLSL hash、策略、拟合像素数、RMSE、系数和 fallback 原因。

## 输入契约

- 只接受 `normalized RGB PNG bytes + TargetMeasurements`；
- PNG SHA-256 和尺寸必须与 `TargetMeasurements` 精确一致；
- 生成器不读取 case id、benchmark manifest、golden Shader、质量 gate 或模型输出。

## 边界

- 输出只是一份候选 seed，不选择 `current_best`，不修改 Graph、预算、stagnation 或 Memory；
- GLSL 必须继续满足 `webgl1_static_no_texture_v1`，声明 `u_image` 但禁止采样；
- affine 公式或 fallback 语义变化必须升级 generator version，并重新运行固定图片的 Chromium/Oracle 集成测试；
- benchmark 可以调用公共 kernel，但生产代码不得依赖 `shaderforge.benchmark`。
