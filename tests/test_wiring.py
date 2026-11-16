# The glue around the pure logic: test pattern, microphone, camera thread, start-up (from mutation testing).
import struct
import sys
import types

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")
from psychea import cli, sources  # noqa: E402
from psychea.settings import Settings  # noqa: E402
from tests.test_edges import Clock, fields_of  # noqa: E402
from tests.test_webcam import FakeCapture  # noqa: E402


def decode(jpeg):
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def near(pixel, colour, tolerance=14):
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(pixel, colour))


# ---- test pattern

def test_the_test_pattern_shows_eight_colour_bars_at_the_games_size():
    pattern = sources.TestPattern(sources.Output(w=480, h=272))
    img = decode(pattern.get())
    assert img.shape == (272, 480, 3)
    for i, colour in enumerate(sources.TestPattern.BARS):
        assert near(img[250, i * 60 + 30], colour), i
    smaller = decode(sources.TestPattern(sources.Output(w=240, h=136)).get())
    assert smaller.shape == (136, 240, 3)


def test_the_grey_box_moves_8_pixels_per_picture_and_wraps_after_400():
    pattern = sources.TestPattern(sources.Output())
    frames = [decode(pattern.get()) for _ in range(52)]
    assert near(frames[0][135, 35], (128, 128, 128)) and not near(frames[0][135, 115], (128, 128, 128))
    assert near(frames[10][135, 115], (128, 128, 128)) and not near(frames[10][135, 35], (128, 128, 128))
    assert near(frames[51][135, 35 + 8], (128, 128, 128))           # 51 * 8 = 408 -> x = 8 after the wrap


def test_the_test_pattern_has_no_camera_to_wait_for_or_close():
    pattern = sources.TestPattern(sources.Output())
    assert pattern.age() == 0.0 and pattern.next_send == 0.0
    assert pattern.reset() is None and pattern.close() is None


def test_the_test_photo_is_cut_to_the_requested_size():
    pattern = sources.TestPattern(sources.Output())
    img = decode(pattern.still(640, 480, 200000))
    assert img.shape == (480, 640, 3)
    assert near(img[420, 360], sources.TestPattern.BARS[4])         # the 5th bar, below the lettering
    assert decode(pattern.still(480, 272, 200000)).shape == (272, 480, 3)


# ---- microphone

class FakeStream:
    instances = []

    def __init__(self, **kw):
        self.kw, self.calls = kw, []
        FakeStream.instances.append(self)

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def close(self):
        self.calls.append("close")


@pytest.fixture
def fake_sounddevice(monkeypatch):
    FakeStream.instances = []
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=FakeStream))
    return FakeStream


def test_the_pc_microphone_is_a_low_latency_16_bit_mono_stream(fake_sounddevice):
    mic = sources.PcMicSource(22050, 3, 0.5)
    kw = fake_sounddevice.instances[0].kw
    callback = kw.pop("callback")
    assert kw == {"samplerate": 22050, "channels": 1, "dtype": "int16", "blocksize": 0, "latency": "low", "device": 3}
    assert callback == mic._on_audio and fake_sounddevice.instances[0].calls == ["start"]
    assert mic.chain.gain == 0.5 and mic.rate == 22050 and mic.have == 0 and len(mic.buf) == 0


asked = []                                        # the devices wasapi_sounddevice() was asked about


def wasapi_sounddevice(monkeypatch, api_name):
    # A sounddevice whose device 33 belongs to the host API api_name.
    FakeStream.instances = []
    asked.clear()
    sd = types.SimpleNamespace(
        RawInputStream=FakeStream,
        query_devices=lambda device: asked.append(device) or {"hostapi": 2},
        query_hostapis=lambda index: {"name": api_name},
        WasapiSettings=lambda **kw: ("wasapi", kw))
    monkeypatch.setitem(sys.modules, "sounddevice", sd)


def test_a_wasapi_microphone_converts_the_sample_rate(monkeypatch):
    # WASAPI shared mode refuses 22050 Hz unless Windows may convert (PortAudio error -9997).
    wasapi_sounddevice(monkeypatch, "Windows WASAPI")
    sources.PcMicSource(22050, 33)
    assert FakeStream.instances[0].kw["extra_settings"] == ("wasapi", {"auto_convert": True}) and asked == [33]


def test_other_host_apis_get_no_extra_settings(monkeypatch):
    wasapi_sounddevice(monkeypatch, "MME")
    sources.PcMicSource(22050, 1)
    assert "extra_settings" not in FakeStream.instances[0].kw


def test_the_default_device_and_a_failing_lookup_get_no_extra_settings(monkeypatch):
    wasapi_sounddevice(monkeypatch, "Windows WASAPI")
    sources.PcMicSource(22050, None)                                 # the default device: nothing to look up
    sd = sys.modules["sounddevice"]
    sd.query_devices = lambda device: 1 / 0                          # unknown or ambiguous device name
    sources.PcMicSource(22050, "nope")
    assert all("extra_settings" not in s.kw for s in FakeStream.instances)


def test_closing_the_microphone_stops_then_closes_the_stream_and_ignores_errors(fake_sounddevice):
    mic = sources.PcMicSource(22050)
    mic.close()
    assert fake_sounddevice.instances[0].calls == ["start", "stop", "close"]
    mic.st.stop = lambda: 1 / 0
    mic.close()                                                    # an unplugged sound card must not raise


def test_make_source_gives_the_pc_microphone_with_the_users_device_and_gain(fake_sounddevice):
    source = sources.make_source(Settings(mic="pc", micdev=2, micgain=0.4), 22050)
    assert isinstance(source, sources.PcMicSource)
    assert fake_sounddevice.instances[0].kw["device"] == 2 and source.chain.gain == 0.4


def test_make_source_falls_back_to_the_tone_and_says_why(monkeypatch, caplog):
    def broken(**kw):
        raise OSError("no sound card")
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawInputStream=broken))
    source = sources.make_source(Settings(mic="pc"), 11025)
    assert isinstance(source, sources.ToneSource) and source.rate == 11025
    [(level, fields)] = fields_of(caplog, "mic_source_unavailable")
    assert level == "WARNING" and "no sound card" in str(fields["error"])
    assert "--list" in caplog.text and "--mic off" in caplog.text


def test_make_source_uses_the_tone_without_touching_the_sound_card(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", None)          # importing it would fail
    for mode in ("tone", "off"):
        assert isinstance(sources.make_source(Settings(mic=mode), 22050), sources.ToneSource)


# ---- camera thread

@pytest.fixture
def camera(monkeypatch):
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)


def test_opening_the_camera_logs_its_size_and_starts_the_capture_thread(camera, caplog):
    caplog.set_level("INFO", logger="psychea")

    class Sized(FakeCapture):
        def get(self, prop):
            return {cv2.CAP_PROP_FRAME_WIDTH: 640.0, cv2.CAP_PROP_FRAME_HEIGHT: 480.0, cv2.CAP_PROP_FPS: 29.97}[prop]
    cv2.VideoCapture = Sized
    cam = sources.Webcam(4, sources.Output())
    try:
        [(_, f)] = fields_of(caplog, "camera_open")
        assert (f["index"], f["w"], f["h"]) == (4, 640, 480) and f["fps"] == 30.0
        assert "Camera 4 opened: 640x480 at 30 fps" in caplog.text
        assert cam.th.daemon and cam.th.is_alive() and cam.err is None and cam.generation == 0
        assert cam.raw.shape == (480, 640, 3) and cam.next_send == 0.0
    finally:
        cam.close()


def test_a_camera_that_cannot_deliver_a_picture_fails_the_start(monkeypatch):
    class Dead(FakeCapture):
        def read(self):
            return False, None
    monkeypatch.setattr(cv2, "VideoCapture", Dead)
    with pytest.raises(RuntimeError, match="webcam read failed"):
        sources.Webcam(0, sources.Output())


def test_a_camera_that_dies_later_is_reported_by_get_and_still(camera):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    cam.err = "webcam read failed"
    for call in (cam.get, lambda: cam.still(100, 100, 10000)):
        with pytest.raises(RuntimeError, match="webcam read failed"):
            call()


def test_a_photo_needs_a_camera_frame(camera):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    cam.raw = None
    with pytest.raises(RuntimeError, match="no camera frame yet"):
        cam.still(100, 100, 10000)


def test_a_finished_picture_is_published_but_one_from_before_a_reset_is_dropped(camera, monkeypatch):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    cam.jpeg = None
    frame = np.zeros((480, 640, 3), np.uint8)
    cam._encode = lambda small: b"fresh"
    cam._encode_frame(frame, 5.0)
    assert cam.jpeg == b"fresh" and cam.captured_at == 5.0
    cam.reset()
    assert cam.jpeg is None and cam.generation == 1

    def encode_then_reset(small):
        cam.reset()                                                # the game restarted the camera meanwhile
        return b"stale"
    cam._encode = encode_then_reset
    cam._encode_frame(frame, 6.0)
    assert cam.jpeg is None


def test_the_encode_time_estimate_moves_a_fifth_of_the_way_to_each_measurement(camera, monkeypatch):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    cam._encode = lambda small: b"x"
    clock = Clock(1000.5)
    monkeypatch.setattr(sources, "time", clock.module())
    cam.enc_s = 1.0
    cam._encode_frame(np.zeros((480, 640, 3), np.uint8), 1000.0)   # took 0.5 s
    assert cam.enc_s == pytest.approx(0.9)


def test_the_picture_age_is_the_time_since_it_was_captured(camera, monkeypatch):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    cam.captured_at = 40.0
    monkeypatch.setattr(sources, "time", Clock(40.25).module())
    assert cam.age() == pytest.approx(0.25)


def test_closing_ends_the_thread_and_releases_the_camera_once_per_close(camera):
    cam = sources.Webcam(0, sources.Output())
    cam.close()
    assert cam.stop is True and not cam.th.is_alive() and cam.cap.released is True


# ---- start-up

def test_a_failed_handshake_is_reported_and_no_session_is_started(monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(cli, "wait_until", lambda cond, step: seen.append(step))
    monkeypatch.setattr(cli, "connect_within_window", lambda connect: (None, 7))
    monkeypatch.setattr(cli, "run_session", lambda *a: seen.append("session"))
    cli.serve_once(Settings(), lambda: None)
    assert seen == [0.01, 0.2]
    [(level, fields)] = fields_of(caplog, "handshake_failed")
    assert level == "ERROR" and fields["attempts"] == 7 and "(7 attempts)" in caplog.text


def test_a_crashing_session_is_logged_and_the_cable_is_released(monkeypatch, caplog):
    caplog.set_level("INFO", logger="psychea")
    closed = []

    class Boom:
        def __init__(self, *a):
            pass

        def run(self):
            raise ValueError("kaput")
    monkeypatch.setattr(cli, "Session", Boom)
    cli.run_session(types.SimpleNamespace(close=lambda: closed.append(1)), Settings())
    [(level, _)] = fields_of(caplog, "session_crashed")
    assert level == "ERROR" and "ValueError('kaput')" in caplog.text and closed == [1]
    assert fields_of(caplog, "waiting_unplug")
    assert any(r.exc_info for r in caplog.records if r.getMessage().startswith("The session crashed"))


def test_the_ready_message_names_the_log_file(tmp_path, caplog):
    caplog.set_level("INFO", logger="psychea")
    path = tmp_path / "x.log"
    cli.announce_ready(path)
    [(_, fields)] = fields_of(caplog, "logfile")
    assert fields["path"] == str(path.resolve()) and str(path.resolve()) in caplog.text
    assert "Ready." in caplog.text and "Ctrl+C quits" in caplog.text


def test_main_hands_the_parsed_settings_to_logging_and_the_server(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.logs, "setup", lambda verbose, logfile: calls.append(("setup", verbose, logfile)))
    monkeypatch.setattr(cli.logs, "describe_environment", lambda s, argv: calls.append(("env", s.verbose, argv)))
    monkeypatch.setattr(cli, "connector", lambda s: "connect")

    def stop(settings, connect):
        calls.append(("serve", settings.testpattern, connect))
        raise KeyboardInterrupt
    monkeypatch.setattr(cli, "serve_once", stop)
    monkeypatch.setattr(cli.os, "_exit", lambda code: calls.append(("exit", code)))
    cli.main(["--verbose", "--testpattern", "--logfile", "z.log"])
    assert calls == [("setup", True, "z.log"), ("env", True, ["--verbose", "--testpattern", "--logfile", "z.log"]),
                     ("serve", True, "connect"), ("exit", 0)]


def test_a_stream_that_cannot_start_is_closed_before_the_error_goes_up(fake_sounddevice, monkeypatch):
    # A stream that failed to start (device busy) is closed at once, not left to garbage collection.
    def start(self):
        raise OSError("device busy")
    monkeypatch.setattr(fake_sounddevice, "start", start)
    with pytest.raises(OSError, match="device busy"):
        sources.PcMicSource(22050)
    assert fake_sounddevice.instances[0].calls == ["stop", "close"]


def test_the_stream_callback_copies_what_portaudio_hands_over_and_takes_the_lock(fake_sounddevice):
    # PortAudio reuses its buffer after the callback returns, so the bytes must be copied inside it.
    mic = sources.PcMicSource(22050)
    mic.chain.band = False                                           # compare the samples, not the filter output
    callback = fake_sounddevice.instances[0].kw["callback"]
    block = bytearray(struct.pack("<4h", 100, 200, 300, 400))
    callback(block, 4, None, None)
    block[:] = bytes(8)                                              # the driver writes the next block into it
    assert mic.have == 8 and struct.unpack("<4h", mic.read(4)) == (100, 200, 300, 400)
    assert not mic.lock.locked() and hasattr(mic.lock, "acquire")
