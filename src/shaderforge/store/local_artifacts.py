"""安全、原子的本地运行产物存储."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PUBLIC_FINAL_BUNDLE_FILES = frozenset({"render.png", "metrics.json", "manifest.json"})
_PUBLIC_FINAL_CONTENT_TYPES = {
    "render.png": "image/png",
    "metrics.json": "application/json; charset=utf-8",
    "manifest.json": "application/json; charset=utf-8",
}


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


def _fsync_directory(directory: Path) -> None:
    """把目录项变更刷盘，补齐 file fsync 之后的持久化边界."""
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_identifier(value: str, field_name: str) -> str:
    if not IDENTIFIER_PATTERN.fullmatch(value) or value in {".", ".."}:
        raise ValueError(f"{field_name} 包含非法字符。")
    return value


class RunArtifactStore:
    """限制在单个 project/run 根目录内的产物操作."""

    def __init__(
        self,
        root: Path,
        *,
        restrictive_permissions: bool = False,
    ) -> None:
        """绑定已隔离的运行根目录."""
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()
        self.restrictive_permissions = restrictive_permissions
        if restrictive_permissions:
            os.chmod(self.root, 0o700)

    def _enforce_private_directories(self, directory: Path) -> None:
        """把 run 根至目标父目录的私有目录收紧为 0700."""
        if not self.restrictive_permissions:
            return
        relative = directory.relative_to(self.root)
        current = self.root
        os.chmod(current, 0o700)
        for part in relative.parts:
            current /= part
            os.chmod(current, 0o700)

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
        self._enforce_private_directories(resolved_parent)

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
            if self.restrictive_permissions:
                os.chmod(destination, 0o600)
            _fsync_directory(destination.parent)
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

    def __init__(
        self,
        base_root: str | Path,
        *,
        restrictive_permissions: bool = False,
    ) -> None:
        """绑定本地 Artifact 根目录."""
        root = Path(base_root)
        root.mkdir(parents=True, exist_ok=True)
        self.base_root = root.resolve()
        self.restrictive_permissions = restrictive_permissions
        if restrictive_permissions:
            os.chmod(self.base_root, 0o700)
        self._run_index = RunArtifactStore(
            self.base_root / ".run-index",
            restrictive_permissions=restrictive_permissions,
        )

    def start_run(self, project_id: str, run_id: str) -> RunArtifactStore:
        """创建或恢复指定项目和运行的目录."""
        project = _safe_identifier(project_id, "project_id")
        run = _safe_identifier(run_id, "run_id")
        project_root = self.base_root / project
        run_root = project_root / run
        run_root.mkdir(parents=True, exist_ok=True)
        if self.restrictive_permissions:
            os.chmod(self.base_root, 0o700)
            os.chmod(project_root, 0o700)
            os.chmod(run_root, 0o700)
        return RunArtifactStore(
            run_root,
            restrictive_permissions=self.restrictive_permissions,
        )

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

    def publish_public_final_bundle(
        self,
        project_id: str,
        run_id: str,
        files: Mapping[str, bytes],
    ) -> dict[str, ArtifactRef]:
        """原子、write-once 发布父 run 的三个公开白名单 Artifact.

        三个文件先写入同一 run 根下的 staging 目录，再以目录 rename 一次提交；
        只有提交完成后才登记公开 run index。目标已存在时只允许内容完全一致的
        幂等重试，任何缺失、额外文件或内容漂移均 fail closed。
        """
        project = _safe_identifier(project_id, "project_id")
        run_id_value = _safe_identifier(run_id, "run_id")
        if set(files) != PUBLIC_FINAL_BUNDLE_FILES:
            raise ValueError("公开 final bundle 必须恰好包含三个白名单文件。")
        normalized: dict[str, bytes] = {}
        for name, data in files.items():
            if not isinstance(data, bytes):
                raise TypeError(f"公开 Artifact {name} 必须是 bytes。")
            normalized[name] = data
        run = self.start_run(project, run_id_value)
        try:
            registered = self.resolve_run(run_id_value)
        except FileNotFoundError:
            registered = None
        if registered is not None and registered.root != run.root:
            raise ValueError("run_id 已登记到其他 project_id。")
        final_dir = run.root / "final"
        staging = run.root / f".final.staging-{os.getpid()}-{uuid4().hex[:8]}"
        staging.mkdir(mode=0o700)
        try:
            for name, data in normalized.items():
                path = staging / name
                with path.open("wb") as artifact_file:
                    os.fchmod(artifact_file.fileno(), 0o600)
                    artifact_file.write(data)
                    artifact_file.flush()
                    os.fsync(artifact_file.fileno())
            _fsync_directory(staging)
            if final_dir.is_symlink():
                raise ValueError("公开 final 目录不得是 symlink。")
            published_new = False
            if not final_dir.exists():
                try:
                    os.rename(staging, final_dir)
                    published_new = True
                except OSError:
                    if final_dir.is_symlink() or not final_dir.is_dir():
                        raise
            if published_new:
                _fsync_directory(run.root)
            self._verify_final_directory(final_dir, normalized)
            self.register_run(project, run_id_value)
            self._verify_final_directory(final_dir, normalized)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        return {
            name: ArtifactRef(
                relative_path=f"final/{name}",
                sha256=sha256(data).hexdigest(),
                size_bytes=len(data),
                content_type=_PUBLIC_FINAL_CONTENT_TYPES[name],
            )
            for name, data in normalized.items()
        }

    def verify_public_final_bundle(self, run_id: str) -> dict[str, bytes]:
        """复验并返回已登记父 run 的三个公开白名单文件."""
        run = self.resolve_run(run_id)
        return self._read_final_directory(run.root / "final")

    @staticmethod
    def _verify_final_directory(
        final_dir: Path,
        expected: Mapping[str, bytes],
    ) -> None:
        """拒绝已发布 final 的 symlink、文件集合或内容漂移."""
        actual = LocalArtifactStore._read_final_directory(final_dir)
        for name, data in expected.items():
            if actual[name] != data:
                raise ValueError(f"公开 final Artifact 内容漂移：{name}")

    @staticmethod
    def _read_final_directory(final_dir: Path) -> dict[str, bytes]:
        """用 pinned dir fd 和 O_NOFOLLOW 读取完整公开 bundle."""
        directory_flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            directory_flags |= os.O_NOFOLLOW
        try:
            directory_fd = os.open(final_dir, directory_flags)
        except OSError as exc:
            raise ValueError("公开 final 目录无效。") from exc
        try:
            directory_stat = os.fstat(directory_fd)
            if not stat.S_ISDIR(directory_stat.st_mode):
                raise ValueError("公开 final 目录无效。")
            names = set(os.listdir(directory_fd))
            if names != PUBLIC_FINAL_BUNDLE_FILES:
                raise ValueError("公开 final bundle 文件集合漂移。")
            result: dict[str, bytes] = {}
            file_snapshots: dict[str, tuple[int, int, int, int, int]] = {}
            file_flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                file_flags |= os.O_NOFOLLOW
            for name in PUBLIC_FINAL_BUNDLE_FILES:
                try:
                    file_descriptor = os.open(
                        name,
                        file_flags,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise ValueError(
                        "公开 final bundle 不得包含 symlink 或非普通文件。"
                    ) from exc
                try:
                    file_stat = os.fstat(file_descriptor)
                    if not stat.S_ISREG(file_stat.st_mode):
                        raise ValueError(
                            "公开 final bundle 不得包含 symlink 或非普通文件。"
                        )
                    chunks: list[bytes] = []
                    while chunk := os.read(file_descriptor, 1024 * 1024):
                        chunks.append(chunk)
                    completed_stat = os.fstat(file_descriptor)
                    initial_snapshot = (
                        file_stat.st_dev,
                        file_stat.st_ino,
                        file_stat.st_size,
                        file_stat.st_mtime_ns,
                        file_stat.st_ctime_ns,
                    )
                    completed_snapshot = (
                        completed_stat.st_dev,
                        completed_stat.st_ino,
                        completed_stat.st_size,
                        completed_stat.st_mtime_ns,
                        completed_stat.st_ctime_ns,
                    )
                    if completed_snapshot != initial_snapshot:
                        raise ValueError("公开 final Artifact 在读取期间发生修改。")
                    file_snapshots[name] = completed_snapshot
                    result[name] = b"".join(chunks)
                finally:
                    os.close(file_descriptor)
            for name, snapshot in file_snapshots.items():
                current_file_stat = os.stat(
                    name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
                current_snapshot = (
                    current_file_stat.st_dev,
                    current_file_stat.st_ino,
                    current_file_stat.st_size,
                    current_file_stat.st_mtime_ns,
                    current_file_stat.st_ctime_ns,
                )
                if (
                    not stat.S_ISREG(current_file_stat.st_mode)
                    or current_snapshot != snapshot
                ):
                    raise ValueError("公开 final Artifact 在读取期间发生替换。")
            current_stat = os.stat(final_dir, follow_symlinks=False)
            if (
                current_stat.st_dev,
                current_stat.st_ino,
            ) != (
                directory_stat.st_dev,
                directory_stat.st_ino,
            ):
                raise ValueError("公开 final 目录在读取期间发生替换。")
            return result
        except OSError as exc:
            raise ValueError("公开 final bundle 无法安全读取。") from exc
        finally:
            os.close(directory_fd)
