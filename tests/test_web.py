from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_motion_notifications_use_the_web_audio_alert_after_connecting():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function prepareAlertAudio()" in source
    assert "function playMotionAlert()" in source
    assert "prepareAlertAudio();" in source
    assert "playMotionAlert();" in source
    assert "function playAlertTone()" in source
    assert "prepareAlertAudio().then((ready) => { if (ready) playAlertTone(); })" in source
    assert 'document.addEventListener("pointerdown", () => { void prepareAlertAudio(); });' in source


def test_camera_page_offers_capability_based_resolution_and_zoom_controls():
    markup = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")

    assert 'id="resolution-options"' in markup
    assert 'id="zoom-in"' in markup and 'id="zoom-out"' in markup
    assert 'fetch("/api/camera/capabilities"' in source
    assert "width: selectedResolution.width" in source
    assert "function setZoom(value)" in source
    assert "Math.min(3, value)" in source and "Math.max(.5" in source
    assert ".resolution-options" in styles
    assert "transform: scale(var(--zoom, 1))" in styles


def test_web_page_has_mobile_viewport_and_video_stage_until_streaming():
    markup = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in markup
    assert 'id="video-stage" class="video-stage" hidden' in markup
    assert "videoStage.hidden = false;" in source
    assert "videoStage.hidden = true;" in source


def test_mobile_styles_keep_controls_and_video_within_the_viewport():
    styles = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    assert "env(safe-area-inset-top)" in styles
    assert ".camera-controls { display: grid; gap: .75rem; }" in styles
    assert "@media (min-width: 34rem)" in styles
    assert "aspect-ratio: 16 / 9" in styles
    assert "object-fit: contain" in styles
