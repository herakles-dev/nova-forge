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
    """in_progress → pending is not an allowed transition and raises ValueError."""
    store = make_store(tmp_path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        task = store.create("Task B", "desc", metadata=valid_meta())
    store.update(task.id, status="in_progress")

    with pytest.raises(ValueError, match="Cannot transition"):
        store.update(task.id, status="pending")


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
