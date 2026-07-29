# Layered Direct Author

本目录负责默认 direct engine 的有界模型调用和单步 graph node：

- Initial 接收参考图、canonical LayerPlan 和 canvas，输出完整
  `LayeredShaderSpecV1` 语义；可信层注入 AuthorIdentity、Plan hash 和内容
  hash。
- Refine 接收参考图、current render、整图指标和 current best，只输出一个
  `LayerPatchV1`；Patch 绑定父 Layered Spec、目标 Layer 和旧 Layer hash。

工作流 node 按职责拆分为：

- `workflow_author_nodes.py`：reference、LayerPlan、Initial、Refine 和 Patch；
- `candidate_nodes.py`：compile、validate、prepare、draw、receipt、
  attestation、evaluate 和 incumbent selection；
- `lifecycle_nodes.py`：Refine 路由、资源释放和结果冻结；
- `workflow_support.py`：稳定的 trace、失败记录和候选路由辅助。

`structured_author.py` 把一次结构化调用展开为受保护的内部子图：

```text
START → invoke_original → parse_original
          ├─ valid/no budget → finalize → END
          └─ repair → invoke_repair
                         ├─ call failed → finalize
                         └─ parse_repair → finalize
```

公共调用必须经过 `invoke_structured_author`；该入口关闭 tracing，并保持
最多两次调用、token/latency/identity 和 repair context hash 契约。

严格 JSON adapter 位于 `agent.app.contracts.layered_direct_glsl`，确定性
Patch/Compiler 位于 `shaderforge.layered_spec`。节点只编排这些领域能力，
不实现历史整份 ProgramSpec Initial/Refine Author。
