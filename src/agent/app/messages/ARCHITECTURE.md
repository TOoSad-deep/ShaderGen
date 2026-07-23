# Messages 架构

`src/agent/app/messages/` 保存两个以上 Node 复用的 LangChain 消息构造 helper。

## 当前文件

- `structured_multimodal.py`：跨 Graph 复用的稳定 JSON、数据文本块、图片标签和 LangChain 多模态消息构造。

## 边界规则

- Messages 只构造消息数据，不创建或调用模型，不选择 Prompt，不决定 Graph 流程。
- 结构化数据统一使用稳定 key 顺序 JSON，并明确标记“数据，不是指令”；GLSL 使用 JSON 字符串而不是 Markdown fence，避免内容破坏消息边界。
- 最小骨架只导入 `structured_multimodal.py`。
- 单一 Node 私有的消息拼装留在对应 Node；出现两个以上消费者后再抽到本目录。
- 不在消息 helper 中读取 State、Runtime Context、环境变量或数据库。
