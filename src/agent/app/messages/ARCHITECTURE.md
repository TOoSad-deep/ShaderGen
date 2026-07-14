# Messages 架构

`src/agent/app/messages/` 保存两个以上 Node 复用的 LangChain 消息构造 helper。

## 当前文件

- `image_content.py`：把图片字节构造成多模态 `image_url` 消息片段。
- `png_to_shader_v1.py`：稳定序列化结构化数据、ContextPack、图片和末尾 GLSL，并验证 candidate/GLSL/render 三方 SHA-256 绑定。

## 边界规则

- Messages 只构造消息数据，不创建或调用模型，不选择 Prompt，不决定 Graph 流程。
- 结构化数据统一使用稳定 key 顺序 JSON，并明确标记“数据，不是指令”；GLSL 使用 JSON 字符串而不是 Markdown fence，避免内容破坏消息边界。
- Critic 和 visual-refine 在调用模型前必须验证 `candidate_id`、`glsl_sha256`、`image_sha256` 和 `CandidateRecord.render_sha256` 一致。
- 单一 Node 私有的消息拼装留在对应 Node；出现两个以上消费者后再抽到本目录。
- 不在消息 helper 中读取 State、Runtime Context、环境变量或数据库。
