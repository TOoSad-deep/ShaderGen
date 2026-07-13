# ShaderForge 架构

`src/shaderforge/` 保存与 HTTP、UI 和具体 LLM 供应商无关的确定性领域能力。

## 当前实现

F09 M0 只落地 `contracts/`：

- `webgl1_static_no_texture_v1` 运行时契约；
- V1 问题域、停止原因和质量档位；
- 预算和候选接受策略。

Renderer、Oracle、Artifact Store、搜索和 Effect Genome 尚未实现。不得因为目录已经创建就假装这些能力存在。

## 公共入口

跨层调用优先通过 `shaderforge.public`。`agent` 可以编排 ShaderForge 能力，但不能把确定性算法复制进 Node；`backend` 不应绕过 Agent service 直接编排未来的完整 Shader 生成闭环。

## 依赖边界

- 可以依赖 Python 标准库和领域算法依赖；
- 不依赖 FastAPI、React、LangChain 或具体 LLM provider；
- `contracts/` 只保存稳定类型、枚举和默认策略，不执行模型调用、浏览器渲染或文件持久化；
- 新增一级子包时同步创建该目录的 `ARCHITECTURE.md` 和聚焦测试。
