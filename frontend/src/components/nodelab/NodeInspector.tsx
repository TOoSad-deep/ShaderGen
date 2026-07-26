import type {
  NodeLabEffectMode,
  NodeLabExecutionMode,
  NodeLabNodeDescriptor,
  NodeLabStepSummary,
} from "../../api/nodeLab";

interface NodeInspectorProps {
  node: NodeLabNodeDescriptor | null;
  hasRun: boolean;
  busy: boolean;
  steps: NodeLabStepSummary[];
  exampleId: string;
  executionMode: NodeLabExecutionMode;
  effectMode: NodeLabEffectMode;
  previewOnly: boolean;
  allowModelCall: boolean;
  fixtureId: string;
  mockArtifactId: string;
  baseStepId: string;
  inputsText: string;
  inputsError: string | null;
  onApplyExample(exampleId: string): void;
  onExecutionModeChange(mode: NodeLabExecutionMode): void;
  onEffectModeChange(mode: NodeLabEffectMode): void;
  onPreviewOnlyChange(value: boolean): void;
  onAllowModelCallChange(value: boolean): void;
  onFixtureIdChange(value: string): void;
  onMockArtifactIdChange(value: string): void;
  onBaseStepIdChange(value: string): void;
  onInputsTextChange(value: string): void;
  onFormatInputs(): void;
  onExecute(): void;
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

/** 中间列：选中节点的执行配置、输入 JSON 编辑器和执行入口。 */
export function NodeInspector({
  node,
  hasRun,
  busy,
  steps,
  exampleId,
  executionMode,
  effectMode,
  previewOnly,
  allowModelCall,
  fixtureId,
  mockArtifactId,
  baseStepId,
  inputsText,
  inputsError,
  onApplyExample,
  onExecutionModeChange,
  onEffectModeChange,
  onPreviewOnlyChange,
  onAllowModelCallChange,
  onFixtureIdChange,
  onMockArtifactIdChange,
  onBaseStepIdChange,
  onInputsTextChange,
  onFormatInputs,
  onExecute,
}: NodeInspectorProps) {
  const activeExample = node?.input_examples.find((example) => example.example_id === exampleId);

  return (
    <section className="node-lab-editor" aria-label="节点执行配置">
      {node ? (
        <>
          <div className="node-lab-section-heading">
            <div>
              <h2><code>{node.node_id}</code></h2>
              <p>{node.summary}</p>
            </div>
            <div className="node-lab-badge-row">
              <span className={`node-lab-pill is-status-${node.implementation_status}`}>
                {node.implementation_status}
              </span>
              {node.requires_model ? <span className="node-lab-pill is-warning">model</span> : null}
            </div>
          </div>

          <div className="node-lab-editor-scroll">
            <div className="node-lab-config-grid">
              <label>
                <span>调用示例</span>
                <select
                  aria-label="调用示例"
                  value={exampleId}
                  onChange={(event) => onApplyExample(event.target.value)}
                >
                  {node.input_examples.map((example) => (
                    <option key={example.example_id} value={example.example_id}>
                      {example.example_id}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>execution_mode</span>
                <select
                  aria-label="执行模式"
                  value={executionMode}
                  onChange={(event) => onExecutionModeChange(event.target.value as NodeLabExecutionMode)}
                >
                  {node.execution_modes.map((mode) => <option key={mode}>{mode}</option>)}
                </select>
              </label>
              <label>
                <span>effect_mode</span>
                <select
                  aria-label="副作用模式"
                  value={effectMode}
                  onChange={(event) => onEffectModeChange(event.target.value as NodeLabEffectMode)}
                >
                  <option value="lab_commit">lab_commit</option>
                  <option value="preview">preview</option>
                </select>
              </label>
              <label>
                <span>base_step_id（分支基点）</span>
                <select
                  aria-label="分支基点"
                  value={baseStepId}
                  onChange={(event) => onBaseStepIdChange(event.target.value)}
                >
                  <option value="">Root State</option>
                  {steps.map((step) => (
                    <option key={step.step_id} value={step.step_id}>
                      {step.step_id.slice(0, 8)} · {step.node_id}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {executionMode === "fixture" ? (
              <label className="node-lab-block-label">
                <span>fixture_id</span>
                <input
                  value={fixtureId}
                  placeholder="版本化 Fixture ID"
                  onChange={(event) => onFixtureIdChange(event.target.value)}
                />
              </label>
            ) : null}
            {executionMode === "mock" ? (
              <label className="node-lab-block-label">
                <span>mock_response_artifact_id</span>
                <input
                  value={mockArtifactId}
                  placeholder="同 LabRun 内的 mock 响应 Artifact ID"
                  onChange={(event) => onMockArtifactIdChange(event.target.value)}
                />
              </label>
            ) : null}
            {node.requires_model ? (
              <fieldset className="node-lab-model-gates">
                <legend>模型门禁</legend>
                <label>
                  <input
                    type="checkbox"
                    checked={previewOnly}
                    onChange={(event) => onPreviewOnlyChange(event.target.checked)}
                  />
                  仅预览 Prompt/Schema，不调用模型
                </label>
                {executionMode === "real" ? (
                  <label className="is-danger">
                    <input
                      type="checkbox"
                      checked={allowModelCall}
                      onChange={(event) => onAllowModelCallChange(event.target.checked)}
                    />
                    我明确允许本步骤产生真实模型费用
                  </label>
                ) : null}
              </fieldset>
            ) : null}

            <div className="node-lab-schema-summary">
              <span>必需输入：{node.prerequisites.join(", ") || "无"}</span>
              <span>副作用：{node.side_effects.join(", ") || "无"}</span>
              {activeExample?.base_step_node_id ? <span>父节点：{activeExample.base_step_node_id}</span> : null}
            </div>

            <div className="node-lab-json-editor">
              <div className="node-lab-json-toolbar">
                <label htmlFor="node-lab-inputs">节点输入 JSON</label>
                <div className="node-lab-json-actions">
                  <span
                    className={`node-lab-json-status ${inputsError ? "is-invalid" : "is-valid"}`}
                    role="status"
                  >
                    {inputsError ?? "JSON 有效"}
                  </span>
                  <button type="button" disabled={Boolean(inputsError)} onClick={onFormatInputs}>
                    格式化
                  </button>
                </div>
              </div>
              <textarea
                id="node-lab-inputs"
                aria-label="节点输入 JSON"
                aria-invalid={Boolean(inputsError)}
                value={inputsText}
                spellCheck={false}
                onChange={(event) => onInputsTextChange(event.target.value)}
              />
            </div>

            <details className="node-lab-schema-details">
              <summary>查看 Input / Output Schema</summary>
              <div>
                <pre>{pretty(node.input_schema)}</pre>
                <pre>{pretty(node.output_schema)}</pre>
              </div>
            </details>
          </div>

          <div className="node-lab-execute-row">
            {!hasRun ? <p className="node-lab-hint">先在上方创建或恢复 LabRun，再执行节点。</p> : null}
            <button
              className="node-lab-execute"
              type="button"
              disabled={busy || !hasRun || Boolean(inputsError)}
              onClick={onExecute}
            >
              {busy ? "执行中…" : "执行节点"}
            </button>
          </div>
        </>
      ) : (
        <div className="node-lab-empty">
          <p>从左侧目录选择一个节点，查看 descriptor、Schema 和调用示例。</p>
        </div>
      )}
    </section>
  );
}
