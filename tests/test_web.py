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
