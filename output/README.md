# Output 目录约定

`output/` 按产物用途分别组织，不对所有结果机械套用同一层级。

```text
output/
├── png-to-shader/<png-slug>/<YYYY-MM-DD>/<parent-run-id>/final/
├── png-to-shader-direct-private/
│   └── <png-slug>/<YYYY-MM-DD>/<parent-run-id>/<attempt-id>/private/
├── diagnostics/run-analysis/<png-slug>/<YYYY-MM-DD>/<run-id>/
├── visual-acceptance/<scenario>/<YYYY-MM-DD>/<screenshot>.png
├── legacy/
│   ├── node-lab/<YYYY-MM-DD>/<pipeline-id>/<lab-run-id>/
│   └── png-to-shader-rollout-private/
├── benchmarks/<sealed-benchmark-run>/
└── black-hole-preprocessing-20260731/
```

- `png-slug` 来自上传图片名，保留中英文和数字，路径字符会被安全净化。
- 日期使用服务端 `Asia/Shanghai` 日历日期；历史 Artifact 以数据库
  `agent_runs.started_at` 为准。
- public 的末级 ID 是 parent run ID；private 再增加 attempt ID，便于二者关联。
- 历史迁移采用 copy + 校验 + v2 index 切换；旧 `project_id/run_id` 目录作为
  可回滚副本保留，不再是 canonical 读取位置。
- `benchmarks` 是封存评测证据，run 内文件、相对路径和哈希不得改写。
- `black-hole-preprocessing-20260731` 已包含语义和日期，保持实验包整体不变。

## 迁移工具

生产 Artifact 默认只 dry-run：

```bash
uv run python scripts/migrate_output_layout.py --scope all
```

只有确认 Backend 已停止写入后才能切换索引：

```bash
uv run python scripts/migrate_output_layout.py \
  --scope all \
  --apply \
  --maintenance-confirmed \
  --journal output/.layout-migrations/output-layout-YYYYMMDD.jsonl
```

回滚只恢复旧 v1 索引，不删除新副本或旧源目录：

```bash
uv run python scripts/migrate_output_layout.py \
  --rollback output/.layout-migrations/output-layout-YYYYMMDD.jsonl
```

历史类别使用独立工具；它通过原子 rename 和 inventory journal 保证可回滚：

```bash
uv run python scripts/organize_legacy_output.py
uv run python scripts/organize_legacy_output.py \
  --rollback output/.layout-migrations/legacy-output-2026-08-07.json
```
