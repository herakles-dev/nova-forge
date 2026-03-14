"""Nova Forge formation definitions — 8 pre-built Agent Team patterns.

Port of V11 FORMATIONS.md to Python data structures with per-role model selection.
Formation selection uses the DAAO routing table (complexity x scope).

Usage:
    from formations import FORMATIONS, select_formation, get_formation

    # Select by task complexity
    formation = select_formation(complexity="medium", scope="large")

    # Direct lookup
    formation = get_formation("feature-impl")

    # Validate ownership
    warnings = validate_ownership(formation)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import DEFAULT_MODELS


# ── Tool policy profiles ─────────────────────────────────────────────────────

TOOL_PROFILES: dict[str, set[str]] = {
    "full":     {"read_file", "write_file", "append_file", "edit_file", "bash", "glob_files", "grep", "claim_file", "check_context"},
    "coding":   {"read_file", "write_file", "append_file", "edit_file", "bash", "glob_files", "grep", "claim_file", "check_context"},
    "testing":  {"read_file", "bash", "glob_files", "grep"},   # No write/edit
    "readonly": {"read_file", "glob_files", "grep"},            # Read-only
    "minimal":  set(),                                          # No tools
}


# ── Model constants (per-role selection) ─────────────────────────────────────

# Smart, careful judgment: planning, review, architecture, readonly analysis
_SMART_MODEL = DEFAULT_MODELS["planning"]       # bedrock/us.amazon.nova-2-lite-v1:0

# Fast, cheap execution: coding, implementation, testing
_FAST_MODEL = DEFAULT_MODELS["coding"]          # openrouter/google/gemini-2.0-flash-001


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class Role:
    """A single role within a formation."""
    name: str           # e.g. "backend-impl", "tester"
    model: str          # Full model ID e.g. "bedrock/us.amazon.nova-2-lite-v1:0"
    tool_policy: str    # Profile name: "full", "coding", "testing", "readonly", "minimal"
    ownership: dict     # {"files": [], "directories": [], "patterns": []}
    description: str = ""


@dataclass
class Formation:
    """An Agent Team formation pattern."""
    name: str               # e.g. "feature-impl"
    description: str        # When to use this formation
    roles: list[Role]       # Roles in this formation
    wave_order: list[list[str]]  # Waves of role names [[arch], [impl-1, impl-2], [tester]]
    gate_criteria: list[str]     # What must be true to pass the gate
    tool_policy_defaults: str    # Default tool policy for all roles


# ── Formation definitions ────────────────────────────────────────────────────

# Formation 1: single-file
# For 1-3 tasks touching a single file. Minimal overhead.
_SINGLE_FILE = Formation(
    name="single-file",
    description=(
        "Small, focused file edits. 1-3 tasks, single file. "
        "<2 hours total. Examples: fix navbar styling, update config variables."
    ),
    roles=[
        Role(
            name="implementer",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["*"],   # Owns whatever single file is in scope
            },
            description="Claims and edits the single target file.",
        ),
    ],
    wave_order=[["implementer"]],
    gate_criteria=[
        "Syntax check passes",
        "Unit tests pass (if defined)",
        "No breakage in dependents",
    ],
    tool_policy_defaults="coding",
)


# Formation 2: lightweight-feature
# For 4-8 tasks, single-layer (frontend-only OR backend-only). Implementer + tester in parallel.
_LIGHTWEIGHT_FEATURE = Formation(
    name="lightweight-feature",
    description=(
        "Small, single-layer features (frontend-only OR backend-only). "
        "4-8 tasks. <4 hours total. Implementer and tester work in parallel; "
        "tester tasks blocked by corresponding impl tasks. "
        "Escalate to feature-impl if both backend + frontend are needed or >8 tasks."
    ),
    roles=[
        Role(
            name="implementer",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": ["src/"],
                "patterns": ["src/**"],
            },
            description="Owns src/ source code changes.",
        ),
        Role(
            name="tester",
            model=_FAST_MODEL,
            tool_policy="testing",
            ownership={
                "files": [],
                "directories": ["tests/", "__tests__/"],
                "patterns": ["*.test.*", "*.spec.*"],
            },
            description="Owns test files; no write/edit to source. Default position is FAIL.",
        ),
    ],
    wave_order=[["implementer"], ["tester"]],
    gate_criteria=[
        "Syntax check passes",
        "Test coverage >80%",
        "No broken imports",
    ],
    tool_policy_defaults="coding",
)


# Formation 3: feature-impl
# Full-stack feature: backend + frontend implemented in parallel, then integration, then testing.
_FEATURE_IMPL = Formation(
    name="feature-impl",
    description=(
        "Adding features to an existing project. Most common formation. "
        "Backend and frontend work in parallel, then integrator, then tester. "
        "Backend/frontend create tasks for integrator describing new API endpoints."
    ),
    roles=[
        Role(
            name="backend-impl",
            model=_FAST_MODEL,
            tool_policy="full",
            ownership={
                "files": [],
                "directories": ["src/routes/", "src/services/", "src/models/"],
                "patterns": ["src/routes/**", "src/services/**", "src/models/**"],
            },
            description="Implements backend routes, services, and models.",
        ),
        Role(
            name="frontend-impl",
            model=_FAST_MODEL,
            tool_policy="full",
            ownership={
                "files": [],
                "directories": ["src/components/", "src/pages/", "src/hooks/"],
                "patterns": ["src/components/**", "src/pages/**", "src/hooks/**"],
            },
            description="Implements frontend components, pages, and hooks.",
        ),
        Role(
            name="integrator",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": ["docker-compose.yml", "nginx.conf"],
                "directories": [],
                "patterns": ["*.yml", "*.conf"],
            },
            description=(
                "Integrates backend and frontend. Claims tasks only after "
                "both backend-impl and frontend-impl complete."
            ),
        ),
        Role(
            name="tester",
            model=_FAST_MODEL,
            tool_policy="testing",
            ownership={
                "files": [],
                "directories": ["tests/", "__tests__/"],
                "patterns": ["*.test.*", "*.spec.*"],
            },
            description=(
                "Runs after integration is verified. Two-phase: JUDGE -> REPORT. "
                "Default position is FAIL — evidence must prove success."
            ),
        ),
    ],
    wave_order=[["backend-impl", "frontend-impl"], ["integrator"], ["tester"]],
    gate_criteria=[
        "Backend and frontend tasks completed",
        "Integration verified (no broken API contracts)",
        "All tests pass",
        "No TypeScript / lint errors",
    ],
    tool_policy_defaults="coding",
)


# Formation 4: new-project
# Greenfield setup: architect first, then two implementers in parallel.
_NEW_PROJECT = Formation(
    name="new-project",
    description=(
        "Greenfield project setup, scaffolding + initial implementation. "
        "Architect output unblocks implementers. "
        "Team lead reviews architecture before unblocking scaffold/DB tasks."
    ),
    roles=[
        Role(
            name="architect",
            model=_SMART_MODEL,
            tool_policy="full",
            ownership={
                "files": ["docker-compose.yml"],
                "directories": ["docs/", "config/"],
                "patterns": ["*.md", "*.yml", "config/**"],
            },
            description=(
                "Produces architecture docs, docker-compose structure, config templates. "
                "Uses smart model for careful design decisions."
            ),
        ),
        Role(
            name="impl-1",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": ["src/"],
                "patterns": ["src/**", "Dockerfile*", "*.json"],
            },
            description="Scaffolds project skeleton, build config, CI setup.",
        ),
        Role(
            name="impl-2",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": ["db/", "migrations/"],
                "patterns": ["db/**", "migrations/**", "*.sql"],
            },
            description="Implements database schema, migrations, seed data.",
        ),
    ],
    wave_order=[["architect"], ["impl-1", "impl-2"]],
    gate_criteria=[
        "Architecture document reviewed by team lead",
        "Project skeleton compiles / starts without errors",
        "Database migrations run cleanly",
    ],
    tool_policy_defaults="coding",
)


# Formation 5: bug-investigation
# Unknown root cause — three parallel investigators with distinct strategies.
_BUG_INVESTIGATION = Formation(
    name="bug-investigation",
    description=(
        "Root cause is unknown. Three parallel investigators with distinct strategies "
        "(Backward Chaining, Temporal Analysis, Isolation Testing). "
        "First to find root cause creates fix task; team lead shuts down others."
    ),
    roles=[
        Role(
            name="investigator-1",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": [],
                "patterns": [],   # Assigned per incident at runtime
            },
            description="Backward Chaining: trace call stack from error to root.",
        ),
        Role(
            name="investigator-2",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": [],
                "patterns": [],
            },
            description="Temporal Analysis: correlate logs/metrics with when bug appeared.",
        ),
        Role(
            name="investigator-3",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": [],
                "patterns": [],
            },
            description="Isolation Testing: disable components one-by-one to find culprit.",
        ),
    ],
    wave_order=[["investigator-1", "investigator-2", "investigator-3"]],
    gate_criteria=[
        "Root cause identified with evidence",
        "Fix task created and linked to investigation",
    ],
    tool_policy_defaults="coding",
)


# Formation 6: security-review
# Threat modeling + scanning in parallel, then fixing.
_SECURITY_REVIEW = Formation(
    name="security-review",
    description=(
        "Security audit, threat modeling, vulnerability assessment. "
        "Threat modeler and scanner run in parallel, then fixer. "
        "Scanner/Modeler create BLOCKER tasks for HIGH/CRITICAL findings. "
        "No parallel fixing while scanning continues (prevents race conditions)."
    ),
    roles=[
        Role(
            name="threat-modeler",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["threat-model.*", "security-findings.*"],
            },
            description=(
                "Produces threat model document, security findings. "
                "READ-ONLY — no code modifications. Smart model for careful judgment."
            ),
        ),
        Role(
            name="scanner",
            model=_SMART_MODEL,
            tool_policy="testing",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["scan-results.*", "dependency-audit.*"],
            },
            description=(
                "Runs scans, produces dependency audit. Testing profile (exec allowed, "
                "no write). Smart model for accurate judgment on severity."
            ),
        ),
        Role(
            name="fixer",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": ["src/"],
                "patterns": ["src/**"],
            },
            description=(
                "Applies source code fixes for findings. Tasks are blocked by "
                "threat-modeler and scanner completing."
            ),
        ),
    ],
    wave_order=[["threat-modeler", "scanner"], ["fixer"]],
    gate_criteria=[
        "All HIGH/CRITICAL findings have fix tasks",
        "Fixer tasks are completed or deferred with justification",
        "Dependency audit clean (no CRITICAL CVEs in production deps)",
    ],
    tool_policy_defaults="coding",
)


# Formation 7: perf-optimization
# Sequential: optimizer (subagent), then tester (subagent).
_PERF_OPTIMIZATION = Formation(
    name="perf-optimization",
    description=(
        "Performance work — profiling, optimization, regression testing. "
        "Sequential subagents (not teammates). Deep single-expert work. "
        "Team lead waits for optimizer report before launching tester."
    ),
    roles=[
        Role(
            name="optimizer",
            model=_FAST_MODEL,
            tool_policy="full",
            ownership={
                "files": [],
                "directories": ["src/"],
                "patterns": ["src/**"],
            },
            description=(
                "Profiles and optimizes. Full tool access. "
                "Fast model for iterative profiling cycles."
            ),
        ),
        Role(
            name="tester",
            model=_FAST_MODEL,
            tool_policy="testing",
            ownership={
                "files": [],
                "directories": ["tests/", "benchmarks/"],
                "patterns": ["*.test.*", "*.bench.*", "benchmarks/**"],
            },
            description="Runs regression tests after optimization. No write/edit to src.",
        ),
    ],
    wave_order=[["optimizer"], ["tester"]],
    gate_criteria=[
        "Optimizer produces profiling report",
        "Regression tests pass (no performance regressions introduced)",
        "Target metric improved (defined in task description)",
    ],
    tool_policy_defaults="full",
)


# Formation 8: code-review
# Three parallel reviewers, each using a different lens. All readonly.
_CODE_REVIEW = Formation(
    name="code-review",
    description=(
        "PR review, code quality assessment, pre-merge checks. "
        "Three reviewers in parallel: security, performance, coverage. "
        "All reviewers are READ-ONLY. Team lead synthesizes into unified report. "
        "Fix tasks created separately for implementers after review."
    ),
    roles=[
        Role(
            name="reviewer-1",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["security-findings.*"],
            },
            description=(
                "Security lens: AuthN/AuthZ, injection risks, secrets exposure, "
                "dependency vulnerabilities. Smart model for accurate threat assessment."
            ),
        ),
        Role(
            name="reviewer-2",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["perf-findings.*"],
            },
            description=(
                "Performance lens: N+1 queries, blocking I/O, memory leaks, "
                "algorithmic complexity. Smart model for careful analysis."
            ),
        ),
        Role(
            name="reviewer-3",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["coverage-findings.*"],
            },
            description=(
                "Coverage lens: test coverage gaps, missing edge cases, "
                "untested error paths. Smart model for thorough gap analysis."
            ),
        ),
    ],
    wave_order=[["reviewer-1", "reviewer-2", "reviewer-3"]],
    gate_criteria=[
        "All three reviewers have produced findings documents",
        "Team lead has synthesized into unified report",
        "VERDICT is binary: approved or rejected (never partial)",
    ],
    tool_policy_defaults="readonly",
)


# Formation 9: recovery
# Post-failure diagnosis: investigator finds root cause, fixer applies fix, validator confirms.
_RECOVERY = Formation(
    name="recovery",
    description=(
        "Post-failure diagnosis and repair. Use when a build/deploy/test has failed "
        "and the root cause needs investigation before fixing. "
        "Investigator reads logs and traces the failure, fixer applies the fix, "
        "validator runs tests to confirm the fix. "
        "Use when: a previously working feature broke, deployment failed, or tests regressed."
    ),
    roles=[
        Role(
            name="investigator",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["*.log", "*.err"],
            },
            description=(
                "Reads logs, traces, and source to identify root cause. "
                "READ-ONLY — produces a diagnosis report, not code changes. "
                "Smart model for careful failure analysis."
            ),
        ),
        Role(
            name="fixer",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": ["src/"],
                "patterns": ["src/**"],
            },
            description=(
                "Applies targeted fix based on investigator's diagnosis. "
                "Minimal changes — fix the root cause only, no refactoring."
            ),
        ),
        Role(
            name="validator",
            model=_FAST_MODEL,
            tool_policy="testing",
            ownership={
                "files": [],
                "directories": ["tests/"],
                "patterns": ["*.test.*", "*.spec.*"],
            },
            description=(
                "Runs the failing test/scenario to confirm the fix works. "
                "Default position is FAIL — evidence must prove the fix resolved the issue."
            ),
        ),
    ],
    wave_order=[["investigator"], ["fixer"], ["validator"]],
    gate_criteria=[
        "Root cause identified with evidence",
        "Fix applied with minimal changes",
        "Original failure no longer reproduces",
        "No new regressions introduced",
    ],
    tool_policy_defaults="coding",
)


# Formation 10: all-hands-planning
# Pre-build spec validation with parallel reviewers and synthesizer.
_ALL_HANDS_PLANNING = Formation(
    name="all-hands-planning",
    description=(
        "Pre-build spec review with cross-functional validation. Use for complex "
        "greenfield projects or when a first attempt failed and needs replanning. "
        "Four parallel reviewers (architecture, feasibility, security, UX) produce "
        "findings, then a synthesizer combines them into an actionable plan. "
        "Use when: starting a large project, recovering from a failed build, "
        "or validating a complex architectural decision."
    ),
    roles=[
        Role(
            name="arch-reviewer",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["arch-review.*"],
            },
            description=(
                "Reviews architecture decisions: tech stack choices, data flow, "
                "scalability, separation of concerns. Identifies structural risks."
            ),
        ),
        Role(
            name="feasibility-reviewer",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["feasibility-review.*"],
            },
            description=(
                "Reviews feasibility: can this be built with the proposed stack in the "
                "allotted time? Identifies missing dependencies, unrealistic scope, "
                "or tasks that need splitting."
            ),
        ),
        Role(
            name="security-reviewer",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["security-review.*"],
            },
            description=(
                "Reviews security implications: auth model, data protection, "
                "injection risks, secrets management. Flags BLOCKER issues."
            ),
        ),
        Role(
            name="ux-reviewer",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["ux-review.*"],
            },
            description=(
                "Reviews UX implications: user flow coherence, accessibility, "
                "responsive design requirements, error state handling."
            ),
        ),
        Role(
            name="synthesizer",
            model=_SMART_MODEL,
            tool_policy="coding",
            ownership={
                "files": ["spec.md", "tasks.json"],
                "directories": [],
                "patterns": ["spec.*", "tasks.*"],
            },
            description=(
                "Reads all four review reports and produces an updated spec.md "
                "and tasks.json incorporating reviewer feedback. Resolves conflicts "
                "between reviewers by choosing the safer/simpler option."
            ),
        ),
    ],
    wave_order=[
        ["arch-reviewer", "feasibility-reviewer", "security-reviewer", "ux-reviewer"],
        ["synthesizer"],
    ],
    gate_criteria=[
        "All four reviewers produced findings documents",
        "Synthesizer updated spec.md with reviewer feedback",
        "No unresolved BLOCKER issues",
        "Revised task plan is actionable",
    ],
    tool_policy_defaults="readonly",
)


# Formation 11: integration-check
# Post-build cross-file verification and repair.
_INTEGRATION_CHECK = Formation(
    name="integration-check",
    description=(
        "Post-build integration verification. Runs after all build tasks complete "
        "to catch cross-file mismatches that parallel agents introduce. "
        "Auditor reads ALL generated files (read-only, smart model) and produces "
        "a diagnosis listing every cross-file issue. Fixer applies targeted repairs. "
        "Verifier starts the server, hits GET /, and confirms the app works end-to-end. "
        "Use when: build completed but verification found file reference mismatches, "
        "broken routes, or missing imports between modules."
    ),
    roles=[
        Role(
            name="auditor",
            model=_SMART_MODEL,
            tool_policy="readonly",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["*"],
            },
            description=(
                "Reads ALL generated project files and identifies cross-file issues: "
                "file reference mismatches (send_static_file vs render_template vs wrong directory), "
                "broken imports between modules, routes that reference missing files, "
                "template/static directory confusion. Produces a structured diagnosis. "
                "READ-ONLY — do not modify any files."
            ),
        ),
        Role(
            name="fixer",
            model=_FAST_MODEL,
            tool_policy="coding",
            ownership={
                "files": [],
                "directories": [],
                "patterns": ["*.py", "*.html", "*.js", "*.css", "*.json"],
            },
            description=(
                "Applies targeted fixes based on auditor's diagnosis. Key rules: "
                "- render_template() is a standalone function from flask, NOT app.render_template() "
                "- Files in templates/ must be served via render_template() "
                "- Files in static/ must be served via send_static_file() or url_for('static') "
                "- Always add missing imports (e.g. from flask import render_template) "
                "- Read each file before editing to confirm the actual content "
                "Minimal changes — fix ONLY the identified issues."
            ),
        ),
        Role(
            name="verifier",
            model=_FAST_MODEL,
            tool_policy="testing",
            ownership={
                "files": [],
                "directories": [],
                "patterns": [],
            },
            description=(
                "Verifies the fixes work by running the app and testing it: "
                "1. Run bash: python3 app.py & (or equivalent) to start the server "
                "2. Run bash: curl -s -o /dev/null -w '%{http_code}' http://localhost:PORT/ "
                "3. Confirm the status code is 200 "
                "4. Kill the server process "
                "Default position is FAIL — evidence must prove the app serves correctly."
            ),
        ),
    ],
    wave_order=[["auditor"], ["fixer"], ["verifier"]],
    gate_criteria=[
        "Auditor has identified all cross-file issues",
        "Fixer has applied minimal, targeted repairs",
        "Verifier confirmed GET / returns 200",
        "No new files created — only existing files fixed",
    ],
    tool_policy_defaults="coding",
)


# ── Module-level FORMATIONS registry ────────────────────────────────────────

FORMATIONS: dict[str, Formation] = {
    "single-file":         _SINGLE_FILE,
    "lightweight-feature": _LIGHTWEIGHT_FEATURE,
    "feature-impl":        _FEATURE_IMPL,
    "new-project":         _NEW_PROJECT,
    "bug-investigation":   _BUG_INVESTIGATION,
    "security-review":     _SECURITY_REVIEW,
    "perf-optimization":   _PERF_OPTIMIZATION,
    "code-review":         _CODE_REVIEW,
    "recovery":            _RECOVERY,
    "all-hands-planning":  _ALL_HANDS_PLANNING,
    "integration-check":   _INTEGRATION_CHECK,
}


# ── Public API ───────────────────────────────────────────────────────────────

def get_formation(name: str) -> Formation:
    """Look up a formation by name.

    Args:
        name: Formation name (e.g. "feature-impl", "code-review").

    Returns:
        The Formation object.

    Raises:
        KeyError: If no formation with that name exists.
    """
    if name not in FORMATIONS:
        available = ", ".join(sorted(FORMATIONS.keys()))
        raise KeyError(
            f"Unknown formation {name!r}. Available formations: {available}"
        )
    return FORMATIONS[name]


# DAAO routing table: (complexity, scope) -> formation name
# Based on Difficulty-Aware Agentic Orchestration (Sept 2025).
_DAAO_TABLE: dict[tuple[str, str], str] = {
    ("routine", "small"):  "single-file",
    ("routine", "medium"): "lightweight-feature",
    ("medium",  "small"):  "lightweight-feature",
    ("medium",  "medium"): "lightweight-feature",
    ("medium",  "large"):  "feature-impl",
    ("complex", "small"):  "lightweight-feature",
    ("complex", "medium"): "feature-impl",
    ("complex", "large"):  "all-hands-planning",
    # novel maps to new-project regardless of scope
    ("novel",   "small"):  "new-project",
    ("novel",   "medium"): "new-project",
    ("novel",   "large"):  "new-project",
}

_VALID_COMPLEXITIES = frozenset({"routine", "medium", "complex", "novel"})
_VALID_SCOPES = frozenset({"small", "medium", "large"})


def select_formation(complexity: str, scope: str) -> Formation:
    """DAAO routing table — select formation from complexity x scope.

    Args:
        complexity: One of "routine", "medium", "complex", "novel".
        scope:      One of "small", "medium", "large".

    Returns:
        The recommended Formation.

    Raises:
        ValueError: If complexity or scope is not a recognised value.
    """
    if complexity not in _VALID_COMPLEXITIES:
        raise ValueError(
            f"Unknown complexity {complexity!r}. "
            f"Expected one of: {', '.join(sorted(_VALID_COMPLEXITIES))}"
        )
    if scope not in _VALID_SCOPES:
        raise ValueError(
            f"Unknown scope {scope!r}. "
            f"Expected one of: {', '.join(sorted(_VALID_SCOPES))}"
        )

    formation_name = _DAAO_TABLE[(complexity, scope)]
    return FORMATIONS[formation_name]


def validate_ownership(formation: Formation) -> list[str]:
    """Check for overlapping file ownership between roles in the same wave.

    For each wave, no two roles should claim the same directory or file pattern.
    Exact-empty ownerships (all lists empty) are skipped — overlap cannot be
    determined at definition time for roles whose scope is assigned at runtime
    (e.g. bug-investigation investigators).

    Args:
        formation: The Formation to validate.

    Returns:
        A list of human-readable warning strings. Empty list means no conflicts.
    """
    role_map: dict[str, Role] = {role.name: role for role in formation.roles}
    warnings: list[str] = []

    for wave_index, wave in enumerate(formation.wave_order):
        # Collect ownership entries per role in this wave
        wave_roles: list[Role] = []
        for role_name in wave:
            if role_name in role_map:
                wave_roles.append(role_map[role_name])

        # Compare each pair within the wave
        for i in range(len(wave_roles)):
            for j in range(i + 1, len(wave_roles)):
                role_a = wave_roles[i]
                role_b = wave_roles[j]

                conflicts = _find_ownership_conflicts(role_a, role_b)
                for conflict in conflicts:
                    warnings.append(
                        f"Wave {wave_index} conflict between "
                        f"{role_a.name!r} and {role_b.name!r}: {conflict}"
                    )

    return warnings


def _find_ownership_conflicts(role_a: Role, role_b: Role) -> list[str]:
    """Return a list of overlap descriptions between two roles' ownership."""
    conflicts: list[str] = []

    files_a = set(role_a.ownership.get("files") or [])
    files_b = set(role_b.ownership.get("files") or [])
    shared_files = files_a & files_b
    for f in sorted(shared_files):
        conflicts.append(f"shared file {f!r}")

    dirs_a = set(role_a.ownership.get("directories") or [])
    dirs_b = set(role_b.ownership.get("directories") or [])
    shared_dirs = dirs_a & dirs_b
    for d in sorted(shared_dirs):
        conflicts.append(f"shared directory {d!r}")

    patterns_a = set(role_a.ownership.get("patterns") or [])
    patterns_b = set(role_b.ownership.get("patterns") or [])

    # Skip wildcard-only patterns for dynamic-ownership roles (e.g. investigators)
    # A single bare "*" pattern means "anything in scope" which is runtime-assigned.
    def is_dynamic(patterns: set[str]) -> bool:
        return patterns <= {"*"}

    if not (is_dynamic(patterns_a) or is_dynamic(patterns_b)):
        shared_patterns = patterns_a & patterns_b
        for p in sorted(shared_patterns):
            conflicts.append(f"shared pattern {p!r}")

    return conflicts
