import {
  resolveNodeLabArtifactUrl,
  type NodeLabStateDiff,
  type NodeLabStep,
} from "../../api/nodeLab";

interface StepResultProps {
  step: NodeLabStep | null;
  loading: boolean;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function diffCount(diff: NodeLabStateDiff): string {
  const added = Object.keys(diff.added).length;
  const changed = Object.keys(diff.changed).length;
  const removed = diff.removed.length;
  return `+${added} ~${changed} -${removed}`;
}

/** 右侧列：选中步骤的安全 Output、State Diff、diagnostics/usage/provenance 和证据 Artifact。 */
export function StepResult({ step, loading }: StepResultProps) {
  return (
    <section className="node-lab-output" aria-label="步骤输出">
      <div className="node-lab-section-heading">
        <h2>输出与差异</h2>
        {step ? (
          <span className={`node-lab-outcome is-${step.outcome}`}>{step.outcome}</span>
        ) : null}
      </div>
      {step ? (
        <div className="node-lab-output-scroll">
          <dl className="node-lab-step-facts">
            <div><dt>step_id</dt><dd>{step.step_id}</dd></div>
            <div><dt>node_id</dt><dd>{step.node_id}</dd></div>
            <div><dt>execution_mode</dt><dd>{step.execution_mode}</dd></div>
            <div><dt>duration</dt><dd>{step.duration_ms.toFixed(2)} ms</dd></div>
            <div><dt>next_action</dt><dd>{step.next_action ?? "—"}</dd></div>
            <div><dt>fingerprint</dt><dd>{step.execution_fingerprint.slice(0, 12)}…</dd></div>
          </dl>

          <h3>Output</h3>
          <pre>{pretty(step.output)}</pre>

          <h3>
            State Diff
            <span className="node-lab-diff-count">{diffCount(step.state_diff)}</span>
          </h3>
          <div className="node-lab-diff-grid">
            <div className="is-added">
              <h4>added</h4>
              <pre>{pretty(step.state_diff.added)}</pre>
            </div>
            <div className="is-changed">
              <h4>changed</h4>
              <pre>{pretty(step.state_diff.changed)}</pre>
            </div>
            <div className="is-removed">
              <h4>removed</h4>
              <pre>{pretty(step.state_diff.removed)}</pre>
            </div>
          </div>

          {step.artifacts.length ? (
            <>
              <h3>本步骤 Artifact</h3>
              <ul className="node-lab-step-artifacts">
                {step.artifacts.map((artifact) => (
                  <li key={artifact.artifact_id}>
                    <a
                      href={resolveNodeLabArtifactUrl(artifact.lab_run_id, artifact.artifact_id)}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {artifact.kind}
                    </a>
                    <code>{artifact.artifact_id}</code>
                  </li>
                ))}
              </ul>
            </>
          ) : null}

          <details>
            <summary>Diagnostics / Usage / Provenance</summary>
            <pre>
              {pretty({
                diagnostics: step.diagnostics,
                usage: step.usage,
                provenance: step.provenance,
              })}
            </pre>
          </details>
        </div>
      ) : (
        <div className="node-lab-empty">
          <p>{loading ? "正在读取步骤详情…" : "执行一个节点，或在下方 DAG 中选中历史步骤查看结果。"}</p>
        </div>
      )}
    </section>
  );
}
