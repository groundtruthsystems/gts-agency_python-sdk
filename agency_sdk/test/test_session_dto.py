"""DTO tests for the session-reporting delegate.

`SessionCommandResponse` is transcribed from the live server schema
(`SessionCommandResponse` / `SessionUpdateCommand` in gts-agency-control, per the
design's §6.0 "transcribe, never assert" rule). `SessionStatus` encodes the
-1/0/2 status int map (③ `control_plane_client.py:176,179`). `AnalyticsEvent` is
the cross-agent shared event shape promoted verbatim from ③'s `event.py`.
"""

from datetime import UTC, datetime

from agency_sdk.delegates.session_dto import AnalyticsEvent, SessionCommandResponse, SessionStatus


def test_session_status_int_map():
    assert int(SessionStatus.FAILED) == -1
    assert int(SessionStatus.COMPLETED) == 0
    assert int(SessionStatus.IN_PROGRESS) == 2


def test_session_command_response_minimal():
    response = SessionCommandResponse(**{"success": True, "message": "updated"})

    assert response.success is True
    assert response.message == "updated"
    assert response.update is None


def test_session_command_response_carries_update_subresult():
    response = SessionCommandResponse(**{"success": True, "message": "ok", "update": {"status": 2}})

    assert response.update == {"status": 2}


def test_analytics_event_new_populates_id_and_defaults():
    event = AnalyticsEvent.new(correlation="run-1", event_type="pipeline.start")

    assert event.id is not None
    assert event.correlation == "run-1"
    assert event.event_type == "pipeline.start"
    assert event.payload == {}
    assert isinstance(event.timestamp, datetime)


def test_analytics_event_serializes_timestamp_in_fixed_format():
    event = AnalyticsEvent(
        id="e1",
        correlation="run-1",
        event_type="pipeline.step",
        timestamp=datetime(2026, 7, 17, 9, 30, 5, tzinfo=UTC),
    )

    dumped = event.model_dump()

    assert dumped["timestamp"] == "2026-07-17 09:30:05"


def test_analytics_event_step_event_sets_step_fields():
    event = AnalyticsEvent.step_event(
        correlation="run-1",
        event_type="pipeline.step",
        step_name="extract",
        workflow_name="dbq",
        duration_ms=1200,
    )

    assert event.step_name == "extract"
    assert event.workflow_name == "dbq"
    assert event.duration_ms == 1200


def test_analytics_event_allows_null_id():
    event = AnalyticsEvent(id=None, correlation="c", event_type="t")

    assert event.id is None
