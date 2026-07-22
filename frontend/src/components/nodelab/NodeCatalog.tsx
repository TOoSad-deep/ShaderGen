import type { NodeLabNodeDescriptor } from "../../api/nodeLab";
import type { NodeLabConnection } from "./LabStatusBar";

interface NodeCatalogProps {
  connection: NodeLabConnection;
  nodes: NodeLabNodeDescriptor[];
  visibleNodes: NodeLabNodeDescriptor[];
  selectedNodeId: string;
  search: string;
  onSearchChange(value: string): void;
  onSelect(nodeId: string): void;
}

const FACTORY_EXAMPLE = `def create_application(settings):
    provider = (
        NodeProviderBuilder("my_pipeline")
        .add_node(my_node, input_model=MyInput, output_model=MyOutput)
        .build()
    )
    return NodeLabApplication.at_root(settings.root, node_provider=provider)`;

/** 左侧节点目录：搜索、筛选和空 Application 的 factory 接入引导。 */
export function NodeCatalog({
  connection,
  nodes,
  visibleNodes,
  selectedNodeId,
  search,
  onSearchChange,
  onSelect,
}: NodeCatalogProps) {
  return (
    <aside className="node-lab-catalog" aria-label="节点目录">
      <div className="node-lab-section-heading">
        <h2>节点目录</h2>
        <span>{visibleNodes.length}</span>
      </div>
      {nodes.length ? (
        <input
          aria-label="搜索节点"
          className="node-lab-search"
          value={search}
          placeholder="搜索 node_id / 分类 / 摘要"
          onChange={(event) => onSearchChange(event.target.value)}
        />
      ) : null}
      {nodes.length ? (
        <div className="node-lab-node-list" aria-label="可用节点">
          {visibleNodes.map((node, index) => (
            <button
              type="button"
              aria-current={node.node_id === selectedNodeId}
              key={node.node_id}
              className={node.node_id === selectedNodeId ? "is-selected" : ""}
              onClick={() => onSelect(node.node_id)}
            >
              <span>{String(index + 1).padStart(2, "0")}</span>
              <strong>{node.node_id}</strong>
              <small>
                {node.category}
                {node.requires_model ? " · model" : ""}
              </small>
            </button>
          ))}
          {!visibleNodes.length ? (
            <p className="node-lab-empty-note">没有匹配“{search}”的节点。</p>
          ) : null}
        </div>
      ) : (
        <div className="node-lab-empty-catalog">
          {connection === "connecting" ? <p>正在读取节点目录…</p> : null}
          {connection === "offline" ? (
            <p>服务不可达，无法读取节点目录。恢复连接后目录会自动出现。</p>
          ) : null}
          {connection === "online" ? (
            <>
              <p>
                当前 Application 未注入任何 Node，这是空安全默认值，不是故障。
                接入自己的 Pipeline：
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
                <li>刷新本页，目录会列出 factory 注入的全部 descriptor。</li>
              </ol>
              <p className="node-lab-empty-note">
                Factory 是受信任的启动配置；HTTP 客户端不能提交 import path。
              </p>
            </>
          ) : null}
        </div>
      )}
    </aside>
  );
}
