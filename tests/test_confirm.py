import json

import pytest

from dials.confirm import format_side, parse_payload

PAYLOAD = {
    "slot": "4",
    "existing": {"label": "Spotify", "match_class": "spotify",
                 "monitor": "HDMI-0", "rect": [0.0, 0.0, 0.5, 1.0]},
    "incoming": {"label": "Slack", "match_class": "slack",
                 "monitor": "HDMI-0", "rect": [0.5, 0.0, 0.5, 1.0]},
}


def test_parse_round_trips_a_valid_payload():
    parsed = parse_payload(json.dumps(PAYLOAD))
    assert parsed["slot"] == "4"
    assert parsed["existing"]["label"] == "Spotify"


def test_parse_rejects_malformed_json():
    with pytest.raises(ValueError):
        parse_payload("{not json")


def test_parse_rejects_a_payload_missing_a_side():
    with pytest.raises(ValueError):
        parse_payload(json.dumps({"slot": "4", "existing": PAYLOAD["existing"]}))


def test_parse_rejects_a_payload_with_no_slot():
    with pytest.raises(ValueError):
        parse_payload(json.dumps({"existing": {}, "incoming": {}}))


def test_format_side_shows_label_class_monitor_and_percentages():
    text = format_side(PAYLOAD["incoming"])
    assert "Slack" in text
    assert "slack" in text
    assert "HDMI-0" in text
    assert "50" in text            # rect rendered as percentages


def test_format_side_tolerates_a_missing_rect():
    text = format_side({"label": "X", "match_class": "x", "monitor": "HDMI-0"})
    assert "X" in text
