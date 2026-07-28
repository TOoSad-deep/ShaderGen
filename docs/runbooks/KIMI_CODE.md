# Kimi Code 运行手册

本手册记录 ShaderGen 中通过终端或 Orca 调用 Kimi Code 的已验证路径。只有任务需要 Kimi Code 时才读取；CLI、模型目录或 Orca 版本变化后，应重新核对帮助和实际行为，不要把本手册当作永久不变的命令契约。

## 已验证基线

- 验证日期：2026-07-28
- Kimi Code CLI：`0.29.2`
- Orca：`1.4.158`
- Kimi K3 模型别名：`kimi-code/k3`
- K3 effort：`low`、`high`、`max`；当前默认值为 `high`

## 路径选择

- 一次性、边界清晰、可以用单个 prompt 描述的子任务，优先直接从终端非交互调用。
- 需要 Orca 的当前 worktree、可见终端或统一运行上下文时，通过 Orca 调度。
- 无论使用哪条路径，主 Agent 都负责限定修改范围、审查产出、运行聚焦测试和完成视觉验收。

## 调用前检查

首次使用、环境变化或调用失败时运行：

```bash
command -v kimi
kimi --version
kimi doctor
kimi provider list --json
```

确认 CLI、认证、准确模型别名和当前支持的 effort。不要根据模型显示名称猜别名。

K3 只接受 `low`、`high`、`max`，不要传入 Codex 使用的 `medium`、`xhigh` 等值，也不要假设当前 CLI 存在 `--effort` 参数。通过当前版本实际支持的 Kimi Code 配置或交互界面设置 effort，并确认生效值。若需要修改用户级 `~/.kimi-code/config.toml` 中的 `[thinking].effort`，必须先取得授权，避免覆盖用户偏好或影响并发会话。

## 终端直连

对一次性子任务使用：

```bash
kimi -m kimi-code/k3 -p "<task>" --output-format text
```

注意事项：

- Kimi 会在 `~/.kimi-code/sessions/` 和 `~/.kimi-code/logs/` 写入运行数据，并需要访问网络。
- `-p` 是非交互执行；prompt 必须明确任务范围、允许修改的文件、验收标准和禁止触碰的内容。
- 若创建 session 或 log 目录时出现 `EPERM`，应申请相应文件系统和网络权限后重试，不要误判为模型、认证或 prompt 故障。
- 调用结束后由主 Agent 检查 `git status` 和 diff，确认没有超出范围的修改。

## 通过 Orca 调度

### 1. 加载当前版本指南

不要凭记忆猜 Orca 子命令。先运行：

```bash
orca skills get orca-cli
orca status --json
```

若 Orca 未运行：

```bash
orca open --json
orca status --json
```

如果 `open` 已报告 runtime ready，但沙箱内后续命令返回 `runtime_unavailable`，应使用与 Orca 桌面 runtime 相同的权限上下文重试，不要反复重启 Orca。

### 2. 在当前 worktree 创建 Kimi 终端

已验证的一次性调用路径：

```bash
orca terminal create --worktree active --title "<title>" \
  --command 'kimi -m kimi-code/k3 -p "<task>" --output-format text' --json
```

从返回值复制完整的 `terminal.handle`，不要自行构造或复用旧 runtime 的 handle。

### 3. 读取并判断结果

```bash
orca terminal read --terminal <handle> --json
```

模型命令结束后，Orca 可能保留 shell 并显示新的 prompt。因此不要只依赖下面的命令判断模型是否完成：

```bash
orca terminal wait --terminal <handle> --for exit --timeout-ms 60000 --json
```

该等待可能超时，即使 Kimi 已经正常返回。应以 `terminal read` 中的模型输出、恢复会话提示和 shell prompt 共同判断。

### 4. 清理测试终端

先确认终端仍然存在：

```bash
orca terminal list --worktree active --json
```

只关闭本次创建且 handle 匹配的终端：

```bash
orca terminal close --terminal <handle> --json
```

若关闭返回 `tab_not_found`，先重新列出终端确认它是否已自动关闭，不要改为关闭其他未知终端。

## 成功判定

一次调用只有同时满足以下条件才算完成：

- 输出确认实际使用 `kimi-code/k3`，并得到与任务相符的结果。
- 没有权限、认证、网络或 Orca runtime 错误被误当成模型失败。
- 主 Agent 已审查工作区 diff，并完成与修改范围匹配的测试。
- 涉及前端或视觉结果时，已通过实际页面、截图或渲染结果完成视觉验收。
