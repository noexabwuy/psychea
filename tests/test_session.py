# Session against a fake transport: what the PSP sees for each message it sends.
import struct
from unittest import mock

import pytest

from psychea.session import Session
from psychea.settings import Settings
from psychea.sources import Output
from psychea.transport import UsbGone, UsbStall
from tests.test_protocol import EYEPET_START_MIC, EYEPET_START_VIDEO


class FakeTransport:
    def __init__(self):
        self.replies, self.alts, self.frames, self.mic_calls, self.messages = [], [], [], [], []
        self.fail_reply = None

    def reply(self, cmd, data):
        if self.fail_reply:
            raise self.fail_reply
        self.replies.append((cmd, data))

    def set_video_alt(self, alt):
        self.alts.append(alt)

    def send_video(self, data, lens):
        self.frames.append((data, lens))
        return 0

    def start_mic(self, source, rate, extended):
        self.mic_calls.append(("start", rate, extended))

    def stop_mic(self):
        self.mic_calls.append(("stop",))

    def read_message(self, timeout_ms):
        if not self.messages:
            raise UsbGone()
        return self.messages.pop(0)


def msg(ident, cmd, payload=b""):
    return struct.pack("<HBB", ident, cmd, len(payload)) + payload


def session(**over):
    t = FakeTransport()
    s = Session(t, Settings(mic="tone", webcam=None, **over), present=lambda: True, out=Output())
    return s, t


def test_hello_is_answered_with_flip_mirror_lens():
    s, t = session()
    s.dispatch(msg(2, 0x2B))
    assert t.replies == [(0x2B, b"\x00\x00\x01\x00")]


def test_camera_type_reply_announces_frames_with_a_trailer():
    s, t = session()
    s.dispatch(msg(3, 0x09))
    assert t.replies == [(9, struct.pack("<H", 0x0500))]


def test_flip_mirror_message_is_applied_and_reported_in_the_next_hello():
    s, t = session()
    s.dispatch(msg(2, 0xAB, b"\x01\x00"))
    s.dispatch(msg(2, 0x2B))
    assert t.replies == [(0x2B, b"\x01\x00\x01\x00")]


def test_unknown_queries_get_four_zero_bytes():
    s, t = session()
    s.dispatch(msg(2, 0x40))
    s.dispatch(msg(3, 0x43))
    assert t.replies == [(0x40, bytes(4)), (0x43, bytes(4))]


def test_start_video_adapts_to_the_game_and_starts_streaming():
    s, t = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert s.streaming and t.alts == [1]
    assert (s.out.w, s.out.h, s.out.max_jpeg) == (480, 272, 18816)      # 30 fps: the bandwidth limit
    assert s.fps == 30


def test_stop_video_selects_alt_0():
    s, t = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    s.dispatch(msg(2, 0x02))
    assert not s.streaming and t.alts == [1, 0]


def test_a_due_frame_is_sent_as_one_framed_transfer():
    s, t = session()
    s.source = mock.Mock(get=lambda: b"\xff\xd8" + b"x" * 2000 + b"\xff\xd9", age=lambda: 0.0)
    s.stream_due_frame(1 / 15)
    (data, lens), = t.frames
    assert lens[-1] == 16 and s.frames == 1


def test_a_frame_is_not_sent_before_its_time():
    s, t = session()
    s.source = mock.Mock(get=lambda: b"\xff\xd8\xff\xd9", age=lambda: 0.0)
    s.next_frame_at = 1e18
    s.stream_due_frame(1 / 15)
    assert t.frames == []


def test_five_failed_transfers_stop_the_stream():
    s, t = session()
    s.streaming = True
    s.source = mock.Mock(get=lambda: b"\xff\xd8\xff\xd9", age=lambda: 0.0)
    t.send_video = mock.Mock(side_effect=UsbStall("boom"))
    with mock.patch("psychea.session.time.sleep"):
        for _ in range(5):
            s.next_frame_at = 0
            s.stream_due_frame(1 / 15)
    assert not s.streaming


def test_microphone_starts_with_the_requested_rate_and_stops():
    s, t = session()
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC))
    s.dispatch(msg(1, 0x02))
    assert ("start", 22050, False) in t.mic_calls and t.mic_calls[-1] == ("stop",)


def test_extended_microphone_mode_is_reported_to_the_transport():
    s, t = session()
    s.dispatch(msg(1, 0x03, EYEPET_START_MIC + b"\x00"))
    assert ("start", 22050, True) in t.mic_calls


def test_microphone_off_never_touches_the_transport():
    t = FakeTransport()
    s = Session(t, Settings(mic="off", webcam=None), present=lambda: True)
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC))
    assert t.mic_calls == []


def test_unplugging_ends_the_session():
    s, t = session()
    s.run()
    assert s.gone


def test_reply_to_a_vanished_device_marks_the_session_gone():
    s, t = session()
    t.fail_reply = UsbGone()
    s.dispatch(msg(2, 0x2B))
    assert s.gone


def test_the_frame_rate_follows_the_game():
    s, _ = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert s.fps == 30


def test_a_60_fps_request_is_limited_to_30():
    s, _ = session()
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[2] = 7
    s.dispatch(msg(2, 0x01, bytes(cfg)))
    assert s.fps == 30


def test_the_source_is_told_when_the_next_frame_will_be_sent():
    s, _ = session()
    s.source = mock.Mock(get=lambda: b"\xff\xd8x\xff\xd9", age=lambda: 0.0)
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert s.source.next_send == s.next_frame_at
    s.next_frame_at = 0.0
    s.stream_due_frame(0.5)
    assert s.source.next_send == s.next_frame_at and s.next_frame_at > 0.0


def test_frame_statistics_are_logged_once_per_period(caplog):
    s, t = session()
    s.source = mock.Mock(get=lambda: b"\xff\xd8\xff\xd9", age=lambda: 0.02, q=72)
    s.stats.start -= 11
    caplog.set_level("INFO", logger="psychea")
    s.stream_due_frame(1 / 30)
    s.log_stats()
    out = caplog.text
    assert "Video:" in out and "JPEG quality 72" in out and "picture delay 20 ms" in out
    caplog.clear()
    s.log_stats()
    assert "Video:" not in caplog.text


def test_known_camera_settings_are_named_in_the_log_and_need_no_reply(caplog):
    caplog.set_level("DEBUG", logger="psychea")
    s, t = session()
    s.dispatch(msg(2, 0xA9, b"\x00"))
    s.dispatch(msg(2, 0xA5, b"\x07"))
    assert t.replies == []
    assert "SET_ANTI_FLICKER" in caplog.text and "SET_BRIGHTNESS" in caplog.text
    assert "does not know" not in caplog.text


def test_a_really_unknown_message_is_reported_once(caplog):
    caplog.set_level("INFO", logger="psychea")
    s, t = session()
    for _ in range(3):
        s.dispatch(msg(2, 0x77, b"\x01"))
    assert caplog.text.count("does not know") == 1


class FakeStillSource:
    def __init__(self):
        self.calls = []

    def still(self, w, h, max_bytes):
        self.calls.append((w, h, max_bytes))
        return b"\xff\xd8" + b"\x11" * 1000 + b"\xff\xd9"


def test_still_request_switches_the_video_interface_on_and_sends_one_picture():
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()
    s.source = FakeStillSource()
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))
    assert t.alts == [1, 0]           # on for the picture, off again afterwards (the camera app waits for that)
    assert s.source.calls == [(480, 272, 60_000)]      # buffer is 378 KB: our own cap applies (one 0xFC00 chunk)
    assert len(t.frames) == 1
    data, lens = t.frames[0]
    assert data[:2] == b"\xff\xd8" and data[-16:-12] == b"\xff\xfe\x00\x0a" and lens[-1] == 16
    assert sum(lens) == len(data)
    assert data[-7] == 1              # trailer byte 9: chunk counter 1 -> the PSP accepts the photo
    assert not s.streaming            # one picture, not a stream


def test_still_picture_is_capped_at_a_sane_size_even_for_a_huge_buffer():
    s, t = session()
    s.source = FakeStillSource()
    big = struct.pack("<I", 4_000_000) + b"\x00\x00\x02\x02" + b"\x00" * 15
    s.dispatch(msg(2, 0x03, big))
    assert s.source.calls == [(640, 480, 60_000)]


def test_unreadable_still_request_is_ignored_without_touching_the_usb():
    s, t = session()
    s.source = FakeStillSource()
    s.dispatch(msg(2, 0x03, b"\x00\x01"))
    assert t.alts == [] and t.frames == [] and s.source.calls == []


def test_still_picture_leaves_a_margin_when_the_psp_buffer_is_small():
    s, t = session()
    s.source = FakeStillSource()
    s.dispatch(msg(2, 0x03, struct.pack("<I", 50_000) + b"\x00\x00\x02\x02" + b"\x00" * 15))
    assert s.source.calls == [(640, 480, 50_000 - 4096)]


@pytest.mark.parametrize("cmd,field", [(0x05, "zoom"), (0xAC, "effect"), (0xA6, "contrast"),
                                       (0xA4, "saturation"), (0xA7, "sharpness"), (0xAF, "ev"), (0xA3, "wb")])
def test_picture_settings_from_the_camera_app_are_stored_for_the_picture(cmd, field):
    s, t = session()
    s.dispatch(msg(2, cmd, bytes([77])))
    assert getattr(s.out, field) == 77
    assert t.replies == []                 # the real camera never answers these


@pytest.mark.parametrize("wire,offset", [(0x00, 0), (0x05, 5), (0x7F, 127), (0x80, -1), (0x85, -6), (0xFF, -128)])
def test_brightness_arrives_as_sign_and_magnitude(wire, offset):
    s, t = session()
    s.dispatch(msg(2, 0xA5, bytes([wire])))
    assert s.out.brightness == 128 + offset


def test_start_video_resets_effect_and_zoom_from_its_config_but_keeps_exposure():
    # The camera app only re-sends an effect when it is not 'normal': the restart must clear the old one.
    # Exposure is not taken from the config: the XMB app and games use different neutral codes (10 and 8).
    s, t = session()
    s.dispatch(msg(2, 0xAC, bytes([2])))
    s.dispatch(msg(2, 0x05, bytes([30])))
    s.dispatch(msg(2, 0xAF, bytes([4])))
    cfg = bytearray(EYEPET_START_VIDEO)
    cfg[18], cfg[20] = 0, 0
    s.dispatch(msg(2, 0x01, bytes(cfg)))
    assert (s.out.effect, s.out.zoom, s.out.ev) == (0, 0, 4)
    cfg[18], cfg[20] = 3, 40
    s.dispatch(msg(2, 0x01, bytes(cfg)))
    assert (s.out.effect, s.out.zoom) == (3, 40)


def test_the_fps_figure_counts_only_the_time_the_camera_was_streaming():
    # The window starts when the cable is plugged in; only the seconds of real streaming count, and a restart
    # does not carry over the frames of the previous start.
    s, t = session()
    s.stats.start = 100.0
    s.stats.frame(10000, 1.0, 20.0)                    # left over from before the game started the camera
    with mock.patch("psychea.session.time.monotonic", return_value=108.0):
        s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert s.stats.sizes == [] and s.stats.start == 108.0
    s.stats.frame(10000, 1.0, 20.0)
    s.dispatch(msg(2, 0x02))
    assert s.stats.sizes == []


def test_every_camera_start_counts_its_own_frames_and_failures():
    # frames and iso_errors restart from 0 with every start: scattered failures of earlier starts do not add up
    # to "Too many failed video transfers", and 'Frame 0..2' is logged for every start.
    s, t = session()
    s.frames, s.iso_errors = 120, 4
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert (s.frames, s.iso_errors) == (0, 0)
    assert s.frames_total == 120


def test_the_first_frame_of_a_new_start_is_never_one_made_for_the_previous_start():
    s, t = session()
    s.source = mock.Mock(get=lambda: None, age=lambda: 0.0)
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    s.source.reset.assert_called_once()
    s.next_frame_at = 0.0
    s.stream_due_frame(0.1)
    assert t.frames == [] and s.next_frame_at > 0.0         # no picture yet: nothing sent, no failure counted
    assert s.iso_errors == 0


def test_run_makes_a_test_pattern_source():
    s, _ = session()
    s.run()
    assert type(s.source).__name__ == "TestPattern"


def test_the_test_pattern_draws_with_the_sessions_picture_settings():
    s, _ = session()
    assert s.make_source().out is s.out


def test_the_webcam_gets_its_number_the_picture_settings_and_the_requested_resolution():
    s, _ = session()
    s.settings.webcam, s.settings.camres = 2, (800, 600)
    with mock.patch("psychea.session.Webcam") as webcam:
        source = s.make_source()
    webcam.assert_called_once_with(2, s.out, (800, 600))
    assert source is webcam.return_value


def test_run_makes_a_webcam_source_when_a_webcam_is_chosen():
    s, _ = session()
    s.settings.webcam = 2
    with mock.patch("psychea.session.Webcam") as webcam:
        s.run()
    webcam.assert_called_once_with(2, s.out, None)
    assert s.source is webcam.return_value
    webcam.return_value.close.assert_called_once_with()          # the camera is released when the session ends


def test_run_stops_when_the_psp_is_no_longer_detected(monkeypatch, caplog):
    caplog.set_level("INFO", logger="psychea")
    import psychea.session as module
    monkeypatch.setattr(module, "PRESENCE_PERIOD", -1.0)
    s, t = session()
    s.present = lambda: False
    t.messages = [b""] * 100
    s.run()
    assert not s.gone
    assert "The PSP disconnected" in caplog.text
    assert "Session finished: 0 frames sent" in caplog.text


def test_run_streams_due_frames_and_counts_them_in_the_summary(monkeypatch):
    s, t = session()
    s.streaming = True
    s.source = None
    calls = []

    def fake_stream(period):
        calls.append(period)
        s.frames += 1
        s.gone = True
    monkeypatch.setattr(s, "stream_due_frame", fake_stream)
    monkeypatch.setattr(s, "poll", lambda: None)
    s.run()
    assert calls == [1.0 / s.fps]


# ---- robustness: what a long or disturbed session must survive

class StubSource:
    def __init__(self):
        self.next_send = 0.0

    def get(self):
        return b"\xff\xd8x\xff\xd9"

    def age(self):
        return 0.0


def test_scattered_failed_frames_do_not_add_up_to_a_stop():
    # iso_errors counted every failed write of the whole stream: the 5th lost frame in an hour stopped the video
    # for good. Only failures with no good frame in between may stop it.
    s, t = session()
    s.streaming, s.source = True, StubSource()
    results = iter([1, 1, 1, 1, 0] * 3)
    t.send_video = lambda data, lens: next(results)
    for _ in range(15):
        s.next_frame_at = 0
        s.stream_due_frame(1 / 15)
    assert s.streaming and s.iso_errors == 0


def problems(caplog):
    return [r.getMessage() for r in caplog.records if r.levelname in ("WARNING", "ERROR")]


@pytest.mark.parametrize("message", [msg(2, 0x01, EYEPET_START_VIDEO), msg(2, 0x02), msg(1, 0x01, EYEPET_START_MIC)])
def test_a_cable_pulled_during_a_handler_ends_the_session_without_a_warning(caplog, message):
    # The camera app closing makes the PSP leave USB right after STOP_VIDEO: the failed set-alt is the normal
    # end.
    s, t = session()
    caplog.set_level("DEBUG", logger="psychea")

    def gone(*args):
        raise UsbGone()
    t.set_video_alt = t.start_mic = gone
    s.dispatch(message)
    assert s.gone and problems(caplog) == []


def test_a_cable_pulled_while_stopping_the_microphone_ends_the_session():
    s, t = session()

    def gone():
        raise UsbGone()
    t.stop_mic = gone
    s.dispatch(msg(1, 0x02))
    assert s.gone


def test_every_way_of_noticing_the_unplug_logs_the_disconnect_once(caplog):
    s, t = session()
    caplog.set_level("INFO", logger="psychea")
    t.fail_reply = UsbGone()
    s.dispatch(msg(2, 0x2B))
    s.dispatch(msg(2, 0x2B))
    assert caplog.text.count("The PSP disconnected") == 1


def test_a_failed_restart_does_not_leave_the_stream_running_without_a_video_buffer():
    # START_VIDEO twice: the transport closes the old iso buffer before it switches the interface, so when the
    # switch fails there is nothing to send on and the next frame would raise inside the session.
    s, t = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert s.streaming

    def fail(alt):
        raise UsbStall("no")
    t.set_video_alt = fail
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    assert not s.streaming


def test_a_photo_that_cannot_be_made_is_reported_and_the_video_interface_is_switched_off(caplog):
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()

    class Broken:
        def still(self, w, h, max_bytes):
            raise RuntimeError("no camera frame yet")
    s.source = Broken()
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))               # must not raise: the session goes on
    assert t.alts == [1, 0] and not s.gone
    assert any("photo" in m for m in problems(caplog))


def test_a_usb_error_while_sending_the_photo_still_switches_the_video_interface_off():
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()
    s.source = FakeStillSource()

    def fail(data, lens):
        raise UsbStall("stall")
    t.send_video = fail
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))
    assert t.alts == [1, 0]                    # without alt 0 the camera app hangs when it goes back


def test_a_handler_that_raises_does_not_end_the_session(caplog):
    s, t = session()
    s.handlers[(2, 0x2B)] = mock.Mock(side_effect=ValueError("bug"))
    s.dispatch(msg(2, 0x2B))                               # must not raise
    s.dispatch(msg(3, 0x09))
    assert t.replies == [(9, struct.pack("<H", 0x0500))]
    assert any("bug" in m for m in problems(caplog))


def test_a_microphone_source_is_closed_when_the_transport_fails_in_an_unexpected_way(monkeypatch):
    s, t = session()
    source = mock.Mock()
    monkeypatch.setattr("psychea.session.make_source", lambda settings, rate: source)

    def boom(src, rate, extended):
        raise OSError("handle")
    t.start_mic = boom
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC))
    source.close.assert_called_once_with()


def test_a_start_video_with_a_short_config_is_reported_and_streams_with_the_old_settings(caplog):
    s, t = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO[:20]))
    assert s.streaming and t.alts == [1] and (s.out.w, s.out.h) == (480, 272) and s.fps == 15
    assert any("start-video" in m for m in problems(caplog))


def test_a_start_mic_with_a_short_config_is_reported_and_ignored(caplog):
    s, t = session()
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC[:10]))
    assert t.mic_calls == []
    assert any("microphone" in m for m in problems(caplog))


def test_a_flood_of_random_messages_never_raises_and_never_logs_an_error(caplog, monkeypatch):
    # Every known message with every kind of payload length and a wrong length byte now and then, with the video
    # running on the real test-pattern source: handlers must cope (failures would show as psp_message_failed).
    import random

    from psychea.sources import TestPattern
    monkeypatch.setattr("psychea.session.time.sleep", lambda s: None)
    caplog.set_level("ERROR", logger="psychea")
    s, t = session()
    s.source = TestPattern(s.out)
    rng = random.Random(3)
    keys = sorted(s.handlers) + [(7, 7), (2, 0xA9)]
    for _ in range(1500):
        ident, cmd = rng.choice(keys)
        payload = bytes(rng.randrange(256) for _ in range(rng.choice([0, 1, 2, 5, 8, 14, 20, 30, 32, 60])))
        length = len(payload) if rng.random() < 0.9 else rng.randrange(256)
        s.dispatch(struct.pack("<HBB", ident, cmd, length) + payload)
        if s.streaming:
            s.next_frame_at = 0.0
            s.stream_due_frame(1 / s.fps)
    assert [r.getMessage() for r in caplog.records] == []


def test_a_camera_that_cannot_be_opened_ends_the_session_with_a_hint_instead_of_a_crash(caplog):
    s, t = session()
    s.settings.webcam = 5
    with mock.patch("psychea.session.Webcam", side_effect=RuntimeError("cannot open webcam 5")):
        s.run()                                                   # must not raise: cli would print a traceback
    [message] = [m for m in problems(caplog) if "camera" in m]
    assert "cannot open webcam 5" in message and "--list" in message and "--testpattern" in message
    assert s.source is None


def test_ctrl_c_inside_the_serve_loop_still_releases_the_camera():
    s, t = session()
    s.settings.webcam = 0
    t.read_message = mock.Mock(side_effect=KeyboardInterrupt)
    with mock.patch("psychea.session.Webcam") as webcam, pytest.raises(KeyboardInterrupt):
        s.run()
    webcam.return_value.close.assert_called_once_with()


def test_a_second_start_mic_with_another_rate_replaces_the_stream():
    s, t = session()
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC))
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC[:12] + b"\x2c\x00"))          # 44 kHz
    assert t.mic_calls == [("stop",), ("start", 22050, False), ("stop",), ("start", 44100, False)]


def test_hello_in_the_middle_of_a_stream_changes_nothing_but_the_reply():
    s, t = session()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    before = (s.streaming, s.fps, s.out.w, s.out.h, s.out.max_jpeg, s.frames)
    s.dispatch(msg(2, 0x2B))
    assert (s.streaming, s.fps, s.out.w, s.out.h, s.out.max_jpeg, s.frames) == before
    assert t.replies == [(0x2B, b"\x00\x00\x01\x00")] and t.alts == [1]


def test_a_photo_during_a_stream_ends_the_stream_and_leaves_the_interface_off():
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()
    s.source = FakeStillSource()
    s.dispatch(msg(2, 0x01, EYEPET_START_VIDEO))
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))
    assert not s.streaming and t.alts == [1, 1, 0]


def test_stop_microphone_without_start_and_stop_video_twice_are_harmless():
    s, t = session()
    s.dispatch(msg(1, 0x02))
    s.dispatch(msg(2, 0x02))
    s.dispatch(msg(2, 0x04))
    assert not s.gone and not s.streaming and t.alts == [0, 0]


def test_a_message_shorter_than_its_header_is_ignored_with_a_warning(caplog):
    s, t = session()
    s.dispatch(b"\x02\x00")
    assert t.replies == [] and any("too-short" in m for m in problems(caplog))


def test_an_answer_the_psp_does_not_take_is_a_warning_and_the_session_goes_on(caplog):
    s, t = session()
    t.fail_reply = UsbStall("stall")
    s.dispatch(msg(2, 0x2B))
    assert not s.gone and any("Could not answer" in m for m in problems(caplog))


def test_a_stop_video_that_fails_for_another_reason_than_the_unplug_is_a_warning(caplog):
    s, t = session()

    def fail(alt):
        raise UsbStall("no")
    t.set_video_alt = fail
    s.dispatch(msg(2, 0x02))
    assert not s.gone and any("video interface off" in m for m in problems(caplog))


def test_a_microphone_that_cannot_start_names_the_driver_as_the_thing_to_check(caplog):
    s, t = session()

    def fail(source, rate, extended):
        raise UsbStall("no")
    t.start_mic = fail
    s.dispatch(msg(1, 0x01, EYEPET_START_MIC))
    assert not s.gone and any("--install-driver" in m for m in problems(caplog))


def test_a_cable_pulled_during_the_photo_ends_the_session_quietly(caplog):
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()
    s.source = FakeStillSource()

    def gone(data, lens):
        raise UsbGone()
    t.send_video = gone
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))
    assert s.gone and problems(caplog) == []


def test_a_cable_pulled_while_the_video_interface_is_switched_off_after_a_failed_photo(caplog):
    from tests.test_protocol import XMB_STILL_SETUP
    s, t = session()
    s.source = FakeStillSource()
    t.send_video = mock.Mock(side_effect=UsbStall("stall"))
    t.set_video_alt = mock.Mock(side_effect=[None, UsbGone()])
    s.dispatch(msg(2, 0x03, XMB_STILL_SETUP))
    assert s.gone


def test_nothing_is_sent_while_there_is_no_source():
    s, t = session()
    s.send_frame()
    assert t.frames == []


def test_every_command_the_session_handles_has_a_name_and_every_picture_setting_a_handler():
    # The command numbers live in protocol.py once; the handler table and the setting table are built from them.
    from psychea.protocol import ID_CAMERA, MESSAGE_NAMES
    s, t = session()
    assert set(s.handlers) <= set(MESSAGE_NAMES)
    for cmd in s.SETTINGS:
        assert s.handlers[(ID_CAMERA, cmd)] == s.on_setting
