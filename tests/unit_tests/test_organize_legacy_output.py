from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.organize_legacy_output import (
    OrganizationError,
    apply_plan,
    build_plan,
    inventory_path,
    rollback_journal,
)

LAB_ID = "daeaef6d-4564-4005-8f06-379e43cb5a70"
DIAGNOSTIC_RUN_ID = "238e6570-6567-4b37-b1ba-7f2d5cf9300b"


def _write(path: Path, data: bytes = b"data") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _fixture_output(tmp_path: Path) -> tuple[Path, dict[str, tuple[str, str]]]:
    root = tmp_path / "output"
    lab = root / "node-lab/service" / LAB_ID
    _write(lab / "payload.bin", b"node-lab")
    _write(
        lab / "run.json",
        (
            json.dumps(
                {
                    "schema_version": "node_lab_run_v1",
                    "lab_run_id": LAB_ID,
                    "created_at": "2026-07-27T23:30:00Z",
                    "pipeline_id": "scene_mvp",
                }
            )
            + "\n"
        ).encode(),
    )
    _write(root / "png-to-shader-rollout-private/.run-index/run.json", b"{}\n")
    _write(root / "playwright/node-lab-ui.png", b"node-lab-png")
    _write(root / "playwright/node_lab_k3_acceptance.py", b"pass\n")
    visual = root / "playwright/direct-node-timeline.png"
    _write(visual, b"visual")
    timestamp = datetime(
        2026,
        7,
        29,
        12,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).timestamp()
    os.utime(visual, (timestamp, timestamp))
    _write(
        root / "diagnostics/run-analysis" / DIAGNOSTIC_RUN_ID / "report.json",
        b"{}\n",
    )
    (root / "benchmarks/report").mkdir(parents=True)
    (root / "black-hole-preprocessing-20260731/results").mkdir(parents=True)
    return root, {DIAGNOSTIC_RUN_ID: ("玻璃-图标", "2026-07-30")}


def _moves_by_source(plan: object) -> dict[str, object]:
    return {
        item.source: item
        for item in plan.items  # type: ignore[attr-defined]
        if item.action == "move"
    }


def test_build_plan_classifies_all_rules_without_mutating_sources(
    tmp_path: Path,
) -> None:
    root, mapping = _fixture_output(tmp_path)

    plan = build_plan(root, diagnostic_mapping=mapping)
    moves = _moves_by_source(plan)

    assert moves[f"node-lab/service/{LAB_ID}"].destination == (
        f"legacy/node-lab/2026-07-28/scene_mvp/{LAB_ID}"
    )
    assert moves["png-to-shader-rollout-private"].destination == (
        "legacy/png-to-shader-rollout-private"
    )
    assert moves["playwright/node-lab-ui.png"].destination == (
        "legacy/node-lab/2026-07-27/visual-acceptance/node-lab-ui.png"
    )
    assert moves["playwright/node_lab_k3_acceptance.py"].destination == (
        "legacy/node-lab/2026-07-27/visual-acceptance/"
        "node_lab_k3_acceptance.py"
    )
    assert moves["playwright/direct-node-timeline.png"].destination == (
        "visual-acceptance/direct-node-timeline/2026-07-29/"
        "direct-node-timeline.png"
    )
    assert moves[f"diagnostics/run-analysis/{DIAGNOSTIC_RUN_ID}"].destination == (
        f"diagnostics/run-analysis/玻璃-图标/2026-07-30/{DIAGNOSTIC_RUN_ID}"
    )
    structured = {
        item.source
        for item in plan.items
        if item.action == "already_structured"
    }
    assert structured == {"benchmarks", "black-hole-preprocessing-20260731"}
    assert (root / f"node-lab/service/{LAB_ID}").is_dir()
    assert not (root / "legacy").exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", "node_lab_run_v2", "schema_version"),
        ("lab_run_id", DIAGNOSTIC_RUN_ID, "目录名不一致"),
        ("created_at", "2026-07-27", "带时区"),
        ("pipeline_id", "../pipeline", "pipeline_id"),
    ],
)
def test_build_plan_rejects_invalid_node_lab_run_metadata(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    root, mapping = _fixture_output(tmp_path)
    manifest_path = root / f"node-lab/service/{LAB_ID}/run.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(OrganizationError, match=message):
        build_plan(root, diagnostic_mapping=mapping)


def test_apply_and_rollback_preserve_every_inventory(
    tmp_path: Path,
) -> None:
    root, mapping = _fixture_output(tmp_path)
    plan = build_plan(root, diagnostic_mapping=mapping)
    moves = [item for item in plan.items if item.action == "move"]
    expected = {item.source: item.inventory_sha256 for item in moves}
    journal = tmp_path / "journal.json"

    apply_plan(plan, journal)

    for item in moves:
        assert not (root / item.source).exists()
        assert inventory_path(root / str(item.destination)).sha256 == expected[item.source]
    applied = json.loads(journal.read_text())
    assert applied["state"] == "applied"
    assert {move["status"] for move in applied["moves"]} == {"applied"}
    assert (root / "node-lab/service").is_dir()
    assert (root / "playwright").is_dir()

    rollback_journal(journal)

    for item in moves:
        assert inventory_path(root / item.source).sha256 == expected[item.source]
        assert not (root / str(item.destination)).exists()
    rolled_back = json.loads(journal.read_text())
    assert rolled_back["state"] == "rolled_back"
    assert {move["status"] for move in rolled_back["moves"]} == {"rolled_back"}


def test_apply_global_preflight_refuses_overwrite_before_any_move(
    tmp_path: Path,
) -> None:
    root, mapping = _fixture_output(tmp_path)
    plan = build_plan(root, diagnostic_mapping=mapping)
    moves = [item for item in plan.items if item.action == "move"]
    blocked = root / str(moves[-1].destination)
    _write(blocked, b"existing")
    journal = tmp_path / "journal.json"

    with pytest.raises(OrganizationError, match="目标已存在"):
        apply_plan(plan, journal)

    assert all((root / item.source).exists() for item in moves)
    assert blocked.read_bytes() == b"existing"
    assert not journal.exists()


def test_apply_rejects_inventory_drift_before_creating_journal(
    tmp_path: Path,
) -> None:
    root, mapping = _fixture_output(tmp_path)
    plan = build_plan(root, diagnostic_mapping=mapping)
    changed = root / "playwright/direct-node-timeline.png"
    changed.write_bytes(b"changed")
    journal = tmp_path / "journal.json"

    with pytest.raises(OrganizationError, match="inventory 漂移"):
        apply_plan(plan, journal)

    assert changed.read_bytes() == b"changed"
    assert not journal.exists()


def test_rollback_global_preflight_refuses_source_conflict_before_any_move(
    tmp_path: Path,
) -> None:
    root, mapping = _fixture_output(tmp_path)
    plan = build_plan(root, diagnostic_mapping=mapping)
    moves = [item for item in plan.items if item.action == "move"]
    journal = tmp_path / "journal.json"
    apply_plan(plan, journal)
    conflicted = next(item for item in moves if item.source.endswith(".png"))
    _write(root / conflicted.source, b"conflict")

    with pytest.raises(OrganizationError, match="同时存在"):
        rollback_journal(journal)

    assert (root / conflicted.source).read_bytes() == b"conflict"
    assert all((root / str(item.destination)).exists() for item in moves)


def test_missing_diagnostics_mapping_holds_apply(tmp_path: Path) -> None:
    root, _ = _fixture_output(tmp_path)
    plan = build_plan(root, diagnostic_mapping={})

    assert any(
        item.action == "hold" and item.category == "diagnostics"
        for item in plan.items
    )
    with pytest.raises(OrganizationError, match="hold"):
        apply_plan(plan, tmp_path / "journal.json")
