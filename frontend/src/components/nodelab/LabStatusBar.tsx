import { NODE_LAB_API_BASE_URL, type NodeLabHealth } from "../../api/nodeLab";

export type NodeLabConnection = "connecting" | "online" | "offline";

interface LabStatusBarProps {
  connection: NodeLabConnection;
  health: NodeLabHealth | null;
  onRetry(): void;
}

const CONNECTION_LABEL: Record<NodeLabConnection, string> = {
  connecting: "正在连接",
  online: "已连接",
  offline: "连接失败",
};

/** 顶部状态栏：服务连接、Pipeline 身份、节点/capability/suite 数量和真实模型门禁。 */
export function LabStatusBar({ connection, health, onRetry }: LabStatusBarProps) {
  return (
    <header className="node-lab-topbar">
      <div className="node-lab-title">
        <a href="/" className="node-lab-back">← ShaderGen</a>
        <h1>Node Lab</h1>
        <p>任意 Pipeline Node 的逐节点调试工作台：输入、观察、分支、留证。</p>
      </div>
      <div className="node-lab-health" aria-label="服务状态">
        <span className={`node-lab-pill is-${connection}`} role="status">
          <span className="node-lab-dot" aria-hidden="true" />
          {CONNECTION_LABEL[connection]}
        </span>
        {connection === "offline" ? (
          <button type="button" className="node-lab-retry" onClick={onRetry}>
            重试连接
          </button>
        ) : null}
        <code className="node-lab-pill is-neutral" title="Node Lab 独立服务地址">
          {NODE_LAB_API_BASE_URL}
        </code>
        {health ? (
          <>
            <span className="node-lab-pill is-neutral" title="当前 Application 的 pipeline_id">
              pipeline：<code>{health.pipeline_id}</code>
            </span>
            <span className="node-lab-pill is-neutral">{health.node_count} 个节点可用</span>
            <span className="node-lab-pill is-neutral">{health.capability_count} capabilities</span>
            <span className="node-lab-pill is-neutral">{health.suite_count} suites</span>
            <span
              className={`node-lab-pill ${health.real_model_enabled ? "is-warning" : "is-ok"}`}
              title="真实模型门禁由服务端环境开关与 Provider 授权共同决定"
            >
              Real Model：{health.real_model_enabled ? "服务端已开启" : "关闭"}
            </span>
          </>
        ) : null}
      </div>
    </header>
  );
}
