import "../styles/node-lab.css";

import { useEffect, useMemo, useState } from "react";

import {
  createNodeLabRun,
  executeNodeLabStep,
  getNodeLabHealth,
  getNodeLabRun,
  getNodeLabStep,
  listNodeLabArtifacts,
  listNodeLabNodes,
  listNodeLabSteps,
  NodeLabApiError,
  resolveNodeLabArtifactUrl,
  summarizeNodeLabStep,
  uploadNodeLabArtifact,
  type NodeLabArtifact,
  type NodeLabEffectMode,
  type NodeLabExecutionMode,
  type NodeLabHealth,
  type NodeLabNodeDescriptor,
  type NodeLabRun,
  type NodeLabStep,
  type NodeLabStepSummary,
} from "../api/nodeLab";

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function parseObject(text: string, label: string): Record<string, unknown> {
  let value: unknown;
  try {
    value = JSON.parse(text);
  } catch {
    throw new Error(`${label}不是合法 JSON。`);
  }
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label}的根节点必须是 object。`);
  }
  return value as Record<string, unknown>;
}

function errorText(reason: unknown): string {
  if (reason instanceof NodeLabApiError) {
    const location = reason.detail.stage ? ` · ${reason.detail.stage}` : "";
    return `${reason.detail.code}${location}：${reason.message}`;
  }
  return reason instanceof Error ? reason.message : "Node Lab 操作失败。";
}

function defaultMode(node: NodeLabNodeDescriptor): NodeLabExecutionMode {
  if (node.requires_model && node.execution_modes.includes("fixture")) return "fixture";
  return node.execution_modes[0] ?? "deterministic";
}

export function NodeLabPage() {
  const [health, setHealth] = useState<NodeLabHealth | null>(null);
  const [nodes, setNodes] = useState<NodeLabNodeDescriptor[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [search, setSearch] = useState("");
  const [run, setRun] = useState<NodeLabRun | null>(null);
  const [steps, setSteps] = useState<NodeLabStepSummary[]>([]);
  const [stepDetails, setStepDetails] = useState<Record<string, NodeLabStep>>({});
  const [selectedStepId, setSelectedStepId] = useState<string | null>(null);
  const [projectId, setProjectId] = useState("node-lab-local");
  const [resumeRunId, setResumeRunId] = useState("");
  const [initialStateText, setInitialStateText] = useState("{}");
  const [inputsText, setInputsText] = useState("{}");
  const [exampleId, setExampleId] = useState("");
  const [executionMode, setExecutionMode] = useState<NodeLabExecutionMode>("deterministic");
  const [effectMode, setEffectMode] = useState<NodeLabEffectMode>("lab_commit");
  const [previewOnly, setPreviewOnly] = useState(false);
  const [allowModelCall, setAllowModelCall] = useState(false);
  const [fixtureId, setFixtureId] = useState("");
  const [mockArtifactId, setMockArtifactId] = useState("");
  const [baseStepId, setBaseStepId] = useState("");
  const [artifactKind, setArtifactKind] = useState("reference_png");
  const [uploadedArtifacts, setUploadedArtifacts] = useState<NodeLabArtifact[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selectedNode = nodes.find((node) => node.node_id === selectedNodeId) ?? null;
  const selectedStep = selectedStepId ? stepDetails[selectedStepId] ?? null : null;
  const visibleNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return nodes;
    return nodes.filter((node) =>
      `${node.node_id} ${node.category} ${node.summary}`.toLowerCase().includes(query),
    );
  }, [nodes, search]);

  useEffect(() => {
    let cancelled = false;
    async function discover() {
      try {
        const [nextHealth, nextNodes] = await Promise.all([
          getNodeLabHealth(),
          listNodeLabNodes(),
        ]);
        if (cancelled) return;
        setHealth(nextHealth);
        setNodes(nextNodes);
        setSelectedNodeId(nextNodes[0]?.node_id ?? "");
      } catch (reason) {
        if (!cancelled) setError(errorText(reason));
      }
    }
    void discover();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedNode) return;
    const example = selectedNode.input_examples[0];
    const mode = example?.execution_mode ?? defaultMode(selectedNode);
    setExecutionMode(mode);
    setExampleId(example?.example_id ?? "");
    setFixtureId(example?.fixture_id ?? selectedNode.default_fixture_ids[0] ?? "");
    setMockArtifactId("");
    setAllowModelCall(false);
    setPreviewOnly(false);
    setEffectMode(example?.effect_mode ?? "lab_commit");
    setInputsText(pretty(example?.inputs ?? {}));
  }, [selectedNode]);

  useEffect(() => {
    const labRunId = run?.lab_run_id;
    const stepId = selectedStepId;
    if (!labRunId || !stepId || stepDetails[stepId]) return;

    let cancelled = false;
    void getNodeLabStep(labRunId, stepId)
      .then((detail) => {
        if (!cancelled) {
          setStepDetails((current) => ({ ...current, [stepId]: detail }));
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(errorText(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [run?.lab_run_id, selectedStepId, stepDetails]);

  function applyExample(nextExampleId: string) {
    setExampleId(nextExampleId);
    const example = selectedNode?.input_examples.find((item) => item.example_id === nextExampleId);
    if (!example) return;
    setExecutionMode(example.execution_mode);
    setEffectMode(example.effect_mode);
    setFixtureId(example.fixture_id ?? "");
    setInputsText(pretty(example.inputs));
  }

  async function withBusy(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy(false);
    }
  }

  function selectRun(
    nextRun: NodeLabRun,
    nextSteps: NodeLabStepSummary[] = [],
    nextArtifacts: NodeLabArtifact[] = [],
  ) {
    setRun(nextRun);
    setResumeRunId(nextRun.lab_run_id);
    setSteps(nextSteps);
    setStepDetails({});
    setSelectedStepId(nextSteps.at(-1)?.step_id ?? null);
    setBaseStepId(nextSteps.at(-1)?.step_id ?? "");
    setUploadedArtifacts(nextArtifacts);
  }

  async function handleCreateRun() {
    await withBusy(async () => {
      const created = await createNodeLabRun(
        projectId.trim() || null,
        parseObject(initialStateText, "初始 State"),
      );
      selectRun(created);
    });
  }

  async function handleResumeRun() {
    const id = resumeRunId.trim();
    if (!id) {
      setError("请输入 LabRun ID。");
      return;
    }
    await withBusy(async () => {
      const [loadedRun, loadedSteps, loadedArtifacts] = await Promise.all([
        getNodeLabRun(id),
        listNodeLabSteps(id),
        listNodeLabArtifacts(id),
      ]);
      selectRun(loadedRun, loadedSteps, loadedArtifacts);
    });
  }

  async function handleExecute() {
    if (!run || !selectedNode) {
      setError("请先创建或恢复 LabRun。 ");
      return;
    }
    await withBusy(async () => {
      const completed = await executeNodeLabStep(run.lab_run_id, {
        node_id: selectedNode.node_id,
        execution_mode: executionMode,
        effect_mode: effectMode,
        preview_only: previewOnly,
        allow_model_call: executionMode === "real" && allowModelCall,
        base_step_id: baseStepId || null,
        fixture_id: executionMode === "fixture" && fixtureId ? fixtureId : null,
        mock_response_artifact_id:
          executionMode === "mock" && mockArtifactId ? mockArtifactId : null,
        inputs: parseObject(inputsText, "节点输入"),
      });
      setSteps((current) => [...current, summarizeNodeLabStep(completed)]);
      setStepDetails((current) => ({ ...current, [completed.step_id]: completed }));
      setUploadedArtifacts((current) => [...current, ...completed.artifacts]);
      setSelectedStepId(completed.step_id);
      setBaseStepId(completed.step_id);
    });
  }

  async function handleUpload(file: File | undefined) {
    if (!run || !file) {
      setError("请先创建 LabRun，再选择 Artifact 文件。 ");
      return;
    }
    await withBusy(async () => {
      const artifact = await uploadNodeLabArtifact(
        run.lab_run_id,
        file,
        artifactKind.trim() || "lab_input",
      );
      setUploadedArtifacts((current) => [...current, artifact]);
    });
  }

  const allArtifacts = uploadedArtifacts.filter(
    (artifact, index, items) =>
      items.findIndex((candidate) => candidate.artifact_id === artifact.artifact_id) === index,
  );

  return (
    <main className="node-lab-app">
      <header className="node-lab-topbar">
        <div>
          <a href="/" className="node-lab-back">← ShaderGen</a>
          <h1>Node Lab</h1>
          <p>逐节点输入、观察输出、分支步骤并保留可复现证据。</p>
        </div>
        <div className="node-lab-health">
          <span>{health ? `${nodes.length} 个节点可用` : "正在连接"}</span>
          <span className={health?.real_model_enabled ? "is-warning" : ""}>
            Real Model：{health?.real_model_enabled ? "服务端已开启" : "关闭"}
          </span>
        </div>
      </header>

      {error ? <div className="node-lab-error" role="alert">{error}</div> : null}

      <section className="node-lab-runbar" aria-label="LabRun 控制">
        <label>
          <span>project_id（可选）</span>
          <input value={projectId} onChange={(event) => setProjectId(event.target.value)} />
        </label>
        <label className="node-lab-initial-state">
          <span>初始 State JSON</span>
          <input value={initialStateText} onChange={(event) => setInitialStateText(event.target.value)} />
        </label>
        <button type="button" disabled={busy} onClick={() => void handleCreateRun()}>
          新建 LabRun
        </button>
        <label>
          <span>恢复 LabRun ID</span>
          <input value={resumeRunId} onChange={(event) => setResumeRunId(event.target.value)} />
        </label>
        <button type="button" disabled={busy || !resumeRunId.trim()} onClick={() => void handleResumeRun()}>
          恢复
        </button>
      </section>

      <div className="node-lab-run-id">
        <strong>当前 LabRun</strong>
        <code>{run?.lab_run_id ?? "尚未创建"}</code>
      </div>

      <section className="node-lab-grid">
        <aside className="node-lab-catalog">
          <div className="node-lab-section-heading">
            <h2>节点目录</h2>
            <span>{visibleNodes.length}</span>
          </div>
          <input
            aria-label="搜索节点"
            className="node-lab-search"
            value={search}
            placeholder="搜索 node_id / 分类"
            onChange={(event) => setSearch(event.target.value)}
          />
          <div className="node-lab-node-list">
            {visibleNodes.map((node, index) => (
              <button
                type="button"
                key={node.node_id}
                className={node.node_id === selectedNodeId ? "is-selected" : ""}
                onClick={() => setSelectedNodeId(node.node_id)}
              >
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{node.node_id}</strong>
                <small>{node.category}{node.requires_model ? " · model" : ""}</small>
              </button>
            ))}
          </div>
        </aside>

        <section className="node-lab-editor">
          <div className="node-lab-section-heading">
            <div>
              <h2>{selectedNode?.node_id ?? "选择节点"}</h2>
              <p>{selectedNode?.summary}</p>
            </div>
            {selectedNode ? <code>{selectedNode.implementation_status}</code> : null}
          </div>

          {selectedNode ? (
            <>
              <div className="node-lab-config-grid">
                <label>
                  <span>调用示例</span>
                  <select aria-label="调用示例" value={exampleId} onChange={(event) => applyExample(event.target.value)}>
                    {selectedNode.input_examples.map((example) => (
                      <option key={example.example_id} value={example.example_id}>{example.example_id}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>execution_mode</span>
                  <select
                    aria-label="执行模式"
                    value={executionMode}
                    onChange={(event) => setExecutionMode(event.target.value as NodeLabExecutionMode)}
                  >
                    {selectedNode.execution_modes.map((mode) => <option key={mode}>{mode}</option>)}
                  </select>
                </label>
                <label>
                  <span>effect_mode</span>
                  <select
                    aria-label="副作用模式"
                    value={effectMode}
                    onChange={(event) => setEffectMode(event.target.value as NodeLabEffectMode)}
                  >
                    <option value="lab_commit">lab_commit</option>
                    <option value="preview">preview</option>
                  </select>
                </label>
                <label>
                  <span>base_step_id（分支基点）</span>
                  <select aria-label="分支基点" value={baseStepId} onChange={(event) => setBaseStepId(event.target.value)}>
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
                  <input value={fixtureId} onChange={(event) => setFixtureId(event.target.value)} placeholder="版本化 Fixture ID" />
                </label>
              ) : null}
              {executionMode === "mock" ? (
                <label className="node-lab-block-label">
                  <span>mock_response_artifact_id</span>
                  <input value={mockArtifactId} onChange={(event) => setMockArtifactId(event.target.value)} />
                </label>
              ) : null}
              {selectedNode.requires_model ? (
                <div className="node-lab-model-gates">
                  <label><input type="checkbox" checked={previewOnly} onChange={(event) => setPreviewOnly(event.target.checked)} /> 仅预览 Prompt/Schema，不调用模型</label>
                  {executionMode === "real" ? (
                    <label className="is-danger"><input type="checkbox" checked={allowModelCall} onChange={(event) => setAllowModelCall(event.target.checked)} /> 我明确允许本步骤产生真实模型费用</label>
                  ) : null}
                </div>
              ) : null}

              <div className="node-lab-schema-summary">
                <span>必需输入：{selectedNode.prerequisites.join(", ") || "无"}</span>
                <span>副作用：{selectedNode.side_effects.join(", ") || "无"}</span>
                {selectedNode.input_examples.find((example) => example.example_id === exampleId)?.base_step_node_id ? (
                  <span>父节点：{selectedNode.input_examples.find((example) => example.example_id === exampleId)?.base_step_node_id}</span>
                ) : null}
              </div>
              <label className="node-lab-json-editor">
                <span>节点输入 JSON</span>
                <textarea aria-label="节点输入 JSON" value={inputsText} onChange={(event) => setInputsText(event.target.value)} spellCheck={false} />
              </label>
              <details className="node-lab-schema-details">
                <summary>查看 Input / Output Schema</summary>
                <div>
                  <pre>{pretty(selectedNode.input_schema)}</pre>
                  <pre>{pretty(selectedNode.output_schema)}</pre>
                </div>
              </details>
              <button className="node-lab-execute" type="button" disabled={busy || !run} onClick={() => void handleExecute()}>
                {busy ? "执行中…" : "执行节点"}
              </button>
            </>
          ) : null}
        </section>

        <section className="node-lab-output">
          <div className="node-lab-section-heading">
            <h2>输出与差异</h2>
            {selectedStep ? <span className={`node-lab-outcome is-${selectedStep.outcome}`}>{selectedStep.outcome}</span> : null}
          </div>
          {selectedStep ? (
            <>
              <dl className="node-lab-step-facts">
                <div><dt>step_id</dt><dd>{selectedStep.step_id}</dd></div>
                <div><dt>duration</dt><dd>{selectedStep.duration_ms.toFixed(2)} ms</dd></div>
                <div><dt>next_action</dt><dd>{selectedStep.next_action ?? "—"}</dd></div>
              </dl>
              <h3>Output</h3>
              <pre>{pretty(selectedStep.output)}</pre>
              <h3>State Diff</h3>
              <pre>{pretty(selectedStep.state_diff)}</pre>
              <details>
                <summary>Diagnostics / Usage / Provenance</summary>
                <pre>{pretty({ diagnostics: selectedStep.diagnostics, usage: selectedStep.usage, provenance: selectedStep.provenance })}</pre>
              </details>
            </>
          ) : (
            <div className="node-lab-empty">
              {selectedStepId ? "正在读取步骤详情…" : "执行一个节点后在这里查看结果。"}
            </div>
          )}
        </section>
      </section>

      <section className="node-lab-artifacts">
        <div className="node-lab-section-heading">
          <div><h2>Lab Artifacts</h2><p>只在当前 LabRun 内通过不透明 ID 访问。</p></div>
          <div className="node-lab-upload">
            <input aria-label="Artifact 类型" value={artifactKind} onChange={(event) => setArtifactKind(event.target.value)} />
            <label>
              上传 Artifact
              <input type="file" disabled={!run || busy} onChange={(event) => {
                const file = event.target.files?.[0];
                void handleUpload(file);
                event.currentTarget.value = "";
              }} />
            </label>
          </div>
        </div>
        <div className="node-lab-artifact-list">
          {allArtifacts.map((artifact) => (
            <a key={artifact.artifact_id} href={resolveNodeLabArtifactUrl(artifact.lab_run_id, artifact.artifact_id)} target="_blank" rel="noreferrer">
              <strong>{artifact.kind}</strong>
              <code>{artifact.artifact_id}</code>
              <span>{artifact.content_type} · {artifact.size_bytes} bytes</span>
            </a>
          ))}
          {!allArtifacts.length ? <p>尚无 Artifact。</p> : null}
        </div>
      </section>

      <section className="node-lab-dag">
        <div className="node-lab-section-heading"><h2>不可变步骤 DAG</h2><span>{steps.length} steps</span></div>
        <div className="node-lab-dag-track">
          <button type="button" className={!baseStepId ? "is-base" : ""} onClick={() => setBaseStepId("")}>Root</button>
          {steps.map((step) => (
            <button
              type="button"
              key={step.step_id}
              className={`${selectedStepId === step.step_id ? "is-selected" : ""} ${baseStepId === step.step_id ? "is-base" : ""}`}
              onClick={() => setSelectedStepId(step.step_id)}
            >
              <small>← {step.base_step_id?.slice(0, 6) ?? "root"}</small>
              <strong>{step.node_id}</strong>
              <span>{step.outcome} · {step.step_id.slice(0, 8)}</span>
            </button>
          ))}
        </div>
      </section>
    </main>
  );
}
