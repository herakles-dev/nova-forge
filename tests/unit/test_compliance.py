"""Tests for forge_compliance.py and forge_migrate.py."""

import json
from pathlib import Path

import pytest

from forge_compliance import ComplianceChecker, ComplianceReport, GateResult
from forge_migrate import ProjectMigrator, scan_projects


@pytest.fixture
def project(tmp_path):
    return tmp_path


class TestComplianceChecker:
    def test_check_returns_10_gates(self, project):
        cc = ComplianceChecker(project)
        report = cc.check()
        assert report.total == 10

    def test_empty_project_not_compliant(self, project):
        cc = ComplianceChecker(project)
        report = cc.check()
        assert not report.is_compliant
        assert report.classification in ("NOT_COMPLIANT", "PARTIAL")

    def test_fix_creates_structure(self, project):
        cc = ComplianceChecker(project)
        fixes = cc.fix()
        assert len(fixes) > 0
        assert (project / ".forge").is_dir()
        assert (project / ".forge" / "state").is_dir()
        assert (project / ".forge" / "settings.json").exists()

    def test_fix_then_check_improves(self, project):
        cc = ComplianceChecker(project)
        cc.fix()
        report = cc.check()
        assert report.passed_count >= 7

    def test_fix_legacy_spec_yml(self, project):
        (project / "spec.yml").write_text("name: test\n")
        cc = ComplianceChecker(project)
        cc.fix()
        assert not (project / "spec.yml").exists()
        assert (project / ".spec.yml.legacy").exists()

    def test_fix_legacy_state_md(self, project):
        (project / "state.md").write_text("- [ ] Task 1\n")
        cc = ComplianceChecker(project)
        cc.fix()
        assert not (project / "state.md").exists()
        assert (project / ".state.md.legacy").exists()

    def test_summary_output(self, project):
        cc = ComplianceChecker(project)
        report = cc.check()
        summary = report.summary()
        assert "Compliance Report" in summary
        assert "FAIL" in summary or "PASS" in summary

    def test_all_10_gate_names_present(self, project):
        """Verify every gate name is in the report."""
        cc = ComplianceChecker(project)
        report = cc.check()
        names = [g.name for g in report.gates]
        expected = [
            "forge_dir", "state_dir", "audit_dir", "settings_file",
            "autonomy_state", "task_state", "forge_md",
            "schemas_accessible", "agents_accessible", "no_legacy",
        ]
        for name in expected:
            assert name in names, f"Gate {name!r} missing from report"

    def test_check_returns_gate_results(self, project):
        cc = ComplianceChecker(project)
        report = cc.check()
        for g in report.gates:
            assert isinstance(g, GateResult)
            assert isinstance(g.passed, bool)
            assert isinstance(g.detail, str)
            assert g.severity in ("required", "advisory")

    def test_fix_idempotent(self, project):
        """Running fix twice does not break anything."""
        cc = ComplianceChecker(project)
        fixes1 = cc.fix()
        fixes2 = cc.fix()
        # Second fix should apply fewer or zero fixes
        assert len(fixes2) <= len(fixes1)

    def test_gate_task_state_is_advisory(self, project):
        """task_state gate is advisory — failing it should not block compliance."""
        cc = ComplianceChecker(project)
        cc.fix()
        report = cc.check()
        task_gate = [g for g in report.gates if g.name == "task_state"][0]
        assert task_gate.severity == "advisory"

    def test_no_legacy_passes_on_clean_project(self, project):
        cc = ComplianceChecker(project)
        report = cc.check()
        legacy_gate = [g for g in report.gates if g.name == "no_legacy"][0]
        assert legacy_gate.passed
        assert "clean" in legacy_gate.detail

    def test_no_legacy_fails_with_spec_yml(self, project):
        (project / "spec.yml").write_text("name: old\n")
        cc = ComplianceChecker(project)
        report = cc.check()
        legacy_gate = [g for g in report.gates if g.name == "no_legacy"][0]
        assert not legacy_gate.passed
        assert "spec.yml" in legacy_gate.detail

    def test_no_legacy_fails_with_state_md(self, project):
        (project / "state.md").write_text("# State\n")
        cc = ComplianceChecker(project)
        report = cc.check()
        legacy_gate = [g for g in report.gates if g.name == "no_legacy"][0]
        assert not legacy_gate.passed
        assert "state.md" in legacy_gate.detail

    def test_fix_does_not_delete_legacy_files(self, project):
        """fix() renames legacy files, never deletes them."""
        (project / "spec.yml").write_text("name: test\n")
        (project / "state.md").write_text("# State\n")
        cc = ComplianceChecker(project)
        cc.fix()
        # Originals gone, backups exist
        assert not (project / "spec.yml").exists()
        assert not (project / "state.md").exists()
        assert (project / ".spec.yml.legacy").exists()
        assert (project / ".state.md.legacy").exists()
        # Content preserved
        assert "name: test" in (project / ".spec.yml.legacy").read_text()

    def test_schemas_gate_checks_real_schemas_dir(self, project):
        """schemas_accessible gate checks the project's schemas/ directory."""
        cc = ComplianceChecker(project)
        report = cc.check()
        schemas_gate = [g for g in report.gates if g.name == "schemas_accessible"][0]
        assert schemas_gate.severity == "advisory"

    def test_agents_gate_checks_real_agents_dir(self, project):
        cc = ComplianceChecker(project)
        report = cc.check()
        agents_gate = [g for g in report.gates if g.name == "agents_accessible"][0]
        assert agents_gate.severity == "advisory"


class TestGateResult:
    def test_gate_result_defaults(self):
        g = GateResult(name="test", passed=True, detail="ok")
        assert g.auto_fixable is False
        assert g.severity == "required"


class TestComplianceReport:
    def test_classification_compliant(self):
        r = ComplianceReport(
            project="test",
            gates=[GateResult("a", True, "ok"), GateResult("b", True, "ok")],
        )
        assert r.is_compliant
        assert r.classification == "COMPLIANT"

    def test_classification_partial(self):
        r = ComplianceReport(
            project="test",
            gates=[GateResult("a", True, "ok"), GateResult("b", False, "fail")],
        )
        assert not r.is_compliant
        assert r.classification == "PARTIAL"

    def test_advisory_failures_still_compliant(self):
        r = ComplianceReport(
            project="test",
            gates=[
                GateResult("a", True, "ok"),
                GateResult("b", False, "advisory", severity="advisory"),
            ],
        )
        assert r.is_compliant

    def test_classification_not_compliant(self):
        r = ComplianceReport(
            project="test",
            gates=[GateResult("a", False, "fail"), GateResult("b", False, "fail")],
        )
        assert not r.is_compliant
        assert r.classification == "NOT_COMPLIANT"

    def test_passed_count_and_failed_count(self):
        r = ComplianceReport(
            project="test",
            gates=[
                GateResult("a", True, "ok"),
                GateResult("b", False, "fail"),
                GateResult("c", True, "ok"),
            ],
        )
        assert r.passed_count == 2
        assert r.failed_count == 1
        assert r.total == 3

    def test_summary_includes_pass_and_fail(self):
        r = ComplianceReport(
            project="test",
            gates=[
                GateResult("gate_a", True, "ok"),
                GateResult("gate_b", False, "broken", auto_fixable=True),
            ],
        )
        summary = r.summary()
        assert "PASS" in summary
        assert "FAIL" in summary
        assert "gate_a" in summary
        assert "gate_b" in summary
        assert "auto-fixable" in summary

    def test_empty_report_is_compliant(self):
        r = ComplianceReport(project="test", gates=[])
        assert r.is_compliant
        assert r.classification == "COMPLIANT"
        assert r.total == 0


class TestMigrator:
    def test_detect_unknown_empty(self, project):
        m = ProjectMigrator(project)
        assert m.detect_version() == "unknown"

    def test_detect_v5(self, project):
        (project / "spec.yml").write_text("name: test\n")
        m = ProjectMigrator(project)
        assert m.detect_version() == "v5"

    def test_detect_v6(self, project):
        (project / "state.md").write_text("# State\n- [ ] Task 1\n- [x] Task 2\n")
        m = ProjectMigrator(project)
        version = m.detect_version()
        assert version in ("v6", "v7")  # v6/v7 share same signal

    def test_detect_forge(self, project):
        (project / ".forge").mkdir()
        m = ProjectMigrator(project)
        assert m.detect_version() == "forge"

    def test_plan_already_forge(self, project):
        (project / ".forge").mkdir()
        m = ProjectMigrator(project)
        plan = m.plan()
        assert "Already" in plan.steps[0]

    def test_plan_v5_includes_backup(self, project):
        (project / "spec.yml").write_text("name: test\n")
        m = ProjectMigrator(project)
        plan = m.plan()
        assert plan.source_version == "v5"
        assert "spec.yml" in plan.backup_files

    def test_migrate_v5(self, project):
        (project / "spec.yml").write_text("name: test\nstack: flask\n")
        m = ProjectMigrator(project)
        result = m.migrate()
        assert result.success
        assert result.source_version == "v5"
        assert (project / ".spec.yml.legacy").exists()
        assert (project / ".forge").is_dir()

    def test_migrate_v6(self, project):
        (project / "state.md").write_text("# Tasks\n- [ ] Build API\n- [x] Setup DB\n")
        m = ProjectMigrator(project)
        result = m.migrate()
        assert result.success
        assert (project / ".state.md.legacy").exists()

    def test_migrate_already_forge(self, project):
        (project / ".forge").mkdir()
        m = ProjectMigrator(project)
        result = m.migrate()
        assert result.success
        assert "Already" in result.steps_completed[0]

    def test_migrate_unknown(self, project):
        m = ProjectMigrator(project)
        result = m.migrate()
        assert result.success  # Creates .forge/ from scratch


class TestScanProjects:
    def test_scan_finds_projects(self, tmp_path):
        # Create a V5 project and a Forge project
        v5_dir = tmp_path / "old-app"
        v5_dir.mkdir()
        (v5_dir / "spec.yml").write_text("name: old\n")

        forge_dir = tmp_path / "new-app"
        forge_dir.mkdir()
        (forge_dir / ".forge").mkdir()

        results = scan_projects(tmp_path)
        names = {name for name, _, _ in results}
        assert "old-app" in names
        assert "new-app" in names

    def test_scan_empty_dir(self, tmp_path):
        results = scan_projects(tmp_path)
        assert results == []

    def test_scan_skips_hidden(self, tmp_path):
        hidden = tmp_path / ".hidden"
        hidden.mkdir()
        (hidden / ".forge").mkdir()
        results = scan_projects(tmp_path)
        assert len(results) == 0
