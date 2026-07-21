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
  return (
    <section className="scene-mvp-panel" aria-label="scene_mvp 运行摘要">
      <div className="panel-header">
        <h2>scene_mvp 最小管线</h2>
        <span>run_id: {runId}</span>
      </div>
      <div className="score-grid">
        <div className="score-primary">
          <span>MAE</span>
          <strong>{formatMae(minPipeline?.mae)}</strong>
        </div>
        <div>
          <span>渲染次数</span>
          <strong>{formatCount(minPipeline?.render_count)}</strong>
        </div>
        <div>
          <span>LLM 调用次数</span>
          <strong>{formatCount(minPipeline?.llm_call_count)}</strong>
        </div>
        <div>
          <span>停止原因</span>
          <strong>{stopReason || "—"}</strong>
        </div>
      </div>
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
