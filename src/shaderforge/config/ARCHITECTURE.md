# ShaderForge 运行配置

`runtime_timeouts.yaml` 是模型、Renderer、Backend engine/production-shadow 与前端等待窗口的唯一默认配置源，所有时间统一使用秒。

- Python 通过 `runtime_timeouts.py` 从 wheel 包资源加载并严格校验；未知字段、非正有限数和不满足内外层顺序的配置会在导入时 fail-fast。
- Vite 在启动或构建时直接读取同一 YAML，把 `frontend` 子树注入浏览器代码；前端不维护第二套默认值。
- 修改 YAML 后必须重启 Backend 与前端开发服务器；生产前端必须重新构建。
- `engine.attempt_seconds` 必须大于模型请求与 Renderer prepare 之和；每个前端质量档的生成请求必须大于两个串行 engine attempt 加 close 的上界，以覆盖 direct 失败后 fresh fallback。
- 构造器仍允许测试显式注入更短 timeout，但产品默认值不得绕过本 YAML。
