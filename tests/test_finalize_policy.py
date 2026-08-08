from __future__ import annotations

from app.main import _should_receive_initial_consignment


def test_receive_now_is_not_blocked_by_queued_enrichment():
    assert _should_receive_initial_consignment(
        receive_immediately=True,
        queued_for_enrichment_count=2,
    ) is True


def test_receive_now_stays_off_when_user_did_not_request_it():
    assert _should_receive_initial_consignment(
        receive_immediately=False,
        queued_for_enrichment_count=0,
    ) is False
