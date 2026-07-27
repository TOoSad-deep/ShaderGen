import { useEffect, useMemo, useRef, useState } from "react";

import {
  resolveMinRunRenderUrl,
  type MinRunProgressEvent,
  type MinRunProgressSnapshot,
} from "../api/shader";
import {
  buildRunViewModel,
  formatClock,
  formatMetric,
  formatMs,
  formatTraceDetails,
  isTerminalRunStatus,
  nodeLabel,
  type BudgetView,
} from "../runStages";

interface MinRunLivePanelProps {
  runId: string;
  referenceUrl: string | null;
  events: MinRunProgressEvent[];
  snapshot: MinRunProgressSnapshot | null;
  status: string;
  /** 后端登记运行的 ISO 时刻；缺省时计时只能按本地观察口径展示。 */
  startedAt?: string | null;
  /** 进度轮询中断等传输层问题提示；不代表服务端运行状态。 */
  progressNotice?: string | null;
}

function BudgetMeter({ view }: { view: BudgetView }) {
  // used 缺失时必须保持“—”，进度条 indeterminate，不得按 0 渲染。
  const ratio =
    view.used !== null && view.budget && view.budget > 0
      ? Math.min(1, view.used / view.budget)
      : null;
  const usedLabel = view.used ?? "—";
  return (
    <div className="budget-meter">
      <span>{view.label}</span>
      {ratio !== null ? (
        <progress
          value={ratio}
          max={1}
          aria-label={`${view.label}预算用量 ${usedLabel} / ${view.budget ?? "未知"}`}
        />
      ) : (
        <progress aria-label={`${view.label}预算用量未知`} />
      )}
      <strong>
        {usedLabel} / {view.budget ?? "—"}
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
  startedAt = null,
  progressNotice = null,
}: MinRunLivePanelProps) {
  const feedRef = useRef<HTMLOListElement | null>(null);
  const [mountedAt] = useState(() => Date.now() / 1000);
  const [nowSeconds, setNowSeconds] = useState(() => Date.now() / 1000);
  const terminal = isTerminalRunStatus(status);

  useEffect(() => {
    // 终态冻结为历史记录，不再走客户端时钟。
    if (terminal) return;
    const timer = window.setInterval(() => {
      setNowSeconds(Date.now() / 1000);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [terminal]);

  useEffect(() => {
    const feed = feedRef.current;
    if (feed) feed.scrollTop = feed.scrollHeight;
  }, [events.length]);

  const vm = useMemo(
    () =>
      buildRunViewModel({
        events,
        snapshot,
        status,
        startedAt,
        nowSeconds,
        mountedAtSeconds: mountedAt,
      }),
    [events, snapshot, status, startedAt, nowSeconds, mountedAt],
  );

  return (
    <div className="min-live-panel" aria-label="scene_mvp 运行过程">
      <div className="min-live-head">
        <span
          className={`min-live-status is-${vm.status}`}
          role="status"
          aria-live="polite"
        >
          {vm.statusLabel}
        </span>
        <span>run_id: {runId}</span>
        <span>
          {vm.timing.elapsedLabel}{" "}
          {vm.timing.elapsedSeconds === null
            ? "—"
            : formatClock(vm.timing.elapsedSeconds)}
        </span>
        {vm.timing.startedAtLabel ? <span>开始 {vm.timing.startedAtLabel}</span> : null}
      </div>

      {vm.nextStageLabel ? (
        <p className="min-live-hint">预计下一节点：{vm.nextStageLabel}（未确认开始）</p>
      ) : null}
      {vm.statusHint ? <p className="min-live-hint">{vm.statusHint}</p> : null}
      {progressNotice ? (
        <p className="min-live-hint is-warning" role="alert">
          {progressNotice}
        </p>
      ) : null}
      {vm.failure ? (
        <p className="min-live-failure" role="alert">
          失败于「{vm.failure.stageLabel}」
          {vm.failure.summary ? `：${vm.failure.summary}` : ""}
          {vm.failure.stopReasonLabel ? `（${vm.failure.stopReasonLabel}）` : ""}
        </p>
      ) : null}
      {vm.initialAuthorSourceLabel ? (
        <p className="min-live-basis">
          Initial Author 输出来源：{vm.initialAuthorSourceLabel}
          {vm.refineCount ? `；本 run 记录 ${vm.refineCount} 次模型修订` : ""}
          。该字段不代表最终 current_best provenance；候选基于参考图约束生成并经真实
          渲染/评分选择，最终 GLSL 与 Render 来自冻结 current_best 的 typed
          ShaderGraph 编译产物。
        </p>
      ) : null}

      <div className="min-live-grid">
        <section className="min-live-timeline" aria-label="节点时间线">
          <h3>
            节点时间线（{vm.completedStageCount}/{vm.stages.length}）
            {vm.unknownEventCount > 0 ? ` · ${vm.unknownEventCount} 个未知节点事件` : ""}
          </h3>
          <ol>
            {vm.stages.map((stage) => (
              <li key={stage.id} className={`is-${stage.state}`}>
                <span className="node-dot" aria-hidden="true" />
                <span className="node-label">
                  {stage.label}
                  {stage.visits > 1 ? ` ×${stage.visits}` : ""}
                </span>
                <span className="node-meta">
                  {stage.state === "completed" ? (
                    <>
                      {formatMs(stage.lastDurationMs)}
                      {stage.lastElapsedMs !== null ? (
                        <span className="node-cumulative">
                          累计 {formatClock(stage.lastElapsedMs / 1000)}
                        </span>
                      ) : null}
                    </>
                  ) : stage.state === "failed" ? (
                    <em>失败</em>
                  ) : (
                    "待执行"
                  )}
                </span>
                {stage.summary && stage.state !== "pending" ? (
                  <span className="node-summary">
                    {stage.summary}
                    {stage.details ? (
                      <span className="node-summary-details">{stage.details}</span>
                    ) : null}
                  </span>
                ) : null}
                {stage.nextAction ? (
                  <span className="node-route">
                    → {stage.nextActionLabel}
                    {stage.stopReasonLabel ? `（${stage.stopReasonLabel}）` : ""}
                  </span>
                ) : null}
              </li>
            ))}
          </ol>
        </section>

        <section className="min-live-side">
          <div className="min-live-budgets" aria-label="预算用量">
            <h3>预算用量</h3>
            {vm.budgets.map((budget) => (
              <BudgetMeter key={budget.id} view={budget} />
            ))}
          </div>

          <div className="min-live-quality" aria-label="质量进度">
            <h3>质量进度（current_best）</h3>
            <div className="quality-row">
              <span>best loss</span>
              <strong className={vm.quality.targetReached === true ? "is-reached" : ""}>
                {formatMetric(vm.quality.bestLoss)}
              </strong>
              <span>目标 {formatMetric(vm.quality.targetLoss)}</span>
            </div>
            <div className="quality-row">
              <span>best MAE</span>
              <strong>{formatMetric(vm.quality.bestMae)}</strong>
              <span>目标 {formatMetric(vm.quality.targetMae)}</span>
            </div>
            {vm.quality.targetReached === true ? (
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
                <figcaption>
                  current_best 实时帧{vm.renderSeq ? `（刷新 #${vm.renderSeq}）` : ""}
                </figcaption>
                {vm.renderSeq ? (
                  <img
                    key={vm.renderSeq}
                    src={resolveMinRunRenderUrl(runId, vm.renderSeq)}
                    alt="运行中最新渲染帧（current_best 实时帧）"
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
        <h3>事件流（{vm.eventCount}）</h3>
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
                  {typeof event.elapsed_ms === "number" ? (
                    <span>累计 {formatClock(event.elapsed_ms / 1000)}</span>
                  ) : null}
                  {event.next_action ? (
                    <span className="node-route">→ {nodeLabel(event.next_action)}</span>
                  ) : null}
                </div>
                {Array.isArray(event.trace)
                  ? event.trace.map((item, index) => {
                      const details = formatTraceDetails(item);
                      return (
                        <p key={`${event.seq}-${index}`} className="feed-message">
                          {typeof item.message === "string" ? item.message : ""}
                          {details ? <span className="feed-details">{details}</span> : null}
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
