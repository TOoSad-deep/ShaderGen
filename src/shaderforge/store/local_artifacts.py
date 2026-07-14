"""安全、原子的本地运行产物存储."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ArtifactRef:
    """一次运行内的内容寻址产物引用."""

    relative_path: str
    sha256: str
    size_bytes: int
    content_type: str


def _json_default(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"无法把 {type(value).__name__} 序列化为 JSON。")


def _safe_identifier(value: str, field_name: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field_name} 包含非法字符。")
    return value


class RunArtifactStore:
    """限制在单个 project/run 根目录内的产物操作."""

    def __init__(self, root: Path) -> None:
        """绑定已隔离的运行根目录."""
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def _resolve(self, relative_path: str | Path) -> Path:
        path = Path(relative_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("Artifact 路径必须是 run 根目录内的相对路径。")
        candidate = (self.root / path).resolve(strict=False)
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise ValueError("Artifact 路径越过 run 根目录。")
        return candidate

    def write_bytes(
        self,
        relative_path: str | Path,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> ArtifactRef:
        """以临时文件和原子替换写入 bytes."""
        if not isinstance(data, bytes):
            raise TypeError("Artifact data 必须是 bytes。")
        destination = self._resolve(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        resolved_parent = destination.parent.resolve()
        if not resolved_parent.is_relative_to(self.root):
            raise ValueError("Artifact 父目录越过 run 根目录。")

        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            os.replace(temporary_path, destination)
        finally:
            temporary_path.unlink(missing_ok=True)

        return ArtifactRef(
            relative_path=destination.relative_to(self.root).as_posix(),
            sha256=sha256(data).hexdigest(),
            size_bytes=len(data),
            content_type=content_type,
        )

    def write_text(
        self,
        relative_path: str | Path,
        text: str,
        *,
        content_type: str = "text/plain; charset=utf-8",
    ) -> ArtifactRef:
        """以 UTF-8 写入文本产物."""
        return self.write_bytes(
            relative_path,
            text.encode("utf-8"),
            content_type=content_type,
        )

    def write_json(self, relative_path: str | Path, value: Any) -> ArtifactRef:
        """以稳定键顺序写入 UTF-8 JSON."""
        text = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=_json_default,
        )
        return self.write_text(
            relative_path,
            text + "\n",
            content_type="application/json; charset=utf-8",
        )

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """读取 run 根目录内的产物 bytes."""
        return self._resolve(relative_path).read_bytes()

    def path_for(self, relative_path: str | Path) -> Path:
        """返回经过边界校验的本地路径，供内部工具使用."""
        return self._resolve(relative_path)


class LocalArtifactStore:
    """按 project_id/run_id 创建隔离运行目录."""

    def __init__(self, base_root: str | Path) -> None:
        """绑定本地 Artifact 根目录."""
        root = Path(base_root)
        root.mkdir(parents=True, exist_ok=True)
        self.base_root = root.resolve()
        self._run_index = RunArtifactStore(self.base_root / ".run-index")

    def start_run(self, project_id: str, run_id: str) -> RunArtifactStore:
        """创建或恢复指定项目和运行的目录."""
        project = _safe_identifier(project_id, "project_id")
        run = _safe_identifier(run_id, "run_id")
        return RunArtifactStore(self.base_root / project / run)

    def register_run(self, project_id: str, run_id: str) -> RunArtifactStore:
        """登记 run_id 到 project_id 的持久索引并返回隔离运行目录."""
        project = _safe_identifier(project_id, "project_id")
        run = _safe_identifier(run_id, "run_id")
        index_path = f"{run}.json"
        try:
            existing = json.loads(self._run_index.read_bytes(index_path))
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if not isinstance(existing, dict) or existing.get("project_id") != project:
                raise ValueError("run_id 已登记到其他 project_id。")
        else:
            self._run_index.write_json(
                index_path,
                {
                    "schema_version": 1,
                    "project_id": project,
                    "run_id": run,
                },
            )
        return self.start_run(project, run)

    def resolve_run(self, run_id: str) -> RunArtifactStore:
        """仅通过已登记 run_id 恢复运行目录，不接受客户端文件路径."""
        run = _safe_identifier(run_id, "run_id")
        try:
            value = json.loads(self._run_index.read_bytes(f"{run}.json"))
        except FileNotFoundError as exc:
            raise FileNotFoundError("未找到对应运行 Artifact。") from exc
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != 1
            or value.get("run_id") != run
            or not isinstance(value.get("project_id"), str)
        ):
            raise ValueError("运行 Artifact 索引损坏。")
        return self.start_run(value["project_id"], run)
