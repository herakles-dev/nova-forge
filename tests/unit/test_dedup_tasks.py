"""Tests for task deduplication and blocked_by parsing in forge_orchestrator.py."""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from forge_orchestrator import ForgeOrchestrator


class TestDedupTasks:
    """Test ForgeOrchestrator._dedup_tasks() merges overlapping file ownership."""

    def test_no_overlap_unchanged(self):
        tasks = [
            {"subject": "Create models", "files": ["models.py"], "risk": "low"},
            {"subject": "Create routes", "files": ["routes.py"], "risk": "low"},
            {"subject": "Create tests", "files": ["tests.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 3

    def test_full_overlap_merges_to_one(self):
        tasks = [
            {"subject": "Setup", "files": ["main.py", "db.py"], "risk": "low"},
            {"subject": "Add feature", "files": ["main.py"], "risk": "medium"},
            {"subject": "Add tests", "files": ["db.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 1
        assert "main.py" in result[0]["files"]
        assert "db.py" in result[0]["files"]
        assert result[0]["risk"] == "medium"  # highest risk preserved

    def test_partial_overlap_groups_correctly(self):
        tasks = [
            {"subject": "A", "files": ["a.py"], "risk": "low"},
            {"subject": "B", "files": ["a.py", "b.py"], "risk": "low"},
            {"subject": "C", "files": ["c.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 2
        # A and B merged (share a.py), C stays separate
        merged = next(t for t in result if "c.py" not in t["files"])
        assert "a.py" in merged["files"]
        assert "b.py" in merged["files"]

    def test_empty_input(self):
        assert ForgeOrchestrator._dedup_tasks([]) == []

    def test_single_task(self):
        tasks = [{"subject": "Only task", "files": ["main.py"], "risk": "low"}]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 1

    def test_no_files_field(self):
        tasks = [
            {"subject": "A", "risk": "low"},
            {"subject": "B", "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 2

    def test_descriptions_combined(self):
        tasks = [
            {"subject": "A", "description": "Do A", "files": ["x.py"], "risk": "low"},
            {"subject": "B", "description": "Do B", "files": ["x.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 1
        assert "Do A" in result[0]["description"]
        assert "Do B" in result[0]["description"]

    def test_transitive_overlap(self):
        """A shares with B, B shares with C -> all three merge."""
        tasks = [
            {"subject": "A", "files": ["a.py", "shared1.py"], "risk": "low"},
            {"subject": "B", "files": ["shared1.py", "shared2.py"], "risk": "low"},
            {"subject": "C", "files": ["shared2.py", "c.py"], "risk": "low"},
            {"subject": "D", "files": ["d.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 2  # A+B+C merged, D separate

    def test_files_deduped_in_merge(self):
        tasks = [
            {"subject": "A", "files": ["x.py", "y.py"], "risk": "low"},
            {"subject": "B", "files": ["x.py", "z.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 1
        # x.py should appear only once
        assert result[0]["files"].count("x.py") == 1
        assert set(result[0]["files"]) == {"x.py", "y.py", "z.py"}

    def test_risk_escalation(self):
        tasks = [
            {"subject": "A", "files": ["x.py"], "risk": "low"},
            {"subject": "B", "files": ["x.py"], "risk": "high"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert result[0]["risk"] == "high"

    def test_empty_files_list_no_overlap(self):
        """Tasks with empty files lists should not overlap with each other."""
        tasks = [
            {"subject": "A", "files": [], "risk": "low"},
            {"subject": "B", "files": [], "risk": "low"},
            {"subject": "C", "files": ["c.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 3

    def test_mixed_empty_and_nonempty_files(self):
        """Task with empty files list should not merge with tasks having files."""
        tasks = [
            {"subject": "Setup", "files": [], "risk": "low"},
            {"subject": "Build", "files": ["main.py"], "risk": "low"},
            {"subject": "Test", "files": ["main.py", "test.py"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # Setup stays separate (empty files); Build+Test merge (share main.py)
        assert len(result) == 2
        merged = next(t for t in result if "main.py" in t.get("files", []))
        assert "test.py" in merged["files"]

    def test_large_task_set_performance(self):
        """Dedup should handle 50+ tasks without errors."""
        tasks = []
        for i in range(50):
            tasks.append({
                "subject": f"Task {i}",
                "files": [f"file_{i}.py", f"file_{i+1}.py"],
                "risk": "low",
            })
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # All tasks share a chain of files, so they should merge into one
        assert len(result) == 1
        # Verify all files are present
        all_files = set(result[0]["files"])
        assert len(all_files) == 51  # file_0 through file_50

    def test_real_world_todo_app(self):
        """Reproduces the Bug #5 scenario from manual testing."""
        tasks = [
            {"subject": "Setup", "files": ["main.py", "db.py", "notes.db"], "risk": "low"},
            {"subject": "DB Schema", "files": ["db.py", "notes.db"], "risk": "low"},
            {"subject": "Create cmd", "files": ["main.py", "db.py"], "risk": "low"},
            {"subject": "Read cmd", "files": ["main.py", "db.py"], "risk": "low"},
            {"subject": "Update cmd", "files": ["main.py", "db.py"], "risk": "low"},
            {"subject": "Delete cmd", "files": ["main.py", "db.py"], "risk": "low"},
            {"subject": "List cmd", "files": ["main.py", "db.py"], "risk": "low"},
            {"subject": "Error handling", "files": ["main.py", "db.py"], "risk": "medium"},
            {"subject": "Tests", "files": ["tests.py", "db.py", "main.py"], "risk": "low"},
            {"subject": "README", "files": ["README.md"], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # All tasks sharing main.py/db.py should merge, README stays separate
        assert len(result) == 2
        merged = next(t for t in result if "README.md" not in t["files"])
        assert merged["risk"] == "medium"  # highest risk preserved


class TestDedupBlockedByRemapping:
    """Test that _dedup_tasks properly remaps blocked_by indices after merging."""

    def test_blocked_by_remapped_after_merge(self):
        """When tasks merge, downstream blocked_by indices are remapped correctly."""
        tasks = [
            {"subject": "A", "files": ["a.py"], "blocked_by": [], "risk": "low"},
            {"subject": "B", "files": ["b.py"], "blocked_by": [0], "risk": "low"},
            {"subject": "C", "files": ["c.py", "d.py"], "blocked_by": [1], "risk": "low"},
            {"subject": "D", "files": ["d.py", "e.py"], "blocked_by": [0, 1], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # C+D merge (share d.py): [A(0), B(1), C+D(2)]
        assert len(result) == 3
        # B's blocked_by: old [0] → new [0] (A is still index 0)
        assert sorted(result[1].get("blocked_by", [])) == [0]
        # C+D merged: union of old C's [1] + old D's [0,1] → remapped [0, 1]
        assert sorted(result[2].get("blocked_by", [])) == [0, 1]

    def test_non_merged_task_remapped(self):
        """Non-merged task's blocked_by remaps when earlier tasks merge."""
        tasks = [
            {"subject": "A", "files": ["a.py"], "blocked_by": [], "risk": "low"},
            {"subject": "B", "files": ["b.py"], "blocked_by": [0], "risk": "low"},
            {"subject": "C", "files": ["c.py", "d.py"], "blocked_by": [1], "risk": "low"},
            {"subject": "D", "files": ["d.py", "e.py"], "blocked_by": [0, 1], "risk": "low"},
            {"subject": "E", "files": ["f.py"], "blocked_by": [0, 1, 2, 3], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # C+D merge: [A(0), B(1), C+D(2), E(3)]
        assert len(result) == 4
        # E's blocked_by: old [0,1,2,3] → new [0,1,2] (old 2 and 3 both map to 2)
        assert sorted(result[3].get("blocked_by", [])) == [0, 1, 2]

    def test_merged_task_inherits_deps(self):
        """Merged tasks inherit the union of all constituents' dependencies."""
        tasks = [
            {"subject": "A", "files": ["a.py"], "blocked_by": [], "risk": "low"},
            {"subject": "B", "files": ["b.py"], "blocked_by": [], "risk": "low"},
            {"subject": "C", "files": ["c.py", "shared.py"], "blocked_by": [0], "risk": "low"},
            {"subject": "D", "files": ["shared.py", "d.py"], "blocked_by": [1], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # C+D merge (share shared.py): [A(0), B(1), C+D(2)]
        assert len(result) == 3
        # C+D's blocked_by: union of C's [0] + D's [1] → [0, 1]
        assert sorted(result[2].get("blocked_by", [])) == [0, 1]

    def test_self_reference_removed(self):
        """When tasks that depend on each other merge, self-references are dropped."""
        tasks = [
            {"subject": "A", "files": ["shared.py", "a.py"], "blocked_by": [], "risk": "low"},
            {"subject": "B", "files": ["shared.py", "b.py"], "blocked_by": [0], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        # A+B merge (share shared.py): [A+B(0)]
        assert len(result) == 1
        # No self-reference: old B had blocked_by [0] pointing to A, but they merged
        assert result[0].get("blocked_by", []) == []

    def test_no_overlap_preserves_blocked_by(self):
        """When no merging occurs, blocked_by is preserved unchanged."""
        tasks = [
            {"subject": "A", "files": ["a.py"], "blocked_by": [], "risk": "low"},
            {"subject": "B", "files": ["b.py"], "blocked_by": [0], "risk": "low"},
            {"subject": "C", "files": ["c.py"], "blocked_by": [0, 1], "risk": "low"},
        ]
        result = ForgeOrchestrator._dedup_tasks(tasks)
        assert len(result) == 3
        assert result[0].get("blocked_by", []) == []
        assert result[1].get("blocked_by", []) == [0]
        assert sorted(result[2].get("blocked_by", [])) == [0, 1]


class TestBlockedByParsing:
    """Test robust blocked_by parsing for different LLM output formats."""

    @pytest.fixture
    def tmp_project(self, tmp_path):
        from config import init_forge_dir
        init_forge_dir(tmp_path)
        return tmp_path

    def _run_plan_load(self, tmp_project, tasks_json_data):
        """Helper: write tasks.json and run the orchestrator's load logic."""
        tasks_json = tmp_project / "tasks.json"
        tasks_json.write_text(json.dumps(tasks_json_data))
        (tmp_project / "spec.md").write_text("# Test Spec\n")

        from forge_tasks import TaskStore
        from config import ForgeProject
        project = ForgeProject(root=tmp_project)
        store = TaskStore(project.tasks_file)

        # Replicate the orchestrator's task loading logic
        tasks_data = ForgeOrchestrator._dedup_tasks(tasks_json_data)
        subject_index = {}
        for idx, td in enumerate(tasks_data):
            subj = td.get("subject", "").lower().strip()
            if subj:
                subject_index[subj] = idx

        for i, t in enumerate(tasks_data):
            blocked = t.get("blocked_by", [])
            blocked_str = None
            if blocked:
                resolved = []
                for b in blocked:
                    if isinstance(b, int):
                        task_id = b + 1
                        if task_id <= i:
                            resolved.append(str(task_id))
                    elif isinstance(b, str):
                        try:
                            idx = int(b)
                            task_id = idx + 1
                            if task_id <= i:
                                resolved.append(str(task_id))
                        except ValueError:
                            key = b.lower().strip()
                            if key in subject_index and subject_index[key] < i:
                                resolved.append(str(subject_index[key] + 1))
                blocked_str = resolved or None
            store.create(
                subject=t.get("subject", f"Task {i+1}"),
                description=t.get("description", ""),
                metadata={"project": "test", "sprint": "sprint-01", "risk": "low"},
                blocked_by=blocked_str,
            )
        return store

    def test_integer_blocked_by(self, tmp_project):
        tasks = [
            {"subject": "First", "files": ["a.py"], "blocked_by": []},
            {"subject": "Second", "files": ["b.py"], "blocked_by": [0]},
        ]
        store = self._run_plan_load(tmp_project, tasks)
        all_tasks = store.list()
        assert len(all_tasks) == 2
        assert all_tasks[1].blocked_by == ["1"]

    def test_string_integer_blocked_by(self, tmp_project):
        tasks = [
            {"subject": "First", "files": ["a.py"], "blocked_by": []},
            {"subject": "Second", "files": ["b.py"], "blocked_by": ["0"]},
        ]
        store = self._run_plan_load(tmp_project, tasks)
        all_tasks = store.list()
        assert all_tasks[1].blocked_by == ["1"]

    def test_string_name_blocked_by(self, tmp_project):
        """LLM uses task subject names instead of indices."""
        tasks = [
            {"subject": "Create database", "files": ["db.py"], "blocked_by": []},
            {"subject": "Create routes", "files": ["routes.py"], "blocked_by": ["Create database"]},
        ]
        store = self._run_plan_load(tmp_project, tasks)
        all_tasks = store.list()
        assert all_tasks[1].blocked_by == ["1"]

    def test_unresolvable_blocked_by_ignored(self, tmp_project):
        """Unknown references are silently dropped."""
        tasks = [
            {"subject": "First", "files": ["a.py"], "blocked_by": []},
            {"subject": "Second", "files": ["b.py"], "blocked_by": ["nonexistent"]},
        ]
        store = self._run_plan_load(tmp_project, tasks)
        all_tasks = store.list()
        assert all_tasks[1].blocked_by is None or all_tasks[1].blocked_by == []

    def test_self_reference_blocked_by_ignored(self, tmp_project):
        """Task can't depend on itself."""
        tasks = [
            {"subject": "First", "files": ["a.py"], "blocked_by": [0]},
        ]
        store = self._run_plan_load(tmp_project, tasks)
        all_tasks = store.list()
        # Task 0 referencing itself (index 0 → task_id 1, but task_id 1 is not <= 0)
        assert all_tasks[0].blocked_by is None or all_tasks[0].blocked_by == []

    def test_mixed_blocked_by_formats(self, tmp_project):
        """LLM mixes integers, strings, and names."""
        tasks = [
            {"subject": "Setup", "files": ["a.py"], "blocked_by": []},
            {"subject": "Build", "files": ["b.py"], "blocked_by": []},
            {"subject": "Test", "files": ["c.py"], "blocked_by": [0, "1", "Setup"]},
        ]
        store = self._run_plan_load(tmp_project, tasks)
        all_tasks = store.list()
        # All three references should resolve to tasks 1 and 2
        assert "1" in all_tasks[2].blocked_by
        assert "2" in all_tasks[2].blocked_by
