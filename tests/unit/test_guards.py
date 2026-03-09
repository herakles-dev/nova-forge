"""Unit tests for forge_guards: RiskClassifier, PathSandbox, AutonomyManager."""
import pytest
from pathlib import Path

from forge_guards import (
    RiskClassifier,
    RiskLevel,
    PathSandbox,
    SandboxViolation,
    AutonomyManager,
)


# ── RiskClassifier ────────────────────────────────────────────────────────────

def test_risk_high_rm_rf():
    """rm -rf / is classified as HIGH risk."""
    clf = RiskClassifier()
    assert clf.classify("Bash", command="rm -rf /") == RiskLevel.HIGH


def test_risk_high_force_push():
    """git push --force is classified as HIGH risk."""
    clf = RiskClassifier()
    assert clf.classify("Bash", command="git push --force origin main") == RiskLevel.HIGH


def test_risk_high_drop_table():
    """DROP TABLE users is classified as HIGH risk."""
    clf = RiskClassifier()
    assert clf.classify("Bash", command="DROP TABLE users") == RiskLevel.HIGH


def test_risk_medium_write():
    """Write tool (no dangerous command) is MEDIUM risk."""
    clf = RiskClassifier()
    result = clf.classify("Write", command="", file_path="/home/hercules/nova-forge/foo.py")
    assert result == RiskLevel.MEDIUM


def test_risk_low_default():
    """A benign command like ls is LOW risk."""
    clf = RiskClassifier()
    assert clf.classify("Bash", command="ls -la") == RiskLevel.LOW


def test_risk_command_resolution(tmp_path):
    """Absolute path to rm resolves to 'rm' and is still HIGH if using -rf."""
    clf = RiskClassifier()
    # /bin/rm -rf / should resolve through PATH and be caught
    result = clf.classify("Bash", command="/bin/rm -rf /tmp/testdir")
    assert result == RiskLevel.HIGH


def test_risk_interpreter_wrapping():
    """bash -c 'rm -rf /' is HIGH due to interpreter wrapping detection."""
    clf = RiskClassifier()
    result = clf.classify("Bash", command="bash -c 'rm -rf /'")
    assert result == RiskLevel.HIGH


def test_sandbox_write_allowed(tmp_path):
    """Writing inside project root does not raise."""
    sandbox = PathSandbox(tmp_path)
    inside = tmp_path / "subdir" / "file.txt"
    # Should not raise
    sandbox.validate_write(inside)


def test_sandbox_write_blocked(tmp_path):
    """Writing to /etc/ raises SandboxViolation."""
    sandbox = PathSandbox(tmp_path)
    with pytest.raises(SandboxViolation):
        sandbox.validate_write("/etc/passwd")


def test_sandbox_read_denied(tmp_path):
    """Reading from ~/.secrets/ raises SandboxViolation."""
    sandbox = PathSandbox(tmp_path)
    home = Path.home()
    secrets_file = home / ".secrets" / "hercules.env"
    with pytest.raises(SandboxViolation):
        sandbox.validate_read(secrets_file)


def test_autonomy_a0_blocks_medium(tmp_path):
    """A0 (default) blocks MEDIUM risk operations."""
    autonomy_file = tmp_path / "autonomy.json"
    mgr = AutonomyManager(autonomy_file)
    # Default level is 0 (Manual)
    result = mgr.check("Write", RiskLevel.MEDIUM, file_path="/some/file.py")
    assert result.allowed is False
    assert "A0" in result.reason or "Manual" in result.reason


def test_autonomy_low_always_allowed(tmp_path):
    """LOW risk is always allowed regardless of autonomy level."""
    autonomy_file = tmp_path / "autonomy.json"
    mgr = AutonomyManager(autonomy_file)
    result = mgr.check("Read", RiskLevel.LOW, file_path="/some/file.py")
    assert result.allowed is True
