# Nova Forge — Issues & Improvements Backlog

> 5-agent architecture review (2026-03-15). 72 raw findings deduplicated to 44.
> Agents: pipeline-reviewer, prompt-reviewer, error-reviewer, security-reviewer, agent-loop-reviewer.

---

## CRITICAL

### C1. Bash command injection via shell execution
- **File**: `forge_agent.py:1472-1478`
- **Problem**: `asyncio.create_subprocess_shell()` passes LLM-provided commands directly to the shell. `RiskClassifier` regex is fundamentally bypassable via base64 encoding (`echo cm0gLXJmIC8= | base64 -d | sh`), variable indirection, hex escapes, here-documents, and `$()` substitution.
- **Fix**: Run bash commands inside Docker containers, or use a command allowlist instead of denylist. At minimum add detection for base64 piping, here-docs, and `$()` patterns.
- **Severity**: CRITICAL (complete sandbox bypass)

---

## HIGH — Directly causes build failures or security issues

### H1. _auto_verify py_compile always fails — shlex.quote produces invalid Python
- **File**: `forge_agent.py:1703-1711`
- **Problem**: `shlex.quote('/tmp/test.py')` returns `/tmp/test.py` (unquoted for simple paths). Interpolated into `py_compile.compile({safe_path}, ...)` this becomes `py_compile.compile(/tmp/test.py, ...)` — Python interprets `/tmp/test.py` as division, not a string. EVERY Python file gets a false `"Syntax issue detected: SyntaxError: invalid syntax"`. Same bug affects json.load/yaml.safe_load checks.
- **Evidence**: `python3 -c "import py_compile; py_compile.compile(/tmp/test.py, doraise=True)"` → `SyntaxError: invalid syntax` (the py_compile command itself fails, not the user's file).
- **Fix**: Use `repr(str(path))` instead of `shlex.quote(str(path))` for Python-level quoting. The `repr()` always produces a quoted string like `'/tmp/test.py'`.
- **Impact**: Every agent turn that writes a .py/.json/.yaml file wastes the model's attention on a phantom syntax error. Models may spend turns "fixing" code that was already correct.

### H2. Syntax error fix injection is dead code — string mismatch
- **File**: `forge_agent.py:843` vs `forge_agent.py:1725`
- **Problem**: Post-turn fix injection checks for `"SYNTAX ERROR"` but `_auto_verify` returns `"Syntax issue detected:"`. Strings never match. The entire syntax error safety net (lines 850-862) never triggers.
- **Fix**: Change line 843 to `if result_str and "Syntax issue" in result_str:`

### H3. Streaming token usage always returns zero
- **File**: `model_router.py:870-876`
- **Problem**: `stream_send()` returns `usage={"input_tokens": 0, "output_tokens": 0}`. All cost tracking is broken when streaming is enabled (the default).
- **Fix**: Parse the `metadata` event in Bedrock streams, `message_stop` for Anthropic, or `stream_options={"include_usage": True}` for OpenAI.

### H4. Wrong role assignment for un-hinted tasks in formations
- **File**: `forge_pipeline.py:553-589`
- **Problem**: `_find_role_for_task` returns the first formation role for any task without `metadata.agent`, regardless of wave. A testing task without an agent hint gets `backend-impl` with full write tools instead of the tester's readonly policy.
- **Fix**: Match tasks to roles by wave index — pass `wave_index` to `_find_role_for_task`.

### H5. Failed task detection uses wrong key scheme — failures silently ignored
- **File**: `forge_pipeline.py:347-352`
- **Problem**: Failed task lookup uses `task.metadata.get("agent")` but `agent_results` is keyed by `role_name:task_id`. Key mismatch means failures are never detected, `_block_dependents` never called, dependent tasks run against broken state.
- **Fix**: Check `task.status == "failed"` (already updated by executor) instead of reverse-looking up in `agent_results`.

### H6. Contradictory chunk size limits across prompt tiers
- **File**: `prompt_builder.py:46,91,202`, `forge_agent.py:112,305`, `forge_cli.py:1731`
- **Problem**: Four different chunk thresholds (80, 100, 120, 150 lines) appear across SLIM prompt, FULL prompt, FOCUSED prompt, and tool descriptions. Models see conflicting numbers in a single conversation turn.
- **Fix**: Standardize: 80 lines for SLIM tier, 120 for FOCUSED/FULL. Update all tool descriptions to match.

### H7. "Write COMPLETE file" directly contradicts "max 80 lines per call"
- **File**: `forge_cli.py:1753-1756`
- **Problem**: Adjacent instructions: "Include ALL functions in your FIRST write_file call" vs "NEVER write more than 80 lines." Logical contradiction — models resolve nondeterministically.
- **Fix**: Replace with chunking-aware completeness: "Include ALL functions. Write first ~80 lines via write_file, then IMMEDIATELY call append_file for the rest. Get ALL functions down before moving to the next file."

### H8. Truncated upstream artifacts lose critical context
- **File**: `forge_pipeline.py:208-214`
- **Problem**: Artifacts >2KB truncated to preview. Dependent agents see partial file, write code assuming full content exists.
- **Fix**: Append "You MUST call read_file('{path}') to see full content." Raise inline threshold to 4KB.

### H9. Preview servers bind to 0.0.0.0 — exposed to network
- **File**: `forge_preview.py:229-305`
- **Problem**: All 14 stack detectors bind preview servers to `0.0.0.0`. Unauthenticated LLM-generated apps are directly accessible from the network. Contradicts deployer's `127.0.0.1` security invariant.
- **Fix**: Change all server commands to bind `127.0.0.1`. Cloudflare tunnel connects to localhost.

### H10. /tmp as sandbox escape staging area
- **File**: `forge_agent.py:424`
- **Problem**: PathSandbox allows writes to `/tmp`. Agent can write a script to `/tmp/exploit.sh` and execute it via bash, bypassing all sandbox restrictions.
- **Fix**: Remove `/tmp` from `extra_allowed` or restrict to a unique `/tmp/forge-{session_id}/` subdirectory.

### H11. Preview servers run LLM-generated code without isolation
- **File**: `forge_preview.py:670-674`
- **Problem**: Preview servers run as the `hercules` user with full filesystem, network, and process access. No containerization, resource limits, or seccomp filters.
- **Fix**: Run preview servers inside Docker containers with `--read-only --memory=512m --cpus=1 --network=none`.

### H12. Nginx config injection via unsanitized domain parameter
- **File**: `forge_deployer.py:248-262`
- **Problem**: `domain` parameter interpolated directly into nginx config. Semicolons, braces, or `include` directives can inject arbitrary nginx configuration.
- **Fix**: Validate domain against strict regex: `^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$`

### H13. Symlink race condition in PathSandbox
- **File**: `forge_guards.py:311-318`
- **Problem**: Sandbox validates resolved path, but the actual write uses a separately resolved path. A symlink planted between check and write follows the symlink, escaping the sandbox.
- **Fix**: Use `os.path.realpath()` immediately before write and re-validate. Reject symlinks pointing outside project root.

---

## MEDIUM — Degrades quality, correctness, or security posture

### M1. SLIM prompt missing "read before write for dependencies"
- **File**: `prompt_builder.py:40-56`
- **Problem**: SLIM says "Read existing files before editing" but not "read files you DEPEND ON before writing NEW code." Nova Lite (32K) is most prone to hallucinating imports.
- **Fix**: Add: "Before writing code that imports/uses other files, read them first."

### M2. SLIM prompt missing self-correction / read-back rules
- **File**: `prompt_builder.py:40-56`
- **Problem**: FOCUSED has "Before finishing, read back files you created." SLIM has nothing — Nova Lite never self-checks for logical errors.
- **Fix**: Add one-line: "Before finishing, read back key files and verify imports match exports."

### M3. list_directory required param inconsistency between tool sets
- **File**: `forge_agent.py:229` vs `forge_agent.py:330-332`
- **Problem**: BUILT_IN_TOOLS: `"required": []`. SLIM_TOOLS: `"required": ["path"]`. Nova Lite agent calling `list_directory()` without path fails, wastes a turn.
- **Fix**: Change SLIM_TOOLS to `"required": []`.

### M4. Autonomy guidance is noise in automated build pipelines
- **File**: `prompt_builder.py:233-271`, `forge_cli.py:1774`
- **Problem**: A0 ("wait for approval") injected into automated builds where no human is present. Agent wastes turns describing actions instead of executing.
- **Fix**: Override autonomy to A4/A5 in the automated build path.

### M5. Context overflow retry burns attempts without reduction check
- **File**: `forge_agent.py:599-606`
- **Problem**: Compaction runs but no check if context size actually decreased. Burns all 3 retry attempts on futile compaction loops.
- **Fix**: After compaction, check `_estimate_tokens(messages)` decreased. If not, break immediately.

### M6. Escalation config not restored on exception
- **File**: `forge_agent.py:896-926`
- **Problem**: Recursive `self.run()` modifies `self.model_config`, `self.max_turns`. If recursive call throws, originals never restored.
- **Fix**: Wrap in `try/finally` that always restores.

### M7. _recover_json quote fixer can corrupt valid strings
- **File**: `forge_orchestrator.py:89-123`
- **Problem**: Heuristic escapes valid closing quotes when the next char isn't `,]}:`. Can corrupt task descriptions.
- **Fix**: Only apply `_fix_inner_quotes()` after initial `json.loads()` has failed.

### M8. Bash timeout does not kill subprocess
- **File**: `forge_agent.py:1480-1482`
- **Problem**: `asyncio.TimeoutError` caught but `proc` continues running. Zombie processes accumulate, hold ports.
- **Fix**: Add `proc.kill(); await proc.wait()` in the timeout handler.

### M9. stream_send silently swallows all exceptions
- **File**: `model_router.py:811-815`
- **Problem**: `except Exception` with no logging falls back to non-streaming. Hides auth errors, SDK bugs, and streaming failures.
- **Fix**: Log the exception. Only catch transient errors (connection, stream interruption).

### M10. Kahn's algorithm misreports blocked dependencies as cycles
- **File**: `forge_tasks.py:462-465`
- **Problem**: Blocked (non-active) dependencies increment in-degree but never resolve. Dependents appear as false-positive cycles.
- **Fix**: Skip blocked deps (treat as resolved) or exclude their dependents with "blocked by upstream" status.

### M11. Announcements list grows unbounded
- **File**: `forge_comms.py:85,137-144`
- **Problem**: Append-only list with no cap. Grows proportionally to build duration.
- **Fix**: Use `collections.deque(maxlen=500)` or add `prune()` between waves.

### M12. GateReviewer JSON extraction uses greedy regex
- **File**: `forge_pipeline.py:797`
- **Problem**: `r"\{.*\}"` with `re.DOTALL` matches from first `{` to last `}`, can include non-JSON text between separate objects.
- **Fix**: Try non-greedy `r"\{.*?\}"` first, fall back to greedy.

### M13. Agent result key collision silently drops exception results
- **File**: `forge_pipeline.py:433-434`
- **Problem**: Two tasks throwing exceptions both get key `"unknown:task_id"`. First exception's result overwritten silently.
- **Fix**: Always use `f"{role_name}:{task.id}"` as the key.

### M14. Environment variables leaked to agent subprocesses
- **File**: `forge_agent.py:1478`
- **Problem**: `{**os.environ, ...}` passes all secrets (AWS keys, API tokens) to LLM-generated bash commands. Agent can read them with `echo $SECRET`.
- **Fix**: Create minimal env with only `PATH`, `HOME`, `LANG`, `TERM`. Don't pass full `os.environ`.

### M15. `sudo` missing from high-risk patterns
- **File**: `forge_guards.py:44-80`
- **Problem**: Agent can prefix any command with `sudo` for root privileges. Not flagged as HIGH risk.
- **Fix**: Add `sudo` to `_HIGH_PATTERNS`.

### M16. Autonomy level inflatable via trivial tool calls
- **File**: `forge_guards.py:757-964`
- **Problem**: 25 successful `think` calls escalate from A0 to A3 (Trusted). Agent can game escalation thresholds.
- **Fix**: Only count write/execute tool calls toward escalation. Exclude `think`, `list_directory`, reads.

### M17. Incomplete bash write pattern detection
- **File**: `forge_agent.py:1425-1432`
- **Problem**: Missing: `dd`, `install`, `rsync`, `wget -O`, `curl -o`, `tar -x`, `unzip`, `patch`, `chmod`, `ln -s`.
- **Fix**: Add missing patterns. For readonly mode, use a positive allowlist instead.

### M18. Sensitive file deny list gaps
- **File**: `forge_guards.py:112-122,273-279`
- **Problem**: Missing: `~/.aws/credentials`, `~/.kube/config`, `~/.docker/config.json`, `~/.gnupg/`, `~/.netrc`, `.pem`/`.key` files, `/etc/ssl/private/`.
- **Fix**: Expand both deny lists.

### M19. Docker containers lack security hardening
- **File**: `forge_deployer.py:351-357`
- **Problem**: No `--read-only`, `--cap-drop=ALL`, `--memory`, `--cpus`, `--user`, `--security-opt=no-new-privileges`.
- **Fix**: Add security flags to `docker run` command.

### M20. SQLite hint injected unconditionally for non-Python tasks
- **File**: `benchmark_nova_models.py:1290-1299`
- **Problem**: ~88 tokens of SQLite threading advice injected even for HTML/CSS tasks. Wastes context on 32K models.
- **Fix**: Wrap in `if any(f.endswith('.py') for f in expected_files)`.

### M21. Server verification has no HTTP-level retry
- **File**: `forge_verify.py:254-271`
- **Problem**: Server passes TCP port check but HTTP request fails (not yet ready). No retry — false negative.
- **Fix**: Add 2-3 retry loop with 1s intervals for `_check_http`.

### M22. No auto-escalation after CLI retry failures
- **File**: `forge_cli.py:1789-1827`
- **Problem**: After no-write + stub retries fail, task is marked failed. No automatic escalation to a better model.
- **Fix**: Wire `escalation_model` into CLI retry path.

### M23. write_file doesn't block on unread existing files
- **File**: `forge_agent.py:1196-1210`
- **Problem**: `edit_file` blocks on unread files. `write_file` only warns. Agent can overwrite files without reading them.
- **Fix**: Block `write_file` on existing, unread files. Allow for new file creation.

### M24. search_replace_all bypasses read-before-edit and BuildContext claims
- **File**: `forge_agent.py:1619-1638`
- **Problem**: `edit_file` enforces `_files_read` check and `claim_file()`. `search_replace_all` has neither — can modify unread files and files claimed by other agents.
- **Fix**: Add same `_files_read` check and `build_context.claim_file()` pattern.

### M25. _unescape_content not applied to edit_file args
- **File**: `forge_agent.py:1269-1298`
- **Problem**: `write_file` and `append_file` run `_unescape_content` on content. `edit_file` doesn't apply it to `old_string` or `new_string`. Nova's double-escaping issue can cause `\n` literal mismatches.
- **Fix**: Apply `_unescape_content` to both `old_string` and `new_string` in `_tool_edit_file`.

### M26. Escalation discards original artifacts
- **File**: `forge_agent.py:499,917`
- **Problem**: Recursive `self.run()` creates fresh `artifacts = {}`. Escalated result only has files from escalation, not the original run's files (which ARE on disk).
- **Fix**: Merge: `escalated_result.artifacts = {**artifacts, **escalated_result.artifacts}`

### M27. _compact_messages safe-split walk has no effect (variable i unused)
- **File**: `forge_agent.py:1845-1858`
- **Problem**: Loop finds a safe cut point and sets `i`, but `i` is never used. All of `middle` is compressed regardless. Tool pair protection is dead code.
- **Fix**: Use `i` to split `middle` into compress vs keep-verbatim.

### M28. 0-file task budget (6 turns) too tight
- **File**: `config.py:125`
- **Problem**: Tasks with no files specified (e.g., "set up project structure") get base=6, hard=10. Insufficient for discovery + bash commands.
- **Fix**: Increase 0-file base to 10-15. These tasks often need MORE discovery, not less.

### M29. Tests mask _auto_verify bug with weak assertions
- **File**: `tests/unit/test_sprint5_comprehensive.py:359`
- **Problem**: Test asserts `"syntax" in result.lower()` which passes on the ERROR message too. Can't distinguish correct behavior from broken behavior.
- **Fix**: Assert `"syntax OK" in result.lower()` for valid-file tests.

### M30. Cloudflare tunnel exposes unauthenticated services
- **File**: `forge_preview.py:697-703`
- **Problem**: Quick Tunnel (`*.trycloudflare.com`) is publicly accessible with no auth.
- **Fix**: Use authenticated Cloudflare Access, or add HTTP basic auth via reverse proxy.

---

## LOW — Nice-to-have improvements

### L1. Gate verdict not enforced after review
### L2. `coding` and `full` tool policies are identical (formations.py:28-34)
### L3. `to_context` budget truncation can cut announcements entirely (forge_comms.py:190)
### L4. `is_claimed` returns mutable internal reference (forge_comms.py:117-120)
### L5. `blocks` field update skips bidirectional consistency (forge_tasks.py:378-379)
### L6. Ownership validation misses directory containment (formations.py:878-907)
### L7. `BuildCancellation.install()` not safe from non-main threads (forge_comms.py:38-42)
### L8. Artifact inline threshold byte/char mismatch for UTF-8 (forge_pipeline.py:212)
### L9. DAAO routes `complex/small` to lightweight-feature (formations.py:797)
### L10. `update_claim_status` accepts arbitrary status strings (forge_comms.py:127-132)
### L11. Duplicate model identity hint in `build_enriched_system_prompt` (prompt_builder.py:601,674)
### L12. SLIM prompt could encounter FULL-tier tool names via context injection
### L13. Role profiles (builder vs implementer) lack differentiation (prompt_builder.py:274-346)
### L14. Budget truncation targets hardcoded section index (prompt_builder.py:726-765)
### L15. Spec constraint extraction regex over-captures (forge_cli.py:1704-1722)
### L16. Port registry race condition — no file locking (forge_deployer.py:70-103)
### L17. No-write retry loses token count from first run (forge_cli.py:1791-1800)
### L18. `_last_artifacts` crash recovery undocumented (forge_agent.py:500)
### L19. `_save()` has no internal lock — relies on caller convention (forge_tasks.py:181-189)
### L20. Container name/image tag injection via special chars (forge_deployer.py:406-408)
### L21. Semaphore created at __init__ — may not bind to event loop (forge_pipeline.py:278)
### L22. No inter-wave artifact diff summary
### L23. No task retry with error feedback injection
### L24. Formation auto-selection not wired to CLI
### L25. Agent memory not persisted across tasks
### L26. No benchmark scenario for multi-file coordination
### L27. Escalation budget not speed-aware for slower models
### L28. check_context tool underused — no prompt hint
### L29. compute_turn_budget returns hard_limit that no caller uses (dead code)
### L30. _escalated flag not reset in run() — blocks escalation on reused agents
### L31. Convergence detection disabled when artifacts dict is empty
### L32. Tool retry wastes 1s on deterministic failures (file not found, CONFLICT)
### L33. _check_completeness false-positives on legitimate `pass` usage
### L34. Bedrock system prompt embedded in user message instead of Converse API system field

---

## Completed (Sprint 17)

- [x] Adaptive turn budgets (compute_turn_budget)
- [x] ConvergenceTracker (disables writes after 5 idle turns)
- [x] Verify phase with hard budget (soft//4 turns)
- [x] Escalation budget reduction (half original, min 8)
- [x] Hard limit tightening (max(n+4, n*1.3) not n*2)
- [x] Soft-limit message injection reverted
- [x] Benchmark aligned to CLI path (PromptBuilder + model-aware tools)
- [x] Completeness directive in user prompt
