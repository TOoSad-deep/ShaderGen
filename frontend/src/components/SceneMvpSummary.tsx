import type {
  MinPipelineSummary,
  ShaderEngineRunSummary,
} from "../api/shader";
import { EngineRunSummary } from "./EngineRunSummary";

interface SceneMvpSummaryProps {
  runId: string;
  stopReason?: string | null;
  minPipeline?: MinPipelineSummary | null;
  engineRun?: ShaderEngineRunSummary | null;
}

function metric(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(4)
    : "—";
}

export function SceneMvpSummary({
  runId,
  stopReason,
  minPipeline,
  engineRun,
}: SceneMvpSummaryProps) {
  return (
    <section className="scene-mvp-summary" aria-label="Layered Direct 运行摘要">
      <div className="panel-header">
        <h2>Layered Direct 摘要</h2>
        <span>run_id: {runId}</span>
      </div>
      <div className="score-grid">
        <div>
          <span>MAE</span>
          <strong>{metric(minPipeline?.mae)}</strong>
        </div>
        <div>
          <span>Objective loss</span>
          <strong>{metric(minPipeline?.objective_loss)}</strong>
        </div>
        <div>
          <span>Draw / budget</span>
          <strong>
            {minPipeline?.render_count ?? "—"} / {minPipeline?.render_budget ?? "—"}
          </strong>
        </div>
        <div>
          <span>LLM / budget</span>
          <strong>
            {minPipeline?.llm_call_count ?? "—"} / {minPipeline?.llm_budget ?? "—"}
          </strong>
        </div>
      </div>
      <p>
        状态：{stopReason ?? "completed"}；质量目标：
        {minPipeline?.target_reached === true
          ? "已达到"
          : minPipeline?.target_reached === false
            ? "未达到"
            : "未知"}
      </p>
      <EngineRunSummary engineRun={engineRun} />
    </section>
  );
}
