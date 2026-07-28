import type { ShaderEngineRunSummary } from "../api/shader";
import { buildEngineRunView } from "../engineRun";

interface EngineRunSummaryProps {
  engineRun?: ShaderEngineRunSummary | null;
}

function shortIdentity(value: string | null): string {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
}

export function EngineRunSummary({ engineRun }: EngineRunSummaryProps) {
  const view = buildEngineRunView(engineRun);
  if (!view) return null;
  return (
    <section className="engine-run-summary" aria-label="Direct attempt 摘要">
      <div className="panel-header">
        <h3>执行来源</h3>
        <span>{view.engineLabel}</span>
      </div>
      <p className="engine-explanation">{view.executionExplanation}</p>
      <dl className="engine-run-facts">
        <div>
          <dt>执行表示</dt>
          <dd>{view.representationLabel}</dd>
        </div>
        <div>
          <dt>选中 attempt</dt>
          <dd title={view.selectedAttemptId ?? undefined}>
            {shortIdentity(view.selectedAttemptId)}
          </dd>
        </div>
      </dl>
      <div className="engine-attempts">
        <h4>Direct attempts（{view.attempts.length}）</h4>
        <ol>
          {view.attempts.map((attempt, index) => (
            <li
              key={attempt.attemptId ?? index}
              className={attempt.selected ? "is-selected" : ""}
            >
              <strong>Attempt {index + 1}</strong>
              {attempt.selected ? <span className="attempt-selected">最终采用</span> : null}
              <span>{attempt.statusLabel}</span>
              <code title={attempt.attemptId ?? undefined}>
                {shortIdentity(attempt.attemptId)}
              </code>
              {attempt.failureCode ? (
                <span className="attempt-failure">
                  failure_code: {attempt.failureCode}
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
