from __future__ import annotations

import json
from hashlib import sha256
from multiprocessing import Queue, get_context
from pathlib import Path
from typing import Any

import pytest

from agent.app.states.png_to_shader_v2_state import BudgetVectorV2
from agent.app.states.png_to_shader_v2_state_store import (
    LocalPngToShaderV2StateStore,
    V2StateCheckpointExistsError,
    V2StateCheckpointIntegrityError,
    V2StateRevisionConflictError,
)
from tests.fixtures.png_to_shader_v2_contracts import make_state


def _delta(*, render_calls: int = 0, artifact_bytes: int = 0) -> BudgetVectorV2:
    return BudgetVectorV2(
        wall_time_ms=0,
        model_calls=0,
        model_tokens=0,
        render_calls=render_calls,
        candidate_attempts=0,
        artifact_bytes=artifact_bytes,
        cost_usd_micros=0,
    )


def _checkpoint_path(root: Path) -> Path:
    (path,) = tuple(root.glob("*.checkpoint.json"))
    return path


def _run_cas_worker(
    root: str,
    run_id: str,
    phase: str,
    results: Queue[Any],
) -> None:
    store = LocalPngToShaderV2StateStore(Path(root))
    try:
        updated = store.compare_and_swap_run(
            run_id,
            expected_run_revision=0,
            changes={"phase": phase},
        )
    except V2StateRevisionConflictError:
        results.put("conflict")
    else:
        results.put(f"committed:{updated.phase}")


def test_store_recovers_only_last_confirmed_checkpoint_and_refuses_reinitialize(
    tmp_path: Path,
) -> None:
    state = make_state()
    store = LocalPngToShaderV2StateStore(tmp_path)

    assert store.initialize(state) == state
    (tmp_path / ".orphan.checkpoint.tmp").write_bytes(b"unconfirmed")
    restarted = LocalPngToShaderV2StateStore(tmp_path)

    assert restarted.load_last_confirmed(state.run_id) == state
    with pytest.raises(V2StateCheckpointExistsError, match="已存在"):
        restarted.initialize(state)


def test_run_and_budget_cas_are_independent_and_budget_survives_restart(
    tmp_path: Path,
) -> None:
    initial = make_state()
    run_id = initial.run_id
    LocalPngToShaderV2StateStore(tmp_path).initialize(initial)

    after_reserve = LocalPngToShaderV2StateStore(tmp_path).reserve_budget(
        run_id,
        _delta(artifact_bytes=100),
        expected_budget_revision=0,
    )
    assert after_reserve.run_revision == 0
    assert after_reserve.budget_state.revision == 1
    assert after_reserve.budget_state.reserved.artifact_bytes == 100

    after_run_cas = LocalPngToShaderV2StateStore(tmp_path).compare_and_swap_run(
        run_id,
        expected_run_revision=0,
        changes={"phase": "measured"},
    )
    assert after_run_cas.run_revision == 1
    assert after_run_cas.budget_state == after_reserve.budget_state

    committed = LocalPngToShaderV2StateStore(tmp_path).commit_budget(
        run_id,
        reservation=_delta(artifact_bytes=100),
        used=_delta(artifact_bytes=80),
        expected_budget_revision=1,
    )
    assert committed.run_revision == 1
    assert committed.budget_state.revision == 2
    assert committed.budget_state.used.artifact_bytes == 80
    assert committed.budget_state.reserved.artifact_bytes == 0

    restarted = LocalPngToShaderV2StateStore(tmp_path)
    assert restarted.load_last_confirmed(run_id) == committed
    with pytest.raises(V2StateRevisionConflictError, match="budget revision"):
        restarted.reserve_budget(
            run_id,
            _delta(render_calls=1),
            expected_budget_revision=1,
        )
    with pytest.raises(V2StateRevisionConflictError, match="run_revision"):
        restarted.compare_and_swap_run(
            run_id,
            expected_run_revision=0,
            changes={"phase": "interpreted"},
        )


def test_checkpoint_rejects_digest_tamper_duplicate_keys_and_typed_state_tamper(
    tmp_path: Path,
) -> None:
    state = make_state()
    store = LocalPngToShaderV2StateStore(tmp_path)
    store.initialize(state)
    checkpoint_path = _checkpoint_path(tmp_path)
    original = checkpoint_path.read_text(encoding="utf-8")

    envelope = json.loads(original)
    envelope["checkpoint_json"] = envelope["checkpoint_json"].replace(
        '"phase":"initialized"',
        '"phase":"measured"',
    )
    checkpoint_path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(V2StateCheckpointIntegrityError, match="SHA-256 不匹配"):
        store.load_last_confirmed(state.run_id)

    duplicate_envelope = original.replace(
        '"schema_version":"local_state_checkpoint_v4"',
        '"schema_version":"local_state_checkpoint_v4",'
        '"schema_version":"local_state_checkpoint_v4"',
        1,
    )
    checkpoint_path.write_text(duplicate_envelope, encoding="utf-8")
    with pytest.raises(V2StateCheckpointIntegrityError, match="重复 JSON key"):
        store.load_last_confirmed(state.run_id)

    envelope = json.loads(original)
    duplicate_state = envelope["checkpoint_json"].replace(
        '"run_revision":0',
        '"run_revision":0,"run_revision":1',
        1,
    )
    envelope["checkpoint_json"] = duplicate_state
    envelope["checkpoint_sha256"] = sha256(duplicate_state.encode()).hexdigest()
    checkpoint_path.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(V2StateCheckpointIntegrityError, match="恢复失败"):
        store.load_last_confirmed(state.run_id)


def test_process_lock_allows_exactly_one_run_revision_winner(tmp_path: Path) -> None:
    state = make_state()
    LocalPngToShaderV2StateStore(tmp_path).initialize(state)
    context = get_context("fork")
    results = context.Queue()
    processes = (
        context.Process(
            target=_run_cas_worker,
            args=(str(tmp_path), state.run_id, "measured", results),
        ),
        context.Process(
            target=_run_cas_worker,
            args=(str(tmp_path), state.run_id, "interpreted", results),
        ),
    )
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)
        assert process.exitcode == 0

    outcomes = sorted((results.get(timeout=1), results.get(timeout=1)))
    assert outcomes[0].startswith("committed:")
    assert outcomes[1] == "conflict"
    assert LocalPngToShaderV2StateStore(tmp_path).load_last_confirmed(
        state.run_id
    ).run_revision == 1
