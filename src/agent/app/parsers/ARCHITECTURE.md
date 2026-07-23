# Parsers 架构

`src/agent/app/parsers/` 保存模型输出的纯解析逻辑。

## 当前文件

- `png_to_shader_min.py`：严格解析 `scene_mvp` 完整 MinScene 或恰好一个 typed patch；拒绝 fence、自然语言、数组 envelope、未知字段、非白名单 path/operation/value 和改变参考图画布的 scene。

## 边界规则

- Parser 只做纯文本到结构化 Python 类型的转换，不调用模型、不读取 Prompt、不决定图流程。
- `scene_mvp` 只接受单个完整 JSON 值；Initial 必须绑定感知画布，Refine 必须是联合类型 Schema 允许的一个 patch 对象。
- Parser 不调用 `validate_shader` 或 Renderer。
- Node 可以调用 Parser 解析模型输出后写回 State。
- Service 不直接 import Node 内部 helper；需要对外暴露 Parser 时，只能 re-export 稳定的纯函数。
- 解析结果影响对外契约时，需要同步 service dataclass、测试和相关功能文档。
