import numpy as np

from app.audio import SoundDetector
from app.config import Settings
from app.event_bus import EventBus


def settings(**overrides) -> Settings:
    values = {
        "app_token": "test-token-that-is-long-enough",
        "audio_loudness_threshold_dbfs": -35,
        "audio_loudness_override_dbfs": -22,
        "audio_confirm_frames": 2,
        "audio_cooldown_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


def pcm(amplitude: int) -> bytes:
    samples = np.full(320, amplitude, dtype="<i2")
    return samples.tobytes()


def test_detector_ignores_quiet_audio():
    detector = SoundDetector(settings(), EventBus())
    assert detector.process_pcm_frame(pcm(50), now=0) is None


def test_detector_reports_sustained_loud_non_speech_audio():
    detector = SoundDetector(settings(), EventBus())
    assert detector.process_pcm_frame(pcm(10_000), now=0) is None
    event = detector.process_pcm_frame(pcm(10_000), now=0.02)

    assert event is not None
    assert event.rms_dbfs > -22
    assert event.confidence > 0


def test_detector_filters_sound_until_the_configured_number_of_hits_is_met():
    detector = SoundDetector(settings(audio_confirm_frames=3), EventBus())

    assert detector.process_pcm_frame(pcm(10_000), now=0) is None
    assert detector.process_pcm_frame(pcm(10_000), now=0.02) is None
    assert detector.process_pcm_frame(pcm(50), now=0.04) is None
    assert detector.process_pcm_frame(pcm(10_000), now=0.06) is None
    assert detector.process_pcm_frame(pcm(10_000), now=0.08) is None
    assert detector.process_pcm_frame(pcm(10_000), now=0.10) is not None


def test_detector_observes_cooldown():
    detector = SoundDetector(settings(), EventBus())
    detector.process_pcm_frame(pcm(10_000), now=0)
    assert detector.process_pcm_frame(pcm(10_000), now=0.02) is not None
    detector.process_pcm_frame(pcm(10_000), now=1)
    assert detector.process_pcm_frame(pcm(10_000), now=1.02) is None


def test_detector_restarts_confirmation_count_after_sound_cooldown():
    detector = SoundDetector(settings(audio_confirm_frames=2, audio_cooldown_seconds=10), EventBus())

    assert detector.process_pcm_frame(pcm(10_000), now=0) is None
    assert detector.process_pcm_frame(pcm(10_000), now=0.02) is not None

    assert detector.process_pcm_frame(pcm(10_000), now=1) is None
    assert detector.process_pcm_frame(pcm(10_000), now=1.02) is None

    assert detector.process_pcm_frame(pcm(10_000), now=10.02) is None
    assert detector.process_pcm_frame(pcm(10_000), now=10.04) is not None
