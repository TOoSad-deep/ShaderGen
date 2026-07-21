# ShaderForge Contracts 架构

`contracts/` 定义 PNG 转 Shader 各阶段共享的稳定、无副作用契约。

## 当前文件

- `png_to_shader_v1.py`：V1 WebGL1 无贴图运行时契约、问题域、停止原因、质量档位、预算和接受策略。
- `base.py`：V2+ strict/frozen Pydantic 基础类型；`pydantic>=2.12.5` 是直接运行依赖。
- `canonical.py`：`canonical_json_v1`，统一 UTF-8/NFC、稳定排序、binary64 小写 hex、`-0` 归零及 NaN/Infinity 拒绝。

## 规则

- 类型使用不可变 dataclass、Enum 和 tuple 表达；
- 默认值必须能在单元测试中确定性复现；
- 阈值是初始工程默认值，后续只能依据 benchmark 证据校准；
- contracts 不读取环境变量、不访问文件、不启动浏览器、不调用模型；
- `import shaderforge.contracts` 是轻量契约边界，不得通过 `shaderforge` 父包的兼容导出间接加载 `shaderforge.rendering` 或 Playwright；
- Prompt、Validator、Renderer、Graph 和 API 应引用同一个 canonical contract；当前 Validator 和 Renderer 只支持 `WEBGL1_STATIC_NO_TEXTURE_V1`，收到不等价的 `RenderContract` 必须拒绝，不能只回显其 contract id 后继续按 V1 规则执行。
- V2 领域模型按 `analysis`、`intent`、`genome`、`evaluation`、`store` 和 Agent `states` 归属，不集中回填到单体 contracts 文件；跨领域只通过 typed 子包公共根引用。
