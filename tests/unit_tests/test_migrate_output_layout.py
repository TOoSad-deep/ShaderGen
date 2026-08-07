import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from scripts.migrate_output_layout import (
    LedgerRecord,
    apply_migration_plan,
    apply_private_migration_plan,
    build_migration_plan,
    build_private_migration_plan,
    rollback_journal,
)

RUN_ID = "11111111-1111-4111-8111-111111111111"
PROJECT_ID = "22222222-2222-4222-8222-222222222222"
ATTEMPT_ID = str(uuid5(UUID(RUN_ID), "direct_glsl_layerplan_v1:0"))


def _legacy_public_run(root: Path) -> Path:
    index = root / ".run-index"
    index.mkdir(parents=True)
    (index / f"{RUN_ID}.json").write_text(
        json.dumps(
            {"schema_version": 1, "project_id": PROJECT_ID, "run_id": RUN_ID}
        ),
        encoding="utf-8",
    )
    final = root / PROJECT_ID / RUN_ID / "final"
    final.mkdir(parents=True)
    for name, value in {
        "render.png": b"png", "metrics.json": b"{}\n", "manifest.json": b"{}\n"
    }.items():
        (final / name).write_bytes(value)
    return root


def _records(*, status: str = "succeeded") -> dict[str, LedgerRecord]:
    return {
        RUN_ID: LedgerRecord(
            filename="玻璃 图标.png",
            started_at=datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc),
            status=status,
            project_id=PROJECT_ID,
        )
    }


def test_plan_uses_injected_ledger_filename_and_shanghai_date(tmp_path: Path) -> None:
    plans = build_migration_plan(_legacy_public_run(tmp_path / "public"), _records())

    assert len(plans) == 1
    plan = plans[0]
    assert plan.action == "migrate"
    assert plan.new_relative_run_root == f"玻璃-图标/2026-08-07/{RUN_ID}"
    assert plan.output_date == "2026-08-07"


def test_plan_holds_running_or_missing_ledger_records(tmp_path: Path) -> None:
    root = _legacy_public_run(tmp_path / "public")

    assert build_migration_plan(root, _records(status="running"))[0].reason == "ledger_running"
    assert build_migration_plan(root, {})[0].reason == "missing_ledger_record"


def test_public_plan_holds_non_success_or_project_mismatch(tmp_path: Path) -> None:
    root = _legacy_public_run(tmp_path / "public")
    failed = _records(status="failed")
    mismatch = _records()
    mismatch[RUN_ID] = LedgerRecord(
        filename="玻璃 图标.png",
        started_at=datetime(2026, 8, 7, 1, 0, tzinfo=timezone.utc),
        status="succeeded",
        project_id="33333333-3333-4333-8333-333333333333",
    )

    assert build_migration_plan(root, failed)[0].reason == "ledger_not_succeeded"
    assert build_migration_plan(root, mismatch)[0].reason == "ledger_project_mismatch"


def test_apply_copies_then_v2_indexes_and_rollback_keeps_new_target(tmp_path: Path) -> None:
    root = _legacy_public_run(tmp_path / "public")
    plans = build_migration_plan(root, _records())
    journal = tmp_path / "journal.jsonl"

    apply_migration_plan(root, plans, journal)

    target = root / "玻璃-图标" / "2026-08-07" / RUN_ID
    assert (target / "final/render.png").read_bytes() == b"png"
    assert (root / PROJECT_ID / RUN_ID / "final/render.png").read_bytes() == b"png"
    assert json.loads((root / ".run-index" / f"{RUN_ID}.json").read_text()) == {
        "project_id": PROJECT_ID,
        "relative_run_root": f"玻璃-图标/2026-08-07/{RUN_ID}",
        "run_id": RUN_ID,
        "schema_version": 2,
    }

    rollback_journal(journal)

    assert json.loads((root / ".run-index" / f"{RUN_ID}.json").read_text())["schema_version"] == 1
    assert target.is_dir()


def test_plan_skips_existing_v2_index(tmp_path: Path) -> None:
    root = _legacy_public_run(tmp_path / "public")
    (root / ".run-index" / f"{RUN_ID}.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "project_id": PROJECT_ID,
                "run_id": RUN_ID,
                "relative_run_root": f"sample/2026-08-07/{RUN_ID}",
            }
        ),
        encoding="utf-8",
    )

    plan = build_migration_plan(root, _records())[0]

    assert (plan.action, plan.reason) == ("skip", "already_v2")


def test_apply_fails_closed_when_target_already_exists(tmp_path: Path) -> None:
    root = _legacy_public_run(tmp_path / "public")
    plans = build_migration_plan(root, _records())
    target = root / "玻璃-图标" / "2026-08-07" / RUN_ID
    target.mkdir(parents=True)

    with pytest.raises(ValueError, match="目标已存在"):
        apply_migration_plan(root, plans, tmp_path / "journal.jsonl")

    assert json.loads((root / ".run-index" / f"{RUN_ID}.json").read_text())["schema_version"] == 1


def _legacy_private_run(root: Path, *, with_manifest: bool = True) -> Path:
    index = root / ".run-index"
    index.mkdir(parents=True)
    (index / f"{ATTEMPT_ID}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "project_id": PROJECT_ID,
                "run_id": ATTEMPT_ID,
            }
        ),
        encoding="utf-8",
    )
    run = root / PROJECT_ID / ATTEMPT_ID
    run.mkdir(parents=True)
    if with_manifest:
        private = run / "private"
        private.mkdir()
        (private / "failure-summary.json").write_text(
            json.dumps(
                {
                    "schema_version": "direct_attempt_failure_v1",
                    "parent_run_id": RUN_ID,
                    "attempt_id": ATTEMPT_ID,
                    "attempt_index": 0,
                    "status": "failed",
                }
            ),
            encoding="utf-8",
        )
    return root


def test_private_plan_uses_parent_ledger_and_supports_empty_attempt(
    tmp_path: Path,
) -> None:
    manifest_root = _legacy_private_run(tmp_path / "with-manifest")
    empty_root = _legacy_private_run(tmp_path / "empty", with_manifest=False)

    manifest_plan = build_private_migration_plan(manifest_root, _records())[0]
    empty_plan = build_private_migration_plan(empty_root, _records())[0]

    expected = f"玻璃-图标/2026-08-07/{RUN_ID}/{ATTEMPT_ID}"
    assert manifest_plan.new_relative_run_root == expected
    assert manifest_plan.parent_run_id == RUN_ID
    assert manifest_plan.copy_scope == "full_tree"
    assert empty_plan.new_relative_run_root == expected


def test_private_plan_holds_active_parent(tmp_path: Path) -> None:
    root = _legacy_private_run(tmp_path / "private")

    plan = build_private_migration_plan(root, _records(status="running"))[0]

    assert (plan.action, plan.reason) == ("hold", "parent_ledger_running")


def test_private_apply_copies_full_tree_and_rollback_restores_index(
    tmp_path: Path,
) -> None:
    root = _legacy_private_run(tmp_path / "private")
    plans = build_private_migration_plan(root, _records())
    journal = tmp_path / "private-journal.jsonl"

    apply_private_migration_plan(root, plans, journal)

    target = root / "玻璃-图标" / "2026-08-07" / RUN_ID / ATTEMPT_ID
    assert (target / "private/failure-summary.json").is_file()
    assert (root / PROJECT_ID / ATTEMPT_ID / "private/failure-summary.json").is_file()
    assert json.loads((root / ".run-index" / f"{ATTEMPT_ID}.json").read_text()) == {
        "project_id": PROJECT_ID,
        "relative_run_root": f"玻璃-图标/2026-08-07/{RUN_ID}/{ATTEMPT_ID}",
        "run_id": ATTEMPT_ID,
        "schema_version": 2,
    }

    rollback_journal(journal)

    assert json.loads((root / ".run-index" / f"{ATTEMPT_ID}.json").read_text())[
        "schema_version"
    ] == 1
    assert target.is_dir()


def test_rollback_rejects_migrated_content_drift(tmp_path: Path) -> None:
    root = _legacy_public_run(tmp_path / "public")
    plans = build_migration_plan(root, _records())
    journal = tmp_path / "journal.jsonl"
    apply_migration_plan(root, plans, journal)
    target = root / "玻璃-图标" / "2026-08-07" / RUN_ID
    (target / "final/render.png").write_bytes(b"changed")

    with pytest.raises(ValueError, match="迁移目标内容漂移"):
        rollback_journal(journal)

    assert json.loads((root / ".run-index" / f"{RUN_ID}.json").read_text())[
        "schema_version"
    ] == 2
