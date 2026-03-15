"""Unit tests for forge_tasks.TaskStore."""
import pytest
import warnings
from pathlib import Path

from forge_tasks import TaskStore, Task, STATUS_TRANSITIONS


# ── Helpers ──────────────────────────────────────────────────────────────────

def make_store(tmp_path: Path) -> TaskStore:
    """Create a TaskStore backed by a temp file."""
    tasks_file = tmp_path / "tasks.json"
    return TaskStore(tasks_file)


def valid_meta(risk="low"):
    return {"project": "test-proj", "sprint": "s1", "risk": risk}


# ── Tests ────────────────────────────────────────────────────────────────────

def test_create_task(tmp_path):
    """Creates a task, verifies ID and fields are set correctly."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        task = store.create(
            "Implement auth",
            "JWT login endpoint",
            metadata=valid_meta(),
        )
    assert task.id == "1"
    assert task.subject == "Implement auth"
    assert task.description == "JWT login endpoint"
    assert task.status == "pending"
    assert task.metadata["project"] == "test-proj"
    assert task.created_at != ""
    assert task.updated_at != ""


def test_update_status(tmp_path):
    """pending → in_progress → completed transitions succeed."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        task = store.create("Task A", "desc", metadata=valid_meta())

    updated = store.update(task.id, status="in_progress")
    assert updated.status == "in_progress"

    completed = store.update(task.id, status="completed")
    assert completed.status == "completed"


def test_invalid_transition(tmp_path):
    """completed → pending is not an allowed transition and raises ValueError."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        task = store.create("Task B", "desc", metadata=valid_meta())
    store.update(task.id, status="in_progress")
    store.update(task.id, status="completed")

    with pytest.raises(ValueError, match="Cannot transition"):
        store.update(task.id, status="pending")


def test_in_progress_to_pending_allowed(tmp_path):
    """in_progress → pending is allowed for interrupted build retries."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        task = store.create("Task C", "desc", metadata=valid_meta())
    store.update(task.id, status="in_progress")
    updated = store.update(task.id, status="pending")
    assert updated.status == "pending"


def test_list_filter_by_status(tmp_path):
    """Create 3 tasks, filter by status returns only matching ones."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t1 = store.create("Task 1", "d", metadata=valid_meta())
        t2 = store.create("Task 2", "d", metadata=valid_meta())
        t3 = store.create("Task 3", "d", metadata=valid_meta())

    store.update(t1.id, status="in_progress")
    store.update(t2.id, status="in_progress")
    # t3 remains pending

    in_progress = store.list(status="in_progress")
    pending = store.list(status="pending")

    assert len(in_progress) == 2
    assert len(pending) == 1
    assert pending[0].id == t3.id


def test_wave_computation_linear(tmp_path):
    """A→B→C chain should produce 3 sequential waves."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])
        c = store.create("Task C", "d", metadata=valid_meta(), blocked_by=[b.id])

    waves = store.compute_waves()
    assert len(waves) == 3
    assert waves[0][0].id == a.id
    assert waves[1][0].id == b.id
    assert waves[2][0].id == c.id


def test_wave_computation_parallel(tmp_path):
    """A and B with no dependencies should both appear in wave 0."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta())

    waves = store.compute_waves()
    assert len(waves) == 1
    ids_in_wave0 = {t.id for t in waves[0]}
    assert a.id in ids_in_wave0
    assert b.id in ids_in_wave0


def test_wave_computation_cycle_detection(tmp_path):
    """A→B→A cycle raises ValueError."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])

    # Manually wire A to be blocked by B to create a cycle
    # Use update with blocks/blocked_by to create the cycle
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        store.update(a.id, blocked_by=[b.id])

    with pytest.raises(ValueError, match="[Cc]ircular"):
        store.compute_waves()


def test_wave_computation_completed_deps_resolved(tmp_path):
    """Completed deps are treated as resolved; dependent tasks appear in wave 0."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])

    # Complete task A
    store.update(a.id, status="in_progress")
    store.update(a.id, status="completed")

    waves = store.compute_waves()
    # Only B should be in waves (A is completed); B's dep is resolved
    assert len(waves) == 1
    assert waves[0][0].id == b.id


def test_wave_computation_failed_deps_resolved(tmp_path):
    """Failed deps are treated as resolved — dependent tasks still schedulable."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])

    store.update(a.id, status="in_progress")
    store.update(a.id, status="failed")

    waves = store.compute_waves()
    assert len(waves) == 1
    assert waves[0][0].id == b.id


def test_wave_computation_blocked_dep_blocks_downstream(tmp_path):
    """When a dep is 'blocked' (not active and not resolved), downstream is stuck.

    Kahn's algorithm treats this as a circular dependency because B has
    an unresolvable in-degree: A is blocked (not resolved, not active),
    so B's in-degree never reaches 0. The algorithm correctly raises ValueError.
    """
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])

    # Move A to blocked
    store.update(a.id, status="blocked")
    # B depends on a blocked task — its in-degree is bumped but never decremented
    with pytest.raises(ValueError, match="Circular dependency"):
        store.compute_waves()


def test_wave_computation_diamond_dependency(tmp_path):
    """Diamond: A -> B, A -> C, B -> D, C -> D produces 3 waves."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("A", "d", metadata=valid_meta())
        b = store.create("B", "d", metadata=valid_meta(), blocked_by=[a.id])
        c = store.create("C", "d", metadata=valid_meta(), blocked_by=[a.id])
        d = store.create("D", "d", metadata=valid_meta(), blocked_by=[b.id, c.id])

    waves = store.compute_waves()
    assert len(waves) == 3
    assert waves[0][0].id == a.id
    wave1_ids = {t.id for t in waves[1]}
    assert wave1_ids == {b.id, c.id}
    assert waves[2][0].id == d.id


def test_failed_to_pending_retry_allowed(tmp_path):
    """failed -> pending is allowed for retry."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = store.create("Retryable", "d", metadata=valid_meta())
    store.update(t.id, status="in_progress")
    store.update(t.id, status="failed")
    retried = store.update(t.id, status="pending")
    assert retried.status == "pending"


def test_blocked_to_pending_allowed(tmp_path):
    """blocked -> pending is allowed when the blocker resolves."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = store.create("Blocked task", "d", metadata=valid_meta())
    store.update(t.id, status="blocked")
    unblocked = store.update(t.id, status="pending")
    assert unblocked.status == "pending"


def test_blocked_to_in_progress_allowed(tmp_path):
    """blocked -> in_progress is allowed (skip pending on resume)."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = store.create("Task", "d", metadata=valid_meta())
    store.update(t.id, status="blocked")
    updated = store.update(t.id, status="in_progress")
    assert updated.status == "in_progress"


def test_update_nonexistent_task_raises(tmp_path):
    """Updating a task that doesn't exist raises KeyError."""
    store = make_store(tmp_path)
    with pytest.raises(KeyError, match="not found"):
        store.update("999", status="in_progress")


def test_update_unknown_field_raises(tmp_path):
    """Passing an unrecognized field to update raises ValueError."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        t = store.create("Task", "d", metadata=valid_meta())
    with pytest.raises(ValueError, match="Unknown update fields"):
        store.update(t.id, priority="high")


def test_delete_task(tmp_path):
    """Deleting a task removes it and cleans up dependency refs in other tasks."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])

    # Verify the bidirectional link was created
    assert b.id in store.get(a.id).blocks

    deleted = store.delete(a.id)
    assert deleted is True
    assert store.get(a.id) is None

    # Task B's blocked_by should now be empty
    b_after = store.get(b.id)
    assert a.id not in b_after.blocked_by


def test_delete_nonexistent_returns_false(tmp_path):
    """Deleting a nonexistent task returns False."""
    store = make_store(tmp_path)
    assert store.delete("999") is False


def test_checkpoint_and_restore(tmp_path):
    """Checkpoint / restore round-trips the task state."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        a = store.create("Task A", "d", metadata=valid_meta())
        b = store.create("Task B", "d", metadata=valid_meta(), blocked_by=[a.id])

    snapshot = store.checkpoint()
    assert snapshot["next_id"] == 3
    assert len(snapshot["tasks"]) == 2

    # Restore into a fresh store
    store2 = make_store(tmp_path / "other")
    store2.restore(snapshot)
    assert len(store2.list()) == 2
    assert store2.get(a.id).subject == "Task A"
    assert store2.get(b.id).blocked_by == [a.id]
