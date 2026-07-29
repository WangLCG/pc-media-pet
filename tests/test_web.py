from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_motion_notifications_use_the_web_audio_alert_after_connecting():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "function prepareAlertAudio()" in source
    assert "function playMotionAlert()" in source
    assert "prepareAlertAudio();" in source
    assert "playMotionAlert();" in source


def test_motion_timestamp_fallback_uses_china_standard_time():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert 'timeZone: "Asia/Shanghai"' in source
    assert 'hourCycle: "h23"' in source


def test_web_page_has_mobile_viewport_and_hidden_video_until_streaming():
    markup = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in markup
    assert 'id="remote-video" autoplay playsinline muted hidden' in markup
    assert "remoteVideo.hidden = false;" in source
    assert "remoteVideo.hidden = true;" in source


def test_mobile_styles_keep_controls_and_video_within_the_viewport():
    styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert "env(safe-area-inset-top)" in styles
    assert ".camera-controls { display: grid; gap: .75rem; }" in styles
    assert "@media (min-width: 30rem)" in styles
    assert "aspect-ratio: 16 / 9" in styles
    assert "object-fit: contain" in styles
