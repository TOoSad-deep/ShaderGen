"""Node Lab 自有的安全原子文件存储原语."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class AtomicFileStore:
    """把所有读写限制在一个根目录内，并以原子替换提交文件."""

    def __init__(self, root: str | Path) -> None:
        """绑定隔离根目录."""
        path = Path(root)
        path.mkdir(parents=True, exist_ok=True)
        self.root = path.resolve()

    def path_for(self, relative_path: str | Path) -> Path:
        """解析安全相对路径，拒绝绝对路径、父级跳转和根目录本身."""
        path = Path(relative_path)
        if path.is_absolute() or not path.parts or ".." in path.parts:
            raise ValueError("文件路径必须是存储根目录内的相对路径。")
        candidate = (self.root / path).resolve(strict=False)
        if not candidate.is_relative_to(self.root) or candidate == self.root:
            raise ValueError("文件路径越过存储根目录。")
        return candidate

    def write_bytes(
        self,
        relative_path: str | Path,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        """原子写入 bytes；content_type 仅用于兼容统一 Artifact API."""
        del content_type
        if not isinstance(data, bytes):
            raise TypeError("文件内容必须是 bytes。")
        destination = self.path_for(relative_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.parent.resolve().is_relative_to(self.root):
            raise ValueError("文件父目录越过存储根目录。")
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def write_text(self, relative_path: str | Path, text: str) -> None:
        """原子写入 UTF-8 文本."""
        self.write_bytes(relative_path, text.encode("utf-8"))

    def write_json(self, relative_path: str | Path, value: Any) -> None:
        """以稳定键顺序写入 JSON."""
        payload = (
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self.write_bytes(relative_path, payload)

    def read_bytes(self, relative_path: str | Path) -> bytes:
        """读取根目录内文件."""
        return self.path_for(relative_path).read_bytes()


__all__ = ["AtomicFileStore"]
