# ShaderForge 架构

`src/shaderforge/` 保存与 HTTP、UI 和具体 LLM 供应商无关的确定性领域能力。

## 当前实现

F09 已完成 M0 契约层、M1 最小事实层和 M3 所需的确定性候选选择：

- `contracts/`：`webgl1_static_no_texture_v1`、问题域、停止原因、质量档位、预算和候选接受策略；
- `analysis/`：PNG 解码、白底 alpha 合成、主体 bbox/置信度、调色板、代表像素、边缘与 ROI 测量；
- `validation/`：WebGL1 GLSL ES 1.00 无贴图静态校验和数值风险诊断；
- `rendering/`：项目自有 Playwright/Chromium WebGL1 编译、链接、渲染、PNG 导出和运行时元数据；
- `evaluation/`：sRGB RMSE/MAE、边缘、几何、代表像素、ROI 与保护区域 Basic Oracle，以及 CandidateRecord/current_best 纯选择器；
- `generation/`：从 normalized reference 与 TargetMeasurements 生成无模型、无贴图的 affine/solid ellipse Shader seed 及稳定 provenance；
- `scene.py`：`scene_mvp` 的严格、版本化单主体 scene 与 typed patch 契约；
- `perception/`：最小链路的背景、bbox、中心/轴长和代表色确定性测量；
- `generation/min_template.py`：scene 到固定 WebGL1 模板、typed uniform 和双 GLSL 导出的确定性物化；
- `evaluation/mae.py`：一次解码后可复用的同尺寸 RGB MAE；
- `store/`：`LocalArtifactStore` 负责 project/run 映射与隔离，run 级 `RunArtifactStore` 负责路径安全、原子写入和完整性读取。
- `benchmark/`：M5 固定数据集加载、AI-off baseline、版本化质量门禁和匿名 A/B 盲评包。

三个模型角色和自动修订 Graph 已在 `src/agent/app/` 的 M2/M3 实现并调用上述公共能力；M4 已通过 Agent Service、Backend 白名单 API 和前端双端复核接入产品路径。并行的 `scene_mvp` 已快速贯通确定性感知、scene、模板、真实 Renderer、MAE、Artifact 和阶段 trace，但暂未启用模型 Author、prepared program 或 CMA-ES。M5 已实现 benchmark harness、AI-off smoke、自动门禁与盲评包，最终发布状态仍取决于固定 10 例真实模型结果和独立人工盲评。

## 公共入口

- `shaderforge.public` 是跨多个领域能力的稳定聚合入口，应用层 Harness 或组合代码优先使用它。
- 根包 `shaderforge` 保留原有公共名的兼容导出，但通过所属 typed 子包惰性解析；仅导入 `shaderforge.contracts` 或访问根包契约类型不得加载 Renderer/Playwright。`shaderforge.public` 仍是显式选择的完整聚合入口，可以加载所有当前领域实现。
- 需要精确领域类型、单一职责依赖或避免加载无关实现时，可以从有架构文档的 typed 子包公共根导入，例如 `shaderforge.analysis`、`contracts`、`evaluation`、`generation`、`rendering`、`store`、`validation` 和 `benchmark`。Agent/Backend 产品代码以子包 `__init__.py`/`__all__` 为公共面；仓库内 benchmark 脚本或聚焦测试若必须直接依赖稳定 typed 定义模块（例如 `shaderforge.benchmark.models`），应把该模块纳入对应架构和回归测试，不能任意依赖未声明的私有 helper。
- `agent` 可以编排上述公共能力，但不能把确定性算法复制进 Node；`backend` 不应绕过 Agent service 直接编排未来的完整 Shader 生成闭环。

本文中的 ShaderForge Store 严格限定为 `LocalArtifactStore`、run 级 `RunArtifactStore` 和 `ArtifactRef`。LangGraph `BaseStore` 属于 Agent Memory 边界，`NodeLabStore` 属于诊断 Harness 的 LabRun/步骤/Artifact 索引与访问边界；即使 `NodeLabStore` 内部复用 `RunArtifactStore` 做安全 I/O，也不因此成为 ShaderForge 领域 Store。

## 依赖边界

- 可以依赖 Python 标准库和领域算法依赖；
- 不依赖 FastAPI、React、LangChain 或具体 LLM provider；
- `contracts/` 只保存稳定类型、枚举和默认策略，不执行模型调用、浏览器渲染或文件持久化；
- typed 子包的导入依赖必须与职责一致；轻量契约/模型导入不能因父包兼容导出而 eager-load Renderer、Playwright 或其他重型实现；
- `analysis/`、`generation/`、`validation/` 和 `evaluation/` 保持确定性；`rendering/` 可以依赖 Playwright，但不能调用模型；
- `store/` 的 `LocalArtifactStore`/`RunArtifactStore` 只管理领域 Artifact 原子 I/O，不接触 Backend 数据库连接、Agent Memory 或 Node Lab 的索引与权限语义；
- 新增一级子包时同步创建该目录的 `ARCHITECTURE.md` 和聚焦测试。
