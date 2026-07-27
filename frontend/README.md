# Frontend

前端提供 `scene_mvp` 产品页和独立 Node Lab 工作台。

- `src/api/client.ts` 统一处理 API base 和错误解析。
- `src/api/shader.ts` 封装生成、进度、运行中渲染与 Artifact URL。
- `App.tsx` 上传图片、选择质量档位、轮询进度并展示 ShaderGraph 摘要、服务端 Render、客户端 WebGL1 和 GLSL。
- `src/runStages.ts` 是单一、可测试的运行阶段视图模型：把进度事件收敛为 running/succeeded/failed/pending/unknown 运行状态、12 节点阶段（含耗时、Graph 事件累计、trace 摘要、路由与停止原因）、失败定位、预算/current_best 质量进度与 Initial Author 输出来源（仅来自 `author_initial` 的 trace 白名单 `author_source`，不代表最终 current_best provenance）。证据语义：事件只在节点完成时发出，`next_action`/数组顺序只推导“预计下一节点（未确认开始）”，不存在“执行中”阶段；只有真实 `elapsed_ms` 才显示 Graph 事件累计，缺失保持“—”；预算 used 缺失显示“—”而非 0；`render_seq` 只是实时帧刷新序号。不推测后端没有的精确百分比进度。
- `MinRunLivePanel.tsx` 消费该视图模型；终态后面板冻结为历史记录。`App.tsx` 的进度轮询保持 single-flight，并对失败与连续 pending 使用 capped backoff；每次 GET 有独立超时，POST 结算（含用户停止等待/超时）后继续有界观察，直到服务端终态、明确请求拒绝、新 run、页面卸载或观察上限。停止等待明确不是服务端取消。
- `/lab` 加载 `NodeLabPage.tsx`，连接独立 Node Lab 服务；它不复用产品 Backend。
- `SceneMvpSummary.tsx` 对权威 `shader_graph_v1` 展示只读 Layer inspector；旧响应若带 `shader_graph_shadow`，仍以明确的非权威兼容区块展示。
- 不再发送 `generation_mode` 或 `project_id`，不再调用项目 Memory API。
- V1 模式选择、score/review/current_best、`RunProgress` 与 `ScoreSummary` 已删除。
- `VITE_API_BASE_URL` 配置产品后端，`VITE_NODE_LAB_API_BASE_URL` 配置独立 Node Lab；`VITE_GENERATION_REQUEST_TIMEOUT_MS` 可覆盖等待超时。它们都会进入浏览器产物，不能包含秘密。

验证：

```bash
npm --prefix frontend run test
npm --prefix frontend run build
npm --prefix frontend run e2e:scene-mvp
```
