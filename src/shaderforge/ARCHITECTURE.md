# ShaderForge 架构

`src/shaderforge/` 保存与 HTTP、UI 和具体 LLM 供应商无关的确定性领域能力。

## 当前实现

F09 已完成 M0 契约层、M1 最小事实层和 M3 所需的确定性候选选择：

- `contracts/`：`webgl1_static_no_texture_v1`、问题域、停止原因、质量档位、预算和候选接受策略；
- `analysis/`：PNG 解码、白底 alpha 合成、主体 bbox/置信度、调色板、代表像素、边缘与 ROI 测量；
- `validation/`：WebGL1 GLSL ES 1.00 无贴图静态校验和数值风险诊断；
- `rendering/`：项目自有 Playwright/Chromium WebGL1 编译、链接、渲染、PNG 导出和运行时元数据；
- `evaluation/`：sRGB RMSE/MAE、边缘、几何、代表像素、ROI 与保护区域 Basic Oracle，以及 CandidateRecord/current_best 纯选择器；
- `store/`：按 project/run 隔离、路径安全、原子写入的本地 Artifact Store。
- `benchmark/`：M5 固定数据集加载、AI-off baseline、版本化质量门禁和匿名 A/B 盲评包。

三个模型角色和自动修订 Graph 已在 `src/agent/app/` 的 M2/M3 实现并调用上述公共能力；M4 已通过 Agent Service、Backend 白名单 API 和前端双端复核接入产品路径。M5 已实现 benchmark harness、AI-off smoke、自动门禁与盲评包，最终发布状态仍取决于固定 10 例真实模型结果和独立人工盲评。搜索、Effect Genome和参数优化留待后续版本。

## 公共入口

跨层调用优先通过 `shaderforge.public`。`agent` 可以编排 ShaderForge 能力，但不能把确定性算法复制进 Node；`backend` 不应绕过 Agent service 直接编排未来的完整 Shader 生成闭环。

## 依赖边界

- 可以依赖 Python 标准库和领域算法依赖；
- 不依赖 FastAPI、React、LangChain 或具体 LLM provider；
- `contracts/` 只保存稳定类型、枚举和默认策略，不执行模型调用、浏览器渲染或文件持久化；
- `analysis/`、`validation/` 和 `evaluation/` 保持确定性；`rendering/` 可以依赖 Playwright，但不能调用模型；
- `store/` 只管理领域 Artifact，不接触 Backend 数据库连接或 Agent Memory；
- 新增一级子包时同步创建该目录的 `ARCHITECTURE.md` 和聚焦测试。
