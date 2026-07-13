# Prompts 架构

`src/agent/app/prompts/` 保存 Prompt YAML 和加载器。Prompt 主体只放在这里。

## 当前文件

- `image_to_glsl.yaml`：当前兼容生成节点使用的 WebGL1 无贴图 GLSL Prompt；`u_image` 只保留声明，禁止采样。
- `shader_review.yaml`：原图、当前渲染图和 GLSL 的评审 Prompt。
- `prompt_loader.py`：Prompt 加载入口，同时读取 YAML `version` 供模型调用审计。

## 边界规则

- 后端 route 和 service 不写 Prompt。
- 节点只通过 loader 引用 Prompt，不在节点里硬编码大段 Prompt 主体。
- Prompt 文件名应表达用途，并与节点中的 `prompt_version` 摘要保持可追踪。
- 改动 Prompt 语义时必须更新 YAML `version`；Node 从 loader 返回的定义读取版本，不另写一份易漂移常量。
- Prompt 改动影响模型输出契约时，需要同步测试和相关功能文档。
