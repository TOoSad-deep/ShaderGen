from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

from shaderforge.store import LocalArtifactStore


def test_store_writes_bytes_text_and_stable_json(tmp_path: Path) -> None:
    run = LocalArtifactStore(tmp_path).start_run("project-1", "run-1")

    binary = run.write_bytes("input/reference.png", b"png", content_type="image/png")
    text = run.write_text("candidates/c1/shader.frag", "void main() {}")
    structured = run.write_json("analysis/metrics.json", {"z": 2, "a": 1})

    assert binary.relative_path == "input/reference.png"
    assert binary.sha256 == sha256(b"png").hexdigest()
    assert binary.size_bytes == 3
    assert binary.content_type == "image/png"
    assert run.read_bytes(text.relative_path) == b"void main() {}"
    assert run.read_bytes(structured.relative_path) == b'{"a":1,"z":2}\n'


def test_store_atomically_replaces_existing_artifact(tmp_path: Path) -> None:
    run = LocalArtifactStore(tmp_path).start_run("project", "run")
    first = run.write_text("final/shader.frag", "first")
    second = run.write_text("final/shader.frag", "second")

    assert first.sha256 != second.sha256
    assert run.read_bytes("final/shader.frag") == b"second"
    assert list(run.path_for("final").glob("*.tmp")) == []
    assert list(run.path_for("final").glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "relative_path",
    ("../escape.txt", "nested/../../escape.txt", "/tmp/escape.txt", "."),
)
def test_store_rejects_path_traversal(tmp_path: Path, relative_path: str) -> None:
    run = LocalArtifactStore(tmp_path).start_run("project", "run")

    with pytest.raises(ValueError, match="根目录|相对路径"):
        run.write_text(relative_path, "escape")


@pytest.mark.parametrize("identifier", ("../project", "project/run", "", ".."))
def test_store_rejects_invalid_identifiers(tmp_path: Path, identifier: str) -> None:
    store = LocalArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="非法字符"):
        store.start_run(identifier, "run")


def test_store_rejects_symlink_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    run = LocalArtifactStore(tmp_path / "artifacts").start_run("project", "run")
    (run.root / "linked").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="越过"):
        run.write_text("linked/escape.txt", "escape")


def test_store_json_supports_dataclasses(tmp_path: Path) -> None:
    @dataclass(frozen=True)
    class Example:
        value: int

    run = LocalArtifactStore(tmp_path).start_run("project", "run")
    ref = run.write_json("example.json", Example(value=7))

    assert json.loads(run.read_bytes(ref.relative_path)) == {"value": 7}


def test_store_resolves_registered_run_without_accepting_a_path(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run = store.register_run("project-1", "run-1")
    run.write_bytes("final/render.png", b"png", content_type="image/png")

    resolved = store.resolve_run("run-1")

    assert resolved.read_bytes("final/render.png") == b"png"
    with pytest.raises(FileNotFoundError, match="未找到"):
        store.resolve_run("unknown-run")


def test_store_rejects_run_id_collision_across_projects(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    store.register_run("project-1", "shared-run")

    with pytest.raises(ValueError, match="其他 project_id"):
        store.register_run("project-2", "shared-run")
