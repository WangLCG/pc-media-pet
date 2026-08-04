"""Voice protocol model validation tests."""

import pytest
from pydantic import ValidationError

from app.models import (
    MESSAGE_ID_PATTERN,
    NotifyEnvelope,
    VoiceDeniedPayload,
    VoiceGrantedPayload,
    VoiceStartPayload,
)


def test_voice_start_payload_defaults_to_opus():
    payload = VoiceStartPayload()
    assert payload.codec == "opus"


def test_voice_granted_payload_generates_unique_voice_ids():
    a = VoiceGrantedPayload()
    b = VoiceGrantedPayload()
    assert a.voice_id != b.voice_id
    assert a.voice_id.startswith("voice_")


def test_voice_granted_payload_voice_id_matches_pattern():
    payload = VoiceGrantedPayload()
    import re
    assert re.match(f"^{MESSAGE_ID_PATTERN}$", payload.voice_id)


def test_voice_denied_payload_includes_reason_and_count():
    payload = VoiceDeniedPayload(reason="voice_senders_full", current_senders=3, max_senders=3)
    assert payload.reason == "voice_senders_full"
    assert payload.current_senders == 3
    assert payload.max_senders == 3


def test_voice_denied_payload_audio_device_error():
    payload = VoiceDeniedPayload(reason="audio_device_error", current_senders=1, max_senders=3)
    assert payload.reason == "audio_device_error"


def test_voice_denied_payload_rejects_invalid_reason():
    with pytest.raises(ValidationError):
        VoiceDeniedPayload(reason="bogus_reason")


def test_notify_envelope_accepts_voice_message_types():
    for msg_type in ("voice_start", "voice_granted", "voice_denied", "voice_stop"):
        envelope = NotifyEnvelope(version=1, type=msg_type, id="msg_01", ts=0, payload={})
        assert envelope.type == msg_type


def test_notify_envelope_rejects_invalid_voice_type():
    with pytest.raises(ValidationError):
        NotifyEnvelope(version=1, type="voice_unknown", id="msg_01", ts=0, payload={})
