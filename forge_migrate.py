"""Nova Forge Migration — detect legacy project versions and upgrade to Forge.

Ports V11's ``migrate-to-v11`` script. Detects V5/V6/V7/V8/V10/V11 projects
and migrates them to Forge-managed projects with .forge/ directory.

Usage::

    from forge_migrate import ProjectMigrator

    migrator = ProjectMigrator(project_root=Path("./myapp"))
    version = migrator.detect_version()
    plan = migrator.plan()
    result = migrator.migrate()
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import FORGE_DIR_NAME
from forge_compliance import ComplianceChecker

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class MigrationPlan:
    """Plan for migrating a project to Forge."""
    source_version: str
    steps: list[str] = field(default_factory=list)
    backup_files: list[str] = field(default_factory=list)
    risk: str = "low"  # "low", "medium"


@dataclass
class MigrationResult:
    """Result of a migration operation."""
    success: bool = False
    source_version: str = ""
    steps_completed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ── Version detection signals ────────────────────────────────────────────────

_VERSION_SIGNALS = {
    "v5": [
        lambda p: (p / "spec.yml").exists(),
    ],
    "v6": [
        lambda p: (p / "state.md").exists() and _has_checkbox(p / "state.md"),
    ],
    "v7": [
        lambda p: (p / "state.md").exists() and _has_checkbox(p / "state.md"),
    ],
    "v8": [
        lambda p: _settings_has_hooks(p, "v8"),
    ],
    "v10": [
        lambda p: _settings_has_hooks(p, "v10"),
    ],
    "v11": [
        lambda p: _settings_has_hooks(p, "v11"),
    ],
    "forge": [
        lambda p: (p / FORGE_DIR_NAME).is_dir(),
    ],
}


def _has_checkbox(path: Path) -> bool:
    """Check if a file has markdown checkboxes (V6/V7 signal)."""
    if not path.exists():
        return False
    content = path.read_text(errors="replace")
    return "- [ ]" in content or "- [x]" in content


def _settings_has_hooks(project_root: Path, version: str) -> bool:
    """Check if .claude/settings.json references hooks from a specific version."""
    settings = project_root / ".claude" / "settings.json"
    if not settings.exists():
        return False
    try:
        content = settings.read_text()
        return f"{version}/hooks" in content or f"{version}/" in content
    except OSError:
        return False


# ── ProjectMigrator ──────────────────────────────────────────────────────────

class ProjectMigrator:
    """Detect legacy versions and migrate to Forge-managed projects."""

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()

    def detect_version(self) -> str:
        """Detect the current project version.

        Returns one of: "forge", "v11", "v10", "v8", "v7", "v6", "v5", "unknown"
        """
        # Check in order of newest → oldest
        for version in ["forge", "v11", "v10", "v8", "v7", "v6", "v5"]:
            signals = _VERSION_SIGNALS.get(version, [])
            for check in signals:
                try:
                    if check(self.project_root):
                        return version
                except Exception:
                    continue
        return "unknown"

    def plan(self, dry_run: bool = True) -> MigrationPlan:
        """Generate a migration plan without executing it."""
        version = self.detect_version()

        if version == "forge":
            return MigrationPlan(
                source_version="forge",
                steps=["Already a Forge project — no migration needed"],
            )

        plan = MigrationPlan(source_version=version)

        # Step 1: Create .forge/
        if not (self.project_root / FORGE_DIR_NAME).is_dir():
            plan.steps.append("Create .forge/ directory structure")

        # Version-specific migration steps
        if version == "v5":
            plan.steps.append("Archive spec.yml → .spec.yml.legacy")
            plan.backup_files.append("spec.yml")
            if (self.project_root / "spec.yml").exists():
                plan.steps.append("Convert spec.yml content to FORGE.md")

        elif version in ("v6", "v7"):
            plan.steps.append("Archive state.md → .state.md.legacy")
            plan.backup_files.append("state.md")
            plan.steps.append("Extract task subjects from state.md checkboxes")

        elif version in ("v8", "v10"):
            plan.steps.append("Backup .claude/settings.json → .claude/settings.json.v-backup")
            plan.backup_files.append(".claude/settings.json")
            plan.risk = "medium"

        elif version == "v11":
            plan.steps.append("Backup .claude/settings.json if exists")
            plan.steps.append("Create .forge/ parallel to .claude/")

        # Common steps
        plan.steps.append("Initialize autonomy at A0")
        plan.steps.append("Create settings.json")
        plan.steps.append("Create FORGE.md")
        plan.steps.append("Run compliance check")

        return plan

    def migrate(self) -> MigrationResult:
        """Execute the migration."""
        version = self.detect_version()
        result = MigrationResult(source_version=version)

        if version == "forge":
            result.success = True
            result.steps_completed.append("Already a Forge project")
            return result

        try:
            # Version-specific pre-migration
            if version == "v5":
                self._migrate_v5(result)
            elif version in ("v6", "v7"):
                self._migrate_v6_v7(result)
            elif version in ("v8", "v10"):
                self._migrate_v8_v10(result, version)
            elif version == "v11":
                self._migrate_v11(result)

            # Common: run compliance auto-fix
            cc = ComplianceChecker(self.project_root)
            fixes = cc.fix()
            result.steps_completed.extend(fixes)

            # Verify
            report = cc.check()
            if report.is_compliant:
                result.success = True
                result.steps_completed.append(
                    f"Compliance: {report.passed_count}/{report.total} gates pass"
                )
            else:
                failed = [g.name for g in report.gates if not g.passed and g.severity == "required"]
                if failed:
                    result.errors.append(f"Failed required gates: {', '.join(failed)}")
                else:
                    result.success = True  # Only advisory failures
                    result.steps_completed.append(
                        f"Compliance: {report.passed_count}/{report.total} (advisory failures only)"
                    )

        except Exception as exc:
            result.errors.append(f"Migration failed: {exc}")
            logger.exception("Migration error for %s", self.project_root)

        return result

    # ── Version-specific migrations ──────────────────────────────────────────

    def _migrate_v5(self, result: MigrationResult) -> None:
        """Migrate V5 project (spec.yml based)."""
        spec_yml = self.project_root / "spec.yml"
        if spec_yml.exists():
            backup = self.project_root / ".spec.yml.legacy"
            shutil.copy2(spec_yml, backup)
            result.steps_completed.append(f"Backed up spec.yml → .spec.yml.legacy")

    def _migrate_v6_v7(self, result: MigrationResult) -> None:
        """Migrate V6/V7 project (state.md checkbox based)."""
        state_md = self.project_root / "state.md"
        if state_md.exists():
            backup = self.project_root / ".state.md.legacy"
            shutil.copy2(state_md, backup)
            result.steps_completed.append(f"Backed up state.md → .state.md.legacy")

            # Extract task subjects from checkboxes
            content = state_md.read_text(errors="replace")
            tasks = []
            for line in content.split("\n"):
                line = line.strip()
                if line.startswith("- [ ]") or line.startswith("- [x]"):
                    task_text = line[5:].strip()
                    if task_text:
                        tasks.append(task_text)

            if tasks:
                result.steps_completed.append(
                    f"Extracted {len(tasks)} task subjects from state.md"
                )

    def _migrate_v8_v10(self, result: MigrationResult, version: str) -> None:
        """Migrate V8/V10 project (hook-based)."""
        settings_file = self.project_root / ".claude" / "settings.json"
        if settings_file.exists():
            backup = self.project_root / ".claude" / f"settings.json.{version}-backup"
            shutil.copy2(settings_file, backup)
            result.steps_completed.append(
                f"Backed up .claude/settings.json → settings.json.{version}-backup"
            )

    def _migrate_v11(self, result: MigrationResult) -> None:
        """Migrate V11 project (create .forge/ alongside .claude/)."""
        settings_file = self.project_root / ".claude" / "settings.json"
        if settings_file.exists():
            backup = self.project_root / ".claude" / "settings.json.v11-backup"
            if not backup.exists():
                shutil.copy2(settings_file, backup)
                result.steps_completed.append(
                    "Backed up .claude/settings.json → settings.json.v11-backup"
                )
        result.steps_completed.append("Creating .forge/ parallel to .claude/")


# ── Scan helper ──────────────────────────────────────────────────────────────

def scan_projects(root: Path) -> list[tuple[str, str, Path]]:
    """Scan a directory for projects and detect their versions.

    Returns list of (project_name, version, path).
    """
    results: list[tuple[str, str, Path]] = []
    if not root.is_dir():
        return results

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("."):
            continue

        migrator = ProjectMigrator(child)
        version = migrator.detect_version()
        if version != "unknown":
            results.append((child.name, version, child))

    return results
