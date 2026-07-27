// scene_mvp 运行阶段视图模型：把后端白名单进度事件收敛为单一、可测试的纯函数视图。
// 只消费 /api/shader/runs/{run_id}/progress 已提供的字段，不推测后端没有给出的精确进度。
// 证据语义约束（D089 修订）：
// - 事件只在节点完成时发出，next_action/数组顺序只能说明“预计下一节点”，不得标为执行中；
// - 终态 elapsed_ms 只是 Graph 事件累计，不是包含 persistence/response 的完整 run 时长；
// - author_source 只是结构化候选来源，不代表 GLSL 生成依据。
import type {
  MinRunProgressEvent,
  MinRunProgressSnapshot,
  ShaderApiFailure,
} from "./api/shader";

// 与 src/agent/app/graphs/png_to_shader_min_graph.py 的 12 个节点一一对应。
export const MIN_GRAPH_NODES: ReadonlyArray<{ id: string; label: string }> = [
  { id: "initialize_run", label: "初始化运行" },
  { id: "perceive_target", label: "感知目标图" },
  { id: "author_initial", label: "生成 ShaderDocument" },
  { id: "materialize_shader", label: "编译 ShaderGraph" },
  { id: "render_and_evaluate", label: "渲染与评估" },
  { id: "decide_after_render", label: "渲染后决策" },
  { id: "optimize_base", label: "基础参数优化" },
  { id: "decide_after_base", label: "基础优化后决策" },
  { id: "optimize_feature", label: "node/layer 参数块优化" },
  { id: "decide_after_feature", label: "参数块优化后决策" },
  { id: "author_refine", label: "模型修订" },
  { id: "finalize", label: "固化产物" },
];

const NODE_LABELS = new Map(MIN_GRAPH_NODES.map((node) => [node.id, node.label]));

export function nodeLabel(id: string): string {
  return NODE_LABELS.get(id) ?? id;
}

export type KnownRunStatus = "pending" | "running" | "succeeded" | "failed";
export type RunStatus = KnownRunStatus | "unknown";
// 后端事件只在节点完成时发出，阶段只有三种可证实状态；不存在“执行中”阶段。
export type StageState = "pending" | "completed" | "failed";

const RUN_STATUS_LABELS: Record<KnownRunStatus, string> = {
  pending: "等待服务端登记",
  running: "运行中",
  succeeded: "已完成",
  failed: "失败",
};

const STOP_REASON_LABELS: Record<string, string> = {
  continue: "继续",
  target_loss_reached: "达到目标损失",
  render_budget_exhausted: "渲染预算用尽",
  render_failed: "渲染失败",
  no_valid_render: "无有效渲染",
  bounded_mvp_complete: "有界流程完成",
};

export function stopReasonLabel(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return STOP_REASON_LABELS[reason] ?? reason;
}

// trace 白名单 author_source 只说明 Initial ShaderDocument 的来源，不是最终
// current_best provenance；render_and_evaluate 的 selected_source 也只证明首轮选择。
const AUTHOR_SOURCE_LABELS: Record<string, string> = {
  model: "模型生成",
  perception_fallback: "感知兜底 ShaderGraph",
};

export function initialAuthorSourceLabel(source: string | null | undefined): string | null {
  if (!source) return null;
  return AUTHOR_SOURCE_LABELS[source] ?? source;
}

const INITIAL_SELECTION_SOURCE_LABELS: Record<string, string> = {
  model_or_fallback: "Author 输出候选",
  perception_fallback: "感知兜底候选",
};

export function initialSelectionSourceLabel(
  source: string | null | undefined,
): string | null {
  if (!source) return null;
  return INITIAL_SELECTION_SOURCE_LABELS[source] ?? source;
}

// 进度轮询策略：失败后 capped backoff 重连，不永久停止；
// 停止条件包括终态、服务端明确失败、新 run 或页面卸载（见 App.tsx）。
export const POLL_BASE_DELAY_MS = 1200;
export const POLL_MAX_DELAY_MS = 10_000;
export const PROGRESS_REQUEST_TIMEOUT_MS = 10_000;

export function nextPollDelayMs(consecutiveFailures: number): number {
  if (consecutiveFailures <= 0) return POLL_BASE_DELAY_MS;
  const doubled = POLL_BASE_DELAY_MS * 2 ** consecutiveFailures;
  return Math.min(doubled, POLL_MAX_DELAY_MS);
}

export function isTerminalRunStatus(status: string): boolean {
  return status === "succeeded" || status === "failed";
}

/** 只有带匹配 run_id 的稳定应用错误才证明本次 run 已明确失败。 */
export function isAuthoritativeRunFailure(
  failure: ShaderApiFailure,
  expectedRunId: string,
): boolean {
  const stableApplicationFailure =
    typeof failure.code === "string" &&
    failure.code.length > 0 &&
    typeof failure.stage === "string" &&
    failure.stage.length > 0;
  // FastAPI 在 multipart/form 本身校验失败时无法可靠回显客户端 form run_id，
  // 但稳定的 request_validation/client_validation 明确发生在 run 创建之前。
  const definitivePreRunValidation =
    failure.status === 422 &&
    failure.retryable === false &&
    failure.code === "client_validation" &&
    failure.stage === "request_validation";
  return (
    stableApplicationFailure &&
    (failure.runId === expectedRunId || definitivePreRunValidation)
  );
}

/** 增量轮询在重试或兼容响应中可能重复事件；按 seq 去重并稳定排序。 */
export function mergeProgressEvents(
  current: MinRunProgressEvent[],
  incoming: MinRunProgressEvent[],
): MinRunProgressEvent[] {
  const bySeq = new Map<number, MinRunProgressEvent>();
  for (const event of current) bySeq.set(event.seq, event);
  for (const event of incoming) {
    if (!bySeq.has(event.seq)) bySeq.set(event.seq, event);
  }
  return [...bySeq.values()].sort((left, right) => left.seq - right.seq);
}

export interface StageView {
  id: string;
  label: string;
  state: StageState;
  visits: number;
  /** 最近一次执行的耗时（服务端相邻节点完成时刻的间隔近似值）。 */
  lastDurationMs: number | null;
  /** 最近一次完成时的 Graph 事件累计耗时。 */
  lastElapsedMs: number | null;
  phase: string | null;
  /** 最近一次 trace 的阶段摘要（后端白名单 message）。 */
  summary: string | null;
  /** 最近一次 trace 的白名单详情 k=v 串。 */
  details: string | null;
  traceFailed: boolean;
  nextAction: string | null;
  nextActionLabel: string | null;
  stopReason: string | null;
  stopReasonLabel: string | null;
}

export interface RunFailureView {
  stageId: string;
  stageLabel: string;
  summary: string | null;
  stopReason: string | null;
  stopReasonLabel: string | null;
}

export interface RunTimingView {
  /** 当前应展示的时长（秒）。 */
  elapsedSeconds: number | null;
  /** 时长口径标签：终态=Graph 事件累计；有 started_at=已运行；否则=已观察。 */
  elapsedLabel: string;
  /** true=终态冻结，不再走字。 */
  frozen: boolean;
  startedAtLabel: string | null;
}

export interface BudgetView {
  id: string;
  label: string;
  used: number | null;
  budget: number | null;
}

export interface RunQualityView {
  bestLoss: number | null;
  bestMae: number | null;
  targetLoss: number | null;
  targetMae: number | null;
  /** 只由真实 best/target 数值推导；数据不足时为 null，不给出结论。 */
  targetReached: boolean | null;
}

export interface RunViewModel {
  status: RunStatus;
  rawStatus: string;
  statusLabel: string;
  /** 对 pending/unknown 等非自明状态的解释；其余状态为 null。 */
  statusHint: string | null;
  terminal: boolean;
  stages: StageView[];
  /** 由最后事件的 next_action 或数组顺序推导的“预计下一节点”，未确认开始。 */
  nextStageId: string | null;
  nextStageLabel: string | null;
  completedStageCount: number;
  failure: RunFailureView | null;
  /** Initial Author 输出来源；不代表最终 current_best provenance。 */
  initialAuthorSource: string | null;
  initialAuthorSourceLabel: string | null;
  /** 首轮真实 render/evaluate 的 selected_source；不代表后续最终 provenance。 */
  initialSelectionSource: string | null;
  initialSelectionSourceLabel: string | null;
  refineCount: number | null;
  quality: RunQualityView;
  budgets: BudgetView[];
  /** 实时帧刷新序号（render_seq），不是 current_best 版本号。 */
  renderSeq: number | null;
  eventCount: number;
  /** 不在 12 节点拓扑内的事件数（后端演进时的前向兼容提示）。 */
  unknownEventCount: number;
  timing: RunTimingView;
}

export interface BuildRunViewModelInput {
  events: MinRunProgressEvent[];
  snapshot: MinRunProgressSnapshot | null;
  status: string;
  /** 后端登记运行的 ISO 时刻；假 API 或旧响应可能缺省。 */
  startedAt?: string | null;
  /** 调用方当前时钟（秒）。 */
  nowSeconds: number;
  /** 面板挂载时钟（秒），仅在缺少 startedAt 时兜底观察计时。 */
  mountedAtSeconds: number;
}

export function formatMs(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)} ms`
    : "—";
}

export function formatMetric(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds);
  const minutes = Math.floor(safe / 60);
  const rest = Math.floor(safe % 60);
  return `${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
}

function formatDetailValue(value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Number.isInteger(value) ? String(value) : value.toFixed(4);
  }
  if (typeof value === "boolean") return value ? "是" : "否";
  if (value === null || value === undefined) return "—";
  return String(value);
}

const TRACE_META_KEYS = new Set(["phase", "status", "message"]);

export function formatTraceDetails(item: Record<string, unknown>): string {
  return Object.entries(item)
    .filter(([key]) => !TRACE_META_KEYS.has(key))
    .map(([key, value]) => `${key}=${formatDetailValue(value)}`)
    .join(" · ");
}

function nextSequentialNodeId(nodeId: string): string | null {
  const index = MIN_GRAPH_NODES.findIndex((node) => node.id === nodeId);
  if (index < 0 || index + 1 >= MIN_GRAPH_NODES.length) return null;
  return MIN_GRAPH_NODES[index + 1].id;
}

function normalizeStatus(raw: string): RunStatus {
  return raw === "pending" || raw === "running" || raw === "succeeded" || raw === "failed"
    ? raw
    : "unknown";
}

function parseIsoSeconds(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed / 1000 : null;
}

function formatWallClock(isoSeconds: number): string {
  const date = new Date(isoSeconds * 1000);
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}

export function buildRunViewModel(input: BuildRunViewModelInput): RunViewModel {
  const { events, snapshot, mountedAtSeconds, nowSeconds } = input;
  const status = normalizeStatus(input.status);
  const terminal = status === "succeeded" || status === "failed";

  const stages: StageView[] = MIN_GRAPH_NODES.map((node) => ({
    id: node.id,
    label: node.label,
    state: "pending",
    visits: 0,
    lastDurationMs: null,
    lastElapsedMs: null,
    phase: null,
    summary: null,
    details: null,
    traceFailed: false,
    nextAction: null,
    nextActionLabel: null,
    stopReason: null,
    stopReasonLabel: null,
  }));
  const byId = new Map(stages.map((stage) => [stage.id, stage]));

  let initialAuthorSource: string | null = null;
  let initialSelectionSource: string | null = null;
  let failure: RunFailureView | null = null;
  let unknownEventCount = 0;

  for (const event of events) {
    const stage = byId.get(event.node);
    if (!stage) {
      unknownEventCount += 1;
      continue;
    }
    stage.visits += 1;
    stage.state = event.status === "failed" ? "failed" : "completed";
    stage.lastDurationMs =
      typeof event.duration_ms === "number" && Number.isFinite(event.duration_ms)
        ? event.duration_ms
        : null;
    stage.lastElapsedMs =
      typeof event.elapsed_ms === "number" && Number.isFinite(event.elapsed_ms)
        ? event.elapsed_ms
        : null;
    stage.phase = typeof event.phase === "string" && event.phase ? event.phase : null;
    if (Array.isArray(event.trace) && event.trace.length > 0) {
      const lastTrace = event.trace[event.trace.length - 1];
      stage.summary = typeof lastTrace.message === "string" && lastTrace.message
        ? lastTrace.message
        : null;
      stage.details = formatTraceDetails(lastTrace) || null;
      stage.traceFailed = lastTrace.status === "failed";
    }
    if (event.next_action) {
      stage.nextAction = event.next_action;
      stage.nextActionLabel = nodeLabel(event.next_action);
      stage.stopReason = event.stop_reason ?? null;
      stage.stopReasonLabel = stopReasonLabel(event.stop_reason);
    }
    if (
      event.node === "author_initial" &&
      !initialAuthorSource &&
      Array.isArray(event.trace)
    ) {
      for (const item of event.trace) {
        if (typeof item.author_source === "string" && item.author_source) {
          initialAuthorSource = item.author_source;
          break;
        }
      }
    }
    if (
      event.node === "render_and_evaluate" &&
      !initialSelectionSource &&
      Array.isArray(event.trace)
    ) {
      for (const item of event.trace) {
        if (typeof item.selected_source === "string" && item.selected_source) {
          initialSelectionSource = item.selected_source;
          break;
        }
      }
    }
    if (event.status === "failed") {
      failure = {
        stageId: stage.id,
        stageLabel: stage.label,
        summary: stage.summary,
        stopReason: event.stop_reason ?? stage.stopReason,
        stopReasonLabel: stopReasonLabel(event.stop_reason ?? stage.stopReason),
      };
    }
  }

  // 最新事件只证明该节点已完成；路由指向或顺序上的下一个只是“预计下一节点”，
  // 后端没有节点开始事件，不得把它当作执行中。终态下没有预计，整视图冻结为历史记录。
  let nextStageId: string | null = null;
  const last = events[events.length - 1];
  if (last && !terminal && last.node !== "finalize") {
    const predictedId = last.next_action ?? nextSequentialNodeId(last.node);
    if (predictedId && byId.has(predictedId)) {
      nextStageId = predictedId;
    }
  }

  const lastElapsedMs =
    last && typeof last.elapsed_ms === "number" && Number.isFinite(last.elapsed_ms)
      ? last.elapsed_ms
      : null;
  const startedAtSeconds = parseIsoSeconds(input.startedAt);

  let elapsedSeconds: number | null;
  let elapsedLabel: string;
  let frozen: boolean;
  if (terminal) {
    // 只有真实 elapsed_ms 才能称为 Graph 事件累计；缺失时保持未知。
    frozen = true;
    elapsedLabel = lastElapsedMs !== null ? "Graph 事件累计" : "终态耗时未知";
    elapsedSeconds = lastElapsedMs !== null ? lastElapsedMs / 1000 : null;
  } else if (startedAtSeconds !== null) {
    frozen = false;
    elapsedLabel = "已运行";
    elapsedSeconds = Math.max(0, nowSeconds - startedAtSeconds);
  } else if (lastElapsedMs !== null) {
    // 无 started_at 时以最后事件的 Graph 累计耗时为下限，叠加本地真实观察时长。
    frozen = false;
    elapsedLabel = "已观察";
    elapsedSeconds = Math.max(lastElapsedMs / 1000, nowSeconds - mountedAtSeconds);
  } else {
    frozen = false;
    elapsedLabel = "已观察";
    elapsedSeconds = Math.max(0, nowSeconds - mountedAtSeconds);
  }

  const timing: RunTimingView = {
    elapsedSeconds,
    elapsedLabel,
    frozen,
    startedAtLabel:
      startedAtSeconds !== null ? formatWallClock(startedAtSeconds) : null,
  };

  const budgets = snapshot?.budgets ?? null;
  const counters = snapshot?.counters ?? null;
  const best = snapshot?.best ?? null;
  const bestLoss = typeof best?.loss === "number" ? best.loss : null;
  const bestMae = typeof best?.mae === "number" ? best.mae : null;
  const targetLoss = typeof budgets?.target_loss === "number" ? budgets.target_loss : null;
  const targetMae = typeof budgets?.target_mae === "number" ? budgets.target_mae : null;

  let statusHint: string | null = null;
  if (status === "pending") {
    statusHint =
      "后端尚未登记该运行（可能仍在排队，或进度注册表已随服务重启清空）；结果以最终响应为准。";
  } else if (status === "unknown") {
    statusHint = `后端返回了未识别的状态“${input.status}”，页面仅按原始事件展示。`;
  } else if (status === "failed" && failure === null) {
    statusHint = "运行被标记为失败，但进度事件中没有失败节点；请结合失败诊断与后端日志定位。";
  }

  return {
    status,
    rawStatus: input.status,
    statusLabel: status === "unknown" ? `未知状态（${input.status}）` : RUN_STATUS_LABELS[status],
    statusHint,
    terminal,
    stages,
    nextStageId,
    nextStageLabel: nextStageId ? nodeLabel(nextStageId) : null,
    completedStageCount: stages.filter((stage) => stage.state === "completed").length,
    failure,
    initialAuthorSource,
    initialAuthorSourceLabel: initialAuthorSourceLabel(initialAuthorSource),
    initialSelectionSource,
    initialSelectionSourceLabel: initialSelectionSourceLabel(initialSelectionSource),
    refineCount: typeof counters?.refine_count === "number" ? counters.refine_count : null,
    quality: {
      bestLoss,
      bestMae,
      targetLoss,
      targetMae,
      targetReached:
        bestLoss !== null && targetLoss !== null ? bestLoss <= targetLoss : null,
    },
    budgets: [
      {
        id: "render",
        label: "渲染 draw",
        used: typeof counters?.render_count === "number" ? counters.render_count : null,
        budget: typeof budgets?.render_budget === "number" ? budgets.render_budget : null,
      },
      {
        id: "llm",
        label: "LLM 调用",
        used: typeof counters?.llm_call_count === "number" ? counters.llm_call_count : null,
        budget: typeof budgets?.llm_budget === "number" ? budgets.llm_budget : null,
      },
      {
        id: "refine",
        label: "模型修订",
        used: typeof counters?.refine_count === "number" ? counters.refine_count : null,
        budget: typeof budgets?.refine_budget === "number" ? budgets.refine_budget : null,
      },
    ],
    renderSeq:
      typeof snapshot?.render_seq === "number" && snapshot.render_seq > 0
        ? snapshot.render_seq
        : null,
    eventCount: events.length,
    unknownEventCount,
    timing,
  };
}
