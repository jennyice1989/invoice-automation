from __future__ import annotations

from app.main import AuditApplyRequest, FinalizeRequest, _should_receive_initial_consignment


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


def test_finalize_request_tracks_inventory_by_default():
    body = FinalizeRequest(invoice_id=123)

    assert body.track_inventory_for_products is True
    assert body.receive_immediately is False


def test_audit_apply_request_can_enable_inventory_tracking():
    body = AuditApplyRequest(enable_inventory_tracking=True)

    assert body.enable_inventory_tracking is True
