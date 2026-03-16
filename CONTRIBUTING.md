# Contributing to Nova Forge

Thank you for your interest in contributing to Nova Forge! This guide will help you get started.

## Quick Start

```bash
git clone https://github.com/herakles-dev/nova-forge.git
cd nova-forge
./setup.sh                          # or: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
source .venv/bin/activate
pytest tests/ -x -q                 # Run tests (1,670 should pass)
python3 forge_cli.py                # Launch the CLI
```

## Development Workflow

1. **Fork** the repository on GitHub
2. **Clone** your fork locally
3. **Create a branch** from `main`: `git checkout -b feature/your-feature`
4. **Make changes** — follow the conventions below
5. **Run tests**: `pytest tests/ -x -q` (all 1,670 must pass)
6. **Syntax check** modified files: `python3 -c "import py_compile; py_compile.compile('your_file.py', doraise=True)"`
7. **Commit** with a clear message describing the "why"
8. **Push** and open a Pull Request against `main`

## Conventions

- **Python 3.11+** — pure Python, no JS/TS dependencies
- **Read before edit** — always read files before modifying them
- **Syntax check after edit** — verify with py_compile
- **Test after changes** — all 1,670 tests must pass
- **No docs unless asked** — don't create README/docs files unless the PR is specifically about documentation
- **Secrets** — never commit credentials, API keys, or .env files

## Code Style

- **Linting**: We use [Ruff](https://github.com/astral-sh/ruff) — config is in `pyproject.toml`
- **Line length**: 120 characters
- **Imports**: Standard library, then third-party, then local — separated by blank lines
- **Type hints**: Encouraged but not enforced on all functions
- **Docstrings**: Module-level docstrings required; function docstrings for public API

## Project Structure

```
forge.py              # Click CLI (14 commands)
forge_cli.py          # Interactive shell (main entry point)
forge_agent.py        # Core agent loop + 13 tools
forge_orchestrator.py # Plan/build/deploy pipeline
forge_pipeline.py     # WaveExecutor + GateReviewer
model_router.py       # LLM provider adapters (Bedrock/OpenAI/Anthropic)
prompt_builder.py     # 3-tier system prompts
formations.py         # 11 agent team formations
forge_guards.py       # Security (risk, sandbox, autonomy A0-A5)
config.py             # Configuration + model definitions
tests/unit/           # 1,670 tests (50 files)
web/                  # Website (forge.herakles.dev)
agents/               # 20 YAML agent definitions
schemas/              # 8 JSON schemas
```

## What to Contribute

### Good First Issues

- Add a new benchmark scenario (see `benchmark_nova_models.py`)
- Add a new app template to `templates/`
- Improve error messages in agent tools
- Add tests for edge cases

### Bigger Contributions

- New LLM provider adapter in `model_router.py`
- New formation in `formations.py`
- New agent tool in `forge_agent.py`
- Performance optimizations to the wave executor
- Frontend improvements to `web/`

### Adding a New Formation

1. Define the formation in `formations.py` (roles, waves, tool policies)
2. Add to the `FORMATIONS` registry dict
3. Update the DAAO routing table if appropriate
4. Add tests in `tests/unit/`
5. Update the website formation grid in `web/index.html`

### Adding a New Model Provider

1. Create an adapter class in `model_router.py` following the `BedrockAdapter` pattern
2. Add model aliases to `config.py` `MODEL_ALIASES`
3. Add context window and cost entries
4. Add credential check to `forge_cli.py`
5. Add tests

## Testing

```bash
pytest tests/ -x -q                          # Quick (stop on first failure)
pytest tests/unit/test_pipeline.py -v        # Single module
pytest tests/ -k "test_circuit_breaker" -v   # Single test
```

All PRs must pass the full test suite. We don't merge with failing tests.

## Benchmarks

After changes that affect the agent loop, prompt system, or model router:

```bash
source ~/.secrets/hercules.env
python3 benchmark_nova_models.py --model nova-lite -v
```

This runs the custom benchmark suite to verify no regressions.

## Pull Request Guidelines

- **Title**: Short, descriptive (under 70 characters)
- **Description**: Explain what changed and why
- **Tests**: Include tests for new functionality
- **One concern per PR**: Don't mix unrelated changes
- **No force-push** to PRs under review

## Reporting Issues

Use the GitHub issue templates:
- **Bug Report** — Include steps to reproduce, expected vs actual behavior
- **Feature Request** — Describe the use case and proposed solution

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
