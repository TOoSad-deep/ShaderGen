# Frontend

前端只提供 `scene_mvp` 最小骨架页面。

- `src/api/client.ts` 统一处理 API base 和错误解析。
- `src/api/shader.ts` 封装生成、进度、运行中渲染与 Artifact URL。
- `App.tsx` 上传图片、选择质量档位、轮询进度并展示 Scene 摘要、服务端 Render、客户端 WebGL1 和 GLSL。
- 不再发送 `generation_mode` 或 `project_id`，不再调用项目 Memory API。
- V1 模式选择、score/review/current_best、`RunProgress` 与 `ScoreSummary` 已删除。
- `VITE_API_BASE_URL` 配置后端地址；`VITE_GENERATION_REQUEST_TIMEOUT_MS` 可覆盖等待超时。两者都会进入浏览器产物，不能包含秘密。

验证：

```bash
npm --prefix frontend run build
npm --prefix frontend run e2e:scene-mvp
```
