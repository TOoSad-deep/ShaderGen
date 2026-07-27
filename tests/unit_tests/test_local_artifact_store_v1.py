from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import pytest

import shaderforge.store.local_artifacts as local_artifacts
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


def test_restrictive_store_uses_0700_directories_and_0600_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "private"
    store = LocalArtifactStore(root, restrictive_permissions=True)
    run = store.register_run("project", "run")
    run.write_json("private/nested/value.json", {"private": True})

    for path in [root, *root.rglob("*")]:
        expected = 0o700 if path.is_dir() else 0o600
        assert stat.S_IMODE(path.stat().st_mode) == expected


def test_public_final_reader_rejects_symlink_before_following_it(
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "public")
    files = {
        "render.png": b"png",
        "metrics.json": b"{}\n",
        "manifest.json": b"{}\n",
    }
    store.publish_public_final_bundle("project", "run", files)
    run = store.resolve_run("run")
    outside = tmp_path / "outside.json"
    outside.write_bytes(files["metrics.json"])
    metrics = run.root / "final/metrics.json"
    metrics.unlink()
    metrics.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink|安全读取"):
        store.verify_public_final_bundle("run")


def test_public_final_publish_fsyncs_files_and_directory_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    synced_modes: list[int] = []
    original_fsync = os.fsync

    def tracked_fsync(descriptor: int) -> None:
        synced_modes.append(os.fstat(descriptor).st_mode)
        original_fsync(descriptor)

    monkeypatch.setattr(local_artifacts.os, "fsync", tracked_fsync)
    store = LocalArtifactStore(tmp_path / "public")
    store.publish_public_final_bundle(
        "project",
        "run",
        {
            "render.png": b"png",
            "metrics.json": b"{}\n",
            "manifest.json": b"{}\n",
        },
    )

    assert sum(stat.S_ISREG(mode) for mode in synced_modes) >= 4
    assert sum(stat.S_ISDIR(mode) for mode in synced_modes) >= 3


def test_public_final_reader_detects_directory_replacement_during_read(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path / "public")
    files = {
        "render.png": b"png",
        "metrics.json": b"{}\n",
        "manifest.json": b"{}\n",
    }
    store.publish_public_final_bundle("project", "run", files)
    final_dir = store.resolve_run("run").root / "final"
    original_listdir = local_artifacts.os.listdir
    replaced = False

    def replace_after_open(path: int | str | bytes | os.PathLike[str]) -> list[str]:
        nonlocal replaced
        names = original_listdir(path)
        if isinstance(path, int) and not replaced:
            replaced = True
            pinned = final_dir.with_name("final-pinned")
            final_dir.rename(pinned)
            final_dir.mkdir()
            for name, data in files.items():
                (final_dir / name).write_bytes(data)
        return names

    monkeypatch.setattr(local_artifacts.os, "listdir", replace_after_open)
    with pytest.raises(ValueError, match="读取期间发生替换"):
        store.verify_public_final_bundle("run")
