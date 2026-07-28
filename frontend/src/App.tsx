import "./styles/app.css";

import { type DragEvent, useCallback, useEffect, useRef, useState } from "react";

import {
  fetchMinRunProgress,
  generateShader,
  resolveShaderApiUrl,
  ShaderApiError,
  type MinRunProgressEvent,
  type MinRunProgressResponse,
  type MinRunProgressSnapshot,
  type QualityPreset,
  type ShaderApiFailure,
  type ShaderResponse,
} from "./api/shader";
import {
  isAuthoritativeRunFailure,
  isTerminalRunStatus,
  mergeProgressEvents,
  nextPollDelayMs,
  PROGRESS_REQUEST_TIMEOUT_MS,
} from "./runStages";
import { RUNTIME_TIMEOUTS } from "./runtimeTimeouts";
import { FailureDetails } from "./components/FailureDetails";
import { MinRunLivePanel } from "./components/MinRunLivePanel";
import { SceneMvpSummary } from "./components/SceneMvpSummary";
import {
  ShaderPreview,
  type ClientCompatibilityReport,
} from "./components/ShaderPreview";

type RequestStopKind = "user" | "timeout" | "unmount";

interface ActiveGenerationRequest {
  controller: AbortController;
  timeoutId: number;
  stopKind: RequestStopKind | null;
  timeoutMs: number;
}

interface LiveMinRun {
  runId: string;
  events: MinRunProgressEvent[];
  snapshot: MinRunProgressSnapshot | null;
  status: string;
  startedAt: string | null;
}

// 运行观察器：POST 结算后仍按 capped backoff 轮询，直到服务端终态、
// 服务端明确失败、新 run 或页面卸载；停止浏览器等待从来不是服务端取消。
interface RunObserver {
  runId: string;
  stopped: boolean;
  deadlineMs: number;
  request: AbortController | null;
}

const PROGRESS_OBSERVATION_GRACE_MS =
  RUNTIME_TIMEOUTS.progressObservationGraceSeconds * 1000;

const DEFAULT_REQUEST_TIMEOUT_MS: Record<QualityPreset, number> = {
  fast: RUNTIME_TIMEOUTS.generationRequestSeconds.fast * 1000,
  balanced: RUNTIME_TIMEOUTS.generationRequestSeconds.balanced * 1000,
  high: RUNTIME_TIMEOUTS.generationRequestSeconds.high * 1000,
  manual: RUNTIME_TIMEOUTS.generationRequestSeconds.manual * 1000,
};

function newClientRunId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "00000000-0000-4000-8000-000000000000".replace(/0/g, () =>
    Math.floor(Math.random() * 16).toString(16),
  );
}

function generationRequestTimeoutMs(qualityPreset: QualityPreset): number {
  return DEFAULT_REQUEST_TIMEOUT_MS[qualityPreset];
}

function formatBytes(bytes: number) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function App() {
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileInfo, setFileInfo] = useState("");
  const [glsl, setGlsl] = useState("");
  const [qualityPreset, setQualityPreset] = useState<QualityPreset>("balanced");
  const [instruction, setInstruction] = useState("");
  const [runResult, setRunResult] = useState<ShaderResponse | null>(null);
  const [compatibility, setCompatibility] =
    useState<ClientCompatibilityReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [apiFailure, setApiFailure] = useState<ShaderApiFailure | null>(null);
  const [requestStopNotice, setRequestStopNotice] = useState("");
  const [dragActive, setDragActive] = useState(false);
  const [copied, setCopied] = useState(false);
  const [liveRun, setLiveRun] = useState<LiveMinRun | null>(null);
  const [progressNotice, setProgressNotice] = useState("");
  const copyTimerRef = useRef<number | null>(null);
  const activeGenerationRef = useRef<ActiveGenerationRequest | null>(null);
  const runObserverRef = useRef<RunObserver | null>(null);

  useEffect(() => () => {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
  }, [imageUrl]);

  useEffect(() => () => {
    if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
    const active = activeGenerationRef.current;
    if (active) {
      active.stopKind = "unmount";
      window.clearTimeout(active.timeoutId);
      active.controller.abort();
    }
    const observer = runObserverRef.current;
    if (observer) {
      observer.stopped = true;
      observer.request?.abort();
    }
  }, []);

  function clearRunOutput() {
    const observer = runObserverRef.current;
    if (observer) {
      observer.stopped = true;
      observer.request?.abort();
    }
    runObserverRef.current = null;
    setGlsl("");
    setRunResult(null);
    setCompatibility(null);
    setError("");
    setApiFailure(null);
    setRequestStopNotice("");
    setCopied(false);
    setLiveRun(null);
    setProgressNotice("");
  }

  async function handleFile(file: File) {
    if (loading) return;
    if (!file.type.startsWith("image/")) {
      setError("请上传图片文件。");
      return;
    }
    setImageUrl(URL.createObjectURL(file));
    setSelectedFile(file);
    setFileInfo(`${file.name} · ${formatBytes(file.size)}`);
    clearRunOutput();
  }

  async function handleRun() {
    if (!selectedFile || loading || activeGenerationRef.current) return;
    clearRunOutput();
    setLoading(true);
    const controller = new AbortController();
    const timeoutMs = generationRequestTimeoutMs(qualityPreset);
    const active: ActiveGenerationRequest = {
      controller,
      timeoutId: 0,
      stopKind: null,
      timeoutMs,
    };
    active.timeoutId = window.setTimeout(() => {
      if (activeGenerationRef.current !== active) return;
      active.stopKind = "timeout";
      controller.abort();
    }, timeoutMs);
    activeGenerationRef.current = active;

    const runId = newClientRunId();
    // 新 run 取代旧观察器；观察不是取消，旧 run 面板随 clearRunOutput 一起清掉。
    if (runObserverRef.current) {
      runObserverRef.current.stopped = true;
      runObserverRef.current.request?.abort();
    }
    const observer: RunObserver = {
      runId,
      stopped: false,
      deadlineMs: Date.now() + timeoutMs + PROGRESS_OBSERVATION_GRACE_MS,
      request: null,
    };
    runObserverRef.current = observer;
    let pollFailures = 0;
    let pendingReads = 0;
    let lastSeq = 0;

    const mergeProgress = (data: MinRunProgressResponse) => {
      // 旧观察请求可能在上传新图片或启动新 run 后才返回；不得覆盖新面板。
      if (observer.stopped || runObserverRef.current !== observer) return;
      lastSeq = Math.max(lastSeq, data.latest_seq ?? 0);
      setLiveRun((current) => {
        const base = current?.runId === runId ? current.events : [];
        return {
          runId,
          events: mergeProgressEvents(base, data.events),
          snapshot: data.snapshot ?? current?.snapshot ?? null,
          status: data.status,
          startedAt: data.started_at ?? current?.startedAt ?? null,
        };
      });
    };

    // capped backoff 轮询：失败后继续重连，只有终态/新 run/卸载才停止。
    const pollProgress = async () => {
      if (observer.stopped || runObserverRef.current !== observer) return;
      if (Date.now() >= observer.deadlineMs) {
        observer.stopped = true;
        setProgressNotice(
          "已达到本次进度观察上限；这不代表服务端任务已取消，可通过 Run ID 检查后端状态。",
        );
        return;
      }
      const pollController = new AbortController();
      observer.request = pollController;
      const pollTimeoutId = window.setTimeout(
        () => pollController.abort(),
        PROGRESS_REQUEST_TIMEOUT_MS,
      );
      try {
        const data = await fetchMinRunProgress(runId, lastSeq, pollController.signal);
        if (observer.stopped || runObserverRef.current !== observer) return;
        mergeProgress(data);
        pollFailures = 0;
        pendingReads = data.status === "pending" ? pendingReads + 1 : 0;
        setProgressNotice("");
        if (isTerminalRunStatus(data.status)) {
          observer.stopped = true;
          return;
        }
      } catch {
        if (observer.stopped || runObserverRef.current !== observer) return;
        pollFailures += 1;
        pendingReads = 0;
        // 传输层中断不等于运行失败；观察循环会按退避继续重连。
        setProgressNotice(
          "进度轮询中断，正在按退避重连；这不代表服务端运行失败，结果以最终状态为准。",
        );
      } finally {
        window.clearTimeout(pollTimeoutId);
        if (observer.request === pollController) observer.request = null;
      }
      if (!observer.stopped && runObserverRef.current === observer) {
        window.setTimeout(
          () => void pollProgress(),
          nextPollDelayMs(Math.max(pollFailures, pendingReads)),
        );
      }
    };

    setLiveRun({ runId, events: [], snapshot: null, status: "pending", startedAt: null });
    window.setTimeout(() => void pollProgress(), 500);
    let definitiveFailure = false;
    try {
      const result = await generateShader(selectedFile, {
        runId,
        qualityPreset,
        instruction: instruction.trim(),
        signal: controller.signal,
      });
      setRunResult(result);
      setGlsl(result.glsl);
    } catch (reason) {
      if (active.stopKind === "user") {
        setRequestStopNotice(
          "已停止等待本次响应；这里只中止了浏览器请求，服务端任务可能仍在运行，下方进度会继续观察到终态。",
        );
      } else if (active.stopKind === "timeout") {
        setRequestStopNotice(
          `浏览器等待超过 ${Math.round(active.timeoutMs / 60_000)} 分钟，已停止等待响应；这不代表服务端任务已取消，下方进度会继续观察到终态。`,
        );
      } else if (active.stopKind !== "unmount") {
        if (reason instanceof ShaderApiError) {
          setApiFailure(reason.failure);
          // 只有匹配本次 run_id 且带稳定 code/stage 的应用错误才是权威失败；
          // 代理 4xx/5xx 或超时仍可能对应一个正在服务端执行的 run。
          definitiveFailure = isAuthoritativeRunFailure(reason.failure, runId);
          if (!definitiveFailure) {
            setProgressNotice(
              "生成响应失败，但服务端运行状态尚未确认；下方进度会继续观察。",
            );
          } else {
            setProgressNotice("");
            setLiveRun((current) =>
              current?.runId === runId ? { ...current, status: "failed" } : current,
            );
          }
        }
        setError(reason instanceof Error ? reason.message : "生成失败。");
      }
    } finally {
      // 不再额外发起并发“收尾 GET”；单飞观察循环负责读取终态，
      // 避免较旧 snapshot/status 晚返回后覆盖较新结果。
      if (definitiveFailure) {
        observer.stopped = true;
        observer.request?.abort();
      }
      window.clearTimeout(active.timeoutId);
      if (activeGenerationRef.current === active) {
        activeGenerationRef.current = null;
        setLoading(false);
      }
    }
  }

  function handleStopWaiting() {
    const active = activeGenerationRef.current;
    if (!active || active.stopKind) return;
    active.stopKind = "user";
    active.controller.abort();
  }

  const handleCompatibility = useCallback((report: ClientCompatibilityReport) => {
    setCompatibility(report);
  }, []);

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragActive(false);
    const file = event.dataTransfer.files[0];
    if (file) void handleFile(file);
  }

  async function copyGlsl() {
    if (!glsl || !navigator.clipboard) return;
    try {
      await navigator.clipboard.writeText(glsl);
      setCopied(true);
      if (copyTimerRef.current) window.clearTimeout(copyTimerRef.current);
      copyTimerRef.current = window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setError("复制失败，请手动选择代码复制。");
    }
  }

  const serverRenderUrl = runResult?.final_render_url
    ? resolveShaderApiUrl(runResult.final_render_url)
    : null;
  const statusText = loading
    ? "最小管线运行中"
    : requestStopNotice
      ? "已停止等待"
      : error || compatibility?.status === "error"
        ? "需要处理"
        : runResult
          ? "已完成"
          : selectedFile
            ? "准备运行"
            : "等待上传";

  return (
    <main className="app">
      <section className="workspace">
        <header className="topbar">
          <div>
            <h1>ShaderGen</h1>
            <p>上传参考图，由 scene_mvp 最小管线生成无贴图 fragment shader。</p>
          </div>
          <div className="topbar-actions">
            <a href="/lab">Node Lab</a>
            <span className={`status ${loading ? "is-loading" : ""}`}>{statusText}</span>
            {loading ? (
              <button type="button" className="stop-button" onClick={handleStopWaiting}>
                停止等待
              </button>
            ) : (
              <button type="button" className="run-button" disabled={!selectedFile} onClick={handleRun}>
                {glsl ? "重新运行" : "开始运行"}
              </button>
            )}
          </div>
        </header>

        <section className="run-config" aria-label="生成配置">
          <label>
            <span>质量档位</span>
            <select
              aria-label="质量档位"
              value={qualityPreset}
              disabled={loading}
              onChange={(event) => setQualityPreset(event.target.value as QualityPreset)}
            >
              <option value="fast">Fast</option>
              <option value="balanced">Balanced</option>
              <option value="high">High</option>
              <option value="manual">Manual（1000/32/30）</option>
            </select>
            <small className="experimental-note">
              scene_mvp 返回质量指标、预算用量、场景 JSON 与阶段追踪。
            </small>
          </label>
          <label className="instruction-field">
            <span>补充约束</span>
            <textarea
              aria-label="补充约束"
              value={instruction}
              maxLength={2000}
              disabled={loading}
              placeholder="例如：保留纯白背景，重点复刻左上高光。"
              onChange={(event) => setInstruction(event.target.value)}
            />
          </label>
        </section>

        {requestStopNotice ? <p className="request-stop-notice">{requestStopNotice}</p> : null}
        {apiFailure ? <FailureDetails message={error} failure={apiFailure} /> : null}
        {liveRun ? (
          <MinRunLivePanel
            key={liveRun.runId}
            runId={liveRun.runId}
            referenceUrl={imageUrl}
            events={liveRun.events}
            snapshot={liveRun.snapshot}
            status={liveRun.status}
            startedAt={liveRun.startedAt}
            progressNotice={progressNotice || null}
          />
        ) : null}
        {runResult ? (
          <SceneMvpSummary
            runId={runResult.run_id}
            stopReason={runResult.stop_reason}
            minPipeline={runResult.min_pipeline}
            engine={runResult.engine}
            representation={runResult.representation}
            engineRun={runResult.engine_run}
          />
        ) : null}

        <section className={`panels ${serverRenderUrl ? "has-server-render" : ""}`}>
          <div className="panel">
            <div className="panel-header"><h2>原图</h2>{fileInfo ? <span>{fileInfo}</span> : null}</div>
            <label
              className={`dropzone ${dragActive ? "is-dragging" : ""} ${loading ? "is-disabled" : ""}`}
              onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={handleDrop}
            >
              <input
                type="file"
                accept="image/*"
                disabled={loading}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void handleFile(file);
                  event.currentTarget.value = "";
                }}
              />
              {imageUrl ? <img src={imageUrl} alt="上传的原图" /> : <span>拖拽图片到这里<small>或点击上传</small></span>}
            </label>
          </div>

          <div className="panel">
            <div className="panel-header"><h2>客户端 WebGL1</h2>{loading ? <span>正在生成...</span> : null}</div>
            {glsl ? (
              <ShaderPreview
                imageUrl={imageUrl}
                glsl={glsl}
                renderWidth={runResult?.render_width}
                renderHeight={runResult?.render_height}
                serverRenderUrl={serverRenderUrl}
                onCompatibility={handleCompatibility}
              />
            ) : <div className="empty">{loading ? "正在生成" : selectedFile ? "等待运行" : "等待上传"}</div>}
          </div>

          {serverRenderUrl ? (
            <div className="panel server-render-panel">
              <div className="panel-header"><h2>服务端最终 Render</h2><span>scene_mvp</span></div>
              <img src={serverRenderUrl} alt="服务端最终渲染图" />
            </div>
          ) : null}
        </section>

        <section className="code-panel">
          <div className="panel-header"><h2>GLSL</h2><button type="button" disabled={!glsl} onClick={copyGlsl}>{copied ? "已复制" : "复制"}</button></div>
          {loading ? <p className="hint">正在感知、生成、渲染并评估 GLSL...</p> : null}
          {error && !apiFailure ? <p className="error">{error}</p> : null}
          <pre>{glsl || (selectedFile ? "点击“开始运行”后会在这里显示生成结果。" : "上传图片后会在这里显示生成结果。")}</pre>
        </section>
      </section>
    </main>
  );
}
