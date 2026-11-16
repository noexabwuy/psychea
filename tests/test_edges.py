# Boundaries and arithmetic that the behaviour tests did not pin down (found by mutation testing).
import time
import types
from unittest import mock

import pytest

from psychea import cli, sources
from psychea.protocol import video_frame
from psychea.session import MAX_ISO_ERRORS
from psychea.sources import Output
from psychea.transport import DriverMissing, UsbError, UsbGone, UsbStall
from tests.test_protocol import EYEPET_START_VIDEO
from tests.test_session import FakeStillSource, msg, session


class Clock:
    # time.monotonic()/sleep() under the test's control: sleeping moves the clock.

    def __init__(self, now=1000.0):
        self.now, self.slept = now, []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def module(self):
        return types.SimpleNamespace(monotonic=self.time, sleep=self.sleep)


def fields_of(caplog, event):
    return [(r.levelname, r.fields) for r in caplog.records if getattr(r, "fields", {}).get("event") == event]


# ---- command-line values

@pytest.mark.parametrize("text, whole, value", [("0.01", False, 0.01), ("2", False, 2.0), ("7", True, 7)])
def test_number_accepts_values_from_the_lower_bound(text, whole, value):
    parsed = cli.number("--x", 0.01 if text == "0.01" else 0, whole)(text)
    assert parsed == value and type(parsed) is (int if whole else float)


@pytest.mark.parametrize("text, whole, message", [
    ("1.5", True, "--x: '1.5' is not a whole number"),
    ("abc", False, "--x: 'abc' is not a number"),
    ("0.0099", False, "--x: '0.0099' is out of range (must be at least 0.01)"),
    ("nan", False, "--x: 'nan' is out of range (must be at least 0.01)"),
    ("inf", False, "--x: 'inf' is out of range (must be at least 0.01)")])
def test_number_rejects_with_a_message_that_names_option_and_value(text, whole, message):
    with pytest.raises(ValueError) as e:
        cli.number("--x", 0.01, whole)(text)
    assert str(e.value) == message


@pytest.mark.parametrize("text, size", [("160x120", (160, 120)), ("4096x4096", (4096, 4096)),
                                        ("1280X720", (1280, 720))])
def test_resolution_accepts_the_whole_range_and_either_case_of_x(text, size):
    assert cli.resolution(text) == size


@pytest.mark.parametrize("text", ["159x120", "160x119", "4097x4096", "4096x4097", "1280x720x5", "x720", "1280x",
                                  "12 80x720", "-1280x720", "1280"])
def test_resolution_rejects_everything_else(text):
    with pytest.raises(ValueError, match="--camres: %r is not WIDTHxHEIGHT" % text):
        cli.resolution(text)


# ---- handshake window

def test_the_handshake_is_retried_every_20_ms_and_each_new_error_is_logged_once(monkeypatch, caplog):
    clock = Clock()
    monkeypatch.setattr(cli, "time", clock.module())
    caplog.set_level("DEBUG", logger="psychea")
    errors = iter([UsbError("a"), UsbError("a"), UsbError("b")])

    def connect():
        e = next(errors, None)
        if e is None:
            return "T", "ok"
        raise e

    assert cli.connect_within_window(connect) == ("T", 4)
    assert clock.slept == [0.02, 0.02, 0.02]
    attempts = fields_of(caplog, "handshake_attempt")
    assert [(f["attempt"], f["error"]) for _, f in attempts] == [(1, "UsbError: a"), (3, "UsbError: b")]
    assert [f["ms"] for _, f in attempts] == [0, 40]


def test_a_missing_driver_is_a_warning_with_the_way_out_not_a_quiet_debug_line(monkeypatch, caplog):
    monkeypatch.setattr(cli, "time", Clock().module())
    caplog.set_level("DEBUG", logger="psychea")
    errors = iter([DriverMissing("the WinUSB driver is not installed (run --install-driver)")])

    def connect():
        e = next(errors, None)
        if e is None:
            return "T", "ok"
        raise e

    cli.connect_within_window(connect)
    [(level, fields)] = fields_of(caplog, "handshake_attempt")
    assert level == "WARNING" and "--install-driver" in fields["error"]


def test_the_handshake_window_is_exclusive_at_its_end(monkeypatch):
    clock = Clock()
    clock.sleep = lambda seconds: setattr(clock, "now", clock.now + 0.25)        # exact in binary: no rounding
    monkeypatch.setattr(cli, "time", clock.module())
    monkeypatch.setattr(cli, "ATTEMPT_WINDOW", 1.0)
    calls = []

    def connect():
        calls.append(clock.now)
        raise UsbError("no")

    transport, attempts = cli.connect_within_window(connect)
    assert transport is None and attempts == len(calls) == 4          # at 0, .25, .5, .75 s; 1.0 s is outside


# ---- JPEG quality search

def size_at(frame, q):
    import cv2
    return len(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])[1])


@pytest.fixture(scope="module")
def noisy_frame():
    # Blurred noise on a gradient: size grows with every quality step from 4 up (below that the tables
    # saturate).
    import cv2
    import numpy as np
    base = np.linspace(0, 255, 160, dtype=np.float32)[None, :, None].repeat(96, 0).repeat(3, 2)
    noise = cv2.GaussianBlur(np.random.default_rng(3).normal(0, 40, (96, 160, 3)).astype(np.float32), (0, 0), 1.2)
    return np.clip(base + noise, 0, 255).astype(np.uint8)


@pytest.mark.parametrize("target, hint", [(40, 40), (40, 34), (40, 46), (40, 1), (40, 100), (5, 11), (5, 1), (6, 12),
                                          (99, 93), (99, 100), (100, 94), (100, 100), (100, 1), (60, 53), (60, 67)])
def test_fit_quality_takes_a_picture_that_fills_the_budget_exactly(noisy_frame, target, hint):
    budget = size_at(noisy_frame, target)             # fits at `target` and not one step higher (or is the top)
    data, q = sources.fit_quality(noisy_frame, budget, hint)
    assert q == target and len(data) == budget


def test_fit_quality_returns_the_smallest_picture_when_even_quality_1_is_too_big(noisy_frame):
    data, q = sources.fit_quality(noisy_frame, size_at(noisy_frame, 1) - 1, 1)
    assert q == 1 and len(data) == size_at(noisy_frame, 1)
    data, q = sources.fit_quality(noisy_frame, size_at(noisy_frame, 1) - 1, 80)
    assert q == 1


# ---- Session timing and counters

class Source:
    def __init__(self, jpeg=b"\xff\xd8x\xff\xd9", age=0.0123):
        self.jpeg, self._age, self.next_send = jpeg, age, 0.0

    def get(self):
        return self.jpeg

    def age(self):
        return self._age


def test_send_frame_reports_size_send_time_and_picture_age_to_the_stats(monkeypatch):
    s, t = session()
    clock = Clock()
    s.source = Source(b"\xff\xd8" + b"x" * 98)
    seen = []
    s.stats.frame = lambda *a: seen.append(a)

    def send(data, lens):
        clock.now += 0.004
        return 0
    t.send_video = send
    monkeypatch.setattr("psychea.session.time", clock.module())
    s.send_frame()
    assert seen == [(100, pytest.approx(4.0), pytest.approx(12.3))]
    assert s.frames == 1 and s.iso_errors == 0


def test_send_frame_logs_the_first_three_frames_and_every_failed_one(caplog):
    s, t = session()
    s.source = Source()
    caplog.set_level("DEBUG", logger="psychea")
    for _ in range(5):
        s.send_frame()
    logged = fields_of(caplog, "frame")
    assert [(lvl, f["n"], f["failed"]) for lvl, f in logged] == [("DEBUG", 0, 0), ("DEBUG", 1, 0), ("DEBUG", 2, 0)]
    assert logged[0][1]["bytes"] > 5 and logged[0][1]["packets"] >= 1
    t.send_video = lambda data, lens: 2
    s.send_frame()
    s.send_frame()
    later = [(lvl, f["n"], f["failed"]) for lvl, f in fields_of(caplog, "frame")[3:]]
    assert later == [("WARNING", 5, 2), ("WARNING", 6, 2)]
    assert s.iso_errors == 4 and s.frames == 7


def test_send_frame_sends_the_framed_picture_and_the_log_line_tells_size_and_failure(caplog):
    s, t = session()
    s.source = Source(b"\xff\xd8" + b"x" * 98)
    caplog.set_level("DEBUG", logger="psychea")
    s.send_frame()
    data, lens = video_frame(s.source.jpeg)
    assert t.frames == [(data, lens)]
    t.send_video = lambda data, lens: 2
    s.send_frame()
    first, second = [r.getMessage() for r in caplog.records if getattr(r, "fields", {}).get("event") == "frame"]
    assert first == "Frame 0: %d bytes in %d packets" % (len(data), len(lens))
    assert second == "Frame 1: %d bytes in %d packets, the transfer failed" % (len(data), len(lens))


def test_the_next_frame_is_not_sent_before_it_is_due_but_exactly_on_time(monkeypatch):
    s, t = session()
    s.source = Source()
    clock = Clock()
    monkeypatch.setattr("psychea.session.time", clock.module())
    s.next_frame_at = 1000.5
    s.stream_due_frame(0.1)
    assert t.frames == []
    clock.now = 1000.5
    s.stream_due_frame(0.1)
    assert len(t.frames) == 1 and s.next_frame_at == pytest.approx(1000.6)


def test_the_schedule_does_not_try_to_catch_up_more_than_one_period(monkeypatch):
    s, t = session()
    s.source = Source()
    clock = Clock(2000.0)
    monkeypatch.setattr("psychea.session.time", clock.module())
    s.next_frame_at = 1000.0                                        # we were stalled for 1000 s
    s.stream_due_frame(0.1)
    assert s.next_frame_at == pytest.approx(1999.9)
    assert s.source.next_send == s.next_frame_at                    # the camera thread is told


def test_the_first_read_error_is_reported_even_right_after_the_clock_started(monkeypatch, caplog):
    s, t = session()
    monkeypatch.setattr("psychea.session.time", Clock(1.0).module())     # a monotonic clock starts near 0 after boot

    def fail(wait):
        raise UsbError("x")
    t.read_message = fail
    s.poll()
    assert len(fields_of(caplog, "psp_read_failed")) == 1


def test_a_wall_clock_step_back_does_not_stall_the_video(monkeypatch):
    s, t = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    s.source = Source()
    wall = time.time
    monkeypatch.setattr(time, "time", lambda: wall() - 3600)       # the user or NTP sets the clock back an hour
    s.stream_due_frame(0.1)
    assert len(t.frames) == 1


def test_a_failed_switch_off_after_a_good_photo_is_a_warning_and_the_session_goes_on(caplog):
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()
    s.source = FakeStillSource()
    t.set_video_alt = mock.Mock(side_effect=[None, UsbStall("stall")])
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))
    [(level, _)] = fields_of(caplog, "still_stop_failed")
    assert level == "WARNING" and not s.gone


def test_a_pulled_cable_found_by_the_presence_check_ends_the_serve_loop(monkeypatch, caplog):
    s, t = session()
    s.present = lambda: False
    t.read_message = lambda timeout_ms: None                    # a quiet PSP: nothing to read, nothing wrong
    clock = Clock()

    def sleep(seconds):
        assert len(clock.slept) < 5000, "serve did not stop"        # fail instead of hanging
        clock.sleep(seconds)
    monkeypatch.setattr("psychea.session.time", types.SimpleNamespace(monotonic=clock.time, sleep=sleep))
    caplog.set_level("INFO", logger="psychea")
    s.serve()
    assert [(level, f["how"]) for level, f in fields_of(caplog, "unplugged")] == [("INFO", "not_detected")]


def test_a_session_without_a_source_still_reports_how_it_ended(caplog):
    s, t = session()
    s.open_source = lambda: True
    s.gone = True
    caplog.set_level("INFO", logger="psychea")
    s.run()
    assert [f["frames"] for _, f in fields_of(caplog, "session_end")] == [0]


def test_a_failed_transfer_is_counted_and_slows_down_for_100_ms(monkeypatch, caplog):
    s, t = session()
    s.source = Source()
    clock = Clock()
    monkeypatch.setattr("psychea.session.time", clock.module())

    def fail(data, lens):
        raise UsbError("boom")
    t.send_video = fail
    s.stream_due_frame(0.1)
    assert s.iso_errors == 1 and clock.slept == [0.1] and s.streaming is False
    assert fields_of(caplog, "video_transfer_failed")


def test_the_stream_stops_after_too_many_failed_transfers(monkeypatch):
    s, t = session()
    s.source, s.streaming = Source(), True
    clock = Clock()
    monkeypatch.setattr("psychea.session.time", clock.module())

    def fail(data, lens):
        raise UsbError("boom")
    t.send_video = fail
    for _ in range(MAX_ISO_ERRORS - 1):
        s.stream_due_frame(0.1)
        assert s.streaming and s.iso_errors > 0
    s.stream_due_frame(0.1)
    assert s.streaming is False and s.iso_errors == 0


def test_an_unplugged_psp_during_streaming_ends_the_session(monkeypatch):
    s, t = session()
    s.source = Source()

    def gone(data, lens):
        raise UsbGone()
    t.send_video = gone
    s.stream_due_frame(0.1)
    assert s.gone is True and s.iso_errors == 0


@pytest.mark.parametrize("streaming, due_in, expected", [
    (False, 0.0, 10), (True, 0.0075, 7), (True, 0.0, 1), (True, -3.0, 1), (True, 0.0004, 1), (True, 5.0, 10),
    (True, 0.0105, 10)])
def test_poll_waits_for_a_message_until_the_next_frame_is_due_at_most_10_ms(monkeypatch, streaming, due_in, expected):
    s, t = session()
    clock = Clock()
    monkeypatch.setattr("psychea.session.time", clock.module())
    s.streaming, s.next_frame_at = streaming, clock.now + due_in
    waits = []
    t.read_message = lambda wait: waits.append(wait)
    s.poll()
    assert waits == [expected]


def test_poll_hands_the_message_to_dispatch():
    s, t = session()
    got = []
    s.dispatch = got.append
    t.read_message = lambda wait: b"abc"
    s.poll()
    assert got == [b"abc"]


def test_read_errors_are_reported_at_most_every_two_seconds(monkeypatch, caplog):
    s, t = session()
    clock = Clock()
    monkeypatch.setattr("psychea.session.time", clock.module())
    s.last_read_error = 0.0

    def fail(wait):
        raise UsbError("x")
    t.read_message = fail
    s.poll()
    clock.now += 2.0
    s.poll()
    clock.now += 0.5
    s.poll()
    clock.now += 1.6
    s.poll()
    assert len(fields_of(caplog, "psp_read_failed")) == 2          # at 1000.0 and 1002.5, not at 1002.0 or 1004.1
    assert s.last_read_error == 1002.5


# ---- webcam set-up

def test_the_webcam_is_configured_with_the_expected_properties(monkeypatch):
    import cv2
    sets = []

    class Cap:
        def __init__(self, *a):
            pass

        def isOpened(self):
            return True

        def set(self, prop, value):
            sets.append((prop, value))
            return True

        def get(self, prop):
            return 0.0

        def read(self):
            import numpy as np
            return True, np.zeros((480, 640, 3), np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(cv2, "VideoCapture", Cap)
    sources.Webcam(0, Output()).close()
    assert sets == [(cv2.CAP_PROP_BUFFERSIZE, 1), (cv2.CAP_PROP_FRAME_WIDTH, 640), (cv2.CAP_PROP_FRAME_HEIGHT, 480),
                    (cv2.CAP_PROP_FPS, 30)]
    sets.clear()
    sources.Webcam(0, Output(), (1280, 720)).close()
    assert sets[1] == (cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
    assert sets[2:4] == [(cv2.CAP_PROP_FRAME_WIDTH, 1280), (cv2.CAP_PROP_FRAME_HEIGHT, 720)]
