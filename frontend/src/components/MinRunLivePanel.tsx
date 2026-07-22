import { useEffect, useMemo, useRef, useState } from "react";

import {
  resolveMinRunRenderUrl,
  type MinRunProgressEvent,
  type MinRunProgressSnapshot,
} from "../api/shader";

interface MinRunLivePanelProps {
  runId: string;
  referenceUrl: string | null;
  events: MinRunProgressEvent[];
  snapshot: MinRunProgressSnapshot | null;
  status: string;
}

interface NodeView {
  id: string;
  label: string;
  state: "pending" | "running" | "completed" | "failed";
  visits: number;
  lastDurationMs: number | null;
  nextAction: string | null;
  stopReason: string | null;
}

// 与 src/agent/app/graphs/png_to_shader_min_graph.py 的 12 个节点一一对应。
const MIN_GRAPH_NODES: Array<{ id: string; label: string }> = [
  { id: "initialize_run", label: "初始化运行" },
  { id: "perceive_target", label: "感知目标图" },
  { id: "author_initial", label: "初始 Scene 生成" },
  { id: "materialize_shader", label: "物化 GLSL" },
  { id: "render_and_evaluate", label: "渲染与评估" },
  { id: "decide_after_render", label: "渲染后决策" },
  { id: "optimize_base", label: "基础参数优化" },
  { id: "decide_after_base", label: "基础优化后决策" },
  { id: "optimize_feature", label: "特性优化" },
  { id: "decide_after_feature", label: "特性优化后决策" },
  { id: "author_refine", label: "模型修订" },
  { id: "finalize", label: "固化产物" },
];

const NODE_LABELS = new Map(MIN_GRAPH_NODES.map((node) => [node.id, node.label]));

const STOP_REASON_LABELS: Record<string, string> = {
  continue: "继续",
  target_loss_reached: "达到目标损失",
  render_budget_exhausted: "渲染预算用尽",
  render_failed: "渲染失败",
  bounded_mvp_complete: "有界流程完成",
};

const TRACE_DETAIL_KEYS = new Set(["phase", "status", "message"]);

function nodeLabel(id: string): string {
  return NODE_LABELS.get(id) ?? id;
}

function formatMs(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)} ms`
    : "—";
}

function formatMetric(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

function formatClock(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = Math.floor(seconds % 60);
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

function buildNodeViews(events: MinRunProgressEvent[]): NodeView[] {
  const views = MIN_GRAPH_NODES.map((node) => ({
    id: node.id,
    label: node.label,
    state: "pending" as NodeView["state"],
    visits: 0,
    lastDurationMs: null as number | null,
    nextAction: null as string | null,
    stopReason: null as string | null,
  }));
  const byId = new Map(views.map((view) => [view.id, view]));
  for (const event of events) {
    const view = byId.get(event.node);
    if (!view) continue;
    view.visits += 1;
    view.state = event.status === "failed" ? "failed" : "completed";
    view.lastDurationMs =
      typeof event.duration_ms === "number" ? event.duration_ms : null;
    if (event.next_action) {
      view.nextAction = event.next_action;
      view.stopReason = event.stop_reason ?? null;
    }
  }
  // 最新事件只说明该节点已完成；当前执行节点是路由指向或顺序上的下一个。
  const last = events[events.length - 1];
  if (last) {
    const nextId = last.next_action ?? nextSequentialNodeId(last.node);
    const current = nextId ? byId.get(nextId) : undefined;
    if (current && last.node !== "finalize") current.state = "running";
  }
  return views;
}

function nextSequentialNodeId(nodeId: string): string | null {
  const index = MIN_GRAPH_NODES.findIndex((node) => node.id === nodeId);
  if (index < 0 || index + 1 >= MIN_GRAPH_NODES.length) return null;
  return MIN_GRAPH_NODES[index + 1].id;
}

function BudgetMeter(props: { label: string; used?: number; budget?: number }) {
  const used = typeof props.used === "number" ? props.used : 0;
  const budget = typeof props.budget === "number" ? props.budget : undefined;
  const ratio = budget && budget > 0 ? Math.min(1, used / budget) : 0;
  return (
    <div className="budget-meter">
      <span>{props.label}</span>
      <progress value={ratio} max={1} />
      <strong>
        {used} / {budget ?? "—"}
      </strong>
    </div>
  );
}

export function MinRunLivePanel({
  runId,
  referenceUrl,
  events,
  snapshot,
  status,
}: MinRunLivePanelProps) {
  const nodeViews = useMemo(() => buildNodeViews(events), [events]);
  const budgets = snapshot?.budgets ?? null;
  const counters = snapshot?.counters ?? null;
  const best = snapshot?.best ?? null;
  const renderSeq =
    typeof snapshot?.render_seq === "number" && snapshot.render_seq > 0
      ? snapshot.render_seq
      : null;
  const feedRef = useRef<HTMLOListElement | null>(null);
  const [mountedAt] = useState(() => Date.now() / 1000);
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setNowSeconds(Date.now() / 1000);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [events.length]);

  const lastElapsed = events.length
    ? (events[events.length - 1].elapsed_ms ?? 0)
    : 0;
  // 运行中用客户端时钟计时；终态冻结在最后一个事件的服务端 elapsed_ms。
  const elapsedSeconds =
    status === "running" || status === "pending"
      ? Math.max(0, nowSeconds - mountedAt)
      : lastElapsed / 1000;

  const targetLoss = budgets?.target_loss;
  const bestLoss = best?.loss;
  const targetReached =
    typeof bestLoss === "number" && typeof targetLoss === "number"
      ? bestLoss <= targetLoss
      : null;

  return (
    <div className="min-live-panel" aria-label="scene_mvp 运行过程">
      <div className="min-live-head">
        <span className={`min-live-status is-${status}`}>
          {status === "running"
            ? "运行中"
            : status === "succeeded"
              ? "已完成"
              : status === "failed"
                ? "失败"
                : "等待服务端登记"}
        </span>
        <span>run_id: {runId}</span>
        <span>已运行 {formatClock(elapsedSeconds)}</span>
      </div>

      <div className="min-live-grid">
        <section className="min-live-timeline" aria-label="节点时间线">
          <h3>节点时间线</h3>
          <ol>
            {nodeViews.map((view) => (
              <li key={view.id} className={`is-${view.state}`}>
                <span className="node-dot" aria-hidden="true" />
                <span className="node-label">
                  {view.label}
                  {view.visits > 1 ? ` ×${view.visits}` : ""}
                </span>
                <span className="node-meta">
                  {view.state === "running" ? (
                    <em>执行中</em>
                  ) : view.state === "completed" ? (
                    formatMs(view.lastDurationMs)
                  ) : view.state === "failed" ? (
                    <em>失败</em>
                  ) : (
                    "待执行"
                  )}
                </span>
                {view.nextAction ? (
                  <span className="node-route">
                    → {nodeLabel(view.nextAction)}
                    {view.stopReason
                      ? `（${STOP_REASON_LABELS[view.stopReason] ?? view.stopReason}）`
                      : ""}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </section>

        <section className="min-live-side">
          <div className="min-live-budgets" aria-label="预算用量">
            <h3>预算用量</h3>
            <BudgetMeter
              label="渲染 draw"
              used={counters?.render_count}
              budget={budgets?.render_budget}
            />
            <BudgetMeter
              label="LLM 调用"
              used={counters?.llm_call_count}
              budget={budgets?.llm_budget}
            />
            <BudgetMeter
              label="模型修订"
              used={counters?.refine_count}
              budget={budgets?.refine_budget}
            />
          </div>

          <div className="min-live-quality" aria-label="质量进度">
            <h3>质量进度</h3>
            <div className="quality-row">
              <span>best loss</span>
              <strong className={targetReached === true ? "is-reached" : ""}>
                {formatMetric(bestLoss)}
              </strong>
              <span>目标 {formatMetric(targetLoss)}</span>
            </div>
            <div className="quality-row">
              <span>best MAE</span>
              <strong>{formatMetric(best?.mae)}</strong>
              <span>目标 {formatMetric(budgets?.target_mae)}</span>
            </div>
            {targetReached === true ? (
              <p className="target-status is-reached">已达到目标损失</p>
            ) : null}
          </div>

          <div className="min-live-renders" aria-label="实时渲染对比">
            <h3>实时渲染</h3>
            <div className="render-pair">
              <figure>
                <figcaption>参考图</figcaption>
                {referenceUrl ? <img src={referenceUrl} alt="参考图" /> : <div className="empty">未上传</div>}
              </figure>
              <figure>
                <figcaption>current_best{renderSeq ? ` #${renderSeq}` : ""}</figcaption>
                {renderSeq ? (
                  <img
                    key={renderSeq}
                    src={resolveMinRunRenderUrl(runId, renderSeq)}
                    alt="运行中最新渲染帧"
                  />
                ) : (
                  <div className="empty">等待首次渲染</div>
                )}
              </figure>
            </div>
          </div>
        </section>
      </div>

      <section className="min-live-feed-section" aria-label="事件流">
        <h3>事件流（{events.length}）</h3>
        <ol className="min-live-feed" ref={feedRef}>
          {events.length === 0 ? (
            <li className="feed-empty">等待第一个节点事件...</li>
          ) : (
            events.map((event) => (
              <li key={event.seq} className={`is-${event.status}`}>
                <div className="feed-head">
                  <strong>
                    #{event.seq} {nodeLabel(event.node)}
                  </strong>
                  <span className={`trace-status is-${event.status}`}>{event.status}</span>
                  <span>{formatMs(event.duration_ms)}</span>
                  {event.next_action ? (
                    <span className="node-route">→ {nodeLabel(event.next_action)}</span>
                  ) : null}
                </div>
                {Array.isArray(event.trace)
                  ? event.trace.map((item, index) => {
                      const details = Object.entries(item).filter(
                        ([key]) => !TRACE_DETAIL_KEYS.has(key),
                      );
                      return (
                        <p key={`${event.seq}-${index}`} className="feed-message">
                          {typeof item.message === "string" ? item.message : ""}
                          {details.length ? (
                            <span className="feed-details">
                              {details
                                .map(([key, value]) => `${key}=${formatDetailValue(value)}`)
                                .join(" · ")}
                            </span>
                          ) : null}
                        </p>
                      );
                    })
                  : null}
              </li>
            ))
          )}
        </ol>
      </section>
    </div>
  );
}
