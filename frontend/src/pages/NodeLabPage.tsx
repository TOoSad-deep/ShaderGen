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
import { resolveRecommendedBaseStepId } from "../utils/nodeLabBaseStep";
import {
  fillArtifactInputs,
  materializeExampleInputs,
  type ArtifactEquivalenceContext,
} from "../utils/nodeLabInputs";
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

  const allArtifacts = useMemo(
    () =>
      uploadedArtifacts.filter(
        (artifact, index, items) =>
          items.findIndex((candidate) => candidate.artifact_id === artifact.artifact_id) === index,
      ),
    [uploadedArtifacts],
  );

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

  /** 上传/执行产生 Artifact 后，仅对当前仍为空的占位字段做增量填充；不覆盖手写非占位值。 */
  useEffect(() => {
    if (!selectedNode) return;
    const example = selectedNode.input_examples.find((item) => item.example_id === exampleId);
    const mapping = example?.artifact_inputs ?? {};
    if (Object.keys(mapping).length === 0) return;
    setInputsText((current) => {
      try {
        const parsed = parseObject(current, "节点输入");
        const context: ArtifactEquivalenceContext = {
          baseStep: baseStepId ? stepDetails[baseStepId] ?? null : null,
        };
        const { inputs: filled } = fillArtifactInputs(parsed, mapping, allArtifacts, context);
        const next = pretty(filled);
        return next === current ? current : next;
      } catch {
        return current;
      }
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allArtifacts, baseStepId, stepDetails]);

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

  /** 统一物化示例输入：普通字段用 example.inputs 默认值，artifact 字段按当前 Run Artifact 候选填充，
   * 并根据示例的 base_step_node_id 自动选择推荐父步骤。
   *
   * 对存在多个 Artifact 的 kind，会先按 sha256 判断是否为内容等价副本；
   * 等价副本优先选择父 Step State 中已引用的 ID，无法证明等价时保留占位符由用户选择。 */
  function applyNodeExample(
    node: NodeLabNodeDescriptor | null,
    nextExampleId: string,
    artifacts: NodeLabArtifact[],
    nextSteps: NodeLabStepSummary[],
    nextStepDetails: Record<string, NodeLabStep> = stepDetails,
  ) {
    if (!node) return;
    const example =
      node.input_examples.find((item) => item.example_id === nextExampleId) ??
      node.input_examples[0];
    if (!example) return;
    const nextBaseStepId = resolveRecommendedBaseStepId(example.base_step_node_id, nextSteps);
    const context: ArtifactEquivalenceContext = {
      baseStep: nextBaseStepId ? nextStepDetails[nextBaseStepId] ?? null : null,
    };
    setExampleId(example.example_id);
    setExecutionMode(example.execution_mode ?? defaultMode(node));
    setEffectMode(example.effect_mode ?? "lab_commit");
    setFixtureId(example.fixture_id ?? node.default_fixture_ids[0] ?? "");
    setMockArtifactId("");
    setAllowModelCall(false);
    setPreviewOnly(false);
    setBaseStepId(nextBaseStepId);
    setInputsText(materializeExampleInputs(example, artifacts, context));
  }

  useEffect(() => {
    if (!selectedNode) return;
    applyNodeExample(
      selectedNode,
      selectedNode.input_examples[0]?.example_id ?? "",
      allArtifacts,
      steps,
      stepDetails,
    );
    // 只在切换 Node 时重置示例输入；allArtifacts 变化由上方增量 effect 处理。
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    applyNodeExample(selectedNode, nextExampleId, allArtifacts, steps, stepDetails);
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

  async function selectRun(
    nextRun: NodeLabRun,
    nextSteps: NodeLabStepSummary[] = [],
    nextArtifacts: NodeLabArtifact[] = [],
  ) {
    setRun(nextRun);
    setResumeRunId(nextRun.lab_run_id);
    setSteps(nextSteps);
    setSelectedStepId(nextSteps.at(-1)?.step_id ?? null);
    setUploadedArtifacts(nextArtifacts);

    // 逐个容忍 Step 详情加载失败：失败时降级为空详情（父 State 优先匹配退化为
    // 等价组内确定性选择），但绝不能中止，否则 base_step_id 会残留上一个 Run 的 Step。
    let loadedDetails: Record<string, NodeLabStep> = {};
    if (nextSteps.length > 0) {
      const results = await Promise.allSettled(
        nextSteps.map((step) => getNodeLabStep(nextRun.lab_run_id, step.step_id)),
      );
      const failures: string[] = [];
      results.forEach((result, index) => {
        if (result.status === "fulfilled") {
          loadedDetails[result.value.step_id] = result.value;
        } else {
          failures.push(nextSteps[index]?.step_id ?? "unknown");
        }
      });
      if (failures.length > 0) {
        setError(`部分 Step 详情加载失败（${failures.length} 个），父 State 匹配已降级。`);
      }
    }
    setStepDetails(loadedDetails);

    // 创建/恢复 LabRun 后，基于新 Run 的 Artifact、Steps 与 Step Details 重新物化当前示例输入和 base_step。
    applyNodeExample(selectedNode, exampleId, nextArtifacts, nextSteps, loadedDetails);
  }

  async function handleCreateRun() {
    await withBusy("create", async () => {
      const created = await createNodeLabRun(
        projectId.trim() || null,
        parseObject(initialStateText, "初始 State"),
      );
      await selectRun(created);
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
      await selectRun(loadedRun, loadedSteps, loadedArtifacts);
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
              artifacts={allArtifacts}
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
