# ShaderForge Validation 架构

`validation/` 在启动浏览器前执行快速、确定性的 WebGL1 无贴图契约检查。

## 当前能力

- 必需 precision、varying、uniform、`main` 和 `gl_FragColor`；
- 禁止参考图采样、WebGL2 输入输出、`#version`、扩展和 `mainImage`；
- Shader 长度和明显无界循环；
- 除零、零向量 normalize、常量反向 smoothstep 等数值问题；
- 大坐标平方等 `mediump` 风险 warning。
- `repair_constant_reversed_smoothsteps()` 只修复常量严格倒序的完整调用；相等边界和注释不改写，修复不是语义等价证明，调用方必须重新执行 Validator 和 Renderer。

## 边界

- 扫描会先移除注释，注释中的 `texture2D` 不构成违规；
- 静态校验不是 GLSL 编译器，真实语法、类型、link 和 draw 结果必须由 WebGL1 Renderer 给出；
- error 会阻止渲染，warning 进入诊断但不单独判失败；
- Validator 是 canonical `WEBGL1_STATIC_NO_TEXTURE_V1` 的专用校验器，不是任意 `RenderContract` 的通用解释器；传入不等价契约会在校验前被拒绝，避免 contract id 与实际规则错配。
- 确定性修复不是 Validator 的隐式步骤，也不会由 Renderer 自动调用；原始源码是否修复必须由上层显式决策并保留审计摘要。
