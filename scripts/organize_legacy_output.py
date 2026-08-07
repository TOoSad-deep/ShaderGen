"""Safely classify and rename legacy output trees without deleting content."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import errno
import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence
from uuid import UUID
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from shaderforge.store.output_layout import safe_png_name_slug, validate_output_date

NODE_LAB_SCHEMA = "node_lab_run_v1"
JOURNAL_SCHEMA = "organize_legacy_output_journal_v1"
SHANGHAI = ZoneInfo("Asia/Shanghai")
_PIPELINE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_NODE_LAB_SCREENSHOT_PREFIX = "node-lab"
_NODE_LAB_SCRIPT = "node_lab_k3_acceptance.py"


class OrganizationError(ValueError):
    """Reject an unsafe, ambiguous, or drifting output organization."""


@dataclass(frozen=True, slots=True)
class Inventory:
    """Describe a path tree using a stable content-and-structure digest."""

    sha256: str
    entry_count: int


@dataclass(frozen=True, slots=True)
class PlanItem:
    """Describe one move or one report-only classification."""

    category: str
    action: Literal["move", "hold", "already_structured", "ignored"]
    source: str
    destination: str | None = None
    reason: str | None = None
    inventory_sha256: str | None = None
    entry_count: int | None = None


@dataclass(frozen=True, slots=True)
class OrganizationPlan:
    """Contain a complete dry-run plan rooted at one output directory."""

    output_root: str
    items: tuple[PlanItem, ...]

    def json_value(self) -> dict[str, object]:
        """Return a stable JSON-compatible dry-run report."""
        return {
            "schema_version": "organize_legacy_output_plan_v1",
            "output_root": self.output_root,
            "summary": dict(sorted(_action_counts(self.items).items())),
            "items": [asdict(item) for item in self.items],
        }


def _action_counts(items: Sequence[PlanItem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.action] = counts.get(item.action, 0) + 1
    return counts


def _safe_uuid(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OrganizationError(f"{label} 必须是 UUID 字符串。")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise OrganizationError(f"{label} 必须是 UUID 字符串。") from exc


def _safe_segment(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or any(not character.isprintable() for character in value)
    ):
        raise OrganizationError(f"{label} 必须是安全路径段。")
    return value


def _safe_pipeline_id(value: object) -> str:
    if not isinstance(value, str) or not _PIPELINE_PATTERN.fullmatch(value):
        raise OrganizationError("pipeline_id 包含非法字符。")
    return value


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str):
        raise OrganizationError(f"{label} 必须是带时区 ISO 时间。")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise OrganizationError(f"{label} 必须是带时区 ISO 时间。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OrganizationError(f"{label} 必须是带时区 ISO 时间。")
    return parsed


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OrganizationError(f"{label} 无法解析。") from exc
    if not isinstance(value, dict):
        raise OrganizationError(f"{label} 必须是 JSON object。")
    return value


def inventory_path(path: Path) -> Inventory:
    """Hash every file byte and relative entry name without following symlinks."""
    if path.is_symlink() or not path.exists():
        raise OrganizationError(f"inventory 源无效：{path}")
    records: list[dict[str, object]] = []
    if path.is_file():
        digest = _hash_file(path)
        records.append(
            {"path": ".", "type": "file", "size": path.stat().st_size, "sha256": digest}
        )
    elif path.is_dir():
        records.append({"path": ".", "type": "directory"})
        for child in sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix()):
            if child.is_symlink():
                raise OrganizationError(f"inventory 不允许 symlink：{child}")
            relative = child.relative_to(path).as_posix()
            if child.is_dir():
                records.append({"path": relative, "type": "directory"})
            elif child.is_file():
                records.append(
                    {
                        "path": relative,
                        "type": "file",
                        "size": child.stat().st_size,
                        "sha256": _hash_file(child),
                    }
                )
            else:
                raise OrganizationError(f"inventory 只允许普通文件和目录：{child}")
    else:
        raise OrganizationError(f"inventory 只允许普通文件和目录：{path}")
    encoded = json.dumps(
        records,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Inventory(sha256(encoded).hexdigest(), len(records))


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _move_item(
    root: Path,
    *,
    category: str,
    source: Path,
    destination: Path,
) -> PlanItem:
    source_relative = source.relative_to(root).as_posix()
    destination_relative = destination.relative_to(root).as_posix()
    inventory = inventory_path(source)
    return PlanItem(
        category=category,
        action="move",
        source=source_relative,
        destination=destination_relative,
        inventory_sha256=inventory.sha256,
        entry_count=inventory.entry_count,
    )


def _plan_node_lab(root: Path) -> list[PlanItem]:
    service_root = root / "node-lab/service"
    if not service_root.is_dir():
        return []
    items: list[PlanItem] = []
    for run_root in sorted(service_root.iterdir()):
        if not run_root.is_dir() or run_root.is_symlink():
            items.append(
                PlanItem("node_lab", "ignored", run_root.relative_to(root).as_posix(), reason="not_lab_run_directory")
            )
            continue
        lab_id = _safe_uuid(run_root.name, "lab_id")
        manifest = _load_json_object(run_root / "run.json", "Node Lab run.json")
        if manifest.get("schema_version") != NODE_LAB_SCHEMA:
            raise OrganizationError("Node Lab run.json schema_version 非法。")
        if _safe_uuid(manifest.get("lab_run_id"), "lab_run_id") != lab_id:
            raise OrganizationError("Node Lab lab_run_id 与目录名不一致。")
        created_at = _parse_timestamp(manifest.get("created_at"), "created_at")
        output_date = created_at.astimezone(SHANGHAI).date().isoformat()
        pipeline_id = _safe_pipeline_id(manifest.get("pipeline_id"))
        destination = root / "legacy/node-lab" / output_date / pipeline_id / lab_id
        items.append(
            _move_item(
                root,
                category="node_lab",
                source=run_root,
                destination=destination,
            )
        )
    return items


def _plan_rollout_private(root: Path) -> list[PlanItem]:
    source = root / "png-to-shader-rollout-private"
    destination = root / "legacy/png-to-shader-rollout-private"
    if source.exists() or source.is_symlink():
        return [
            _move_item(
                root,
                category="rollout_private",
                source=source,
                destination=destination,
            )
        ]
    if destination.is_dir() and not destination.is_symlink():
        return [
            PlanItem(
                "rollout_private",
                "already_structured",
                destination.relative_to(root).as_posix(),
                reason="legacy_root_present",
            )
        ]
    return []


def _scenario_slug(filename: str) -> str:
    try:
        return safe_png_name_slug(filename, fallback="unnamed-scenario")
    except (TypeError, ValueError) as exc:
        raise OrganizationError(f"Playwright 文件名无法生成 scenario slug：{filename}") from exc


def _plan_playwright(root: Path) -> list[PlanItem]:
    playwright_root = root / "playwright"
    if not playwright_root.is_dir():
        return []
    items: list[PlanItem] = []
    for source in sorted(playwright_root.iterdir()):
        if not source.is_file() or source.is_symlink():
            items.append(
                PlanItem("playwright", "ignored", source.relative_to(root).as_posix(), reason="not_regular_file")
            )
            continue
        is_node_lab_screenshot = (
            source.name.startswith(_NODE_LAB_SCREENSHOT_PREFIX)
            and source.suffix.lower() == ".png"
        )
        if is_node_lab_screenshot or source.name == _NODE_LAB_SCRIPT:
            destination = (
                root
                / "legacy/node-lab/2026-07-27/visual-acceptance"
                / source.name
            )
        elif source.suffix.lower() == ".png":
            output_date = datetime.fromtimestamp(source.stat().st_mtime, SHANGHAI)
            destination = (
                root
                / "visual-acceptance"
                / _scenario_slug(source.name)
                / output_date.date().isoformat()
                / source.name
            )
        else:
            items.append(
                PlanItem("playwright", "ignored", source.relative_to(root).as_posix(), reason="unclassified_file")
            )
            continue
        items.append(
            _move_item(
                root,
                category="playwright",
                source=source,
                destination=destination,
            )
        )
    return items


def _plan_diagnostics(
    root: Path,
    diagnostic_mapping: Mapping[str, tuple[str, str]],
) -> list[PlanItem]:
    analysis_root = root / "diagnostics/run-analysis"
    if not analysis_root.is_dir():
        return []
    items: list[PlanItem] = []
    for source in sorted(analysis_root.iterdir()):
        if not source.is_dir() or source.is_symlink():
            items.append(
                PlanItem("diagnostics", "ignored", source.relative_to(root).as_posix(), reason="not_run_directory")
            )
            continue
        try:
            run_id = _safe_uuid(source.name, "diagnostics run_id")
        except OrganizationError:
            items.append(
                PlanItem("diagnostics", "already_structured", source.relative_to(root).as_posix(), reason="non_run_top_level")
            )
            continue
        mapped = diagnostic_mapping.get(run_id)
        if mapped is None:
            items.append(
                PlanItem("diagnostics", "hold", source.relative_to(root).as_posix(), reason="missing_database_mapping")
            )
            continue
        source_slug = _safe_segment(mapped[0], "source_slug")
        output_date = validate_output_date(mapped[1])
        destination = analysis_root / source_slug / output_date / run_id
        items.append(
            _move_item(
                root,
                category="diagnostics",
                source=source,
                destination=destination,
            )
        )
    return items


def build_plan(
    output_root: Path,
    *,
    diagnostic_mapping: Mapping[str, tuple[str, str]] | None = None,
) -> OrganizationPlan:
    """Build a complete report and migration plan without changing the filesystem."""
    if output_root.is_symlink():
        raise OrganizationError("output_root 不得是 symlink。")
    root = output_root.resolve()
    if not root.is_dir():
        raise OrganizationError("output_root 必须是普通目录。")
    items = [
        *_plan_node_lab(root),
        *_plan_rollout_private(root),
        *_plan_playwright(root),
        *_plan_diagnostics(root, diagnostic_mapping or {}),
    ]
    for name, category in (
        ("benchmarks", "benchmarks"),
        ("black-hole-preprocessing-20260731", "black_hole_preprocessing"),
    ):
        path = root / name
        if path.exists() and not path.is_symlink():
            items.append(
                PlanItem(
                    category,
                    "already_structured",
                    path.relative_to(root).as_posix(),
                    reason="already_structured",
                )
            )
    plan = OrganizationPlan(str(root), tuple(items))
    preflight_plan(plan, allow_holds=True)
    return plan


def _path_from_relative(root: Path, value: str, label: str) -> Path:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise OrganizationError(f"{label} 不是安全相对路径。")
    path = root / relative
    if not path.resolve(strict=False).is_relative_to(root):
        raise OrganizationError(f"{label} 越过 output_root。")
    return path


def _has_symlink_ancestor(root: Path, path: Path) -> bool:
    current = root
    for part in path.relative_to(root).parts:
        current /= part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def preflight_plan(plan: OrganizationPlan, *, allow_holds: bool = False) -> None:
    """Validate every move globally before apply performs its first write."""
    root = Path(plan.output_root).resolve()
    if not root.is_dir() or root.is_symlink():
        raise OrganizationError("journal output_root 无效。")
    if not allow_holds and any(item.action == "hold" for item in plan.items):
        raise OrganizationError("计划包含 hold 项，拒绝 apply。")
    moves = [item for item in plan.items if item.action == "move"]
    sources: list[Path] = []
    destinations: list[Path] = []
    for item in moves:
        if item.destination is None or item.inventory_sha256 is None:
            raise OrganizationError("move 项缺少目标或 inventory。")
        source = _path_from_relative(root, item.source, "source")
        destination = _path_from_relative(root, item.destination, "destination")
        if source.is_symlink() or not source.exists():
            raise OrganizationError(f"迁移源不存在或为 symlink：{item.source}")
        if os.path.lexists(destination):
            raise OrganizationError(f"迁移目标已存在：{item.destination}")
        if _has_symlink_ancestor(root, source) or _has_symlink_ancestor(
            root, destination.parent
        ):
            raise OrganizationError("迁移路径不得经过 symlink。")
        current_inventory = inventory_path(source)
        if (
            current_inventory.sha256 != item.inventory_sha256
            or current_inventory.entry_count != item.entry_count
        ):
            raise OrganizationError(f"迁移源 inventory 漂移：{item.source}")
        sources.append(source)
        destinations.append(destination)
    if len(set(sources)) != len(sources) or len(set(destinations)) != len(destinations):
        raise OrganizationError("计划包含重复 source 或 destination。")
    for left_index, source in enumerate(sources):
        for other in sources[left_index + 1 :]:
            if source.is_relative_to(other) or other.is_relative_to(source):
                raise OrganizationError("迁移 source 不得互相嵌套。")
        for destination in destinations:
            if destination == source or destination.is_relative_to(source):
                raise OrganizationError("destination 不得位于任一迁移 source 内。")
    for left_index, destination in enumerate(destinations):
        for other in destinations[left_index + 1 :]:
            if destination.is_relative_to(other) or other.is_relative_to(destination):
                raise OrganizationError("迁移 destination 不得互相嵌套。")


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _rename_no_replace(source: Path, destination: Path) -> None:
    """Atomically rename while asking the kernel to reject an existing target."""
    libc = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(source)
    encoded_destination = os.fsencode(destination)
    if sys.platform == "darwin" and hasattr(libc, "renamex_np"):
        renamex_np = libc.renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(encoded_source, encoded_destination, 0x00000004)
    elif sys.platform.startswith("linux") and hasattr(libc, "renameat2"):
        renameat2 = libc.renameat2
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            encoded_source,
            -100,
            encoded_destination,
            0x00000001,
        )
    else:
        raise OrganizationError("当前平台不支持 atomic no-replace rename。")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise OrganizationError(f"迁移目标已存在：{destination}")
    raise OSError(error_number, os.strerror(error_number), str(source), str(destination))


def _validate_journal_location(plan: OrganizationPlan, journal_path: Path) -> None:
    root = Path(plan.output_root).resolve()
    journal = journal_path.resolve(strict=False)
    for item in plan.items:
        if item.action != "move" or item.destination is None:
            continue
        source = _path_from_relative(root, item.source, "source")
        destination = _path_from_relative(root, item.destination, "destination")
        if journal == source or journal.is_relative_to(source):
            raise OrganizationError("journal 不得位于迁移 source 内。")
        if journal == destination or journal.is_relative_to(destination):
            raise OrganizationError("journal 不得位于迁移 destination 内。")


def _journal_value(
    plan: OrganizationPlan,
    *,
    state: str,
    statuses: Sequence[str],
    created_at: str,
) -> dict[str, object]:
    moves = [item for item in plan.items if item.action == "move"]
    return {
        "schema_version": JOURNAL_SCHEMA,
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
        "output_root": plan.output_root,
        "moves": [
            {**asdict(item), "status": status}
            for item, status in zip(moves, statuses, strict=True)
        ],
    }


def apply_plan(plan: OrganizationPlan, journal_path: Path) -> None:
    """Rename every planned source after global preflight and journal each step."""
    preflight_plan(plan)
    _validate_journal_location(plan, journal_path)
    if os.path.lexists(journal_path):
        raise OrganizationError("journal 已存在，拒绝覆盖。")
    root = Path(plan.output_root).resolve()
    moves = [item for item in plan.items if item.action == "move"]
    statuses = ["pending"] * len(moves)
    created_at = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(
        journal_path,
        _journal_value(
            plan,
            state="applying",
            statuses=statuses,
            created_at=created_at,
        ),
    )
    for index, item in enumerate(moves):
        source = _path_from_relative(root, item.source, "source")
        destination = _path_from_relative(root, item.destination or "", "destination")
        current = inventory_path(source)
        if current.sha256 != item.inventory_sha256 or current.entry_count != item.entry_count:
            raise OrganizationError(f"rename 前 inventory 漂移：{item.source}")
        if os.path.lexists(destination):
            raise OrganizationError(f"rename 前目标已存在：{item.destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if _has_symlink_ancestor(root, destination.parent):
            raise OrganizationError("rename 前目标父路径出现 symlink。")
        _rename_no_replace(source, destination)
        _fsync_directory(source.parent)
        _fsync_directory(destination.parent)
        moved = inventory_path(destination)
        if moved != current:
            raise OrganizationError(f"rename 后 inventory 漂移：{item.destination}")
        statuses[index] = "applied"
        _atomic_write_json(
            journal_path,
            _journal_value(
                plan,
                state="applying",
                statuses=statuses,
                created_at=created_at,
            ),
        )
    _atomic_write_json(
        journal_path,
        _journal_value(
            plan,
            state="applied",
            statuses=statuses,
            created_at=created_at,
        ),
    )


def _plan_from_journal(value: Mapping[str, object]) -> tuple[OrganizationPlan, list[str], str]:
    if value.get("schema_version") != JOURNAL_SCHEMA:
        raise OrganizationError("journal schema_version 非法。")
    output_root = value.get("output_root")
    raw_moves = value.get("moves")
    created_at = value.get("created_at")
    if (
        not isinstance(output_root, str)
        or not isinstance(raw_moves, list)
        or not isinstance(created_at, str)
    ):
        raise OrganizationError("journal 缺少必要字段。")
    items: list[PlanItem] = []
    statuses: list[str] = []
    for raw in raw_moves:
        if not isinstance(raw, dict) or not isinstance(raw.get("status"), str):
            raise OrganizationError("journal move 非法。")
        try:
            item = PlanItem(
                category=str(raw["category"]),
                action="move",
                source=str(raw["source"]),
                destination=str(raw["destination"]),
                reason=raw.get("reason") if isinstance(raw.get("reason"), str) else None,
                inventory_sha256=str(raw["inventory_sha256"]),
                entry_count=int(raw["entry_count"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise OrganizationError("journal move 缺少必要字段。") from exc
        items.append(item)
        status = raw["status"]
        if status not in {"pending", "applied", "rolled_back"}:
            raise OrganizationError("journal move status 非法。")
        statuses.append(status)
    return OrganizationPlan(output_root, tuple(items)), statuses, created_at


def rollback_journal(journal_path: Path) -> None:
    """Reverse applied renames after a complete no-overwrite rollback preflight."""
    value = _load_json_object(journal_path, "journal")
    plan, statuses, created_at = _plan_from_journal(value)
    _validate_journal_location(plan, journal_path)
    root = Path(plan.output_root).resolve()
    moves = [item for item in plan.items if item.action == "move"]
    reverse_indexes: list[int] = []
    for index in reversed(range(len(moves))):
        item = moves[index]
        source = _path_from_relative(root, item.source, "rollback destination")
        destination = _path_from_relative(root, item.destination or "", "rollback source")
        source_exists = os.path.lexists(source)
        destination_exists = os.path.lexists(destination)
        if source_exists and destination_exists:
            raise OrganizationError("rollback source 与 destination 同时存在。")
        if not source_exists and not destination_exists:
            raise OrganizationError("rollback source 与 destination 均不存在。")
        if source_exists:
            inventory = inventory_path(source)
            if (
                inventory.sha256 != item.inventory_sha256
                or inventory.entry_count != item.entry_count
            ):
                raise OrganizationError("已回滚 source inventory 漂移。")
            statuses[index] = "rolled_back"
            continue
        inventory = inventory_path(destination)
        if inventory.sha256 != item.inventory_sha256 or inventory.entry_count != item.entry_count:
            raise OrganizationError("rollback source inventory 漂移。")
        if _has_symlink_ancestor(root, source.parent):
            raise OrganizationError("rollback 路径不得经过 symlink。")
        reverse_indexes.append(index)
    _atomic_write_json(
        journal_path,
        _journal_value(
            plan,
            state="rolling_back",
            statuses=statuses,
            created_at=created_at,
        ),
    )
    for index in reverse_indexes:
        item = moves[index]
        source = _path_from_relative(root, item.source, "rollback destination")
        destination = _path_from_relative(root, item.destination or "", "rollback source")
        source.parent.mkdir(parents=True, exist_ok=True)
        if os.path.lexists(source):
            raise OrganizationError("rollback destination 已存在。")
        before = inventory_path(destination)
        _rename_no_replace(destination, source)
        _fsync_directory(destination.parent)
        _fsync_directory(source.parent)
        if inventory_path(source) != before:
            raise OrganizationError("rollback rename 后 inventory 漂移。")
        statuses[index] = "rolled_back"
        _atomic_write_json(
            journal_path,
            _journal_value(
                plan,
                state="rolling_back",
                statuses=statuses,
                created_at=created_at,
            ),
        )
    statuses = ["rolled_back" if status == "applied" else status for status in statuses]
    _atomic_write_json(
        journal_path,
        _journal_value(
            plan,
            state="rolled_back",
            statuses=statuses,
            created_at=created_at,
        ),
    )


def diagnostics_run_ids(output_root: Path) -> list[str]:
    """Return only immediate UUID run directories awaiting diagnostics organization."""
    analysis_root = output_root / "diagnostics/run-analysis"
    if not analysis_root.is_dir():
        return []
    result: list[str] = []
    for path in analysis_root.iterdir():
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            result.append(_safe_uuid(path.name, "diagnostics run_id"))
        except OrganizationError:
            continue
    return sorted(result)


async def read_diagnostic_mapping(
    database_url: str,
    run_ids: Sequence[str],
) -> dict[str, tuple[str, str]]:
    """Read filename and started_at from agent_runs using a read-only transaction."""
    import asyncpg  # type: ignore[import-untyped]

    if not run_ids:
        return {}
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction(readonly=True):
            rows = await connection.fetch(
                "SELECT id::text AS run_id, input->>'filename' AS filename, started_at "
                "FROM agent_runs WHERE id::text = ANY($1::text[])",
                list(run_ids),
            )
    finally:
        await connection.close()
    mapping: dict[str, tuple[str, str]] = {}
    for row in rows:
        started_at = row["started_at"]
        if (
            not isinstance(started_at, datetime)
            or started_at.tzinfo is None
            or started_at.utcoffset() is None
        ):
            raise OrganizationError("agent_runs.started_at 必须带时区。")
        try:
            source_slug = safe_png_name_slug(row["filename"])
        except (TypeError, ValueError) as exc:
            raise OrganizationError("agent_runs filename 无法生成安全 slug。") from exc
        mapping[str(row["run_id"])] = (
            source_slug,
            started_at.astimezone(SHANGHAI).date().isoformat(),
        )
    return mapping


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    parser.add_argument("--journal", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", type=Path, metavar="JOURNAL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run dry-run planning, apply, or rollback."""
    load_dotenv()
    args = _parser().parse_args(argv)
    if args.rollback is not None:
        rollback_journal(args.rollback)
        return 0
    run_ids = diagnostics_run_ids(args.output_root)
    database_url = args.database_url or os.getenv("DATABASE_URL")
    if run_ids and not database_url:
        raise SystemExit("DATABASE_URL is required for diagnostics run mapping.")
    mapping = (
        asyncio.run(read_diagnostic_mapping(database_url, run_ids))
        if database_url and run_ids
        else {}
    )
    plan = build_plan(args.output_root, diagnostic_mapping=mapping)
    sys.stdout.write(
        json.dumps(plan.json_value(), ensure_ascii=False, sort_keys=True, indent=2)
        + "\n"
    )
    if args.apply:
        journal = args.journal or args.output_root / ".organize-legacy-output.json"
        apply_plan(plan, journal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
