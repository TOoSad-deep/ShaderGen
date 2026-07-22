# ShaderForge Generation 架构

`generation/` 把已经规范化的参考图和确定性测量转换为可进入 Validator、Renderer 与 Oracle 的静态 Shader seed，不调用模型。

## 当前能力

- `measurement_affine_seed_v1`：使用背景色差最大连通前景 component 的像素，在测量 bbox 的局部 UV 中拟合紧凑 RGB affine plane；
- 使用测量 bbox 构造抗锯齿 ellipse mask，并把 affine 系数固化进 WebGL1 GLSL；
- 前景低置信、bbox 缺失、component 不足或拟合病态时，回退到 palette solid ellipse；
- 输出稳定 generator version、输入/测量/GLSL hash、策略、拟合像素数、RMSE、系数和 fallback 原因。
- `png_to_shader_min_template_v3` 从严格 `png_to_shader_min_scene_v3` 生成固定 WebGL1 uniform 模板、typed schema/值集和自包含导出版。Scene 基础参数使用 4 个 `vec4`，另有 1 个 scene meta；最多 4 个 feature 各压成 shape/color-power 2 个 `vec4`，类型集中到 1 个 kinds `vec4`。连同 Renderer 管理的 `u_resolution`，静态使用量精确为 15 个 fragment uniform vectors，不超过 WebGL1 最低保证的 16。
- solid/radial/linear 由 scene meta 进入真实模板分支；circle 在契约层等轴并由模板使用平均半径，ellipse 保留双轴。`rim`、`shadow`、`polar_arc`、`edge_line`、`gaussian_lobe`、`glow` 具有固定且互异的主体内/外像素语义；主体内四槽按 `gaussian_lobe → rim → polar_arc/edge_line` 三个固定 stage 重放，不受 feature 列表顺序改变。固定 slot 使 add/remove/replace 不改变 prepared program 签名；运行评估使用 prepared uniform，最终 `webgl1.glsl` 仍把 uniform 烘焙为常量。

## 输入契约

- 只接受 `normalized RGB PNG bytes + TargetMeasurements`；
- PNG SHA-256 和尺寸必须与 `TargetMeasurements` 精确一致；
- 生成器不读取 case id、benchmark manifest、golden Shader、质量 gate 或模型输出。

## 边界

- 输出只是一份候选 seed，不选择 `current_best`，不修改 Graph、预算、stagnation 或 Memory；
- 最小模板物化时必须按最保守的“每个标量/向量 uniform 占一个 vector”方式校验容量；修改 packed 布局、slot 数或几何公式必须升级模板版本并用真实 Chromium 验证。
- GLSL 必须继续满足 `webgl1_static_no_texture_v1`，声明 `u_image` 但禁止采样；
- affine 公式或 fallback 语义变化必须升级 generator version，并重新运行固定图片的 Chromium/Oracle 集成测试；
- benchmark 可以调用公共 kernel，但生产代码不得依赖 `shaderforge.benchmark`。
