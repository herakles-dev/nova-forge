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


# ── analyze_goal — edge cases ─────────────────────────────────────────────

class TestAnalyzeGoalEdgeCases:
    def test_empty_goal_returns_all_false(self):
        """Empty string has no signals."""
        a = make_assistant()
        ctx = a.analyze_goal("")
        assert ctx["has_auth"] is False
        assert ctx["has_data"] is False
        assert ctx["has_frontend"] is False
        assert ctx["has_api"] is False
        assert ctx["has_realtime"] is False
        assert ctx["complexity_hint"] == "simple"
        assert ctx["goal"] == ""
        assert ctx["stack"] == ""

    def test_visual_without_frontend(self):
        """Visual keywords alone can set has_visual=True even without frontend."""
        a = make_assistant()
        ctx = a.analyze_goal("a beautiful styled modern design theme")
        assert ctx["has_visual"] is True

    def test_complexity_medium(self):
        """2-3 feature signals -> medium complexity."""
        a = make_assistant()
        ctx = a.analyze_goal("A user login page with stored data")
        assert ctx["complexity_hint"] == "medium"
        assert ctx["has_auth"] is True
        assert ctx["has_data"] is True

    def test_stack_flask_triggers_api(self):
        a = make_assistant()
        ctx = a.analyze_goal("something", "fastapi")
        assert ctx["has_api"] is True

    def test_all_signals_triggered(self):
        """Goal with all keywords sets every flag."""
        a = make_assistant()
        ctx = a.analyze_goal(
            "A live chat app with user accounts, database storage, "
            "REST API, dashboard interface, real-time notifications"
        )
        assert ctx["has_auth"] is True
        assert ctx["has_data"] is True
        assert ctx["has_frontend"] is True
        assert ctx["has_api"] is True
        assert ctx["has_realtime"] is True
        assert ctx["has_visual"] is True
        assert ctx["complexity_hint"] == "ambitious"


# ── Question bank — completeness ──────────────────────────────────────────

class TestQuestionBankCompleteness:
    def test_all_8_categories_present(self):
        """DEEP_DIVE_QUESTIONS should have all 8 categories."""
        expected = {"features", "data", "auth", "visual_aesthetic",
                    "api_design", "realtime", "deployment", "testing"}
        assert set(DEEP_DIVE_QUESTIONS.keys()) == expected

    def test_no_duplicate_keys_across_categories(self):
        """Question keys should be unique across all categories."""
        seen_keys = set()
        for cat, questions in DEEP_DIVE_QUESTIONS.items():
            for q in questions:
                key = q["key"]
                assert key not in seen_keys, f"Duplicate key '{key}' in {cat}"
                seen_keys.add(key)

    def test_api_design_conditional_on_has_api(self):
        """API design questions only show when has_api=True."""
        ctx_yes = {"has_api": True}
        ctx_no = {"has_api": False}
        for q in DEEP_DIVE_QUESTIONS["api_design"]:
            assert q["condition"](ctx_yes) is True
            assert q["condition"](ctx_no) is False

    def test_realtime_conditional_on_has_realtime(self):
        """Realtime questions only show when has_realtime=True."""
        ctx_yes = {"has_realtime": True}
        ctx_no = {"has_realtime": False}
        for q in DEEP_DIVE_QUESTIONS["realtime"]:
            assert q["condition"](ctx_yes) is True
            assert q["condition"](ctx_no) is False

    def test_select_questions_have_choices(self):
        """All select/checkbox questions have choices or choices_fn."""
        for cat, questions in DEEP_DIVE_QUESTIONS.items():
            for q in questions:
                if q["type"] in ("select", "checkbox"):
                    has_choices = "choices" in q or "choices_fn" in q
                    assert has_choices, (
                        f"{cat}/{q['key']} is type={q['type']} but has no choices or choices_fn"
                    )


# ── Dynamic choices — edge cases ──────────────────────────────────────────

class TestDynamicChoicesEdgeCases:
    def test_feature_choices_no_duplicates(self):
        """Feature choices should have no duplicate values."""
        choices = _feature_choices({"goal": "a social analytics dashboard with exports"})
        values = [c[1] for c in choices]
        assert len(values) == len(set(values)), f"Duplicate values: {values}"

    def test_feature_choices_always_has_search_export_settings(self):
        """Even without goal keywords, search/export/settings are always offered."""
        choices = _feature_choices({"goal": ""})
        values = {c[1] for c in choices}
        assert "search" in values
        assert "export" in values
        assert "settings" in values

    def test_data_entity_with_comments_keyword(self):
        """'comment' keyword adds comments entity."""
        choices = _data_entity_choices({"goal": "blog with comments", "has_auth": False})
        values = {c[1] for c in choices}
        assert "comments" in values

    def test_data_entity_always_has_items(self):
        """Items/records entity is always present."""
        choices = _data_entity_choices({"goal": "", "has_auth": False})
        values = {c[1] for c in choices}
        assert "items" in values

    def test_feature_choices_notifications_keyword(self):
        """'notification' keyword adds notification feature."""
        choices = _feature_choices({"goal": "a task manager with notifications"})
        values = {c[1] for c in choices}
        assert "notifications" in values


# ── build_scope_summary — additional sections ────────────────────────────

class TestBuildScopeSummaryEdgeCases:
    def test_api_design_section(self):
        """API design info appears in scope summary."""
        a = make_assistant()
        core = {"goal": "API project"}
        deep = {"api_style": "rest", "api_auth": "jwt"}
        summary = a.build_scope_summary(core, deep)
        assert "## API Design" in summary
        assert "Style: rest" in summary
        assert "Auth: jwt" in summary

    def test_realtime_section(self):
        """Realtime type appears in scope summary."""
        a = make_assistant()
        core = {"goal": "Chat app"}
        deep = {"realtime_type": "websocket"}
        summary = a.build_scope_summary(core, deep)
        assert "## Real-time Features" in summary
        assert "websocket" in summary

    def test_risk_level_section(self):
        """Risk level from core answers appears in scope summary."""
        a = make_assistant()
        core = {"goal": "App", "risk": "high"}
        deep = {}
        summary = a.build_scope_summary(core, deep)
        assert "## Risk Level" in summary
        assert "high" in summary

    def test_data_entities_listed(self):
        """Data entities are listed individually."""
        a = make_assistant()
        core = {"goal": "App"}
        deep = {"database": "sqlite", "data_entities": ["users", "posts", "comments"]}
        summary = a.build_scope_summary(core, deep)
        assert "## Data Model" in summary
        assert "Entity: users" in summary
        assert "Entity: posts" in summary
        assert "Entity: comments" in summary

    def test_features_extra_text(self):
        """Extra features text is appended to core features."""
        a = make_assistant()
        core = {"goal": "App"}
        deep = {"features_main": ["create"], "features_extra": "Dark mode toggle"}
        summary = a.build_scope_summary(core, deep)
        assert "## Core Features" in summary
        assert "Dark mode toggle" in summary
