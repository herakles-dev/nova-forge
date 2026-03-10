"""Nova Forge Compliance Checker — 10-gate V11 compliance validation.

Ports V11's ``v11-compliance-check`` script to pure Python. Each gate
validates one aspect of project readiness. Auto-fixable gates can be
repaired with :meth:`ComplianceChecker.fix`.

Usage::

    from forge_compliance import ComplianceChecker

    cc = ComplianceChecker(project_root=Path("./myapp"))
    report = cc.check()
    print(report.summary())
    cc.fix()                 # auto-fix what we can
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config import ForgeProject, init_forge_dir, FORGE_DIR_NAME
from forge_session import SessionManager, AutonomyState

logger = logging.getLogger(__name__)


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class GateResult:
    """Result of a single compliance gate."""
    name: str
    passed: bool
    detail: str
    auto_fixable: bool = False
    severity: str = "required"  # "required" or "advisory"


@dataclass
class ComplianceReport:
    """Full compliance report for a project."""
    project: str
    gates: list[GateResult] = field(default_factory=list)
    checked_at: str = ""

    @property
    def passed_count(self) -> int:
        return sum(1 for g in self.gates if g.passed)

    @property
    def failed_count(self) -> int:
        return sum(1 for g in self.gates if not g.passed)

    @property
    def total(self) -> int:
        return len(self.gates)

    @property
    def is_compliant(self) -> bool:
        required = [g for g in self.gates if g.severity == "required"]
        return all(g.passed for g in required)

    @property
    def classification(self) -> str:
        if self.is_compliant:
            return "COMPLIANT"
        elif self.passed_count > 0:
            return "PARTIAL"
        return "NOT_COMPLIANT"

    def summary(self) -> str:
        lines = [
            f"Compliance Report: {self.project}",
            f"Status: {self.classification} ({self.passed_count}/{self.total} gates)",
            "",
        ]
        for g in self.gates:
            marker = "PASS" if g.passed else "FAIL"
            fix_hint = " (auto-fixable)" if not g.passed and g.auto_fixable else ""
            sev = " [advisory]" if g.severity == "advisory" else ""
            lines.append(f"  [{marker}] {g.name}{sev}: {g.detail}{fix_hint}")
        return "\n".join(lines)


# ── ComplianceChecker ────────────────────────────────────────────────────────

class ComplianceChecker:
    """Run 10-gate compliance validation for a Forge project.

    Gates:
    1. forge_dir — .forge/ directory exists
    2. state_dir — .forge/state/ directory exists
    3. audit_dir — .forge/audit/ directory exists
    4. settings_file — .forge/settings.json exists
    5. autonomy_state — .forge/state/autonomy.json exists
    6. task_state — .forge/state/task-state.json exists (advisory)
    7. forge_md — .forge/FORGE.md exists
    8. schemas_accessible — schemas/ directory with JSON files
    9. agents_accessible — agents/ directory with YAML files
    10. no_legacy — no spec.yml or state.md in project root
    """

    def __init__(self, project_root: Path | str) -> None:
        self.project_root = Path(project_root).resolve()
        self.project = ForgeProject(root=self.project_root)
        self.sm = SessionManager(self.project_root)

    def check(self) -> ComplianceReport:
        """Run all 10 compliance gates."""
        report = ComplianceReport(
            project=self.project.name,
            checked_at=datetime.now(timezone.utc).isoformat(),
        )

        report.gates = [
            self._gate_forge_dir(),
            self._gate_state_dir(),
            self._gate_audit_dir(),
            self._gate_settings_file(),
            self._gate_autonomy_state(),
            self._gate_task_state(),
            self._gate_forge_md(),
            self._gate_schemas_accessible(),
            self._gate_agents_accessible(),
            self._gate_no_legacy(),
        ]

        return report

    def fix(self) -> list[str]:
        """Auto-fix all fixable gates. Returns list of fixes applied."""
        fixes: list[str] = []

        # Gate 1-3: create directories
        if not self.project.forge_dir.is_dir():
            init_forge_dir(self.project_root)
            fixes.append("Created .forge/ directory structure")
        else:
            for d in [self.project.state_dir, self.project.audit_dir,
                       self.project.artifacts_dir]:
                if not d.is_dir():
                    d.mkdir(parents=True, exist_ok=True)
                    fixes.append(f"Created {d.name}/ directory")

        # Gate 4: settings.json
        if not self.project.settings_file.exists():
            settings = {
                "hooks": {},
                "forge_version": "1.0",
                "created": datetime.now(timezone.utc).isoformat(),
            }
            self.project.settings_file.write_text(json.dumps(settings, indent=2) + "\n")
            fixes.append("Created settings.json")

        # Gate 5: autonomy state
        if not self.project.autonomy_file.exists():
            self.sm.save_autonomy(AutonomyState(level=2))
            fixes.append("Initialized autonomy at A2 (Supervised)")

        # Gate 7: FORGE.md
        if not self.project.forge_md.exists():
            self.project.forge_md.write_text(
                f"# {self.project.name}\n\nManaged by Nova Forge.\n"
            )
            fixes.append("Created FORGE.md")

        # Gate 10: legacy cleanup (rename, don't delete)
        spec_yml = self.project_root / "spec.yml"
        state_md = self.project_root / "state.md"
        if spec_yml.exists():
            backup = self.project_root / ".spec.yml.legacy"
            spec_yml.rename(backup)
            fixes.append(f"Renamed spec.yml → .spec.yml.legacy")
        if state_md.exists():
            backup = self.project_root / ".state.md.legacy"
            state_md.rename(backup)
            fixes.append(f"Renamed state.md → .state.md.legacy")

        return fixes

    # ── Individual gates ──────────────────────────────────────────────────────

    def _gate_forge_dir(self) -> GateResult:
        return GateResult(
            name="forge_dir",
            passed=self.project.forge_dir.is_dir(),
            detail=str(self.project.forge_dir),
            auto_fixable=True,
        )

    def _gate_state_dir(self) -> GateResult:
        return GateResult(
            name="state_dir",
            passed=self.project.state_dir.is_dir(),
            detail=str(self.project.state_dir),
            auto_fixable=True,
        )

    def _gate_audit_dir(self) -> GateResult:
        return GateResult(
            name="audit_dir",
            passed=self.project.audit_dir.is_dir(),
            detail=str(self.project.audit_dir),
            auto_fixable=True,
        )

    def _gate_settings_file(self) -> GateResult:
        return GateResult(
            name="settings_file",
            passed=self.project.settings_file.exists(),
            detail=str(self.project.settings_file),
            auto_fixable=True,
        )

    def _gate_autonomy_state(self) -> GateResult:
        return GateResult(
            name="autonomy_state",
            passed=self.project.autonomy_file.exists(),
            detail=str(self.project.autonomy_file),
            auto_fixable=True,
        )

    def _gate_task_state(self) -> GateResult:
        task_file = self.project.state_dir / "task-state.json"
        return GateResult(
            name="task_state",
            passed=task_file.exists(),
            detail=str(task_file),
            auto_fixable=False,
            severity="advisory",
        )

    def _gate_forge_md(self) -> GateResult:
        return GateResult(
            name="forge_md",
            passed=self.project.forge_md.exists(),
            detail=str(self.project.forge_md),
            auto_fixable=True,
        )

    def _gate_schemas_accessible(self) -> GateResult:
        schemas_dir = Path(__file__).parent / "schemas"
        has_schemas = schemas_dir.is_dir() and any(schemas_dir.glob("*.json"))
        return GateResult(
            name="schemas_accessible",
            passed=has_schemas,
            detail=str(schemas_dir),
            severity="advisory",
        )

    def _gate_agents_accessible(self) -> GateResult:
        agents_dir = Path(__file__).parent / "agents"
        has_agents = agents_dir.is_dir() and any(agents_dir.glob("*.yml"))
        return GateResult(
            name="agents_accessible",
            passed=has_agents,
            detail=str(agents_dir),
            severity="advisory",
        )

    def _gate_no_legacy(self) -> GateResult:
        spec_yml = self.project_root / "spec.yml"
        state_md = self.project_root / "state.md"
        has_legacy = spec_yml.exists() or state_md.exists()
        detail = "clean"
        if spec_yml.exists():
            detail = "spec.yml found (legacy V5)"
        if state_md.exists():
            detail = "state.md found (legacy V6/V7)"
        return GateResult(
            name="no_legacy",
            passed=not has_legacy,
            detail=detail,
            auto_fixable=True,
        )
