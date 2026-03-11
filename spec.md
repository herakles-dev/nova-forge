# Project: Nova Forge — Large File Support via `append_file` Tool

## Intent
Add an `append_file` tool so agents can write large files incrementally. Nova Forge agents hit Bedrock's output token limit (4096-5120 tokens, ~16-20KB) when building large frontend files. In the Pomodoro Timer benchmark, `app.js` and `index.html` were stubs because the agent couldn't fit 300+ lines in a single `write_file` call. The backend was excellent — the frontend was empty.

**Root cause**: No tool exists to write files incrementally. `write_file` does full replacement. `edit_file` requires a unique `old_string` match. Agents needing multiple turns for a large file have no append mechanism.

**Solution**: New `append_file` tool — agents use `write_file` for the first section, then `append_file` for subsequent sections.

## Stack
- Backend: Python 3.11+ (pure Python)
- Testing: pytest (736+ tests)
- Key modules: forge_agent.py, formations.py, forge_hooks_impl.py, prompt_builder.py, forge_cli.py

## Constraints
- ~80 LOC total across 6 files — surgical addition, no refactors
- Must not break existing 736 tests
- Same sandbox/claim/verify behavior as write_file
- No "haven't read" warning (intentional — building incrementally)

## Tasks

### Sprint 1: append_file Tool (6 changes + tests)

#### Wave 1 — Core Implementation (no dependencies)
- **T1**: Tool schema + method + dispatch in forge_agent.py [~50 LOC]
- **T2**: Add to tool profiles in formations.py [~2 LOC]
- **T3**: Hook mapping in forge_hooks_impl.py [~1 LOC]

#### Wave 2 — Prompt Guidance (depends on T1)
- **T4**: System prompt guidance in prompt_builder.py [~2 LOC]
- **T5**: Build + stub retry prompts in forge_cli.py [~4 LOC]

#### Wave 3 — Tests (after all changes)
- **T6**: 10 new tests in tests/unit/test_append_file.py [~120 LOC]

### Task Details

#### T1: Tool Schema + Method + Dispatch (forge_agent.py)
**Schema**: Add after `write_file` definition (after line 103):
```python
{
    "name": "append_file",
    "description": "Append content to end of file (create if not exists).\n"
                   "Use write_file FIRST for initial section, then append_file for rest.\n"
                   "For large files (>150 lines): write_file (first part) + append_file (rest).\n"
                   "Runs syntax check after appending (.py, .json, .yaml).",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path"},
            "content": {"type": "string", "description": "Content to append"},
        },
        "required": ["path", "content"],
    },
}
```

**Dispatch**: Add at tool dispatch (after `write_file` case):
```python
elif name == "append_file":
    return await self._tool_append_file(args, artifacts)
```

**file_action map**: Add `"append_file": "append"`.

**Method** `_tool_append_file()`: After `_tool_write_file`:
- Uses `open(path, "a")` instead of `path.write_text()`
- Same sandbox check, BuildContext claim, auto-verify, unescape, artifact tracking
- No "haven't read" warning (building incrementally is expected)
- Artifact: `{"action": "append", "size": new_total, "appended": chunk_len}`
- Returns: `f"Appended to {rel}: +{chunk_len} chars (total: {total}){verify}"`

#### T2: Tool Profiles (formations.py)
Add `"append_file"` to `full` and `coding` profiles in TOOL_PROFILES dict.

#### T3: Hook Mapping (forge_hooks_impl.py)
Add `"append_file": "Write"` to the tool name mapping so hooks treat it same as write_file.

#### T4: System Prompt Guidance (prompt_builder.py)
Add to `_SECTION_TOOL_RULES` after the write_file guidance:
```
- For large files (>150 lines): use write_file for the first ~100 lines, then append_file to add remaining sections. Each append adds to the end. Never leave files incomplete.
```

#### T5: Build + Retry Prompts (forge_cli.py)
**Build prompt** (~line 1476): Add instruction about append_file for large files.
**Stub retry prompt** (~line 1529): Mention append_file for large file rebuilds.

#### T6: Tests (tests/unit/test_append_file.py)
10 new tests:
1. `test_append_creates_new_file` — append to non-existent file creates it
2. `test_append_to_existing` — write_file then append_file, content concatenated
3. `test_append_multiple_times` — write + 3x append, all content present in order
4. `test_append_sandbox_rejects_outside` — sandbox blocks out-of-bounds paths
5. `test_append_build_context_claim` — auto-claims file via BuildContext
6. `test_append_build_context_conflict` — returns CONFLICT if another agent owns file
7. `test_append_auto_verify_python` — syntax check runs after append on .py
8. `test_append_artifacts_tracking` — artifacts dict has action="append" with sizes
9. `test_append_unescape_content` — Nova escaping handled
10. `test_append_in_tool_profiles` — append_file in full and coding profiles

## Files Modified

| File | Tasks | LOC | Risk |
|------|-------|-----|------|
| forge_agent.py | T1 | ~50 | Medium — core tool dispatch |
| formations.py | T2 | ~2 | Low — additive only |
| forge_hooks_impl.py | T3 | ~1 | Low — mapping addition |
| prompt_builder.py | T4 | ~2 | Low — prompt text |
| forge_cli.py | T5 | ~4 | Low — prompt text |
| tests/unit/test_append_file.py | T6 | ~120 | None — new test file |

## Verification
1. Syntax check all modified files: `py_compile`
2. `python3 -m pytest tests/ -x -q` — all 736+ tests pass (existing + ~10 new)
3. Re-run Pomodoro build: `app.js` written incrementally (write_file + append_file)
4. Check auto-preview starts with complete frontend

## Formation
**single-file** — One primary file (forge_agent.py) with small additions to 4 supporting files + new test file.

## Risk
**Low** — All changes are additive. New tool, new method, new tests. No existing behavior modified. Worst case: tool exists but agents don't use it (no regression).

---
Created: 2026-03-11
Tasks: Use TaskList to view (created via TaskCreate)
