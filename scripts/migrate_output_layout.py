"""Plan and safely copy indexed Artifacts into the readable output layout.

The tool deliberately leaves every legacy run directory in place. Public parents
must be succeeded strict final bundles; terminal private attempts are copied as a
complete tree. The process ledger is authoritative for filename, date and parent
project identity. Apply requires an explicit maintenance-window confirmation.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from shaderforge.store.output_layout import (
    private_attempt_relative_path,
    public_run_relative_path,
    validate_output_date,
)

FINAL_FILES = frozenset({"render.png", "metrics.json", "manifest.json"})
SHANGHAI = ZoneInfo("Asia/Shanghai")
DIRECT_ENGINE = "direct_glsl_layerplan_v1"


class MigrationError(ValueError):
    """Reject an unsafe or incomplete legacy Artifact migration."""


@dataclass(frozen=True, slots=True)
class LedgerRecord:
    """The small, injectable process-ledger projection used by planning."""

    filename: str | None
    started_at: datetime
    status: str
    project_id: str | None = None


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """One JSONL-visible migration decision."""

    action: str
    reason: str | None
    run_id: str
    project_id: str | None
    old_relative_run_root: str | None
    new_relative_run_root: str | None
    filename: str | None = None
    output_date: str | None = None
    parent_run_id: str | None = None
    copy_scope: str | None = None

    def json_value(self) -> dict[str, object]:
        """Return a stable JSON-compatible plan row."""
        return asdict(self)


def _safe_id(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise MigrationError(f"{label} 必须是字符串。")
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise MigrationError(f"{label} 不是 UUID。") from exc


def _read_v1_index(index_path: Path) -> tuple[str, str]:
    try:
        value = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("run index 无法解析。") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise MigrationError("只允许迁移 schema_version=1 的 index。")
    if set(value) != {"schema_version", "project_id", "run_id"}:
        raise MigrationError("v1 run index 包含未知或缺失字段。")
    return _safe_id(value["project_id"], "project_id"), _safe_id(value["run_id"], "run_id")


def _read_v2_index(value: object, index_name: str) -> str:
    """Validate an existing readable-layout index before idempotently skipping it."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "project_id", "run_id", "relative_run_root"
    }:
        raise MigrationError("v2 run index 包含未知或缺失字段。")
    project = _safe_id(value["project_id"], "project_id")
    run_id = _safe_id(value["run_id"], "run_id")
    if index_name != run_id or not isinstance(value["relative_run_root"], str):
        raise MigrationError("v2 run index 身份或路径无效。")
    raw = value["relative_run_root"]
    relative = Path(raw)
    if (
        "\\" in raw
        or any(not character.isprintable() for character in raw)
        or relative.is_absolute()
        or len(relative.parts) != 3
        or any(part in {"", ".", "..", ".run-index"} for part in relative.parts)
        or relative.parts[-1] != run_id
    ):
        raise MigrationError("v2 relative_run_root 无效。")
    validate_output_date(relative.parts[1])
    if not relative.parts[0]:
        raise MigrationError("v2 png slug 为空。")
    return project


def _strict_final_inventory(run_root: Path) -> dict[str, str]:
    """Verify exactly the public final bundle and return content hashes."""
    final = run_root / "final"
    if final.is_symlink() or not final.is_dir():
        raise MigrationError("legacy final 目录无效。")
    names = {path.name for path in final.iterdir()}
    if names != FINAL_FILES:
        raise MigrationError("legacy final 不是严格三件套。")
    inventory: dict[str, str] = {}
    for name in sorted(FINAL_FILES):
        path = final / name
        if path.is_symlink() or not path.is_file():
            raise MigrationError("legacy final 包含非普通文件。")
        digest = sha256()
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
        inventory[name] = digest.hexdigest()
    return inventory


def _tree_inventory(run_root: Path) -> dict[str, str]:
    """Hash every private file and record empty directories without following links."""
    if run_root.is_symlink() or not run_root.is_dir():
        raise MigrationError("private run 目录无效。")
    inventory: dict[str, str] = {}
    for path in sorted(run_root.rglob("*")):
        if path.is_symlink():
            raise MigrationError("private run 不得包含 symlink。")
        relative = path.relative_to(run_root).as_posix()
        if path.is_dir():
            inventory[f"{relative}/"] = "directory"
            continue
        if not path.is_file():
            raise MigrationError("private run 包含非普通文件。")
        digest = sha256()
        with path.open("rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
        inventory[relative] = digest.hexdigest()
    return inventory


def build_migration_plan(
    public_root: Path,
    records: Mapping[str, LedgerRecord],
) -> list[MigrationPlan]:
    """Classify all local index entries without changing any files."""
    root = public_root.resolve()
    index_root = root / ".run-index"
    if not index_root.is_dir():
        raise MigrationError("public .run-index 不存在。")
    plans: list[MigrationPlan] = []
    for index_path in sorted(index_root.glob("*.json")):
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationError(f"坏 index：{index_path.name}") from exc
        if isinstance(raw, dict) and raw.get("schema_version") == 2:
            try:
                project_id = _read_v2_index(raw, index_path.stem)
                run_id = str(raw["run_id"])
            except MigrationError as exc:
                plans.append(MigrationPlan("hold", str(exc), index_path.stem, None, None, None))
                continue
            plans.append(MigrationPlan("skip", "already_v2", run_id, project_id, None, None))
            continue
        try:
            project_id, run_id = _read_v1_index(index_path)
            if index_path.stem != run_id:
                raise MigrationError("index 文件名与 run_id 不一致。")
            old_relative = Path(project_id) / run_id
            run_root = root / old_relative
            if run_root.is_symlink() or not run_root.is_dir():
                raise MigrationError("legacy run 目录无效。")
            _strict_final_inventory(run_root)
        except MigrationError as exc:
            plans.append(MigrationPlan("hold", str(exc), index_path.stem, None, None, None))
            continue

        record = records.get(run_id)
        if record is None:
            plans.append(MigrationPlan("hold", "missing_ledger_record", run_id, project_id, str(old_relative), None))
            continue
        if record.project_id is not None and record.project_id != project_id:
            plans.append(MigrationPlan("hold", "ledger_project_mismatch", run_id, project_id, str(old_relative), None))
            continue
        if record.status == "running":
            plans.append(MigrationPlan("hold", "ledger_running", run_id, project_id, str(old_relative), None))
            continue
        if record.status != "succeeded":
            plans.append(MigrationPlan("hold", "ledger_not_succeeded", run_id, project_id, str(old_relative), None))
            continue
        if record.started_at.tzinfo is None or record.started_at.utcoffset() is None:
            plans.append(MigrationPlan("hold", "naive_ledger_started_at", run_id, project_id, str(old_relative), None))
            continue
        output_date = record.started_at.astimezone(SHANGHAI).date().isoformat()
        try:
            target = public_run_relative_path(record.filename or "", output_date, run_id)
        except (TypeError, ValueError) as exc:
            plans.append(MigrationPlan("hold", f"unsafe_filename: {exc}", run_id, project_id, str(old_relative), None))
            continue
        plans.append(
            MigrationPlan(
                "migrate",
                None,
                run_id,
                project_id,
                str(old_relative),
                target.as_posix(),
                record.filename,
                output_date,
                copy_scope="public_final",
            )
        )
    return plans


def _private_parent_from_run(
    run_root: Path,
    attempt_id: str,
    derived_parents: Mapping[str, str],
) -> str:
    """Read a private parent identity, falling back only to deterministic UUID5."""
    for relative in ("private/manifest.json", "private/failure-summary.json"):
        path = run_root / relative
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationError("private attempt manifest 无法解析。") from exc
        if not isinstance(value, dict):
            raise MigrationError("private attempt manifest 必须是 object。")
        manifest_attempt = value.get("attempt_id")
        if manifest_attempt is not None and _safe_id(
            manifest_attempt, "manifest attempt_id"
        ) != attempt_id:
            raise MigrationError("private manifest attempt_id 漂移。")
        return _safe_id(value.get("parent_run_id"), "parent_run_id")
    parent = derived_parents.get(attempt_id)
    if parent is None:
        raise MigrationError("private attempt 缺少 parent_run_id。")
    return parent


def _read_private_v2_index(value: object, index_name: str) -> str:
    """Validate one existing private readable-layout index."""
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "project_id",
        "run_id",
        "relative_run_root",
    }:
        raise MigrationError("private v2 run index 字段无效。")
    project = _safe_id(value["project_id"], "project_id")
    attempt_id = _safe_id(value["run_id"], "attempt_id")
    raw = value["relative_run_root"]
    if index_name != attempt_id or not isinstance(raw, str):
        raise MigrationError("private v2 run index 身份或路径无效。")
    relative = Path(raw)
    if (
        "\\" in raw
        or any(not character.isprintable() for character in raw)
        or relative.is_absolute()
        or len(relative.parts) != 4
        or any(part in {"", ".", "..", ".run-index"} for part in relative.parts)
        or relative.parts[-1] != attempt_id
    ):
        raise MigrationError("private v2 relative_run_root 无效。")
    validate_output_date(relative.parts[1])
    _safe_id(relative.parts[2], "parent_run_id")
    return project


def build_private_migration_plan(
    private_root: Path,
    records: Mapping[str, LedgerRecord],
) -> list[MigrationPlan]:
    """Classify Direct private attempts without changing evidence bytes."""
    root = private_root.resolve()
    index_root = root / ".run-index"
    if not index_root.is_dir():
        raise MigrationError("private .run-index 不存在。")
    derived_parents: dict[str, str] = {}
    for parent_run_id in records:
        try:
            parent_uuid = UUID(parent_run_id)
        except ValueError as exc:
            raise MigrationError("ledger run_id 不是 UUID。") from exc
        for attempt_index in range(3):
            attempt_id = str(
                uuid5(parent_uuid, f"{DIRECT_ENGINE}:{attempt_index}")
            )
            existing = derived_parents.setdefault(attempt_id, parent_run_id)
            if existing != parent_run_id:
                raise MigrationError("deterministic attempt identity 冲突。")

    plans: list[MigrationPlan] = []
    for index_path in sorted(index_root.glob("*.json")):
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationError(f"坏 private index：{index_path.name}") from exc
        if isinstance(raw, dict) and raw.get("schema_version") == 2:
            try:
                project_id = _read_private_v2_index(raw, index_path.stem)
                attempt_id = str(raw["run_id"])
            except MigrationError as exc:
                plans.append(
                    MigrationPlan(
                        "hold", str(exc), index_path.stem, None, None, None
                    )
                )
                continue
            plans.append(
                MigrationPlan(
                    "skip", "already_v2", attempt_id, project_id, None, None
                )
            )
            continue
        try:
            project_id, attempt_id = _read_v1_index(index_path)
            if index_path.stem != attempt_id:
                raise MigrationError("private index 文件名与 attempt_id 不一致。")
            old_relative = Path(project_id) / attempt_id
            run_root = _safe_run_root(root, old_relative.as_posix())
            _tree_inventory(run_root)
            parent_run_id = _private_parent_from_run(
                run_root,
                attempt_id,
                derived_parents,
            )
        except MigrationError as exc:
            plans.append(
                MigrationPlan("hold", str(exc), index_path.stem, None, None, None)
            )
            continue
        record = records.get(parent_run_id)
        if record is None:
            plans.append(
                MigrationPlan(
                    "hold",
                    "missing_parent_ledger_record",
                    attempt_id,
                    project_id,
                    old_relative.as_posix(),
                    None,
                    parent_run_id=parent_run_id,
                )
            )
            continue
        if record.project_id is not None and record.project_id != project_id:
            reason = "ledger_project_mismatch"
        elif record.status == "running":
            reason = "parent_ledger_running"
        elif record.status not in {"succeeded", "failed"}:
            reason = "parent_ledger_not_terminal"
        elif record.started_at.tzinfo is None or record.started_at.utcoffset() is None:
            reason = "naive_ledger_started_at"
        else:
            reason = None
        if reason is not None:
            plans.append(
                MigrationPlan(
                    "hold",
                    reason,
                    attempt_id,
                    project_id,
                    old_relative.as_posix(),
                    None,
                    record.filename,
                    parent_run_id=parent_run_id,
                )
            )
            continue
        output_date = record.started_at.astimezone(SHANGHAI).date().isoformat()
        try:
            target = private_attempt_relative_path(
                record.filename,
                output_date,
                parent_run_id,
                attempt_id,
            )
        except (TypeError, ValueError) as exc:
            plans.append(
                MigrationPlan(
                    "hold",
                    f"unsafe_filename: {exc}",
                    attempt_id,
                    project_id,
                    old_relative.as_posix(),
                    None,
                    record.filename,
                    output_date,
                    parent_run_id,
                )
            )
            continue
        plans.append(
            MigrationPlan(
                "migrate",
                None,
                attempt_id,
                project_id,
                old_relative.as_posix(),
                target.as_posix(),
                record.filename,
                output_date,
                parent_run_id,
                "full_tree",
            )
        )
    return plans


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_run_root(root: Path, relative_root: str) -> Path:
    """Resolve one planned run root without following a symlink component."""
    relative = Path(relative_root)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", "..", ".run-index"} for part in relative.parts)
    ):
        raise MigrationError("计划中的 run 相对路径无效。")
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise MigrationError("计划中的 run 路径不得经过 symlink。")
    resolved = current.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise MigrationError("计划中的 run 路径越过 Artifact root。")
    return current


def _write_index_atomically(index_path: Path, value: Mapping[str, object]) -> None:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    descriptor, name = tempfile.mkstemp(dir=index_path.parent, prefix=f".{index_path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as target:
            target.write(encoded)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, index_path)
        _fsync_directory(index_path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _append_jsonl(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as journal:
        journal.write(json.dumps(value, ensure_ascii=False, sort_keys=True).encode() + b"\n")
        journal.flush()
        os.fsync(journal.fileno())
    _fsync_directory(path.parent)


def _copy_final_to_target(source_root: Path, target_root: Path, expected: Mapping[str, str]) -> None:
    if target_root.exists() or target_root.is_symlink():
        raise MigrationError("迁移目标已存在。")
    target_root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".output-layout-staging-", dir=target_root.parent))
    try:
        final = staging / "final"
        final.mkdir()
        for name in sorted(FINAL_FILES):
            shutil.copyfile(source_root / "final" / name, final / name)
            with (final / name).open("rb") as copied:
                os.fsync(copied.fileno())
        _fsync_directory(final)
        if _strict_final_inventory(staging) != dict(expected):
            raise MigrationError("staging Artifact inventory 不一致。")
        os.rename(staging, target_root)
        _fsync_directory(target_root.parent)
        if _strict_final_inventory(target_root) != dict(expected):
            raise MigrationError("迁移后 Artifact inventory 不一致。")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _ensure_private_target_parent(root: Path, target_root: Path) -> None:
    """Create private target parents with restrictive permissions and no links."""
    current = root
    relative = target_root.relative_to(root)
    for part in relative.parts[:-1]:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            if current.is_symlink() or not current.is_dir():
                raise MigrationError("private 迁移目标父路径无效。")
        os.chmod(current, 0o700)


def _copy_tree_to_private_target(
    root: Path,
    source_root: Path,
    target_root: Path,
    expected: Mapping[str, str],
) -> None:
    """Copy a complete private attempt, preserve bytes, then atomically publish it."""
    if target_root.exists() or target_root.is_symlink():
        raise MigrationError("private 迁移目标已存在。")
    _ensure_private_target_parent(root, target_root)
    staging = Path(
        tempfile.mkdtemp(prefix=".output-layout-staging-", dir=target_root.parent)
    )
    try:
        shutil.copytree(
            source_root,
            staging,
            dirs_exist_ok=True,
            copy_function=shutil.copy2,
        )
        for path in sorted(staging.rglob("*"), reverse=True):
            if path.is_file():
                with path.open("rb") as copied:
                    os.fsync(copied.fileno())
            elif path.is_dir():
                _fsync_directory(path)
        _fsync_directory(staging)
        if _tree_inventory(staging) != dict(expected):
            raise MigrationError("private staging inventory 不一致。")
        os.rename(staging, target_root)
        _fsync_directory(target_root.parent)
        if _tree_inventory(target_root) != dict(expected):
            raise MigrationError("private 迁移后 inventory 不一致。")
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _preflight_public_plan(
    root: Path,
    plans: Iterable[MigrationPlan],
) -> list[tuple[MigrationPlan, Path, Path, Path, dict[str, str]]]:
    """Validate every public mutation before the first target is created."""
    prepared: list[tuple[MigrationPlan, Path, Path, Path, dict[str, str]]] = []
    targets: set[Path] = set()
    for plan in plans:
        if plan.action != "migrate":
            continue
        old_relative = plan.old_relative_run_root
        new_relative = plan.new_relative_run_root
        if plan.project_id is None or old_relative is None or new_relative is None:
            raise MigrationError("迁移计划缺少必要位置字段。")
        index = root / ".run-index" / f"{plan.run_id}.json"
        project_id, run_id = _read_v1_index(index)
        if project_id != plan.project_id or run_id != plan.run_id:
            raise MigrationError("迁移期间 index 身份漂移。")
        source = _safe_run_root(root, old_relative)
        target = _safe_run_root(root, new_relative)
        if target in targets:
            raise MigrationError("迁移计划包含重复目标。")
        targets.add(target)
        if target.exists() or target.is_symlink():
            raise MigrationError("迁移目标已存在。")
        prepared.append((plan, index, source, target, _strict_final_inventory(source)))
    return prepared


def apply_migration_plan(public_root: Path, plans: Iterable[MigrationPlan], journal: Path) -> None:
    """Copy eligible bundles and atomically advance their v1 index to v2."""
    root = public_root.resolve()
    prepared = _preflight_public_plan(root, plans)
    for plan, index, source, target, source_inventory in prepared:
        assert plan.project_id is not None
        assert plan.new_relative_run_root is not None
        project_id = plan.project_id
        run_id = plan.run_id
        old_index = index.read_bytes()
        journal_row: dict[str, object] = {
            "action": "apply", "stage": "prepared", "artifact_root": str(root),
            "copy_scope": "public_final",
            "run_id": run_id, "old_relative_run_root": plan.old_relative_run_root,
            "new_relative_run_root": plan.new_relative_run_root,
            "old_index_b64": base64.b64encode(old_index).decode("ascii"),
            "inventory": source_inventory,
        }
        _append_jsonl(journal, journal_row)
        _copy_final_to_target(source, target, source_inventory)
        _write_index_atomically(index, {"schema_version": 2, "project_id": project_id, "run_id": run_id, "relative_run_root": plan.new_relative_run_root})
        journal_row["stage"] = "applied"
        _append_jsonl(journal, journal_row)


def _preflight_private_plan(
    root: Path,
    plans: Iterable[MigrationPlan],
) -> list[tuple[MigrationPlan, Path, Path, Path, dict[str, str]]]:
    """Validate every private mutation before the first target is created."""
    prepared: list[tuple[MigrationPlan, Path, Path, Path, dict[str, str]]] = []
    targets: set[Path] = set()
    for plan in plans:
        if plan.action != "migrate":
            continue
        if (
            plan.copy_scope != "full_tree"
            or not plan.project_id
            or not plan.old_relative_run_root
            or not plan.new_relative_run_root
        ):
            raise MigrationError("private 迁移计划缺少必要字段。")
        index = root / ".run-index" / f"{plan.run_id}.json"
        project_id, run_id = _read_v1_index(index)
        if project_id != plan.project_id or run_id != plan.run_id:
            raise MigrationError("private 迁移期间 index 身份漂移。")
        source = _safe_run_root(root, plan.old_relative_run_root)
        target = _safe_run_root(root, plan.new_relative_run_root)
        if target in targets:
            raise MigrationError("private 迁移计划包含重复目标。")
        targets.add(target)
        if target.exists() or target.is_symlink():
            raise MigrationError("private 迁移目标已存在。")
        prepared.append((plan, index, source, target, _tree_inventory(source)))
    return prepared


def apply_private_migration_plan(
    private_root: Path,
    plans: Iterable[MigrationPlan],
    journal: Path,
) -> None:
    """Copy complete private attempts and atomically advance their indexes to v2."""
    root = private_root.resolve()
    prepared = _preflight_private_plan(root, plans)
    for plan, index, source, target, source_inventory in prepared:
        assert plan.project_id is not None
        assert plan.old_relative_run_root is not None
        assert plan.new_relative_run_root is not None
        old_index = index.read_bytes()
        journal_row: dict[str, object] = {
            "action": "apply",
            "stage": "prepared",
            "artifact_root": str(root),
            "copy_scope": "full_tree",
            "run_id": plan.run_id,
            "old_relative_run_root": plan.old_relative_run_root,
            "new_relative_run_root": plan.new_relative_run_root,
            "old_index_b64": base64.b64encode(old_index).decode("ascii"),
            "inventory": source_inventory,
        }
        _append_jsonl(journal, journal_row)
        _copy_tree_to_private_target(root, source, target, source_inventory)
        _write_index_atomically(
            index,
            {
                "schema_version": 2,
                "project_id": plan.project_id,
                "run_id": plan.run_id,
                "relative_run_root": plan.new_relative_run_root,
            },
        )
        journal_row["stage"] = "applied"
        _append_jsonl(journal, journal_row)


def rollback_journal(journal: Path) -> None:
    """Restore only the old indexes recorded by applied journal rows."""
    prepared: list[tuple[Path, bytes]] = []
    seen: set[tuple[Path, str]] = set()
    for line in journal.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if not isinstance(value, dict) or value.get("action") != "apply" or value.get("stage") != "applied":
            continue
        root_value = value.get("artifact_root", value.get("public_root"))
        if not isinstance(root_value, str):
            raise MigrationError("journal 缺少 artifact root。")
        root = Path(root_value).resolve()
        run_id = _safe_id(value["run_id"], "run_id")
        identity = (root, run_id)
        if identity in seen:
            raise MigrationError("journal 包含重复 applied run。")
        seen.add(identity)
        encoded = value.get("old_index_b64")
        if not isinstance(encoded, str):
            raise MigrationError("journal 缺少旧 index。")
        old_index = base64.b64decode(encoded, validate=True)
        index = root / ".run-index" / f"{run_id}.json"
        old_project, old_run_id = _read_v1_index_bytes(old_index)
        if old_run_id != run_id:
            raise MigrationError("journal 旧 index 身份漂移。")
        old_relative = value.get("old_relative_run_root")
        new_relative = value.get("new_relative_run_root")
        inventory = value.get("inventory")
        if (
            not isinstance(old_relative, str)
            or not isinstance(new_relative, str)
            or not isinstance(inventory, dict)
            or not all(isinstance(key, str) and isinstance(item, str) for key, item in inventory.items())
        ):
            raise MigrationError("journal 位置或 inventory 无效。")
        expected_inventory = dict(inventory)
        copy_scope = value.get("copy_scope", "public_final")
        if copy_scope == "public_final":
            inventory_reader = _strict_final_inventory
        elif copy_scope == "full_tree":
            inventory_reader = _tree_inventory
        else:
            raise MigrationError("journal copy_scope 无效。")
        if inventory_reader(_safe_run_root(root, old_relative)) != expected_inventory:
            raise MigrationError("rollback 前 legacy source 内容漂移。")
        if inventory_reader(_safe_run_root(root, new_relative)) != expected_inventory:
            raise MigrationError("rollback 前迁移目标内容漂移。")
        current = index.read_bytes()
        if current == old_index:
            continue
        try:
            current_value = json.loads(current)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MigrationError("rollback 前当前 index 损坏。") from exc
        expected_v2 = {
            "schema_version": 2,
            "project_id": old_project,
            "run_id": run_id,
            "relative_run_root": new_relative,
        }
        if current_value != expected_v2:
            raise MigrationError("rollback 前当前 index 已发生漂移。")
        prepared.append((index, old_index))

    for index, old_index in reversed(prepared):
        descriptor, name = tempfile.mkstemp(dir=index.parent, prefix=f".{index.name}.", suffix=".tmp")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as restored:
                restored.write(old_index)
                restored.flush()
                os.fsync(restored.fileno())
            os.replace(temporary, index)
            _fsync_directory(index.parent)
        finally:
            temporary.unlink(missing_ok=True)


def _read_v1_index_bytes(value: bytes) -> tuple[str, str]:
    """Parse the exact v1 index bytes retained by a rollback journal."""
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MigrationError("journal 旧 index 无法解析。") from exc
    if not isinstance(decoded, dict) or decoded.get("schema_version") != 1:
        raise MigrationError("journal 旧 index 不是 v1。")
    if set(decoded) != {"schema_version", "project_id", "run_id"}:
        raise MigrationError("journal 旧 index 字段无效。")
    return _safe_id(decoded["project_id"], "project_id"), _safe_id(
        decoded["run_id"], "run_id"
    )


async def read_ledger_records(
    database_url: str,
    run_ids: list[str] | None = None,
) -> dict[str, LedgerRecord]:
    """Read the only authoritative filename/date data for legacy public runs."""
    import asyncpg  # type: ignore[import-untyped]

    connection = await asyncpg.connect(database_url)
    try:
        query = (
            "SELECT id::text AS run_id, project_id::text AS project_id, "
            "input->>'filename' AS filename, started_at, status FROM agent_runs"
        )
        if run_ids is None:
            rows = await connection.fetch(query)
        else:
            rows = await connection.fetch(
                query + " WHERE id = ANY($1::uuid[])",
                [UUID(run_id) for run_id in run_ids],
            )
    finally:
        await connection.close()
    return {
        str(row["run_id"]): LedgerRecord(
            row["filename"],
            row["started_at"],
            str(row["status"]),
            str(row["project_id"]),
        )
        for row in rows
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-root", type=Path, default=Path("output/png-to-shader"))
    parser.add_argument(
        "--private-root",
        type=Path,
        default=Path("output/png-to-shader-direct-private"),
    )
    parser.add_argument(
        "--scope",
        choices=("all", "public", "private"),
        default="all",
    )
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--maintenance-confirmed", action="store_true")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--rollback", type=Path)
    return parser


def main() -> int:
    """Run the command-line planner, applier, or index-only rollback."""
    load_dotenv()
    args = _parser().parse_args()
    if args.rollback is not None:
        rollback_journal(args.rollback)
        return 0
    if not args.database_url:
        raise SystemExit("DATABASE_URL is required to plan migration.")
    records = asyncio.run(read_ledger_records(args.database_url))
    public_plans: list[MigrationPlan] = []
    private_plans: list[MigrationPlan] = []
    if args.scope in {"all", "public"}:
        public_plans = build_migration_plan(args.public_root, records)
    if args.scope in {"all", "private"}:
        private_plans = build_private_migration_plan(args.private_root, records)
    for store_name, plans in (
        ("public", public_plans),
        ("private", private_plans),
    ):
        for plan in plans:
            sys.stdout.write(
                json.dumps(
                    {"store": store_name, **plan.json_value()},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    if args.apply:
        if not args.maintenance_confirmed:
            raise SystemExit("--apply requires --maintenance-confirmed.")
        journal = args.journal or Path(
            "output/.layout-migrations/output-layout-migration.jsonl"
        )
        if journal.exists() or journal.is_symlink():
            raise SystemExit("Migration journal already exists; choose a new --journal.")
        _preflight_public_plan(args.public_root.resolve(), public_plans)
        _preflight_private_plan(args.private_root.resolve(), private_plans)
        apply_migration_plan(args.public_root, public_plans, journal)
        apply_private_migration_plan(args.private_root, private_plans, journal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
