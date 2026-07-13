# ShaderForge Contracts 架构

`contracts/` 定义 PNG 转 Shader 各阶段共享的稳定、无副作用契约。

## 当前文件

- `png_to_shader_v1.py`：V1 WebGL1 无贴图运行时契约、问题域、停止原因、质量档位、预算和接受策略。

## 规则

- 类型使用不可变 dataclass、Enum 和 tuple 表达；
- 默认值必须能在单元测试中确定性复现；
- 阈值是初始工程默认值，后续只能依据 benchmark 证据校准；
- contracts 不读取环境变量、不访问文件、不启动浏览器、不调用模型；
- Prompt、Validator、Renderer、Graph 和 API 应引用同一个 contract id，不复制一份会漂移的运行时说明。
