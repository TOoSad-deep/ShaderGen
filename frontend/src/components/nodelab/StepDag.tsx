import type { NodeLabStepSummary } from "../../api/nodeLab";

interface StepDagProps {
  steps: NodeLabStepSummary[];
  selectedStepId: string | null;
  baseStepId: string;
  onSelectStep(stepId: string): void;
  onResetBase(): void;
}

/** 底部不可变步骤 DAG：选中切换查看，基点决定下一步从哪个快照分支。 */
export function StepDag({ steps, selectedStepId, baseStepId, onSelectStep, onResetBase }: StepDagProps) {
  return (
    <section className="node-lab-dag" aria-label="不可变步骤 DAG">
      <div className="node-lab-section-heading">
        <div>
          <h2>不可变步骤 DAG</h2>
          <p>点击卡片只切换右侧查看的输出；分支基点在中间列的 base_step_id 中选择。</p>
        </div>
        <span>{steps.length} steps</span>
      </div>
      <div className="node-lab-dag-track">
        <button
          type="button"
          className={`node-lab-dag-root ${!baseStepId ? "is-base" : ""}`}
          aria-pressed={!baseStepId}
          onClick={onResetBase}
          title="把分支基点重置为 Root State"
        >
          Root
        </button>
        {steps.map((step) => (
          <button
            type="button"
            key={step.step_id}
            className={[
              selectedStepId === step.step_id ? "is-selected" : "",
              baseStepId === step.step_id ? "is-base" : "",
              `is-outcome-${step.outcome}`,
            ].join(" ").trim()}
            aria-pressed={selectedStepId === step.step_id}
            onClick={() => onSelectStep(step.step_id)}
            title={`${step.step_id} · fingerprint ${step.execution_fingerprint.slice(0, 12)}…`}
          >
            <small>← {step.base_step_id?.slice(0, 6) ?? "root"}</small>
            <strong>{step.node_id}</strong>
            <span>{step.outcome} · {step.step_id.slice(0, 8)}</span>
            <span>{step.duration_ms.toFixed(0)} ms · {step.artifact_count} artifacts</span>
          </button>
        ))}
        {!steps.length ? (
          <p className="node-lab-empty-note">尚无步骤。执行节点后，这里会按 DAG 摘要重建不可变步骤链。</p>
        ) : null}
      </div>
    </section>
  );
}
