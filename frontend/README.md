# Frontend

前端提供 `scene_mvp` 产品页和独立 Node Lab 工作台。

- `src/api/client.ts` 统一处理 API base 和错误解析。
- `src/api/shader.ts` 封装生成、进度、运行中渲染与 Artifact URL。
- `App.tsx` 上传图片、选择质量档位、轮询进度并展示 ShaderGraph 摘要、服务端 Render、客户端 WebGL1 和 GLSL。
- `/lab` 加载 `NodeLabPage.tsx`，连接独立 Node Lab 服务；它不复用产品 Backend。空 Application 使用整宽接入引导，不渲染无内容的三列工作台，但仍允许创建/恢复 LabRun 和上传 Artifact；离线状态可原地重试，在线空目录可重新读取 factory 注入的节点。设置 `NODELAB_APPLICATION_FACTORY=agent.app.services.node_lab:create_application` 后可调试当前 12 个 `scene_mvp` Node。
- Node Inspector 读取 descriptor 示例中的 `artifact_inputs` 映射，为对应字段提供下拉选择；上传/恢复/执行得到 Artifact 后，对仍为空或占位符的字段自动填充，同 kind 存在多个 Artifact 时保留选择器由用户决定，不会覆盖手写非占位值。切换节点/调用示例时，还会根据 `base_step_node_id` 自动选择推荐父步骤。
- `SceneMvpSummary.tsx` 对权威 `shader_graph_v1` 展示只读 Layer inspector；旧响应若带 `shader_graph_shadow`，仍以明确的非权威兼容区块展示。
- 不再发送 `generation_mode` 或 `project_id`，不再调用项目 Memory API。
- V1 模式选择、score/review/current_best、`RunProgress` 与 `ScoreSummary` 已删除。
- `VITE_API_BASE_URL` 配置产品后端，`VITE_NODE_LAB_API_BASE_URL` 配置独立 Node Lab；`VITE_GENERATION_REQUEST_TIMEOUT_MS` 可覆盖等待超时。它们都会进入浏览器产物，不能包含秘密。

验证：

```bash
npm --prefix frontend run build
npm --prefix frontend run e2e:scene-mvp
```

Node Lab 输入填充纯函数使用 Node 内置 test runner 直接运行（无需额外测试依赖）：

```bash
npm --prefix frontend run test:unit
```
