import type { ChangeEvent } from "react";

import {
  resolveNodeLabArtifactUrl,
  type NodeLabArtifact,
} from "../../api/nodeLab";

interface ArtifactPanelProps {
  artifacts: NodeLabArtifact[];
  artifactKind: string;
  disabled: boolean;
  onArtifactKindChange(value: string): void;
  onUpload(file: File | undefined): void;
}

/** LabRun 私有 Artifact 面板：上传输入和按不透明 ID 下载。 */
export function ArtifactPanel({
  artifacts,
  artifactKind,
  disabled,
  onArtifactKindChange,
  onUpload,
}: ArtifactPanelProps) {
  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    onUpload(event.target.files?.[0]);
    event.currentTarget.value = "";
  }

  return (
    <section className="node-lab-artifacts" aria-label="Lab Artifacts">
      <div className="node-lab-section-heading">
        <div>
          <h2>Lab Artifacts</h2>
          <p>只在当前 LabRun 内通过不透明 ID 访问。</p>
        </div>
        <div className="node-lab-upload">
          <input
            aria-label="Artifact 类型"
            value={artifactKind}
            onChange={(event) => onArtifactKindChange(event.target.value)}
          />
          <label className={disabled ? "is-disabled" : ""}>
            上传 Artifact
            <input type="file" disabled={disabled} onChange={handleFileChange} />
          </label>
        </div>
      </div>
      <div className="node-lab-artifact-list">
        {artifacts.map((artifact) => (
          <a
            key={artifact.artifact_id}
            href={resolveNodeLabArtifactUrl(artifact.lab_run_id, artifact.artifact_id)}
            target="_blank"
            rel="noreferrer"
            title={`sha256: ${artifact.sha256}`}
          >
            <strong>{artifact.kind}</strong>
            <code>{artifact.artifact_id}</code>
            <span>{artifact.content_type} · {artifact.size_bytes} bytes</span>
          </a>
        ))}
        {!artifacts.length ? <p className="node-lab-empty-note">尚无 Artifact。</p> : null}
      </div>
    </section>
  );
}
