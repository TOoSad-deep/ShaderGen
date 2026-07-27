"""从干净 sdist 构建 wheel，并检查关键 package 边界."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
logger = logging.getLogger("check_wheel")

REQUIRED_NODE_LAB_FILES = {
    "nodelab/http/routes/__init__.py",
    "nodelab/http/routes/artifacts.py",
    "nodelab/http/routes/batch.py",
    "nodelab/http/routes/catalog.py",
    "nodelab/http/routes/dependencies.py",
    "nodelab/http/routes/health.py",
    "nodelab/http/routes/runs.py",
    "nodelab/http/schemas/__init__.py",
    "nodelab/http/schemas/batch.py",
    "nodelab/http/schemas/common.py",
    "nodelab/http/schemas/errors.py",
    "nodelab/http/schemas/execution.py",
}


def _single_artifact(root: Path, pattern: str) -> Path:
    artifacts = tuple(root.glob(pattern))
    if len(artifacts) != 1:
        raise RuntimeError(
            f"期望 {root} 中只有一个 {pattern} Artifact，实际为 {len(artifacts)}。"
        )
    return artifacts[0]


def main() -> None:
    """构建不复用工作区 build/ 缓存的 wheel 并验证内容."""
    with tempfile.TemporaryDirectory(prefix="shadergen-wheel-check-") as temp_dir:
        artifact_root = Path(temp_dir)
        sdist_root = artifact_root / "sdist"
        wheel_root = artifact_root / "wheel"
        subprocess.run(
            [
                "uv",
                "build",
                "--sdist",
                "--clear",
                "--no-build-logs",
                "--out-dir",
                str(sdist_root),
            ],
            cwd=ROOT,
            check=True,
        )
        sdist = _single_artifact(sdist_root, "*.tar.gz")
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--clear",
                "--no-build-logs",
                "--out-dir",
                str(wheel_root),
                str(sdist),
            ],
            cwd=ROOT,
            check=True,
        )
        wheel = _single_artifact(wheel_root, "*.whl")

        with ZipFile(wheel) as archive:
            names = set(archive.namelist())
            missing = REQUIRED_NODE_LAB_FILES - names
            if missing:
                raise RuntimeError(f"wheel 缺少 Node Lab 文件：{sorted(missing)}")
            if "nodelab/http/schemas.py" in names:
                raise RuntimeError("wheel 混入已删除的 nodelab/http/schemas.py。")
            if "nodelab/http/routes.py" in names:
                raise RuntimeError("wheel 混入已删除的 nodelab/http/routes.py。")
            if any(name.startswith("nodelab_service/") for name in names):
                raise RuntimeError("wheel 混入已删除的 nodelab_service 顶级包。")

            entry_points_path = next(
                (
                    name
                    for name in names
                    if name.endswith(".dist-info/entry_points.txt")
                ),
                None,
            )
            if entry_points_path is None:
                raise RuntimeError("wheel 缺少 console script entry_points.txt。")
            entry_points = archive.read(entry_points_path).decode("utf-8")
            if "nodelab-service = nodelab.http.cli:main" not in entry_points:
                raise RuntimeError("nodelab-service 未指向 nodelab.http.cli:main。")

    logger.info("wheel-check passed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
