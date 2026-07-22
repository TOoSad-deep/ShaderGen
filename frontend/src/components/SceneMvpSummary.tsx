import type { MinPipelineSummary } from "../api/shader";

interface SceneMvpSummaryProps {
  runId: string;
  stopReason?: string | null;
  minPipeline?: MinPipelineSummary | null;
}

function formatCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
}

function formatMae(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(4) : "—";
}

function formatMs(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)} ms`
    : "—";
}

function formatSceneJson(scene: unknown): string {
  if (scene === undefined || scene === null) return "（本次响应未返回 scene）";
  if (typeof scene === "string") return scene;
  try {
    return JSON.stringify(scene, null, 2);
  } catch {
    return String(scene);
  }
}

export function SceneMvpSummary({ runId, stopReason, minPipeline }: SceneMvpSummaryProps) {
  const trace = Array.isArray(minPipeline?.trace) ? minPipeline.trace : [];
  // 质量达标只来自后端 target_reached；缺省（旧响应）时不展示结论，避免误报。
  const targetReached =
    typeof minPipeline?.target_reached === "boolean" ? minPipeline.target_reached : null;
  const rendererPath =
    typeof minPipeline?.renderer_path === "string" && minPipeline.renderer_path.trim()
      ? minPipeline.renderer_path
      : null;
  return (
    <section className="scene-mvp-panel" aria-label="scene_mvp 运行摘要">
      <div className="panel-header">
        <h2>scene_mvp 最小管线</h2>
        <span>run_id: {runId}</span>
      </div>
      {targetReached !== null ? (
        <p className={`target-status ${targetReached ? "is-reached" : "is-missed"}`}>
          {targetReached ? "质量达标" : "流程完成，质量未达标"}
        </p>
      ) : null}
      <div className="score-grid">
        <div className="score-primary">
          <span>综合损失</span>
          <strong>{formatMae(minPipeline?.objective_loss)}</strong>
        </div>
        <div>
          <span>目标损失</span>
          <strong>{formatMae(minPipeline?.target_loss)}</strong>
        </div>
        <div>
          <span>整图 MAE</span>
          <strong>{formatMae(minPipeline?.mae)}</strong>
        </div>
        <div>
          <span>前景 MAE</span>
          <strong>{formatMae(minPipeline?.metric_breakdown?.foreground_mae)}</strong>
        </div>
        <div>
          <span>高光 / 阴影 MAE</span>
          <strong>
            {formatMae(minPipeline?.metric_breakdown?.highlight_mae)} /{" "}
            {formatMae(minPipeline?.metric_breakdown?.shadow_mae)}
          </strong>
        </div>
        <div>
          <span>渲染次数</span>
          <strong>
            {formatCount(minPipeline?.render_count)} /{" "}
            {formatCount(minPipeline?.render_budget)}
          </strong>
        </div>
        <div>
          <span>LLM 调用次数</span>
          <strong>
            {formatCount(minPipeline?.llm_call_count)} /{" "}
            {formatCount(minPipeline?.llm_budget)}
          </strong>
        </div>
        <div>
          <span>停止原因</span>
          <strong>{stopReason || "—"}</strong>
        </div>
      </div>
      <div className="score-grid">
        <div>
          <span>prepare 耗时</span>
          <strong>{formatMs(minPipeline?.prepare_duration_ms)}</strong>
        </div>
        <div>
          <span>uniform 热渲染次数</span>
          <strong>{formatCount(minPipeline?.uniform_render_count)}</strong>
        </div>
        <div>
          <span>uniform 热渲染 P95</span>
          <strong>{formatMs(minPipeline?.uniform_render_p95_ms)}</strong>
        </div>
      </div>
      {rendererPath ? (
        <p className="renderer-path" title={rendererPath}>
          prepared 渲染路径：{rendererPath}
          {minPipeline?.template_version
            ? ` · 模板：${minPipeline.template_version}`
            : ""}
        </p>
      ) : null}
      {trace.length ? (
        <details className="scene-mvp-details">
          <summary>阶段追踪（{trace.length}）</summary>
          <ul className="scene-mvp-trace">
            {trace.map((phase, index) => (
              <li key={`${index}-${phase.phase}`}>
                <strong>{phase.phase}</strong>
                <span className={`trace-status is-${phase.status}`}>{phase.status}</span>
                {typeof phase.duration_ms === "number" ? (
                  <span>{Math.round(phase.duration_ms)} ms</span>
                ) : null}
                {phase.message ? <span>{phase.message}</span> : null}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      <details className="scene-mvp-details">
        <summary>场景 JSON</summary>
        <pre>{formatSceneJson(minPipeline?.scene)}</pre>
      </details>
    </section>
  );
}
