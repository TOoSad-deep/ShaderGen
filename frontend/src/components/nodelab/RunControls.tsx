import type { NodeLabRun } from "../../api/nodeLab";

interface RunControlsProps {
  projectId: string;
  initialStateText: string;
  resumeRunId: string;
  run: NodeLabRun | null;
  stepCount: number;
  busy: boolean;
  creating: boolean;
  resuming: boolean;
  disabled: boolean;
  onProjectIdChange(value: string): void;
  onInitialStateTextChange(value: string): void;
  onResumeRunIdChange(value: string): void;
  onCreateRun(): void;
  onResumeRun(): void;
}

/** LabRun 创建/恢复控制条，以及当前 Run 身份摘要。 */
export function RunControls({
  projectId,
  initialStateText,
  resumeRunId,
  run,
  stepCount,
  busy,
  creating,
  resuming,
  disabled,
  onProjectIdChange,
  onInitialStateTextChange,
  onResumeRunIdChange,
  onCreateRun,
  onResumeRun,
}: RunControlsProps) {
  return (
    <section className="node-lab-runbar" aria-label="LabRun 控制" aria-busy={busy}>
      <div className="node-lab-runbar-actions">
        <label>
          <span>project_id（可选）</span>
          <input
            value={projectId}
            disabled={disabled}
            onChange={(event) => onProjectIdChange(event.target.value)}
          />
        </label>
        <label className="node-lab-initial-state">
          <span>初始 State JSON</span>
          <input
            value={initialStateText}
            disabled={disabled}
            spellCheck={false}
            onChange={(event) => onInitialStateTextChange(event.target.value)}
          />
        </label>
        <button type="button" disabled={busy || disabled} onClick={onCreateRun}>
          {creating ? "创建中…" : "新建 LabRun"}
        </button>
        <label>
          <span>恢复 LabRun ID</span>
          <input
            value={resumeRunId}
            disabled={disabled}
            spellCheck={false}
            placeholder="lab-…"
            onChange={(event) => onResumeRunIdChange(event.target.value)}
          />
        </label>
        <button
          type="button"
          disabled={busy || disabled || !resumeRunId.trim()}
          onClick={onResumeRun}
        >
          {resuming ? "恢复中…" : "恢复"}
        </button>
      </div>
      <div className="node-lab-run-id" aria-live="polite">
        <strong>当前 LabRun</strong>
        {run ? (
          <>
            <code title={run.lab_run_id}>{run.lab_run_id}</code>
            <span>{run.project_id ?? "无 project_id"}</span>
            <span>{stepCount} 个不可变步骤</span>
          </>
        ) : (
          <span className="node-lab-run-id-empty">尚未创建或恢复，执行节点前请先完成这一步。</span>
        )}
      </div>
    </section>
  );
}
