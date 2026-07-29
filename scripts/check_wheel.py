"""Build a clean wheel and verify the current package boundary."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {
    "agent/app/contracts/layer_plan.py",
    "agent/app/contracts/layerplan_glsl_direct.py",
    "agent/app/graphs/layerplan_glsl_direct.py",
    "agent/app/graphs/layerplan_glsl_direct_studio.py",
    "agent/app/nodes/layered_direct/authors.py",
    "agent/app/services/layerplan_glsl_direct.py",
    "agent/app/states/layerplan_glsl_direct.py",
    "shaderforge/layered_spec/compiler.py",
    "shaderforge/program_spec/models.py",
}
FORBIDDEN_FILES = {
    "agent/app/graphs/png_to_shader_min_graph.py",
}
FORBIDDEN_PREFIXES = (
    "nodelab/",
    "agent/app/nodes/png_to_shader_min/",
    "shaderforge/dsl/",
)


def _single(root: Path, pattern: str) -> Path:
    values = tuple(root.glob(pattern))
    if len(values) != 1:
        raise RuntimeError(f"expected one {pattern}, found {len(values)}")
    return values[0]


def main() -> None:
    """Build from a clean sdist and validate wheel contents."""
    with tempfile.TemporaryDirectory(prefix="shadergen-wheel-") as temp:
        root = Path(temp)
        sdist_root = root / "sdist"
        wheel_root = root / "wheel"
        subprocess.run(
            ["uv", "build", "--sdist", "--clear", "--out-dir", str(sdist_root)],
            cwd=ROOT,
            check=True,
        )
        subprocess.run(
            [
                "uv",
                "build",
                "--wheel",
                "--clear",
                "--out-dir",
                str(wheel_root),
                str(_single(sdist_root, "*.tar.gz")),
            ],
            cwd=ROOT,
            check=True,
        )
        with ZipFile(_single(wheel_root, "*.whl")) as archive:
            names = set(archive.namelist())
            missing = REQUIRED - names
            if missing:
                raise RuntimeError(f"wheel missing current files: {sorted(missing)}")
            legacy = sorted(
                name
                for name in names
                if name in FORBIDDEN_FILES
                or any(name.startswith(prefix) for prefix in FORBIDDEN_PREFIXES)
            )
            if legacy:
                raise RuntimeError(f"wheel contains legacy files: {legacy}")


if __name__ == "__main__":
    main()
