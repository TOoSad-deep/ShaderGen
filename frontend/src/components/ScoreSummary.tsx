import type { ShaderScore } from "../api/shader";

interface ScoreSummaryProps {
  score: ShaderScore | null | undefined;
}

function metric(value: number | null): string {
  return value === null ? "—" : value.toFixed(4);
}

export function ScoreSummary({ score }: ScoreSummaryProps) {
  if (!score) return null;

  return (
    <section className="score-panel" aria-label="评分摘要">
      <div className="panel-header">
        <h2>确定性评分</h2>
        <span>{score.metric_version}</span>
      </div>
      <div className="score-grid">
        <div className="score-primary">
          <span>Total loss</span>
          <strong>{metric(score.total_loss)}</strong>
        </div>
        <div>
          <span>Global RMSE</span>
          <strong>{metric(score.global_rmse)}</strong>
        </div>
        <div>
          <span>Edge loss</span>
          <strong>{metric(score.edge_loss)}</strong>
        </div>
        <div>
          <span>Geometry loss</span>
          <strong>{metric(score.geometry_loss)}</strong>
        </div>
        <div>
          <span>Representative pixels</span>
          <strong>{metric(score.representative_pixel_loss)}</strong>
        </div>
      </div>
    </section>
  );
}
