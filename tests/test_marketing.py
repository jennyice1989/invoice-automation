"""Marketing Center safety and workflow tests."""
from app.marketing import MarketingDraft, Decision, SYSTEM_PROMPT, serialize
import pytest
from pydantic import ValidationError


def test_draft_serialization_and_default_status():
    post = MarketingDraft(title="Aquarium Care Tip", category="education",
        caption="Test water parameters regularly.",
        creative_brief="Real store aquarium photo", source_notes="General care tip")
    post.id = 1
    from datetime import datetime
    post.created_at = datetime(2026, 11, 4)
    data = serialize(post)
    assert data["status"] == "pending"
    assert data["platforms"] == ["Facebook", "Instagram"]
    assert data["title"] == "Aquarium Care Tip"


def test_only_owner_approval_actions_accepted():
    for action in ("approve", "reject"):
        assert Decision(action=action).action == action
    with pytest.raises(ValidationError):
        Decision(action="publish")


def test_policy_prohibits_unverified_inventory_and_prices():
    assert "Never claim" in SYSTEM_PROMPT
    assert "stock" in SYSTEM_PROMPT
    assert "No livestock shipping promises" in SYSTEM_PROMPT


def test_edit_rejects_empty_content():
    from app.marketing import DraftEdit, RevisionRequest
    with pytest.raises(ValidationError):
        DraftEdit(caption="", creative_brief="Use real photography")
    with pytest.raises(ValidationError):
        RevisionRequest(instruction="x")


def test_weekly_run_is_unique_per_week():
    from app.marketing import MarketingWeeklyRun
    assert any(c.primary_key for c in MarketingWeeklyRun.__table__.columns)
    assert MarketingWeeklyRun.__table__.c.week_key.primary_key
