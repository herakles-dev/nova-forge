"""Tests for forge_prompt — selection menus, helpers, build_model_choices."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import asyncio
from unittest.mock import patch, AsyncMock

import pytest
import questionary
from questionary import Choice, Separator
from prompt_toolkit.keys import Keys

from forge_prompt import (
    ask_select,
    ask_confirm,
    ask_text,
    ask_checkbox,
    build_model_choices,
    _auto_instruction,
    _add_escape_binding,
    _first_selectable,
    PROMPT_STYLE,
)


# ── _first_selectable ────────────────────────────────────────────────────────

class TestFirstSelectable:
    """Test the non-TTY fallback helper."""

    def test_plain_strings(self):
        assert _first_selectable(["a", "b", "c"]) == "a"

    def test_choices(self):
        choices = [Choice(title="Alpha", value="alpha"), Choice(title="Beta", value="beta")]
        assert _first_selectable(choices) == "alpha"

    def test_skips_separators(self):
        choices = [Separator("group"), Choice(title="First", value="first")]
        assert _first_selectable(choices) == "first"

    def test_skips_disabled(self):
        choices = [
            Choice(title="Disabled", value="no", disabled="reason"),
            Choice(title="Enabled", value="yes"),
        ]
        assert _first_selectable(choices) == "yes"

    def test_all_separators_returns_none(self):
        assert _first_selectable([Separator("a"), Separator("b")]) is None

    def test_empty_list(self):
        assert _first_selectable([]) is None

    def test_mixed(self):
        choices = [
            Separator("── Group ──"),
            Choice(title="Disabled", value="d", disabled="nope"),
            Separator("── Next ──"),
            Choice(title="OK", value="ok"),
        ]
        assert _first_selectable(choices) == "ok"


# ── _auto_instruction ────────────────────────────────────────────────────────

class TestAutoInstruction:
    """Test dynamic instruction text generation."""

    def test_default(self):
        inst = _auto_instruction()
        assert "arrow keys" in inst
        assert "Enter" in inst
        assert "filter" not in inst

    def test_with_shortcuts(self):
        inst = _auto_instruction(use_shortcuts=True)
        assert "shortcut" in inst

    def test_with_search(self):
        inst = _auto_instruction(use_search_filter=True)
        assert "filter" in inst

    def test_both(self):
        inst = _auto_instruction(use_shortcuts=True, use_search_filter=True)
        assert "shortcut" in inst
        assert "filter" in inst


# ── ask_select non-TTY fallback ──────────────────────────────────────────────

class TestAskSelectNonTTY:
    """Non-TTY should return default or first selectable, never block."""

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_returns_default_when_set(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_select("Pick", ["a", "b"], default="b"))
        assert result == "b"

    def test_returns_first_selectable_without_default(self):
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_select("Pick", ["x", "y"]))
        assert result == "x"

    def test_skips_separator_in_fallback(self):
        choices = [Separator("group"), Choice(title="Real", value="real")]
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_select("Pick", choices))
        assert result == "real"

    def test_skips_disabled_in_fallback(self):
        choices = [
            Choice(title="No", value="no", disabled="reason"),
            Choice(title="Yes", value="yes"),
        ]
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_select("Pick", choices))
        assert result == "yes"


# ── build_model_choices ──────────────────────────────────────────────────────

class TestBuildModelChoices:
    """Test the rich model choice builder."""

    def test_returns_choices_and_separators(self):
        choices = build_model_choices()
        types = {type(c) for c in choices}
        assert Choice in types
        assert Separator in types

    def test_all_7_models_present(self):
        choices = build_model_choices()
        values = [c.value for c in choices if isinstance(c, Choice)]
        expected = ["nova-lite", "nova-pro", "nova-premier",
                    "gemini-flash", "gemini-pro", "claude-sonnet", "claude-haiku"]
        for alias in expected:
            assert alias in values, f"{alias} missing from model choices"

    def test_3_provider_groups(self):
        choices = build_model_choices()
        seps = [c for c in choices if isinstance(c, Separator)]
        assert len(seps) == 3  # Bedrock, Google, Anthropic

    def test_descriptions_populated(self):
        choices = build_model_choices()
        for c in choices:
            if isinstance(c, Choice) and not isinstance(c, Separator):
                assert c.description, f"{c.value} has no description"

    def test_current_model_marked(self):
        choices = build_model_choices(current_model="gemini-flash")
        flash = next(c for c in choices if isinstance(c, Choice) and c.value == "gemini-flash")
        assert "(current)" in flash.title

    def test_unavailable_providers_disabled(self):
        providers = {"bedrock": True, "openrouter": False, "anthropic": False}
        choices = build_model_choices(available_providers=providers)

        for c in choices:
            if isinstance(c, Choice):
                if c.value in ("nova-lite", "nova-pro", "nova-premier"):
                    assert not c.disabled, f"{c.value} should be enabled"
                elif c.value in ("gemini-flash", "gemini-pro", "claude-sonnet", "claude-haiku"):
                    assert c.disabled, f"{c.value} should be disabled"

    def test_provider_status_in_separator(self):
        providers = {"bedrock": True, "openrouter": False, "anthropic": True}
        choices = build_model_choices(available_providers=providers)
        seps = [c for c in choices if isinstance(c, Separator)]
        # Check that status text appears in separator titles
        sep_titles = [s.title for s in seps]
        assert any("ready" in t for t in sep_titles)
        assert any("needs /login" in t for t in sep_titles)


# ── PROMPT_STYLE ─────────────────────────────────────────────────────────────

class TestPromptStyle:
    """Verify style includes description entry."""

    def test_style_is_style_object(self):
        assert PROMPT_STYLE is not None

    def test_description_style_defined(self):
        # The Style object should have been created with a description rule
        style_list = PROMPT_STYLE.style_rules
        names = [rule[0] for rule in style_list]
        assert "description" in names


# ── Separator re-export ──────────────────────────────────────────────────────

class TestEscapeBinding:
    """Test Escape key binding injection."""

    def test_escape_binding_injected(self):
        """_add_escape_binding adds an escape handler to a Question's key bindings."""
        question = questionary.select("Pick", choices=["a", "b"])
        original_bindings = question.application.key_bindings

        _add_escape_binding(question)

        # After injection, key_bindings should be a merged set (different object)
        assert question.application.key_bindings is not original_bindings

        # The merged bindings should contain an Escape handler
        merged = question.application.key_bindings
        escape_found = any(
            Keys.Escape in binding.keys
            for binding in merged.bindings
        )
        assert escape_found, "Escape key binding not found in merged bindings"

    def test_escape_binding_returns_question(self):
        """_add_escape_binding returns the question for chaining."""
        question = questionary.select("Pick", choices=["a", "b"])
        result = _add_escape_binding(question)
        assert result is question


class TestAutoInstructionEscape:
    """Test that instruction hints include Esc to cancel."""

    def test_default_includes_escape(self):
        inst = _auto_instruction()
        assert "Esc to cancel" in inst

    def test_with_shortcuts_includes_escape(self):
        inst = _auto_instruction(use_shortcuts=True)
        assert "Esc to cancel" in inst

    def test_with_search_includes_escape(self):
        inst = _auto_instruction(use_search_filter=True)
        assert "Esc to cancel" in inst


class TestSeparatorReExport:
    """Verify Separator is importable from forge_prompt."""

    def test_separator_importable(self):
        from forge_prompt import Separator as Sep
        assert Sep is Separator


# ── ask_confirm non-TTY fallback ────────────────────────────────────────

class TestAskConfirmNonTTY:
    """Non-TTY fallback for ask_confirm."""

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_returns_true_default(self):
        """Default=True returns True when non-TTY."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_confirm("Continue?", default=True))
        assert result is True

    def test_returns_false_default(self):
        """Default=False returns False when non-TTY."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_confirm("Continue?", default=False))
        assert result is False


# ── ask_text non-TTY fallback ───────────────────────────────────────────

class TestAskTextNonTTY:
    """Non-TTY fallback for ask_text."""

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_returns_default(self):
        """Returns default value when non-TTY."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_text("Name?", default="test"))
        assert result == "test"

    def test_returns_none_no_default(self):
        """Returns None when non-TTY and no default."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_text("Name?"))
        assert result is None


# ── ask_checkbox non-TTY fallback ───────────────────────────────────────

class TestAskCheckboxNonTTY:
    """Non-TTY fallback for ask_checkbox."""

    def _run(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_returns_empty_list(self):
        """Non-TTY returns empty list."""
        with patch.object(sys.stdin, "isatty", return_value=False):
            result = self._run(ask_checkbox("Pick", ["a", "b", "c"]))
        assert result == []


# ── build_model_choices — edge cases ────────────────────────────────────

class TestBuildModelChoicesEdgeCases:
    """Edge cases for model choice builder."""

    def test_no_providers_all_enabled(self):
        """When available_providers is None, all models are enabled."""
        choices = build_model_choices(available_providers=None)
        for c in choices:
            if isinstance(c, Choice) and not isinstance(c, Separator):
                assert not c.disabled, f"{c.value} should be enabled when no providers given"

    def test_all_providers_disabled(self):
        """When all providers disabled, all models are disabled."""
        providers = {"bedrock": False, "openrouter": False, "anthropic": False}
        choices = build_model_choices(available_providers=providers)
        for c in choices:
            if isinstance(c, Choice) and not isinstance(c, Separator):
                assert c.disabled, f"{c.value} should be disabled"

    def test_no_current_model_no_marker(self):
        """When current_model is None, no choice has (current) marker."""
        choices = build_model_choices(current_model=None)
        for c in choices:
            if isinstance(c, Choice) and not isinstance(c, Separator):
                assert "(current)" not in c.title

    def test_choices_values_are_strings(self):
        """All choice values are non-empty strings."""
        choices = build_model_choices()
        for c in choices:
            if isinstance(c, Choice) and not isinstance(c, Separator):
                assert isinstance(c.value, str)
                assert len(c.value) > 0


# ── _first_selectable — edge cases ─────────────────────────────────────

class TestFirstSelectableEdgeCases:
    """Additional edge cases for _first_selectable."""

    def test_only_disabled_returns_none(self):
        """All choices disabled returns None."""
        choices = [
            Choice(title="A", value="a", disabled="reason"),
            Choice(title="B", value="b", disabled="reason"),
        ]
        assert _first_selectable(choices) is None

    def test_plain_strings_first_returned(self):
        """Plain string list returns first element."""
        assert _first_selectable(["alpha", "beta", "gamma"]) == "alpha"

    def test_single_choice_returns_its_value(self):
        """Single non-disabled choice returns its value."""
        choices = [Choice(title="Only", value="only")]
        assert _first_selectable(choices) == "only"
