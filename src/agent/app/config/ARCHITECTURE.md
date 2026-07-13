# Config 架构

`src/agent/app/config/` 保存 Agent 通用运行配置，例如默认模型名和通用环境变量解析。

## 当前文件

- `model_config.py`：定义 `SHADER_GEN_MODEL_NAME`、布尔环境变量解析和冻结的 `NodeModelConfig`。

## 边界规则

- 配置模块只解释配置值，不创建模型实例。
- 真实密钥只从 `.env` 或环境变量读取，不写入仓库。
- provider 的 API key/base URL 和 model-family 兼容差异放在 `app/llms/`。
- 与模型实例创建和调用相关的逻辑放在 `app/llms/`；通用调用类型放在 `app/contracts/`。
- 与后端应用生命周期、数据库连接、HTTP 配置相关的内容放在 `backend/app/`，不要放进 Agent config。
