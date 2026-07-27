import { describe, expect, it } from "vitest";

import type { MinRunProgressEvent, MinRunProgressSnapshot } from "./api/shader";
import {
  buildRunViewModel,
  formatClock,
  formatTraceDetails,
  initialAuthorSourceLabel,
  isTerminalRunStatus,
  MIN_GRAPH_NODES,
  nextPollDelayMs,
  nodeLabel,
  POLL_BASE_DELAY_MS,
  POLL_MAX_DELAY_MS,
  stopReasonLabel,
} from "./runStages";

const NOW = 1_800_000_000; // 固定调用方时钟（秒）
const MOUNT = NOW - 30;

const BUDGETS = {
  render_budget: 96,
  llm_budget: 4,
  refine_budget: 2,
  target_mae: 0.12,
  target_loss: 0.12,
};

function event(partial: Partial<MinRunProgressEvent> & { seq: number; node: string }) {
  return { status: "completed", ...partial } as MinRunProgressEvent;
}

function snapshot(partial: Partial<MinRunProgressSnapshot> = {}) {
  return partial as MinRunProgressSnapshot;
}

function build(
  overrides: Partial<Parameters<typeof buildRunViewModel>[0]> = {},
) {
  return buildRunViewModel({
    events: [],
    snapshot: null,
    status: "running",
    startedAt: null,
    nowSeconds: NOW,
    mountedAtSeconds: MOUNT,
    ...overrides,
  });
}

describe("buildRunViewModel 空数据与状态映射", () => {
  it("空事件 + pending：全部阶段待执行，给出登记提示，不伪造进度", () => {
    const vm = build({ status: "pending" });
    expect(vm.status).toBe("pending");
    expect(vm.statusLabel).toBe("等待服务端登记");
    expect(vm.statusHint).toContain("尚未登记");
    expect(vm.stages).toHaveLength(MIN_GRAPH_NODES.length);
    expect(vm.stages.every((stage) => stage.state === "pending")).toBe(true);
    expect(vm.completedStageCount).toBe(0);
    expect(vm.nextStageId).toBeNull();
    expect(vm.nextStageLabel).toBeNull();
    expect(vm.failure).toBeNull();
    expect(vm.initialAuthorSource).toBeNull();
    expect(vm.quality.targetReached).toBeNull();
    expect(vm.renderSeq).toBeNull();
    expect(vm.timing.frozen).toBe(false);
    expect(vm.timing.startedAtLabel).toBeNull();
  });

  it("未识别的后端状态映射为 unknown，保留原始值并给出提示", () => {
    const vm = build({ status: "canceled" });
    expect(vm.status).toBe("unknown");
    expect(vm.rawStatus).toBe("canceled");
    expect(vm.statusLabel).toBe("未知状态（canceled）");
    expect(vm.statusHint).toContain("canceled");
    expect(vm.terminal).toBe(false);
  });

  it("failed 但事件中没有失败节点时给出诊断提示并冻结", () => {
    const vm = build({ status: "failed" });
    expect(vm.status).toBe("failed");
    expect(vm.failure).toBeNull();
    expect(vm.statusHint).toContain("没有失败节点");
    // 终态即使无事件也必须冻结，不能继续走字。
    expect(vm.timing.frozen).toBe(true);
    expect(vm.timing.elapsedLabel).toBe("Graph 事件累计");
    expect(vm.timing.elapsedSeconds).toBeNull();
  });
});

describe("buildRunViewModel 预计下一节点（不是执行中）", () => {
  const events = [
    event({
      seq: 1,
      node: "initialize_run",
      phase: "initialize",
      elapsed_ms: 500,
      duration_ms: 500,
      trace: [{ phase: "initialize", status: "completed", message: "已登记运行并写入参考图。" }],
    }),
    event({
      seq: 2,
      node: "perceive_target",
      phase: "perception",
      elapsed_ms: 1500,
      duration_ms: 1000,
    }),
  ];

  it("最新事件完成 -> 顺序下一节点只是预计，阶段状态保持待执行", () => {
    const vm = build({ events });
    expect(vm.stages[0].state).toBe("completed");
    expect(vm.stages[1].state).toBe("completed");
    expect(vm.nextStageId).toBe("author_initial");
    expect(vm.nextStageLabel).toBe("生成 ShaderDocument");
    // 任何阶段都不得被标为执行中：阶段状态集合里根本没有 running。
    expect(vm.stages.every((stage) => stage.state !== ("running" as never))).toBe(true);
    expect(vm.stages[2].state).toBe("pending");
    expect(vm.completedStageCount).toBe(2);
  });

  it("decide 节点的 next_action 路由优先于顺序推导（支持 Refine 回跳）", () => {
    const vm = build({
      events: [
        ...events,
        event({
          seq: 3,
          node: "decide_after_feature",
          next_action: "author_refine",
          stop_reason: "continue",
          elapsed_ms: 9000,
        }),
      ],
    });
    const decide = vm.stages.find((stage) => stage.id === "decide_after_feature");
    expect(decide?.nextActionLabel).toBe("模型修订");
    expect(decide?.stopReasonLabel).toBe("继续");
    expect(vm.nextStageId).toBe("author_refine");
    const refine = vm.stages.find((stage) => stage.id === "author_refine");
    expect(refine?.state).toBe("pending");
  });

  it("finalize 完成或终态后不再有预计下一节点", () => {
    const afterFinalize = build({
      events: [event({ seq: 1, node: "finalize", elapsed_ms: 7000 })],
    });
    expect(afterFinalize.nextStageId).toBeNull();

    const terminal = build({
      status: "succeeded",
      events: [event({ seq: 1, node: "optimize_base", elapsed_ms: 7000 })],
    });
    expect(terminal.nextStageId).toBeNull();
  });

  it("阶段摘要与详情来自最近一次 trace，耗时与累计时间使用服务端数值", () => {
    const vm = build({ events });
    const init = vm.stages[0];
    expect(init.summary).toBe("已登记运行并写入参考图。");
    expect(init.lastDurationMs).toBe(500);
    expect(init.lastElapsedMs).toBe(500);
    const perceive = vm.stages[1];
    expect(perceive.summary).toBeNull();
    expect(perceive.lastElapsedMs).toBe(1500);
  });

  it("重复访问的节点累计 visits", () => {
    const vm = build({
      events: [
        event({ seq: 1, node: "render_and_evaluate", elapsed_ms: 1000 }),
        event({ seq: 2, node: "optimize_base", elapsed_ms: 2000 }),
        event({ seq: 3, node: "render_and_evaluate", elapsed_ms: 3000 }),
      ],
    });
    const render = vm.stages.find((stage) => stage.id === "render_and_evaluate");
    expect(render?.visits).toBe(2);
    expect(render?.lastElapsedMs).toBe(3000);
  });
});

describe("buildRunViewModel 失败与阻塞原因", () => {
  it("failed 事件沉淀为失败视图：阶段、摘要与停止原因", () => {
    const vm = build({
      status: "failed",
      events: [
        event({ seq: 1, node: "initialize_run", elapsed_ms: 100 }),
        event({
          seq: 2,
          node: "render_and_evaluate",
          status: "failed",
          elapsed_ms: 2000,
          stop_reason: "render_failed",
          trace: [
            { phase: "render", status: "failed", message: "ShaderGraph typed patch 渲染失败。" },
          ],
        }),
      ],
    });
    expect(vm.failure).not.toBeNull();
    expect(vm.failure?.stageLabel).toBe("渲染与评估");
    expect(vm.failure?.summary).toBe("ShaderGraph typed patch 渲染失败。");
    expect(vm.failure?.stopReasonLabel).toBe("渲染失败");
    const render = vm.stages.find((stage) => stage.id === "render_and_evaluate");
    expect(render?.state).toBe("failed");
    expect(render?.traceFailed).toBe(true);
    // 终态下没有预计下一节点。
    expect(vm.nextStageId).toBeNull();
  });
});

describe("buildRunViewModel 计时口径", () => {
  it("终态冻结在最后事件的 Graph 事件累计，标题不含完整 run 时长暗示", () => {
    const vm = build({
      status: "succeeded",
      events: [
        event({ seq: 1, node: "initialize_run", elapsed_ms: 500 }),
        event({ seq: 2, node: "finalize", elapsed_ms: 7000 }),
      ],
    });
    expect(vm.timing.frozen).toBe(true);
    expect(vm.timing.elapsedSeconds).toBe(7);
    expect(vm.timing.elapsedLabel).toBe("Graph 事件累计");
  });

  it("有 started_at 的运行中计时写“已运行”并展示开始时刻", () => {
    const startedAt = new Date((NOW - 95) * 1000).toISOString();
    const vm = build({
      startedAt,
      events: [event({ seq: 1, node: "optimize_base", elapsed_ms: 90_000 })],
    });
    expect(vm.timing.elapsedLabel).toBe("已运行");
    expect(vm.timing.elapsedSeconds).toBe(95);
    expect(vm.timing.startedAtLabel).toMatch(/^\d{2}:\d{2}:\d{2}$/);
    expect(vm.timing.frozen).toBe(false);
  });

  it("无 started_at 的运行中计时写“已观察”，以最后事件 Graph 累计为下限", () => {
    const vm = build({
      events: [event({ seq: 1, node: "optimize_base", elapsed_ms: 120_000 })],
    });
    // 本地只观察了 30s，但服务端已报 120s，不得回退到客户端时钟。
    expect(vm.timing.elapsedLabel).toBe("已观察");
    expect(vm.timing.elapsedSeconds).toBe(120);
    expect(vm.timing.startedAtLabel).toBeNull();
    expect(vm.timing.frozen).toBe(false);
  });
});

describe("buildRunViewModel Initial Author 输出来源与 current_best", () => {
  it("只从 author_initial trace 提取 Initial Author 输出来源", () => {
    const vm = build({
      events: [
        event({
          seq: 1,
          node: "author_initial",
          trace: [
            {
              phase: "author_initial",
              status: "completed",
              message: "模型调用或解析失败，安全回退到感知 ShaderGraph。",
              author_source: "perception_fallback",
            },
          ],
        }),
      ],
    });
    expect(vm.initialAuthorSource).toBe("perception_fallback");
    expect(vm.initialAuthorSourceLabel).toBe("感知兜底 ShaderGraph");

    const refineOnly = build({
      events: [
        event({
          seq: 1,
          node: "author_refine",
          trace: [{ author_source: "model", status: "completed" }],
        }),
      ],
    });
    expect(refineOnly.initialAuthorSource).toBeNull();
  });

  it("质量进度只由真实 best/target 推导，缺 target 不给结论", () => {
    const reached = build({
      snapshot: snapshot({
        budgets: BUDGETS,
        best: { mae: 0.08, loss: 0.08 },
        render_seq: 3,
      }),
    });
    expect(reached.quality.targetReached).toBe(true);
    expect(reached.renderSeq).toBe(3);
    expect(reached.budgets[0]).toMatchObject({ used: null, budget: 96 });

    const noTarget = build({
      snapshot: snapshot({ budgets: { render_budget: 96 }, best: { loss: 0.5 } }),
    });
    expect(noTarget.quality.targetReached).toBeNull();

    const missed = build({
      snapshot: snapshot({ budgets: BUDGETS, best: { loss: 0.5 } }),
    });
    expect(missed.quality.targetReached).toBe(false);
  });

  it("预算 used 缺省保持 null（UI 必须渲染为 —，不是 0）", () => {
    const vm = build({
      snapshot: snapshot({
        budgets: BUDGETS,
        counters: { render_count: 36, llm_call_count: 1, refine_count: 2 },
      }),
    });
    expect(vm.budgets).toEqual([
      { id: "render", label: "渲染 draw", used: 36, budget: 96 },
      { id: "llm", label: "LLM 调用", used: 1, budget: 4 },
      { id: "refine", label: "模型修订", used: 2, budget: 2 },
    ]);
    expect(vm.refineCount).toBe(2);

    const missing = build({ snapshot: snapshot({ budgets: BUDGETS }) });
    expect(missing.budgets.every((budget) => budget.used === null)).toBe(true);
  });
});

describe("buildRunViewModel 前向兼容", () => {
  it("未知节点事件计数并跳过，不影响已知阶段", () => {
    const vm = build({
      events: [
        event({ seq: 1, node: "initialize_run", elapsed_ms: 100 }),
        event({ seq: 2, node: "future_node", elapsed_ms: 200 }),
      ],
    });
    expect(vm.unknownEventCount).toBe(1);
    expect(vm.eventCount).toBe(2);
    expect(vm.stages[0].state).toBe("completed");
  });
});

describe("标签与格式化辅助", () => {
  it("节点标签符合当前产品事实", () => {
    expect(nodeLabel("initialize_run")).toBe("初始化运行");
    expect(nodeLabel("author_initial")).toBe("生成 ShaderDocument");
    expect(nodeLabel("materialize_shader")).toBe("编译 ShaderGraph");
    expect(nodeLabel("optimize_feature")).toBe("node/layer 参数块优化");
    expect(nodeLabel("decide_after_feature")).toBe("参数块优化后决策");
    expect(nodeLabel("future_node")).toBe("future_node");
    // 不得再出现旧语义标签。
    const labels = MIN_GRAPH_NODES.map((node) => node.label).join("|");
    expect(labels).not.toContain("Scene 生成");
    expect(labels).not.toContain("物化");
    expect(labels).not.toContain("特性优化");
  });

  it("停止原因/候选来源标签带原始值兜底", () => {
    expect(stopReasonLabel("target_loss_reached")).toBe("达到目标损失");
    expect(stopReasonLabel("something_new")).toBe("something_new");
    expect(stopReasonLabel(null)).toBeNull();
    expect(initialAuthorSourceLabel("model")).toBe("模型生成");
    expect(initialAuthorSourceLabel(undefined)).toBeNull();
  });

  it("formatClock 与 formatTraceDetails 稳定输出", () => {
    expect(formatClock(0)).toBe("00:00");
    expect(formatClock(125)).toBe("02:05");
    expect(formatClock(-5)).toBe("00:00");
    expect(
      formatTraceDetails({
        phase: "render",
        status: "completed",
        message: "x",
        candidates_evaluated: 32,
        accepted_parameter: "object.scale",
        author_tokens: null,
      }),
    ).toBe("candidates_evaluated=32 · accepted_parameter=object.scale · author_tokens=—");
  });
});

describe("轮询策略", () => {
  it("capped backoff：失败次数指数退避并封顶", () => {
    expect(nextPollDelayMs(0)).toBe(POLL_BASE_DELAY_MS);
    expect(nextPollDelayMs(1)).toBe(POLL_BASE_DELAY_MS * 2);
    expect(nextPollDelayMs(2)).toBe(POLL_BASE_DELAY_MS * 4);
    expect(nextPollDelayMs(100)).toBe(POLL_MAX_DELAY_MS);
  });

  it("终态判定只认 succeeded/failed，running 不属于终态", () => {
    expect(isTerminalRunStatus("succeeded")).toBe(true);
    expect(isTerminalRunStatus("failed")).toBe(true);
    expect(isTerminalRunStatus("running")).toBe(false);
    expect(isTerminalRunStatus("pending")).toBe(false);
    expect(isTerminalRunStatus("canceled")).toBe(false);
  });
});
