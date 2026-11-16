# LibusbTransport (Linux path) against a fake libusb handle.
import itertools

import pytest

from psychea import libusb_backend as lb
from psychea import winusb
from psychea.transport import UsbError, UsbGone, UsbStall, UsbTimeout


class FakeTransfer:
    def __init__(self, handle):
        self.h = handle

    def setIsochronous(self, ep, data, callback=None, timeout=0, iso_transfer_length_list=None):
        self.ep, self.data, self.cb, self.lens = ep, data, callback, iso_transfer_length_list

    def submit(self):
        if self.h.submit_error is not None:
            raise self.h.submit_error
        self.h.submitted.append(self)
        if not self.h.stuck:
            self.cb(self)

    def getStatus(self):
        return self.status

    def iterISO(self):
        return [(self.h.iso_status, b"")] * len(self.lens)

    def cancel(self):
        self.h.cancelled.append(self)
        if self.h.cancel_status is not None and self in self.h.submitted:
            self.status = self.h.cancel_status
            self.h.pending_callbacks.append(self)

    def close(self):
        self.h.freed.append((self, self in self.h.pending_callbacks))      # was libusb still holding it?

    status = lb.usb1.TRANSFER_COMPLETED


class FakeHandle:
    def __init__(self):
        self.claimed, self.writes, self.alts, self.submitted = [], [], [], []
        self.read_error, self.stuck, self.iso_status = None, False, 0
        self.claim_error = self.submit_error = self.cancel_status = None
        self.detach, self.closed, self.cancelled, self.pending_callbacks = [], 0, [], []
        self.freed = []

    def setAutoDetachKernelDriver(self, enable):
        self.detach.append((enable, list(self.claimed)))

    def claimInterface(self, n):
        if self.claim_error is not None:
            raise self.claim_error
        self.claimed.append(n)

    def controlRead(self, bm, req, value, index, length, timeout=0):
        return b"\x00"

    def controlWrite(self, bm, req, value, index, data, timeout=0):
        self.writes.append((bm, req, value, index, bytes(data)))
        return len(data)

    def interruptRead(self, ep, length, timeout=0):
        if self.read_error:
            raise self.read_error
        return b"\x02\x00\x2b\x00"

    def setInterfaceAltSetting(self, n, alt):
        self.alts.append((n, alt))

    def getTransfer(self, iso_packets=0):
        return FakeTransfer(self)

    def close(self):
        self.closed += 1


class FakeCtx:
    def __init__(self, handle=None):
        self.handle = handle

    def getDeviceIterator(self, skip_on_error=False):
        return iter([])

    def handleEventsTimeout(self, t):
        if self.handle is not None:                          # cancelled transfers report their end
            while self.handle.pending_callbacks:
                tr = self.handle.pending_callbacks.pop(0)
                tr.h.submitted.remove(tr)
                tr.cb(tr)


class FakeDev:
    def __init__(self, handle):
        self.handle = handle

    def open(self):
        return self.handle


@pytest.fixture
def transport(monkeypatch):
    h = FakeHandle()
    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: FakeCtx(h))
    monkeypatch.setattr(lb, "_find", lambda ctx: FakeDev(h))
    t, text = lb.LibusbTransport.connect()
    return t, h, text


def test_connect_claims_all_interfaces_and_handshakes(transport):
    t, h, text = transport
    assert h.claimed == [0, 2, 3] and h.writes[0][:4] == (0x41, 1, 0, 0) and "libusb" in text


def test_reply_uses_windex_3(transport):
    t, h, _ = transport
    t.reply(9, b"\x00\x05")
    assert h.writes[-1] == (0x41, 9, 9, 3, b"\x00\x05")


def test_read_message_translates_timeout_and_unplug(transport):
    t, h, _ = transport
    assert t.read_message(10) == b"\x02\x00\x2b\x00"
    h.read_error = lb.usb1.USBErrorTimeout()
    assert t.read_message(10) is None
    h.read_error = lb.usb1.USBErrorNoDevice()
    with pytest.raises(UsbGone):
        t.read_message(10)
    h.read_error = lb.usb1.USBErrorPipe()
    with pytest.raises(UsbStall):
        t.read_message(10)


def test_video_frame_is_one_iso_transfer_with_the_given_packet_lengths(transport):
    t, h, _ = transport
    assert t.send_video(b"x" * 1800, [896, 896, 8]) == 0
    assert h.submitted[0].lens == [896, 896, 8]
    h.iso_status = 1
    assert t.send_video(b"x" * 10, [10]) == 1


def test_a_transfer_that_never_completes_times_out(transport, monkeypatch):
    t, h, _ = transport
    h.stuck = True
    monkeypatch.setattr(lb.time, "monotonic", itertools.count(0, 100).__next__)   # each look at the clock: 100 s later
    with pytest.raises(UsbError):
        t.send_video(b"x", [1])
    assert h.freed == []                                        # libusb never let go of it: freeing it would be unsafe


def test_the_video_alt_setting_goes_to_the_video_interface(transport):
    t, h, _ = transport
    t.set_video_alt(1)
    assert h.alts == [(lb.IF_VIDEO, 1)]


def test_a_libusb_context_is_opened_and_kept_alive_for_the_whole_run(monkeypatch):
    opened = []

    class Context:
        def open(self):
            opened.append(self)
    monkeypatch.setattr(lb.usb1, "USBContext", Context)
    monkeypatch.setattr(lb, "_KEEP", [])
    ctx = lb._context()
    assert opened == [ctx] and lb._KEEP == [ctx]


def test_a_timed_out_transfer_is_cancelled_and_freed_once_libusb_has_let_go_of_it(transport, monkeypatch):
    t, h, _ = transport
    h.stuck = True
    h.cancel_status = lb.usb1.TRANSFER_CANCELLED
    monkeypatch.setattr(lb, "VIDEO_WAIT_S", 0.0)                # time out at once
    with pytest.raises(UsbTimeout):
        t.send_video(b"x", [1])
    assert len(h.cancelled) == 1 and h.freed == [(h.cancelled[0], False)]


def test_microphone_streams_eight_packets_per_transfer(transport):
    t, h, _ = transport

    class Src:
        def read(self, n):
            return b"\x00\x00" * n

        def close(self):
            pass

    h.stuck = True                      # transfers stay queued
    t.start_mic(Src(), 22050, False)
    assert (2, 1) in h.alts and len(h.submitted) == lb.MicStreamer.NTR
    assert all(len(tr.lens) == lb.MicStreamer.PPT for tr in h.submitted)
    t.mic.inflight = 0
    t.stop_mic()
    assert h.alts[-1] == (2, 0)


class Src:
    def read(self, n):
        return b"\x00\x00" * n

    def close(self):
        self.closed = True


def _mic(transport, **tr):
    t, h, _ = transport
    h.stuck = True
    t.start_mic(Src(), 22050, False)
    return t, h, t.mic


class Tr(FakeTransfer):
    def __init__(self, status, handle=None):
        super().__init__(handle)
        self.status = status

    def getStatus(self):
        return self.status


def test_a_completed_transfer_is_queued_again(transport):
    t, h, mic = _mic(transport)
    before = len(h.submitted)
    mic.inflight = 1
    mic._done(Tr(lb.usb1.TRANSFER_COMPLETED, h))
    assert len(h.submitted) == before + 1 and mic.sent == lb.MicStreamer.PPT and mic.errors == 0


def test_a_failed_transfer_is_counted_and_still_queued_again(transport, caplog):
    t, h, mic = _mic(transport)
    before = len(h.submitted)
    mic._done(Tr(lb.usb1.TRANSFER_ERROR, h))
    assert mic.errors == 1 and len(h.submitted) == before + 1
    assert any("failed" in r.getMessage().lower() for r in caplog.records)


@pytest.mark.parametrize("status", ["TRANSFER_NO_DEVICE", "TRANSFER_CANCELLED"])
def test_a_gone_or_cancelled_transfer_is_not_queued_again(transport, status):
    t, h, mic = _mic(transport)
    before = len(h.submitted)
    mic._done(Tr(getattr(lb.usb1, status), h))
    assert mic.errors == 1 and len(h.submitted) == before and mic.sent == 0


def test_too_many_failed_transfers_give_up(transport):
    t, h, mic = _mic(transport)
    mic.streak = lb.MicStreamer.GIVE_UP
    before = len(h.submitted)
    mic._done(Tr(lb.usb1.TRANSFER_ERROR, h))
    assert mic.errors == 1 and len(h.submitted) == before


def test_a_stopped_streamer_does_not_queue_again(transport):
    t, h, mic = _mic(transport)
    mic.active = False
    before = len(h.submitted)
    mic._done(Tr(lb.usb1.TRANSFER_COMPLETED, h))
    assert len(h.submitted) == before


def test_a_submit_error_ends_the_stream_quietly(transport, monkeypatch):
    t, h, mic = _mic(transport)

    def boom(tr):
        raise lb.usb1.USBError("gone")

    monkeypatch.setattr(mic, "_submit", boom)
    mic._done(Tr(lb.usb1.TRANSFER_COMPLETED, h))             # logged, no exception


def test_stats_are_logged_every_10_s_of_audio_like_on_windows(transport, caplog):
    # One period for both backends (transport.STATS_PERIOD_S), so a Linux log and a Windows log read the same.
    import logging
    caplog.set_level(logging.INFO)
    t, h, mic = _mic(transport)
    mic.sent = 5000 - lb.MicStreamer.PPT
    mic._done(Tr(lb.usb1.TRANSFER_COMPLETED, h))
    assert not any("sent" in r.getMessage() for r in caplog.records)          # 5 s: too early
    mic.sent = 10000 - lb.MicStreamer.PPT
    mic._done(Tr(lb.usb1.TRANSFER_COMPLETED, h))
    assert any("10 s sent" in r.getMessage() for r in caplog.records)
    assert lb.MicStreamer.STAT_PERIOD == winusb.MicStreamer.STAT_PERIOD == 10.0


def test_stopping_cancels_transfers_and_waits_for_them(transport):
    t, h, mic = _mic(transport)
    mic.inflight = 0
    t.stop_mic()
    assert t.mic is None and mic.source.closed and not mic.active


def test_a_cancel_error_does_not_stop_the_shutdown(transport, monkeypatch):
    t, h, mic = _mic(transport)

    def boom():
        raise lb.usb1.USBError("x")

    for tr in mic.transfers:
        monkeypatch.setattr(tr, "cancel", boom)
    mic.inflight = 0
    t.stop_mic()
    assert t.mic is None


def test_switching_the_audio_interface_off_may_fail(transport, monkeypatch):
    t, h, mic = _mic(transport)
    mic.inflight = 0

    def boom(n, alt):
        raise lb.usb1.USBError("x")

    monkeypatch.setattr(h, "setInterfaceAltSetting", boom)
    t.stop_mic()
    assert t.mic is None


def test_close_stops_the_microphone_and_ignores_a_handle_error(transport, monkeypatch):
    t, h, mic = _mic(transport)
    mic.inflight = 0

    def boom():
        raise lb.usb1.USBError("x")

    monkeypatch.setattr(h, "close", boom)
    t.close()
    assert t.mic is None


class _Dev:
    def __init__(self, vid, pid):
        self.vid, self.pid = vid, pid

    def getVendorID(self):
        return self.vid

    def getProductID(self):
        return self.pid


class _Ctx:
    def __init__(self, devs):
        self.devs = devs

    def getDeviceIterator(self, skip_on_error=False):
        return iter(self.devs)


def test_find_returns_the_psp_among_other_devices_or_none():
    psp = _Dev(lb.VID, lb.PID)
    assert lb._find(_Ctx([_Dev(1, 2), psp])) is psp
    assert lb._find(_Ctx([_Dev(1, 2)])) is None


def test_present_reports_whether_the_psp_is_plugged_in(monkeypatch):
    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: _Ctx([_Dev(lb.VID, lb.PID)]))
    assert lb.present() is True
    lb._BASE[0].devs = [_Dev(1, 2)]
    assert lb.present() is False


def test_present_is_false_when_libusb_fails(monkeypatch):
    monkeypatch.setattr(lb, "_BASE", [])

    def boom():
        raise lb.usb1.USBError("no libusb")

    monkeypatch.setattr(lb, "_context", boom)
    assert lb.present() is False


def test_translate_maps_libusb_errors_to_ours():
    from psychea.transport import UsbTimeout

    def raiser(exc):
        def f():
            raise exc
        return f

    for exc, ours in ((lb.usb1.USBErrorTimeout(), UsbTimeout), (lb.usb1.USBErrorNoDevice(), UsbGone),
                      (lb.usb1.USBErrorPipe(), UsbStall), (lb.usb1.USBError(), UsbError)):
        with pytest.raises(ours):
            lb.translate(raiser(exc))
    assert lb.translate(lambda: 5) == 5


def test_connect_without_the_device_fails(monkeypatch):
    monkeypatch.setattr(lb, "_context", lambda: FakeCtx())
    monkeypatch.setattr(lb, "_find", lambda ctx: None)
    with pytest.raises(UsbError):
        lb.LibusbTransport.connect()


# --- lifecycle: contexts, claiming, half-open states

def test_fifty_failed_connect_attempts_share_one_libusb_context(monkeypatch):
    # Every attempt of the two-second handshake window reuses one context (no file descriptors pile up).
    made = []
    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: made.append(1) or FakeCtx())
    monkeypatch.setattr(lb, "_find", lambda ctx: None)
    for _ in range(50):
        with pytest.raises(UsbError):
            lb.LibusbTransport.connect()
    assert len(made) == 1
    assert lb.present() is False and len(made) == 1                 # and presence checks use the same one


def test_a_libusb_that_cannot_start_or_list_devices_is_a_usb_error_the_retry_loop_understands(monkeypatch):
    def boom():
        raise lb.usb1.USBErrorAccess()

    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", boom)
    with pytest.raises(UsbError):
        lb.LibusbTransport.connect()

    class Listing:
        def getDeviceIterator(self, skip_on_error=False):
            raise lb.usb1.USBErrorIO()

    monkeypatch.setattr(lb, "_BASE", [Listing()])
    with pytest.raises(UsbError):
        lb.LibusbTransport.connect()


def test_the_kernel_driver_is_detached_automatically_before_claiming(transport):
    t, h, _ = transport
    assert h.detach == [(True, [])]                                 # asked before the first interface was claimed


def test_a_libusb_without_auto_detach_still_connects(monkeypatch):
    h = FakeHandle()

    def unsupported(enable):
        raise lb.usb1.USBErrorNotSupported()

    h.setAutoDetachKernelDriver = unsupported
    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: FakeCtx(h))
    monkeypatch.setattr(lb, "_find", lambda ctx: FakeDev(h))
    t, _ = lb.LibusbTransport.connect()
    assert h.claimed == [0, 2, 3]


def test_a_claim_that_fails_closes_the_handle_and_is_a_usb_error(monkeypatch):
    h = FakeHandle()
    h.claim_error = lb.usb1.USBErrorBusy()                          # a kernel driver owns the interface
    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: FakeCtx(h))
    monkeypatch.setattr(lb, "_find", lambda ctx: FakeDev(h))
    with pytest.raises(UsbError):
        lb.LibusbTransport.connect()
    assert h.closed == 1


def test_a_device_that_cannot_be_opened_is_a_usb_error(monkeypatch):
    class Gone(FakeDev):
        def open(self):
            raise lb.usb1.USBErrorNoDevice()

    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: FakeCtx())
    monkeypatch.setattr(lb, "_find", lambda ctx: Gone(None))
    with pytest.raises(UsbGone):
        lb.LibusbTransport.connect()


def test_a_microphone_that_cannot_queue_its_transfers_is_a_usb_error_and_leaves_alt_0(transport):
    t, h, _ = transport
    h.submit_error = lb.usb1.USBErrorIO()
    with pytest.raises(UsbError):
        t.start_mic(Src(), 22050, False)
    assert h.alts == [(2, 1), (2, 0)] and t.mic is None


def test_a_failed_second_submit_cancels_the_first(transport):
    t, h, _ = transport
    real = FakeTransfer.submit
    count = []

    def second_fails(self):
        count.append(1)
        if len(count) == 2:
            raise lb.usb1.USBErrorIO()
        real(self)

    h.stuck = True
    FakeTransfer.submit = second_fails
    try:
        with pytest.raises(UsbError):
            t.start_mic(Src(), 22050, False)
    finally:
        FakeTransfer.submit = real
    assert len(h.cancelled) >= 1 and h.alts[-1] == (2, 0)


def test_stopping_the_microphone_is_quiet_and_does_not_count_its_own_cancellations(transport, caplog):
    import logging
    t, h, _ = transport
    h.stuck = True
    h.cancel_status = lb.usb1.TRANSFER_CANCELLED
    caplog.set_level(logging.DEBUG)
    t.start_mic(Src(), 22050, False)
    mic = t.mic
    t.stop_mic()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert mic.errors == 0 and mic.inflight == 0 and h.alts[-1] == (2, 0)


def test_the_audio_goes_on_after_sporadic_failures_and_stops_only_after_a_streak(transport):
    t, h, mic = _mic(transport)
    for _ in range(80):                                              # 80 failures over a long session, never in a row
        before = len(h.submitted)
        mic._done(Tr(lb.usb1.TRANSFER_ERROR, h))
        assert len(h.submitted) == before + 1                        # still queued again
        mic._done(Tr(lb.usb1.TRANSFER_COMPLETED, h))
    assert mic.errors == 80
    for _ in range(50):
        mic._done(Tr(lb.usb1.TRANSFER_ERROR, h))
    before = len(h.submitted)
    mic._done(Tr(lb.usb1.TRANSFER_ERROR, h))                         # the 51st in a row
    assert len(h.submitted) == before


def test_an_unplugged_device_is_a_debug_line_and_failures_name_the_libusb_status(transport, caplog):
    import logging
    t, h, mic = _mic(transport)
    caplog.set_level(logging.DEBUG)
    mic._done(Tr(lb.usb1.TRANSFER_NO_DEVICE, h))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    mic._done(Tr(lb.usb1.TRANSFER_STALL, h))
    (rec,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "STALL" in rec.getMessage()


def test_a_source_that_fails_to_close_does_not_keep_the_interface_on_or_the_handle_open(transport):
    t, h, mic = _mic(transport)
    mic.inflight = 0

    def boom():
        raise RuntimeError("sound card gone")

    mic.source.close = boom
    with pytest.raises(RuntimeError):
        t.close()
    assert t.mic is None and h.alts[-1] == (2, 0) and h.closed == 1
    t.close()                                                        # a second close is harmless
    assert h.closed >= 1


def test_fifty_connect_and_close_cycles_close_every_handle(monkeypatch):
    handles = []

    def make():
        h = FakeHandle()
        h.stuck = True                                               # the transfers stay queued
        handles.append(h)
        return h

    monkeypatch.setattr(lb, "_BASE", [])
    monkeypatch.setattr(lb, "_context", lambda: FakeCtx())
    monkeypatch.setattr(lb, "_find", lambda ctx: FakeDev(make()))
    for _ in range(50):
        t, _ = lb.LibusbTransport.connect()
        t.start_mic(Src(), 22050, False)
        t.mic.inflight = 0
        t.close()
    assert len(handles) == 50 and all(h.closed == 1 and h.alts[-1] == (2, 0) for h in handles)


def test_a_video_transfer_that_cannot_be_submitted_is_closed_and_a_usb_error(transport):
    t, h, _ = transport
    closed = []
    h.submit_error = lb.usb1.USBErrorNoDevice()
    real = FakeTransfer.close
    FakeTransfer.close = lambda self: closed.append(1)
    try:
        with pytest.raises(UsbGone):
            t.send_video(b"x" * 10, [10])
    finally:
        FakeTransfer.close = real
    assert closed == [1]
