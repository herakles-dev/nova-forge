# Nova Forge — Devpost Submission Draft

## Project Name
Nova Forge

## Tagline
Describe it. Nova builds it. Open-source agent orchestration that turns natural language into deployed applications.

## Inspiration

We've built 89+ production services using multi-agent orchestration patterns — parallel task execution, quality gates, artifact handoffs, autonomy systems. But those patterns were locked inside a proprietary system tied to a single LLM provider.

When Amazon announced the Nova AI Hackathon, we saw the opportunity to extract those battle-tested patterns into an open-source framework powered by Amazon Nova. The question wasn't "can Nova build software?" — it was "can we build an orchestration layer that makes Nova as productive as possible?"

The answer is Nova Forge: 25,600 lines of Python, 1,051 tests, and all 3 Nova models achieving S-tier benchmark scores.

## What it does

Nova Forge takes a natural language description of what you want to build and orchestrates Amazon Nova models to plan, build, review, and deploy it automatically.

**Example**: Type "Build an expense tracker with categories, monthly charts, and CSV export" — Nova Forge:

1. **Interviews** you with a 3-phase deep planning session (scope, technical decisions, risk assessment)
2. **Plans** the project as a dependency graph of tasks with topological sort
3. **Builds** tasks in parallel waves using multiple AI agents with pre-seeded upstream context
4. **Reviews** the output with an adversarial gate reviewer (PASS/FAIL/CONDITIONAL)
5. **Previews** the result via Cloudflare Tunnel (shareable URL, no account needed)
6. **Deploys** to production with Docker + nginx + SSL in one command

**Result**: A working Flask application with 5 files in 170 seconds. Benchmark grade: S (100%).

### Key capabilities:
- **3 Amazon Nova models** — Lite (32K, fast), Pro (300K, balanced), Premier (1M, deep reasoning)
- **10 agent formations** — Pre-built team layouts from single-file edits to full-stack multi-agent builds
- **12 agent tools** — read, write, edit, bash, glob, grep, and more
- **6-level autonomy system** — A0 (ask for everything) through A5 (fully autonomous)
- **14-stack preview detection** — Auto-detects Flask, FastAPI, Node, React, and 10 more
- **Circuit breaker** — Auto-disables failing tools to prevent infinite retry loops
- **Agent self-correction** — Agents verify their own output with read-back checks

## How we built it

**Pure Python, no dependencies on proprietary agent frameworks.** Nova Forge is a standalone tool-use loop that calls Amazon Nova via AWS Bedrock's Converse API.

**Architecture**:
- `ForgeAgent` — The core loop: send prompt → Nova responds with tool calls → execute tools → loop until done. 12 tools available (read_file, write_file, bash, etc.)
- `ModelRouter` — Adapter for AWS Bedrock with custom `botocore.Config` (300s read timeout for Premier's ~100s inference time)
- `WaveExecutor` — Runs independent tasks in parallel using `asyncio.gather()` with semaphore throttling
- `GateReviewer` — Spawns a read-only reviewer agent that adversarially checks the build output
- `PromptBuilder` — 3-tier system prompts: Slim (~600 chars for 32K Lite), Focused (~1,500 chars for 300K+ Pro/Premier), Full (~5K chars for 1M+)

**The 3-tier prompt system was the single biggest breakthrough.** Nova Lite's 32K context window means every token matters. We created a slim prompt that strips tools to essentials and adds output coaching ("respond with JSON, not markdown"). This took Lite from C-grade to S-grade.

**Pre-seeded upstream context** was the second breakthrough. Instead of letting dependent tasks discover files through tool calls (2-3 turns wasted), we inject the file content from completed upstream tasks directly into the prompt. Premier went from A to S with this change.

**16 sprints in 5 days.** We tracked every change through a sprint system, enabling rapid iteration on model-specific optimizations.

## Challenges we ran into

1. **Nova Lite's 32K context window** — Our initial system prompts used ~5,000 characters. At 32K, that's a significant fraction of the budget. We had to create an entirely different prompt tier (Slim) with minimal tools and coaching to make Lite work.

2. **Nova Premier's inference time** — Premier takes ~100 seconds per inference call. The default boto3 timeout of 60 seconds caused every Premier request to fail. We had to discover and configure `botocore.Config(read_timeout=300)` — this isn't well-documented.

3. **Malformed JSON from LLM output** — All Nova models occasionally produce trailing commas, truncated JSON, or markdown fences around JSON. We built `_recover_json()` with progressive fallback: strip fences → fix trailing commas → extract JSON substring → parse partial.

4. **Agent tool failure spirals** — When a tool fails (e.g., edit_file with wrong old_string), agents would retry the exact same call repeatedly. We implemented a circuit breaker that disables a tool after 3 consecutive failures, forcing the agent to try alternative approaches.

5. **Context window exhaustion** — On long builds (30+ turns), the conversation exceeds the model's context. We implemented budget-based compaction (60% threshold for 32K, 65% for 200K+) that preserves `toolUse`/`toolResult` pairs to avoid breaking the conversation structure.

## Accomplishments that we're proud of

- **All 3 Nova models at S-tier** on the Expense Tracker benchmark (100%, 99%, 100%)
- **A-tier on the Hard scenario** (Kanban Board: auth + 3 tables + 7 files) across all 3 models
- **1,051 tests passing** across 48 test files — comprehensive coverage of the entire framework
- **16 sprints in 5 days** — from zero to competition-ready with disciplined iteration
- **Grade progression C → S** through systematic model-specific optimization (not prompt hacking)
- **Pure Python** — no dependency on Claude Code, Cursor, or any proprietary agent framework. The orchestration patterns are the innovation, not the model.

## What we learned

1. **Model-specific prompts matter enormously.** A one-size-fits-all system prompt works poorly across 32K-1M context windows. Tailoring the prompt to the model's capacity was the #1 performance lever.

2. **Pre-seeding context is better than letting agents discover it.** Injecting upstream file content saves 2-3 turns per task — and with Premier's ~100s/turn, that's 3-5 minutes saved per task.

3. **Adversarial review catches real bugs.** The gate reviewer (a separate Nova agent with read-only tools) found issues that the building agents missed in ~30% of builds.

4. **Amazon Nova is genuinely capable of complex multi-file builds.** With the right orchestration, even Nova Lite (the smallest model) can build a full-stack app with auth, database, and frontend — and score S-tier.

## What's next

- **More benchmark scenarios** — Expand beyond Expense Tracker and Kanban Board to test against more diverse application types
- **Real-time streaming UI** — Replace the CLI spinner with a web-based build monitor showing live agent activity
- **Multi-model orchestration** — Use Lite for simple tasks and Premier for complex ones within the same build, automatically routing by task complexity
- **Community templates** — Let users contribute app templates and formation patterns
- **Plugin system** — Allow custom tools beyond the built-in 12

## Built with

- Python 3.11
- Amazon Nova (Lite, Pro, Premier) via AWS Bedrock
- AWS Bedrock Converse API
- boto3 + botocore
- Click (CLI framework)
- Rich (terminal UI)
- pytest (testing)
- Flask (website + Ask Nova chat)
- Docker + nginx (deployment)
- Cloudflare Tunnels (preview)

## Links

- **Live demo**: [forge.herakles.dev](https://forge.herakles.dev)
- **Interactive demos**: [forge.herakles.dev/demos/](https://forge.herakles.dev/demos/)
- **GitHub**: [github.com/herakles-dev/nova-forge](https://github.com/herakles-dev/nova-forge)
