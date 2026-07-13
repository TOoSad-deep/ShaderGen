# Parsers 架构

`src/agent/app/parsers/` 保存模型输出的纯解析逻辑，例如 GLSL 提取和结构化 Review JSON 解析。

## 当前文件

- `shader_response.py`：解析 Shader 生成和渲染评审相关模型输出。

## 边界规则

- Parser 只做纯文本到结构化 Python 类型的转换，不调用模型、不读取 Prompt、不决定图流程。
- Node 可以调用 Parser 解析模型输出后写回 State。
- Service 可以 re-export 稳定 Parser 函数，供后端或测试复用，不直接 import Node 内部 helper。
- 解析结果影响对外契约时，需要同步 service dataclass、测试和相关功能文档。
