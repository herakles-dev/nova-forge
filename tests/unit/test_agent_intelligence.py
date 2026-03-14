"""Tests for Sprint 8 — Agent Intelligence Upgrade.

Covers:
- Multi-language auto-verify (JS, HTML, CSS)
- Read-before-write enforcement
- Smart error recovery hints
- JS/TS interface extraction
- Completeness check (stub/TODO detection)
- Enhanced system prompt sections
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch


# ── Multi-language auto-verify ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_verify_js_syntax_ok(tmp_path):
    """Valid JS should pass node --check."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    js_file = tmp_path / "app.js"
    js_file.write_text("function hello() { return 'world'; }\n")
    result = await agent._auto_verify(js_file)
    assert "syntax OK" in result


@pytest.mark.asyncio
async def test_auto_verify_js_syntax_error(tmp_path):
    """Invalid JS should report syntax error."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    js_file = tmp_path / "bad.js"
    js_file.write_text("function hello( { return 'world'; }\n")
    result = await agent._auto_verify(js_file)
    assert "SYNTAX ERROR" in result


def test_verify_html_ok(tmp_path):
    """Valid HTML should pass."""
    from forge_agent import ForgeAgent

    html_file = tmp_path / "index.html"
    html_file.write_text("<html><body><script>var x = 1;</script></body></html>")
    result = ForgeAgent._verify_html(html_file)
    assert "HTML OK" in result


def test_verify_html_unclosed_script(tmp_path):
    """Unclosed script tag should be detected."""
    from forge_agent import ForgeAgent

    html_file = tmp_path / "bad.html"
    html_file.write_text("<html><body><script>var x = 1;</body></html>")
    result = ForgeAgent._verify_html(html_file)
    assert "HTML ERROR" in result
    assert "unclosed <script>" in result


def test_verify_html_missing_close(tmp_path):
    """Missing </html> should be detected."""
    from forge_agent import ForgeAgent

    html_file = tmp_path / "bad2.html"
    html_file.write_text("<html><body></body>")
    result = ForgeAgent._verify_html(html_file)
    assert "HTML ERROR" in result
    assert "</html>" in result


def test_verify_css_ok(tmp_path):
    """Valid CSS should pass."""
    from forge_agent import ForgeAgent

    css_file = tmp_path / "style.css"
    css_file.write_text("body { color: red; }\n.container { display: flex; }")
    result = ForgeAgent._verify_css(css_file)
    assert "CSS OK" in result


def test_verify_css_unbalanced(tmp_path):
    """Unbalanced braces should be detected."""
    from forge_agent import ForgeAgent

    css_file = tmp_path / "bad.css"
    css_file.write_text("body { color: red;\n.container { display: flex; }")
    result = ForgeAgent._verify_css(css_file)
    assert "CSS ERROR" in result
    assert "unbalanced" in result


# ── Read-before-write enforcement ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_file_blocked_without_read(tmp_path):
    """edit_file should be BLOCKED if file hasn't been read first."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    target = tmp_path / "existing.py"
    target.write_text("old_content = True\n")

    result = await agent._execute_tool_call(
        __import__("model_router", fromlist=["ToolCall"]).ToolCall(
            id="t1", name="edit_file",
            args={"path": "existing.py", "old_string": "old_content", "new_string": "new_content"}
        ),
        {},
    )
    assert "BLOCKED" in result
    assert "read_file" in result


@pytest.mark.asyncio
async def test_edit_file_allowed_after_read(tmp_path):
    """edit_file should work after reading the file."""
    from forge_agent import ForgeAgent
    from config import get_model_config
    from model_router import ToolCall

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    target = tmp_path / "existing.py"
    target.write_text("old_content = True\n")

    # Read first
    await agent._execute_tool_call(
        ToolCall(id="t0", name="read_file", args={"path": "existing.py"}), {}
    )

    # Now edit should work
    result = await agent._execute_tool_call(
        ToolCall(id="t1", name="edit_file",
                 args={"path": "existing.py", "old_string": "old_content = True", "new_string": "new_content = False"}),
        {},
    )
    assert "File edited" in result
    assert target.read_text().strip() == "new_content = False"


# ── Smart error recovery hints ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_not_found_shows_hints(tmp_path):
    """When old_string not found, error should suggest reading file and show nearby lines."""
    from forge_agent import ForgeAgent
    from config import get_model_config
    from model_router import ToolCall

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    target = tmp_path / "code.py"
    target.write_text("def hello():\n    print('world')\n\ndef goodbye():\n    pass\n")
    agent._files_read.add(str(target))

    result = await agent._execute_tool_call(
        ToolCall(id="t1", name="edit_file",
                 args={"path": "code.py", "old_string": "def hello(name):", "new_string": "def hello(name, age):"}),
        {},
    )
    assert "not found" in result
    assert "read_file" in result or "Read" in result
    # Should show nearby line numbers or mention similar text
    assert "lines" in result.lower() or "Similar" in result


@pytest.mark.asyncio
async def test_edit_multiple_shows_line_numbers(tmp_path):
    """When old_string appears multiple times, error should show line numbers."""
    from forge_agent import ForgeAgent
    from config import get_model_config
    from model_router import ToolCall

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    target = tmp_path / "dup.py"
    target.write_text("x = 1\nx = 1\nx = 1\n")
    agent._files_read.add(str(target))

    result = await agent._execute_tool_call(
        ToolCall(id="t1", name="edit_file",
                 args={"path": "dup.py", "old_string": "x = 1", "new_string": "x = 2"}),
        {},
    )
    assert "3 times" in result
    assert "more surrounding context" in result


# ── JS/TS interface extraction ───────────────────────────────────────────────


def test_extract_interface_js(tmp_path):
    """JS functions and routes should be extracted."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    js_file = tmp_path / "server.js"
    js_file.write_text(
        "function createUser(name, email) { return db.insert(name, email); }\n"
        "async function getUsers() { return db.query('SELECT * FROM users'); }\n"
        "app.get('/api/users', getUsers);\n"
        "app.post('/api/users', createUser);\n"
        "export const API_VERSION = '1.0';\n"
    )
    result = agent._extract_interface_summary(js_file)
    assert "createUser" in result
    assert "getUsers" in result
    assert "GET /api/users" in result
    assert "POST /api/users" in result


def test_extract_interface_python(tmp_path):
    """Python functions should still be extracted via AST."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    mc = get_model_config("openrouter/google/gemini-2.0-flash-001")
    agent = ForgeAgent(model_config=mc, project_root=tmp_path, max_turns=1)

    py_file = tmp_path / "models.py"
    py_file.write_text(
        "def create_user(name: str, email: str) -> int:\n    pass\n\n"
        "def get_users() -> list:\n    pass\n\n"
        "class UserService:\n    pass\n"
    )
    result = agent._extract_interface_summary(py_file)
    assert "create_user(name, email)" in result
    assert "get_users()" in result
    assert "class UserService" in result


# ── Completeness check ───────────────────────────────────────────────────────


def test_completeness_detects_todos():
    """TODO/FIXME markers should be flagged."""
    from forge_agent import ForgeAgent

    content = "def hello():\n    # TODO: implement this\n    pass\n"
    result = ForgeAgent._check_completeness(content, ".py")
    assert "INCOMPLETE" in result
    assert "TODO" in result


def test_completeness_detects_stubs():
    """Python 'pass' stubs and NotImplementedError should be flagged."""
    from forge_agent import ForgeAgent

    content = "def hello():\n    raise NotImplementedError\n"
    result = ForgeAgent._check_completeness(content, ".py")
    assert "INCOMPLETE" in result
    assert "stub" in result.lower() or "placeholder" in result.lower()


def test_completeness_detects_empty_js_functions():
    """Empty JS function bodies should be flagged."""
    from forge_agent import ForgeAgent

    content = "function handleClick() {}\nfunction process() {}\n"
    result = ForgeAgent._check_completeness(content, ".js")
    assert "INCOMPLETE" in result
    assert "empty function" in result.lower()


def test_completeness_clean_code():
    """Clean code should pass completeness check."""
    from forge_agent import ForgeAgent

    content = "def hello():\n    return 'world'\n\ndef add(a, b):\n    return a + b\n"
    result = ForgeAgent._check_completeness(content, ".py")
    assert result == ""


# ── Enhanced system prompt ───────────────────────────────────────────────────


def test_system_prompt_has_self_verify():
    """System prompt should include verification guidance."""
    from prompt_builder import PromptBuilder

    pb = PromptBuilder("/tmp")
    prompt = pb.build_system_prompt(
        role="builder",
        model_id="openrouter/google/gemini-2.0-flash-001",
    )
    assert "Verification" in prompt
    assert "SYNTAX ERROR" in prompt


def test_system_prompt_has_code_quality():
    """System prompt should include code quality guidance."""
    from prompt_builder import PromptBuilder

    pb = PromptBuilder("/tmp")
    prompt = pb.build_system_prompt(
        role="builder",
        model_id="openrouter/google/gemini-2.0-flash-001",
    )
    assert "Code Quality" in prompt or "parameterized SQL" in prompt
    assert "threading" in prompt.lower() or "parameterized" in prompt.lower()


def test_slim_prompt_has_verify_hints():
    """Slim prompt should include verify and quality hints."""
    from prompt_builder import PromptBuilder

    pb = PromptBuilder("/tmp")
    prompt = pb.build_system_prompt(
        role="builder",
        model_id="bedrock/us.amazon.nova-2-lite-v1:0",
    )
    assert "SYNTAX ERROR" in prompt
    assert "CONFLICT" in prompt
    assert "write_file" in prompt


# ── _unescape_content tests ──────────────────────────────────────────────────

def test_unescape_json_wrapped_content(tmp_path):
    """Content wrapped in outer quotes with \\n should be JSON-decoded."""
    from forge_agent import ForgeAgent
    from config import get_model_config

    result = ForgeAgent._unescape_content('"import os\\nprint(\\"hello\\")"')
    assert result == 'import os\nprint("hello")'


def test_unescape_backslash_n_without_quotes(tmp_path):
    """Content with \\n literals but no outer quotes gets newlines restored."""
    from forge_agent import ForgeAgent

    result = ForgeAgent._unescape_content('line1\\nline2\\nline3')
    assert result == 'line1\nline2\nline3'


def test_unescape_normal_content_unchanged(tmp_path):
    """Normal content without escaping passes through unchanged."""
    from forge_agent import ForgeAgent

    content = "# Normal Python\nimport os\nprint('hello')\n"
    assert ForgeAgent._unescape_content(content) == content


def test_unescape_json_with_forward_slash(tmp_path):
    """Content with \\/ (escaped forward slash) should be handled."""
    from forge_agent import ForgeAgent

    result = ForgeAgent._unescape_content('"\\/\\/ comment\\nimport os"')
    assert '// comment' in result
    assert 'import os' in result
    assert '\\n' not in result
