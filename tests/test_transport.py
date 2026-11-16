import time

import pytest

from psychea import transport as t


class FakeDevice:
    def __init__(self, answer=b"\x00", written=8):
        self.answer, self.written, self.calls = answer, written, []

    def control_in(self, *args):
        self.calls.append(("in", *args))
        return self.answer

    def control_out(self, *args):
        self.calls.append(("out", *args))
        return self.written


def test_the_backends_errors_share_one_base_but_are_told_apart():
    for error in (t.UsbTimeout, t.UsbGone, t.UsbStall, t.HandshakeRejected):
        assert issubclass(error, t.UsbError) and issubclass(error, Exception)
    assert not issubclass(t.UsbGone, t.UsbTimeout) and not issubclass(t.UsbStall, t.UsbGone)
    assert not issubclass(t.UsbTimeout, t.UsbStall) and not issubclass(t.HandshakeRejected, t.UsbGone)


def test_a_backend_must_implement_every_transport_method():
    base = t.Transport()
    for call in (lambda: base.read_message(10), lambda: base.control_out(0, 0, 0, 0, b"", 1),
                 lambda: base.set_video_alt(1), lambda: base.send_video(b"", []),
                 lambda: base.start_mic(None, 22050, False), base.stop_mic, base.close):
        with pytest.raises(NotImplementedError):
            call()


def test_reply_is_a_vendor_control_out_to_the_video_interface_for_every_backend():
    class Recorder(t.Transport):
        def control_out(self, bm, req, value, index, data, timeout):
            self.sent = (bm, req, value, index, data, timeout)
            return len(data)

    r = Recorder()
    r.reply(0x2B, b"\x01\x02")
    assert r.sent == (0x41, 9, 0x2B, 3, b"\x01\x02", 1000)


def test_handshake_reads_the_answer_then_sends_the_info():
    dev = FakeDevice(answer=bytearray(b"\x00"), written=8)
    text = t.handshake(dev, b"INFOINFO")
    assert dev.calls == [("in", 0xC1, 0x01, 0x0003, 0x0000, 1, 1000),
                         ("out", 0x41, 0x01, 0, 0x0000, b"INFOINFO", 1000)]
    assert text.startswith("IN 0xC1/0x01 -> 00 ; OUT 0x41/0x01 wrote 8 (") and text.endswith(" ms)")


def test_handshake_reports_how_long_it_took_in_milliseconds(monkeypatch):
    real, ticks = time.monotonic, iter([50.0, 150.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, None) or real())
    assert t.handshake(FakeDevice(), b"x").endswith("wrote 8 (100000 ms)")


def test_handshake_is_rejected_when_the_psp_does_not_answer_zero():
    dev = FakeDevice(answer=b"\x01")
    with pytest.raises(t.HandshakeRejected) as err:
        t.handshake(dev, b"x")
    assert str(err.value).startswith("PSP rejected: IN 0xC1/0x01 -> 01 ;")


@pytest.mark.parametrize("answer", [b"", b"\x00\x00", b"\x00\x01"])
def test_only_exactly_one_zero_byte_is_accepted(answer):
    with pytest.raises(t.HandshakeRejected):
        t.handshake(FakeDevice(answer=answer), b"x")


def test_audio_failures_are_logged_for_the_first_three_then_every_hundredth():
    assert [n for n in range(1, 401) if t.report_failure(n)] == [1, 2, 3, 100, 200, 300, 400]
    assert t.report_failure(0)


def test_audio_failure_and_stats_lines():
    assert t.audio_failure_text(5, 2) == "An audio transfer failed (status 5, failures so far: 2)"
    assert t.audio_stats_text(1_500_000, 3) == "Audio: 1500 s sent | failed transfers 3"
    assert t.audio_stats_text(1_000, 0, " | delay 5 ms") == "Audio: 1 s sent | failed transfers 0 | delay 5 ms"


def test_nt_status_codes_are_named_in_the_log():
    assert t.nt_status_text(0xC000000D) == "0xC000000D (STATUS_INVALID_PARAMETER)"
    assert t.nt_status_text(0x80000011) == "0x80000011 (STATUS_DEVICE_BUSY)"
    assert t.nt_status_text(0xC0000120) == "0xC0000120 (STATUS_CANCELLED)"       # first audio failure at an unplug
    assert t.nt_status_text(0x12345678) == "0x12345678"


def test_a_stream_is_dead_after_a_hundred_failures_in_a_row():
    assert not t.stream_is_dead(t.DEAD_STREAK - 1)
    assert t.stream_is_dead(t.DEAD_STREAK)


def test_a_failure_streak_that_starts_with_cancelled_is_an_unplug():
    assert t.is_unplug_status(0xC0000120)                       # STATUS_CANCELLED: the first failure at an unplug
    assert not t.is_unplug_status(0x80000011) and not t.is_unplug_status(0xC000000D)


def test_after_the_first_cancelled_an_unplug_shows_only_cancelled_or_busy():
    assert t.is_unplug_follow_up(0xC0000120) and t.is_unplug_follow_up(0x80000011)
    assert not t.is_unplug_follow_up(0xC000000D) and not t.is_unplug_follow_up(0)
