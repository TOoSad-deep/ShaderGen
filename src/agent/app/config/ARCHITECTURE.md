# Config 架构

`src/agent/app/config/` 保存 Agent 通用运行配置，例如默认模型名和通用环境变量解析。

## 当前文件

- `model_config.py`：定义 `SHADER_GEN_MODEL_NAME`、布尔环境变量解析和冻结的 `NodeModelConfig`。
- `png_to_shader_min.yaml`：`scene_mvp` 的共享质量目标，以及 ShaderGraph engine 的 run 身份、报告版本与 fast/balanced/high/manual render/LLM/Refine 硬预算。Direct engine 读取同名质量档位的目标，但使用独立 `LayerPlanGlslDirectConfig` 预算。
- `png_to_shader_min.py`：从包资源加载上述 YAML，严格校验字段、类型、值域、档位完整性和身份一致性，生成规范配置 SHA-256，按 LLM/Refine 预算与最多 12 个参数 block 推导每档 Graph recursion limit 和 ShaderGraph program compile 上限，并向 Graph Service/Model Author 提供不可变策略。保留的 `frozen_benchmark` 校验只用于历史配置复验，不触发或要求当前 benchmark。

## 边界规则

- 配置模块只解释配置值，不创建模型实例。
- YAML 在进程导入时加载，修改后必须重启服务；无效配置 fail-fast，不使用代码内备用目标或产品预算。
- 合法路径的最坏节点步数为 `9 + 2F + 6R`，其中 `R=min(refine_budget,max(llm_budget-1,0))`、`F<=12`；首次遍历参数 block，后续 Refine 经 render 与 no-op base 过桥，不重新遍历参数队列。运行上限再增加 4 步框架余量，推导值超过 256 时拒绝启动。
- ShaderGraph 合法路径的 program compile 上限为 `I + 1 + F + R`：`I` 在启用 Initial 模型时为 2（模型文档与感知 fallback），否则为 1；`1 + F` 对应 canvas 与参数 block active program；每轮 Refine 最多增加一个结构 program。manual `32/30` 因此为 45，cache 存活容量仍独立固定为 4。
- 真实密钥只从 `.env` 或环境变量读取，不写入仓库。
- provider 的 API key/base URL 和 model-family 兼容差异放在 `app/llms/`。
- 与模型实例创建和调用相关的逻辑放在 `app/llms/`；通用调用类型放在 `app/contracts/`。
- 与后端应用生命周期、数据库连接、HTTP 配置相关的内容放在 `backend/app/`，不要放进 Agent config。
