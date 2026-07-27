import { NODE_LAB_API_BASE_URL } from "../../api/nodeLab";
import type { NodeLabConnection } from "./LabStatusBar";

interface LabOnboardingProps {
  connection: NodeLabConnection;
  onRefresh(): void;
}

const FACTORY_EXAMPLE = `def create_application(settings):
    provider = (
        NodeProviderBuilder("my_pipeline")
        .add_node(my_node, input_model=MyInput, output_model=MyOutput)
        .build()
    )
    return NodeLabApplication.at_root(settings.root, node_provider=provider)`;

/** 节点目录为空时的整宽引导：连接中/离线占位，在线时给出 factory 接入步骤和当前可用能力。 */
export function LabOnboarding({ connection, onRefresh }: LabOnboardingProps) {
  if (connection !== "online") {
    return (
      <section className="node-lab-onboarding is-status" aria-label="节点目录状态">
        <p className="node-lab-onboarding-status">
          {connection === "connecting"
            ? "正在连接 Node Lab 服务并读取节点目录…"
            : "服务不可达，无法读取节点目录。启动服务后点击上方「重试连接」。"}
        </p>
      </section>
    );
  }

  return (
    <section className="node-lab-onboarding" aria-label="空 Application 引导">
      <div className="node-lab-onboarding-main">
        <h2>当前 Application 未注入任何 Node</h2>
        <p>
          这是空安全默认值，不是故障。由受信任的启动配置注入 factory
          后，节点目录、执行配置和输出列会自动出现在这里：
        </p>
        <ol>
          <li>在已安装到服务 Python 环境的模块中实现 Application factory：</li>
        </ol>
        <pre>{FACTORY_EXAMPLE}</pre>
        <ol start={2}>
          <li>
            用 <code>NODELAB_APPLICATION_FACTORY=my_project.node_lab:create_application</code>{" "}
            重新运行 <code>make dev-node-lab</code>。
          </li>
          <li>重新读取节点目录，页面会列出 factory 注入的全部 descriptor。</li>
        </ol>
        <button type="button" className="node-lab-refresh-catalog" onClick={onRefresh}>
          重新读取节点目录
        </button>
        <p className="node-lab-empty-note">
          Factory 是受信任的启动配置；HTTP 客户端不能提交 import path。
        </p>
      </div>
      <aside className="node-lab-onboarding-next" aria-label="现在可以做什么">
        <h3>现在可以做什么</h3>
        <ul>
          <li>
            <strong>新建 / 恢复 LabRun</strong>
            <span>使用上方控制条；空目录下 LabRun 和不可变步骤记录仍然可用。</span>
          </li>
          <li>
            <strong>上传 Artifact</strong>
            <span>创建 LabRun 后，即可在下方面板按不透明 ID 留证。</span>
          </li>
          <li>
            <strong>浏览 HTTP 契约</strong>
            <span>
              Swagger 文档：
              <a href={`${NODE_LAB_API_BASE_URL}/docs`} target="_blank" rel="noreferrer">
                {NODE_LAB_API_BASE_URL}/docs
              </a>
            </span>
          </li>
        </ul>
      </aside>
    </section>
  );
}
