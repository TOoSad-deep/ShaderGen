import type { GenerationMode, ShaderResponse } from "../api/shader";
import type { ClientCompatibilityReport } from "./ShaderPreview";

const STOP_REASON_LABELS: Record<string, string> = {
  quality_threshold_met: "达到质量阈值",
  stagnation: "连续修订无提升",
  visual_iteration_budget_exhausted: "视觉修订预算用尽",
  model_budget_exhausted: "模型调用预算用尽",
  wall_time_exhausted: "运行时间预算用尽",
  compile_repair_exhausted: "编译修复预算用尽",
  renderer_unavailable: "服务端 Renderer 不可用",
  cancelled: "任务已取消",
  completed_with_best_effort: "以历史最佳结果完成",
};

interface RunProgressProps {
  loading: boolean;
  mode: GenerationMode;
  result: ShaderResponse | null;
  compatibility: ClientCompatibilityReport | null;
}

export function RunProgress({ loading, mode, result, compatibility }: RunProgressProps) {
  if (!loading && !result) return null;

  return (
    <section className="run-progress" aria-label="运行进度摘要">
      <div className="panel-header">
        <h2>运行进度</h2>
        <span>
          {loading
            ? mode === "procedural_v1"
              ? "服务端实验性自动闭环执行中"
              : "服务端单次生成中"
            : "已完成"}
        </span>
      </div>
      {loading && mode === "procedural_v1" ? (
        <ol className="stage-list">
          <li>量化参考图并读取项目 Context</li>
          <li>生成候选并进行 WebGL1 编译</li>
          <li>服务端渲染、评分与 current_best 选择</li>
          <li>按预算自动 Review 和定向修订</li>
        </ol>
      ) : loading ? (
        <p className="hint run-wait-hint">正在执行 Legacy 单次生成；如需离开可点击“停止等待”。</p>
      ) : result ? (
        <dl className="run-facts">
          <div>
            <dt>模式</dt>
            <dd>{result.generation_mode === "procedural_v1" ? "程序化闭环 V1" : "Legacy"}</dd>
          </div>
          <div>
            <dt>质量档位</dt>
            <dd>{result.quality_preset ?? "—"}</dd>
          </div>
          <div>
            <dt>视觉修订</dt>
            <dd>{result.iterations} 轮</dd>
          </div>
          <div>
            <dt>停止原因</dt>
            <dd>
              {result.unscored_fallback
                ? "WebGL 已通过，但评分不可用；以 fallback 完成"
                : result.stop_reason
                  ? STOP_REASON_LABELS[result.stop_reason] ?? result.stop_reason
                  : "单次生成完成"}
            </dd>
          </div>
          {result.best_candidate_id ? (
            <div>
              <dt>{result.unscored_fallback ? "WebGL fallback" : "current_best"}</dt>
              <dd>{result.best_candidate_id}</dd>
            </div>
          ) : null}
          {compatibility ? (
            <div className={`compatibility is-${compatibility.status}`}>
              <dt>客户端复核</dt>
              <dd>{compatibility.message}</dd>
            </div>
          ) : null}
        </dl>
      ) : null}
    </section>
  );
}
