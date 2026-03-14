"""Tests for deep planning interview: analyze_goal, question bank, scope summary."""

import pytest
from forge_assistant import (
    ForgeAssistant,
    DEEP_DIVE_QUESTIONS,
    _feature_choices,
    _data_entity_choices,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

class FakeShell:
    """Minimal shell stub for ForgeAssistant."""
    def __init__(self):
        self.state = {}
        self.project_path = None
        self.config = {}


def make_assistant() -> ForgeAssistant:
    return ForgeAssistant(FakeShell())


# ── analyze_goal tests ─────────────────────────────────────────────────────────

class TestAnalyzeGoal:
    def test_detects_auth(self):
        a = make_assistant()
        ctx = a.analyze_goal("A social app with user accounts and login")
        assert ctx["has_auth"] is True

    def test_detects_frontend(self):
        a = make_assistant()
        ctx = a.analyze_goal("A dashboard for tracking expenses")
        assert ctx["has_frontend"] is True
        assert ctx["has_visual"] is True

    def test_detects_api_no_frontend(self):
        a = make_assistant()
        ctx = a.analyze_goal("REST API for inventory management")
        assert ctx["has_api"] is True
        assert ctx["has_frontend"] is False

    def test_detects_data(self):
        a = make_assistant()
        ctx = a.analyze_goal("Track my daily habits and store history")
        assert ctx["has_data"] is True

    def test_detects_realtime(self):
        a = make_assistant()
        ctx = a.analyze_goal("A live chat application with notifications")
        assert ctx["has_realtime"] is True

    def test_complexity_simple(self):
        a = make_assistant()
        ctx = a.analyze_goal("A todo app")
        assert ctx["complexity_hint"] == "simple"

    def test_complexity_ambitious(self):
        a = make_assistant()
        ctx = a.analyze_goal(
            "A real-time collaboration platform with video chat, "
            "file sharing, user accounts, team management, REST API, "
            "analytics dashboard, and notification system"
        )
        assert ctx["complexity_hint"] == "ambitious"

    def test_stack_contributes_to_detection(self):
        a = make_assistant()
        ctx = a.analyze_goal("Build something", "flask")
        assert ctx["has_api"] is True  # "flask" triggers server detection

    def test_preserves_goal_and_stack(self):
        a = make_assistant()
        ctx = a.analyze_goal("My project", "node")
        assert ctx["goal"] == "My project"
        assert ctx["stack"] == "node"


# ── Question bank tests ───────────────────────────────────────────────────────

class TestQuestionBank:
    def test_features_always_present(self):
        """Features questions are always asked regardless of context."""
        for q in DEEP_DIVE_QUESTIONS["features"]:
            assert q["condition"]({"has_auth": False, "has_data": False}) is True

    def test_auth_skipped_when_no_auth(self):
        """Auth questions are skipped when has_auth=False."""
        ctx = {"has_auth": False}
        for q in DEEP_DIVE_QUESTIONS["auth"]:
            assert q["condition"](ctx) is False

    def test_auth_included_when_has_auth(self):
        ctx = {"has_auth": True}
        for q in DEEP_DIVE_QUESTIONS["auth"]:
            assert q["condition"](ctx) is True

    def test_visual_included_for_frontend(self):
        """Visual aesthetic questions included when has_frontend=True."""
        ctx = {"has_frontend": True}
        for q in DEEP_DIVE_QUESTIONS["visual_aesthetic"]:
            assert q["condition"](ctx) is True

    def test_visual_skipped_for_api_only(self):
        """Visual aesthetic questions skipped when has_frontend=False."""
        ctx = {"has_frontend": False}
        for q in DEEP_DIVE_QUESTIONS["visual_aesthetic"]:
            assert q["condition"](ctx) is False

    def test_data_questions_conditional(self):
        ctx_yes = {"has_data": True}
        ctx_no = {"has_data": False}
        for q in DEEP_DIVE_QUESTIONS["data"]:
            assert q["condition"](ctx_yes) is True
            assert q["condition"](ctx_no) is False

    def test_deployment_always_asked(self):
        for q in DEEP_DIVE_QUESTIONS["deployment"]:
            assert q["condition"]({}) is True

    def test_testing_always_asked(self):
        for q in DEEP_DIVE_QUESTIONS["testing"]:
            assert q["condition"]({}) is True

    def test_all_questions_have_required_keys(self):
        """Every question has condition, question, type, and key."""
        for cat, questions in DEEP_DIVE_QUESTIONS.items():
            for q in questions:
                assert "condition" in q, f"Missing condition in {cat}"
                assert "question" in q, f"Missing question in {cat}"
                assert "type" in q, f"Missing type in {cat}"
                assert "key" in q, f"Missing key in {cat}"
                assert q["type"] in ("select", "checkbox", "text", "confirm"), \
                    f"Invalid type {q['type']} in {cat}"


# ── Dynamic choices tests ──────────────────────────────────────────────────────

class TestDynamicChoices:
    def test_feature_choices_always_has_crud(self):
        choices = _feature_choices({"goal": "anything"})
        values = {c[1] for c in choices}
        assert "create" in values
        assert "view" in values
        assert "edit" in values
        assert "delete" in values

    def test_feature_choices_adds_share_for_social(self):
        choices = _feature_choices({"goal": "a social app where users share photos"})
        values = {c[1] for c in choices}
        assert "share" in values

    def test_feature_choices_adds_analytics_for_dashboard(self):
        choices = _feature_choices({"goal": "an analytics dashboard with charts"})
        values = {c[1] for c in choices}
        assert "analytics" in values

    def test_data_entity_choices_adds_users_for_auth(self):
        choices = _data_entity_choices({"goal": "app", "has_auth": True})
        values = {c[1] for c in choices}
        assert "users" in values

    def test_data_entity_choices_no_users_without_auth(self):
        choices = _data_entity_choices({"goal": "app", "has_auth": False})
        values = {c[1] for c in choices}
        assert "users" not in values


# ── build_scope_summary tests ─────────────────────────────────────────────────

class TestBuildScopeSummary:
    def test_includes_all_sections(self):
        a = make_assistant()
        core = {"goal": "Recipe sharing app", "stack": "Flask", "risk": "low"}
        deep = {
            "features_main": ["create", "view", "share"],
            "database": "postgres",
            "auth_method": "session",
            "color_scheme": "dark",
            "deployment": "tunnel",
            "testing": "basic",
        }
        summary = a.build_scope_summary(core, deep)
        assert "## User Intent" in summary
        assert "Recipe sharing app" in summary
        assert "## Tech Stack" in summary
        assert "## Core Features" in summary
        assert "## Data Model" in summary
        assert "## Authentication" in summary
        assert "## Visual Aesthetic" in summary
        assert "## Deployment" in summary
        assert "## Testing Strategy" in summary

    def test_includes_visual_aesthetic_details(self):
        a = make_assistant()
        core = {"goal": "Dashboard", "stack": "flask"}
        deep = {
            "color_scheme": "dark",
            "layout_style": "dashboard",
            "css_approach": "tailwind",
            "dark_mode": "Yes",
            "responsive": "Yes",
            "animation_level": "subtle",
            "visual_inspiration": "like Linear",
        }
        summary = a.build_scope_summary(core, deep)
        assert "## Visual Aesthetic" in summary
        assert "Color scheme: dark" in summary
        assert "Layout: dashboard" in summary
        assert "CSS framework: tailwind" in summary
        assert "Dark mode: Yes" in summary
        assert "Inspiration: like Linear" in summary

    def test_omits_empty_sections(self):
        a = make_assistant()
        core = {"goal": "Simple script"}
        deep = {}
        summary = a.build_scope_summary(core, deep)
        assert "## User Intent" in summary
        assert "## Visual Aesthetic" not in summary
        assert "## Authentication" not in summary
        assert "## Data Model" not in summary

    def test_extra_notes_included(self):
        a = make_assistant()
        core = {"goal": "An app"}
        deep = {"extra_notes": "Must support dark mode toggle"}
        summary = a.build_scope_summary(core, deep)
        assert "## Additional Requirements" in summary
        assert "dark mode toggle" in summary


# ── get_deep_dive_questions tests ──────────────────────────────────────────────

class TestGetDeepDiveQuestions:
    def test_returns_applicable_questions(self):
        a = make_assistant()
        ctx = a.analyze_goal("A dashboard app with user login and database")
        questions = a.get_deep_dive_questions(ctx)
        categories = {q["category"] for q in questions}
        assert "features" in categories
        assert "visual_aesthetic" in categories  # has_frontend=True
        assert "auth" in categories  # has_auth=True
        assert "data" in categories  # has_data=True

    def test_api_only_skips_visual(self):
        a = make_assistant()
        ctx = a.analyze_goal("A REST API for data processing")
        # Override to ensure no frontend detection
        ctx["has_frontend"] = False
        ctx["has_visual"] = False
        questions = a.get_deep_dive_questions(ctx)
        categories = {q["category"] for q in questions}
        assert "visual_aesthetic" not in categories

    def test_always_includes_deployment_and_testing(self):
        a = make_assistant()
        ctx = {"goal": "anything", "has_auth": False, "has_data": False,
               "has_frontend": False, "has_api": False, "has_realtime": False}
        questions = a.get_deep_dive_questions(ctx)
        categories = {q["category"] for q in questions}
        assert "deployment" in categories
        assert "testing" in categories
        assert "features" in categories
