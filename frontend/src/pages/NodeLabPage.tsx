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
  NODE_LAB_API_BASE_URL,
  NodeLabApiError,
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
import { ArtifactPanel } from "../components/nodelab/ArtifactPanel";
import { LabOnboarding } from "../components/nodelab/LabOnboarding";
import { LabStatusBar, type NodeLabConnection } from "../components/nodelab/LabStatusBar";
import { NodeCatalog } from "../components/nodelab/NodeCatalog";
import { NodeInspector } from "../components/nodelab/NodeInspector";
import { RunControls } from "../components/nodelab/RunControls";
import { StepDag } from "../components/nodelab/StepDag";
import { StepResult } from "../components/nodelab/StepResult";

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

type NodeLabBusyAction = "create" | "resume" | "execute" | "upload";

export function NodeLabPage() {
  const [connection, setConnection] = useState<NodeLabConnection>("connecting");
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
  const [busyAction, setBusyAction] = useState<NodeLabBusyAction | null>(null);
  const [error, setError] = useState("");
  const [discoveryAttempt, setDiscoveryAttempt] = useState(0);
  const busy = busyAction !== null;

  const selectedNode = nodes.find((node) => node.node_id === selectedNodeId) ?? null;
  const selectedStep = selectedStepId ? stepDetails[selectedStepId] ?? null : null;
  const stepLoading = Boolean(selectedStepId) && !selectedStep;
  const visibleNodes = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return nodes;
    return nodes.filter((node) =>
      `${node.node_id} ${node.category} ${node.summary}`.toLowerCase().includes(query),
    );
  }, [nodes, search]);
  const inputsError = useMemo(() => {
    try {
      parseObject(inputsText, "节点输入");
      return null;
    } catch (reason) {
      return reason instanceof Error ? reason.message : "节点输入不是合法 JSON。";
    }
  }, [inputsText]);

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
        setSelectedNodeId((current) =>
          nextNodes.some((node) => node.node_id === current)
            ? current
            : nextNodes[0]?.node_id ?? "",
        );
        setConnection("online");
      } catch {
        if (!cancelled) setConnection("offline");
      }
    }
    void discover();
    return () => {
      cancelled = true;
    };
  }, [discoveryAttempt]);

  /** 重试连接：回到 connecting 并触发 effect 重新请求 health/nodes。 */
  function handleRetry() {
    setConnection("connecting");
    setDiscoveryAttempt((attempt) => attempt + 1);
  }

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

  async function withBusy(action: NodeLabBusyAction, task: () => Promise<void>) {
    setBusyAction(action);
    setError("");
    try {
      await task();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusyAction(null);
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
    await withBusy("create", async () => {
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
    await withBusy("resume", async () => {
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
    await withBusy("execute", async () => {
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

  function handleFormatInputs() {
    try {
      setInputsText(pretty(parseObject(inputsText, "节点输入")));
    } catch {
      // 非法 JSON 由编辑器状态提示，不覆盖用户文本。
    }
  }

  async function handleUpload(file: File | undefined) {
    if (!run || !file) {
      setError("请先创建 LabRun，再选择 Artifact 文件。 ");
      return;
    }
    await withBusy("upload", async () => {
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
      <LabStatusBar connection={connection} health={health} onRetry={handleRetry} />

      <div className="node-lab-body">
        {connection === "offline" ? (
          <div className="node-lab-error" role="alert">
            <p>
              无法连接 Node Lab 独立服务（<code>{NODE_LAB_API_BASE_URL}</code>）。请先在另一个终端运行
              <code>make dev-node-lab</code>，再点击重试；只运行产品 Backend 时本页面不可用。
            </p>
            <div className="node-lab-error-actions">
              <button type="button" onClick={handleRetry}>
                重试连接
              </button>
            </div>
          </div>
        ) : null}
        {error ? (
          <div className="node-lab-error" role="alert">
            <p>{error}</p>
            <div className="node-lab-error-actions">
              <button type="button" aria-label="关闭错误提示" onClick={() => setError("")}>
                关闭
              </button>
            </div>
          </div>
        ) : null}

        <RunControls
          projectId={projectId}
          initialStateText={initialStateText}
          resumeRunId={resumeRunId}
          run={run}
          stepCount={steps.length}
          busy={busy}
          creating={busyAction === "create"}
          resuming={busyAction === "resume"}
          disabled={connection !== "online"}
          onProjectIdChange={setProjectId}
          onInitialStateTextChange={setInitialStateText}
          onResumeRunIdChange={setResumeRunId}
          onCreateRun={() => void handleCreateRun()}
          onResumeRun={() => void handleResumeRun()}
        />

        {nodes.length ? (
          <section className="node-lab-workbench">
            <NodeCatalog
              visibleNodes={visibleNodes}
              selectedNodeId={selectedNodeId}
              search={search}
              onSearchChange={setSearch}
              onSelect={setSelectedNodeId}
            />
            <NodeInspector
              node={selectedNode}
              hasRun={Boolean(run)}
              busy={busy}
              executing={busyAction === "execute"}
              steps={steps}
              exampleId={exampleId}
              executionMode={executionMode}
              effectMode={effectMode}
              previewOnly={previewOnly}
              allowModelCall={allowModelCall}
              fixtureId={fixtureId}
              mockArtifactId={mockArtifactId}
              baseStepId={baseStepId}
              inputsText={inputsText}
              inputsError={inputsError}
              onApplyExample={applyExample}
              onExecutionModeChange={setExecutionMode}
              onEffectModeChange={setEffectMode}
              onPreviewOnlyChange={setPreviewOnly}
              onAllowModelCallChange={setAllowModelCall}
              onFixtureIdChange={setFixtureId}
              onMockArtifactIdChange={setMockArtifactId}
              onBaseStepIdChange={setBaseStepId}
              onInputsTextChange={setInputsText}
              onFormatInputs={handleFormatInputs}
              onExecute={() => void handleExecute()}
            />
            <StepResult step={selectedStep} loading={stepLoading} />
          </section>
        ) : (
          <LabOnboarding connection={connection} onRefresh={handleRetry} />
        )}

        <ArtifactPanel
          artifacts={allArtifacts}
          artifactKind={artifactKind}
          hasRun={Boolean(run)}
          disabled={!run || busy}
          uploading={busyAction === "upload"}
          onArtifactKindChange={setArtifactKind}
          onUpload={(file) => void handleUpload(file)}
        />

        <StepDag
          steps={steps}
          selectedStepId={selectedStepId}
          baseStepId={baseStepId}
          onSelectStep={setSelectedStepId}
          onResetBase={() => setBaseStepId("")}
        />
      </div>
    </main>
  );
}
