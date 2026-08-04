"""Frontend smoke tests for voice UI elements."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_voice_push_to_talk_button_exists_in_markup():
    markup = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="voice-talk-button"' in markup
    assert "按住说话" in markup


def test_voice_status_indicator_exists():
    markup = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="voice-status"' in markup
    assert 'id="voice-hint"' in markup
    assert "语音通道" in markup


def test_voice_get_user_media_is_called():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "navigator.mediaDevices.getUserMedia" in source
    assert "audio: true" in source


def test_voice_replace_track_controls_mute():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "replaceTrack" in source
    assert "replaceTrack(null)" in source


def test_voice_transceiver_is_added_at_connection_time():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'addTransceiver("audio"' in source
    assert '"sendonly"' in source


def test_voice_button_has_pointer_event_handlers():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert 'voiceTalkButton.addEventListener("pointerdown"' in source
    assert 'voiceTalkButton.addEventListener("pointerup"' in source
    assert 'voiceTalkButton.addEventListener("pointerleave"' in source


def test_voice_full_prompt_is_displayed_on_deny():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "语音通道已满" in source


def test_voice_button_css_has_recording_and_ready_states():
    styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert ".voice-talk-button" in styles
    assert 'data-state="recording"' in styles
    assert 'data-state="ready"' in styles
    assert "@keyframes voice-recording-pulse" in styles
