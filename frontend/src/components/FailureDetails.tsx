import type { ShaderApiFailure } from "../api/shader";

interface FailureDetailsProps {
  message: string;
  failure: ShaderApiFailure;
}

function provided(value: string | undefined): string {
  return value ?? "—（旧版响应未提供）";
}

export function FailureDetails({ message, failure }: FailureDetailsProps) {
  const retryability =
    failure.retryable === true
      ? "可重试"
      : failure.retryable === false
        ? "不建议直接重试"
        : "未知（旧版响应未提供）";

  return (
    <section className="failure-panel" aria-label="生成失败诊断">
      <div className="panel-header">
        <h2>生成失败诊断</h2>
        <span>HTTP {failure.status}</span>
      </div>
      <p className="failure-message">{message}</p>
      <dl className="failure-facts">
        <div>
          <dt>错误代码</dt>
          <dd>{provided(failure.code)}</dd>
        </div>
        <div>
          <dt>Run ID</dt>
          <dd>{provided(failure.runId)}</dd>
        </div>
        <div>
          <dt>失败阶段</dt>
          <dd>{provided(failure.stage)}</dd>
        </div>
        <div>
          <dt>停止原因</dt>
          <dd>{provided(failure.stopReason)}</dd>
        </div>
        <div>
          <dt>是否可重试</dt>
          <dd>{retryability}</dd>
        </div>
      </dl>
      {failure.retryable === true ? (
        <p className="failure-guidance">服务端标记为可重试；建议先用 Run ID 检查后端日志，再重新运行。</p>
      ) : null}
    </section>
  );
}
