# Messages 架构

`src/agent/app/messages/` 保存两个以上 Node 复用的 LangChain 消息构造 helper。

## 当前文件

- `image_content.py`：把图片字节构造成多模态 `image_url` 消息片段。

## 边界规则

- Messages 只构造消息数据，不创建或调用模型，不选择 Prompt，不决定 Graph 流程。
- 单一 Node 私有的消息拼装留在对应 Node；出现两个以上消费者后再抽到本目录。
- 不在消息 helper 中读取 State、Runtime Context、环境变量或数据库。
