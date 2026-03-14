"""Nova Forge Competition Validator — Amazon Nova AI Hackathon readiness checks.

Validates submission requirements: Nova model usage, README, demo, license,
Docker, tests, and dependencies. Each check returns a CompetitionCheck with
pass/fail, detail, and fix suggestion.

Usage:
    from forge_competition import CompetitionValidator
    validator = CompetitionValidator(Path("."))
    checks = validator.run_all()
    for check in checks:
        print(f"{'✓' if check.passed else '✗'} {check.name}: {check.detail}")
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


__all__ = ["CompetitionValidator", "CompetitionCheck"]


@dataclass
class CompetitionCheck:
    """Result of a single competition validation check."""
    name: str
    passed: bool
    detail: str
    fix_suggestion: str = ""


class CompetitionValidator:
    """Run competition readiness checks against a Nova Forge project."""

    def __init__(self, project_path: Path):
        self.project_path = Path(project_path).resolve()

    def run_all(self) -> list[CompetitionCheck]:
        """Run all competition checks and return results."""
        return [
            self._check_nova_model(),
            self._check_readme(),
            self._check_demo(),
            self._check_license(),
            self._check_bedrock_integration(),
            self._check_dockerfile(),
            self._check_requirements(),
            self._check_tests(),
        ]

    def _check_nova_model(self) -> CompetitionCheck:
        """Check that project uses Amazon Nova model."""
        patterns = ["bedrock", "nova", "amazon.nova"]
        found_in = []
        for py_file in self.project_path.glob("*.py"):
            try:
                content = py_file.read_text()
                for p in patterns:
                    if p in content.lower():
                        found_in.append(py_file.name)
                        break
            except Exception:
                pass

        if found_in:
            return CompetitionCheck(
                name="Amazon Nova Model",
                passed=True,
                detail=f"Nova references in: {', '.join(found_in[:5])}",
            )
        return CompetitionCheck(
            name="Amazon Nova Model",
            passed=False,
            detail="No Nova/Bedrock references found in Python files",
            fix_suggestion="Ensure config.py references bedrock/us.amazon.nova models",
        )

    def _check_readme(self) -> CompetitionCheck:
        """Check README.md exists and is substantial."""
        readme = self.project_path / "README.md"
        if not readme.exists():
            return CompetitionCheck(
                name="README.md",
                passed=False,
                detail="README.md not found",
                fix_suggestion="Create README.md with project description, setup, and usage",
            )
        size = readme.stat().st_size
        if size < 500:
            return CompetitionCheck(
                name="README.md",
                passed=False,
                detail=f"README.md too small ({size} bytes)",
                fix_suggestion="Expand README with architecture, setup instructions, and examples",
            )
        return CompetitionCheck(
            name="README.md",
            passed=True,
            detail=f"README.md ({size:,} bytes)",
        )

    def _check_demo(self) -> CompetitionCheck:
        """Check demo recording exists."""
        demo_paths = [
            self.project_path / "web" / "demo.cast",
            self.project_path / "web" / "demo-real.cast",
            self.project_path / "demo.cast",
        ]
        for dp in demo_paths:
            if dp.exists() and dp.stat().st_size > 100:
                return CompetitionCheck(
                    name="Demo Recording",
                    passed=True,
                    detail=f"{dp.name} ({dp.stat().st_size:,} bytes)",
                )
        return CompetitionCheck(
            name="Demo Recording",
            passed=False,
            detail="No demo.cast found",
            fix_suggestion="Record with: scripts/record-demo.sh or asciinema rec web/demo.cast",
        )

    def _check_license(self) -> CompetitionCheck:
        """Check license file exists (MIT or Apache-2.0)."""
        for name in ("LICENSE", "LICENSE.md", "LICENSE.txt"):
            lf = self.project_path / name
            if lf.exists():
                content = lf.read_text()[:200].lower()
                license_type = "MIT" if "mit" in content else "Apache" if "apache" in content else "Unknown"
                return CompetitionCheck(
                    name="License",
                    passed=True,
                    detail=f"{name} ({license_type})",
                )
        return CompetitionCheck(
            name="License",
            passed=False,
            detail="No LICENSE file found",
            fix_suggestion="Add MIT or Apache-2.0 LICENSE file",
        )

    def _check_bedrock_integration(self) -> CompetitionCheck:
        """Check boto3 is in requirements (Bedrock API dependency)."""
        req_file = self.project_path / "requirements.txt"
        if not req_file.exists():
            return CompetitionCheck(
                name="Bedrock Integration",
                passed=False,
                detail="requirements.txt not found",
                fix_suggestion="Create requirements.txt with boto3 listed",
            )
        content = req_file.read_text().lower()
        if "boto3" in content:
            return CompetitionCheck(
                name="Bedrock Integration",
                passed=True,
                detail="boto3 found in requirements.txt",
            )
        return CompetitionCheck(
            name="Bedrock Integration",
            passed=False,
            detail="boto3 not in requirements.txt",
            fix_suggestion="Add boto3 to requirements.txt for Bedrock API access",
        )

    def _check_dockerfile(self) -> CompetitionCheck:
        """Check Dockerfile exists with health check."""
        dockerfile = self.project_path / "Dockerfile"
        if not dockerfile.exists():
            return CompetitionCheck(
                name="Dockerfile",
                passed=False,
                detail="Dockerfile not found",
                fix_suggestion="Create a Dockerfile for containerized deployment",
            )
        content = dockerfile.read_text()
        has_health = "HEALTHCHECK" in content or "healthcheck" in content
        return CompetitionCheck(
            name="Dockerfile",
            passed=True,
            detail=f"Dockerfile found{' (with health check)' if has_health else ''}",
        )

    def _check_requirements(self) -> CompetitionCheck:
        """Check requirements.txt exists and lists dependencies."""
        req_file = self.project_path / "requirements.txt"
        if not req_file.exists():
            return CompetitionCheck(
                name="Requirements",
                passed=False,
                detail="requirements.txt not found",
                fix_suggestion="Create requirements.txt with pip freeze > requirements.txt",
            )
        lines = [l.strip() for l in req_file.read_text().splitlines() if l.strip() and not l.startswith("#")]
        if len(lines) < 3:
            return CompetitionCheck(
                name="Requirements",
                passed=False,
                detail=f"Only {len(lines)} dependencies listed",
                fix_suggestion="Run pip freeze > requirements.txt to capture all deps",
            )
        return CompetitionCheck(
            name="Requirements",
            passed=True,
            detail=f"{len(lines)} dependencies listed",
        )

    def _check_tests(self) -> CompetitionCheck:
        """Check tests pass."""
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", "tests/", "-x", "-q", "--tb=no"],
                cwd=str(self.project_path),
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode == 0:
                # Parse count from pytest output
                for line in result.stdout.strip().splitlines():
                    if "passed" in line:
                        return CompetitionCheck(
                            name="Tests",
                            passed=True,
                            detail=line.strip(),
                        )
                return CompetitionCheck(
                    name="Tests",
                    passed=True,
                    detail="Tests passed",
                )
            return CompetitionCheck(
                name="Tests",
                passed=False,
                detail=f"Tests failed (exit code {result.returncode})",
                fix_suggestion="Run pytest tests/ -x -v to see failures",
            )
        except subprocess.TimeoutExpired:
            return CompetitionCheck(
                name="Tests",
                passed=False,
                detail="Tests timed out (>120s)",
                fix_suggestion="Check for hanging tests",
            )
        except FileNotFoundError:
            return CompetitionCheck(
                name="Tests",
                passed=False,
                detail="pytest not found",
                fix_suggestion="pip install pytest",
            )
