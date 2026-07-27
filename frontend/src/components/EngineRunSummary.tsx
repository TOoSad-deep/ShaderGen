import type {
  ShaderEngineId,
  ShaderEngineRunSummary,
  ShaderRepresentation,
} from "../api/shader";
import { buildEngineRunView } from "../engineRun";

interface EngineRunSummaryProps {
  engine?: ShaderEngineId | null;
  representation?: ShaderRepresentation | null;
  engineRun?: ShaderEngineRunSummary | null;
}

function shortIdentity(value: string | null): string {
  if (!value) return "—";
  return value.length > 16 ? `${value.slice(0, 12)}…` : value;
}

export function EngineRunSummary({
  engine,
  representation,
  engineRun,
}: EngineRunSummaryProps) {
  const view = buildEngineRunView(engine, representation, engineRun);
  if (!view) return null;

  const fallbackVisible = Boolean(view.fallbackFrom || view.fallbackReason);
  const policyVisible = Boolean(
    view.policyId ||
      view.policySha256 ||
      view.configuredStage ||
      view.effectiveStage ||
      view.bucket !== null ||
      view.promotionAuthorizationSha256,
  );

  return (
    <section className="engine-run-summary" aria-label="Engine 与 attempt 摘要">
      <div className="panel-header">
        <h3>执行来源</h3>
        <span>{view.engineLabel}</span>
      </div>
      <p className="engine-explanation">{view.executionExplanation}</p>
      <dl className="engine-run-facts">
        <div>
          <dt>Engine</dt>
          <dd>{view.engineLabel}</dd>
        </div>
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

      {fallbackVisible ? (
        <div className="engine-fallback" role="status">
          <strong>本次发生显式 fallback</strong>
          <span>
            来源：{view.fallbackFromLabel ?? "未返回"} · 原因：
            {view.fallbackReason ?? "未返回"}
          </span>
        </div>
      ) : null}

      {view.shadowSubmissionStatus ? (
        <div className="shadow-submission">
          <strong>Direct shadow 提交：{view.shadowSubmissionStatusLabel}</strong>
          <span>
            {view.shadowSubmissionReason ?? "未返回原因"}
            {view.shadowAttemptId
              ? ` · attempt ${shortIdentity(view.shadowAttemptId)}`
              : ""}
          </span>
          <small>
            这里只表示异步 shadow 是否入队，不代表其执行成功，也不影响本次产品结果。
          </small>
        </div>
      ) : null}

      {view.attempts.length ? (
        <div className="engine-attempts">
          <h4>Engine attempts（{view.attempts.length}）</h4>
          <ol>
            {view.attempts.map((attempt, index) => (
              <li
                key={attempt.attemptId ?? `${attempt.engine ?? "unknown"}-${index}`}
                className={attempt.selected ? "is-selected" : ""}
              >
                <div>
                  <strong>{attempt.engineLabel}</strong>
                  {attempt.selected ? <span className="attempt-selected">最终采用</span> : null}
                </div>
                <span>
                  {attempt.representationLabel} · {attempt.statusLabel}
                </span>
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
      ) : null}

      {policyVisible ? (
        <details className="scene-mvp-details engine-policy">
          <summary>Engine policy 快照</summary>
          <dl className="engine-run-facts">
            <div>
              <dt>Policy</dt>
              <dd>{view.policyId ?? "—"}</dd>
            </div>
            <div>
              <dt>配置 / 生效阶段</dt>
              <dd>
                {view.configuredStage ?? "—"} / {view.effectiveStage ?? "—"}
              </dd>
            </div>
            <div>
              <dt>稳定桶</dt>
              <dd>{view.bucket ?? "—"}</dd>
            </div>
            <div>
              <dt>Policy hash</dt>
              <dd title={view.policySha256 ?? undefined}>
                {shortIdentity(view.policySha256)}
              </dd>
            </div>
            <div>
              <dt>Promotion auth</dt>
              <dd title={view.promotionAuthorizationSha256 ?? undefined}>
                {shortIdentity(view.promotionAuthorizationSha256)}
              </dd>
            </div>
          </dl>
        </details>
      ) : null}
    </section>
  );
}
