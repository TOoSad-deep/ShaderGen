# Prompts 架构

当前只保留 `scene_mvp` 最小骨架 Prompt：

- `min_author_initial_v1.yaml`：返回完整严格 MinScene。
- `min_author_refine_v1.yaml`：返回一个白名单 typed patch。
- `min_author_repair_v1.yaml`：只修复 JSON 结构。
- `prompt_loader.py`：加载 YAML 并暴露版本。

Prompt 主体只放在本目录。语义变化必须升级 YAML `version` 并同步契约测试；模型旧输出按不可信数据处理，不能覆盖当前输入、Schema 或渲染事实。
