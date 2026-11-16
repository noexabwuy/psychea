from __future__ import annotations

import functools
import time
from typing import Any, Callable, Optional

import usb1

from . import logs
from .audio_packets import MicPackets
from .protocol import EP_INT_IN, EP_INT_SIZE, EP_ISO_MIC, EP_ISO_VIDEO, IF_CONTROL, IF_MIC, IF_VIDEO, INFO, PID, VID
from .transport import (
    STATS_PERIOD_S,
    Transport,
    UsbError,
    UsbGone,
    UsbStall,
    UsbTimeout,
    audio_failure_text,
    audio_stats_text,
    handshake,
    report_failure,
)

log = logs.get("libusb")
MIC_TIMEOUT_MS, VIDEO_TIMEOUT_MS = 1000, 2000    # libusb transfer timeouts: a 8-ms audio transfer, one video frame
VIDEO_WAIT_S = 3.0                              # how long send_video waits for a frame to complete
STOP_WAIT_S = 1.0                               # how long stopping the microphone waits for cancelled transfers
EVENT_POLL_S = 0.01                             # one handleEventsTimeout slice
_KEEP: list = []          # libusb contexts/devices are never destroyed (use-after-exit crashes on Windows)
_BASE: list = []


def _context() -> Any:
    ctx = usb1.USBContext()
    ctx.open()
    _KEEP.append(ctx)
    return ctx


def _is_psp(dev: Any) -> bool:
    return bool(dev.getVendorID() == VID and dev.getProductID() == PID)


def _find(ctx: Any) -> Any:
    for d in ctx.getDeviceIterator(skip_on_error=True):
        if _is_psp(d):
            _KEEP.append(d)
            return d
    return None


def _shared_context() -> Any:
    if not _BASE:
        _BASE.append(_context())
    return _BASE[0]


def present() -> bool:
    try:
        return any(_is_psp(d) for d in _shared_context().getDeviceIterator(skip_on_error=True))
    except usb1.USBError:
        return False


def _auto_detach(handle: Any) -> None:
    try:
        handle.setAutoDetachKernelDriver(True)
    except usb1.USBErrorNotSupported:
        pass                                      # this platform has no kernel driver to detach


def translate(fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except usb1.USBErrorTimeout as e:
        raise UsbTimeout(str(e)) from e
    except usb1.USBErrorNoDevice as e:
        raise UsbGone(str(e)) from e
    except usb1.USBErrorPipe as e:
        raise UsbStall(str(e)) from e
    except usb1.USBError as e:
        raise UsbError(str(e)) from e


STATUS_NAMES = {0: "COMPLETED", 1: "ERROR", 2: "TIMED_OUT", 3: "CANCELLED", 4: "STALL", 5: "NO_DEVICE", 6: "OVERFLOW"}


class MicStreamer:
    NTR, PPT = 4, 8
    GIVE_UP = 50                                  # failed transfers in a row
    STAT_PERIOD = STATS_PERIOD_S
    STAT_PERIOD_MS = int(STATS_PERIOD_S * 1000)

    def __init__(self, transport: LibusbTransport, source: Any, rate: int, extended: bool) -> None:
        self.t, self.source, self.rate, self.extended = transport, source, rate, extended
        self.packets = MicPackets(source, rate, extended)
        self.active = True
        self.inflight = self.sent = self.errors = self.streak = 0
        self.transfers = [transport.handle.getTransfer(iso_packets=self.PPT) for _ in range(self.NTR)]
        try:
            for tr in self.transfers:
                self._submit(tr)
        except usb1.USBError as e:
            self.active = False
            self._cancel_all()                    # what was queued must not go on after the failed start
            raise UsbError(str(e)) from e

    def packet(self) -> bytes:
        return self.packets.next()

    def _submit(self, tr: Any) -> None:
        packets = [self.packet() for _ in range(self.PPT)]
        tr.setIsochronous(EP_ISO_MIC, b"".join(packets), callback=self._done, timeout=MIC_TIMEOUT_MS,
                          iso_transfer_length_list=[len(p) for p in packets])
        tr.submit()
        self.inflight += 1

    def _log_failure(self, status: int) -> None:
        if report_failure(self.errors):
            level = log.debug if status == usb1.TRANSFER_NO_DEVICE else log.warning   # an unplug is reported elsewhere
            level(audio_failure_text("%d (%s)" % (status, STATUS_NAMES.get(status, "?")), self.errors),
                  event="mic_packet_failed", status=status, errors=self.errors)

    def _transfer_failed(self, status: int) -> bool:
        if status == usb1.TRANSFER_CANCELLED and not self.active:
            return False                          # our own stop() cancelled it
        self.errors += 1
        self.streak += 1
        self._log_failure(status)
        if status in (usb1.TRANSFER_NO_DEVICE, usb1.TRANSFER_CANCELLED):
            return False
        if self.streak > self.GIVE_UP:
            log.error("Audio stopped: too many failed transfers in a row", event="mic_gave_up", errors=self.errors)
            return False
        return True

    def _resubmit(self, tr: Any) -> None:
        try:
            self._submit(tr)
        except usb1.USBError as e:
            log.error("Audio stopped: cannot queue audio for the PSP: %s" % e, event="mic_submit_failed", error=e)

    def _done(self, tr: Any) -> None:
        self.inflight -= 1
        status = tr.getStatus()
        if status == usb1.TRANSFER_COMPLETED:
            self.streak = 0
        elif not self._transfer_failed(status):
            return
        self.sent += self.PPT
        if self.sent % self.STAT_PERIOD_MS < self.PPT:
            log.info(audio_stats_text(self.sent, self.errors),
                     event="stats_mic", sent_ms=self.sent, failed=self.errors)
        if self.active:
            self._resubmit(tr)

    def _cancel_all(self) -> None:
        for tr in self.transfers:
            try:
                tr.cancel()
            except usb1.USBError:
                pass

    def stop(self) -> None:
        self.active = False
        self._cancel_all()
        deadline = time.monotonic() + STOP_WAIT_S
        while self.inflight > 0 and time.monotonic() < deadline:
            self.t.ctx.handleEventsTimeout(EVENT_POLL_S)
        self.source.close()


class LibusbTransport(Transport):
    def __init__(self, ctx: Any, handle: Any) -> None:
        self.ctx, self.handle = ctx, handle
        self.mic: Optional[MicStreamer] = None

    @classmethod
    def connect(cls) -> tuple[LibusbTransport, str]:
        ctx = translate(_shared_context)
        dev = translate(lambda: _find(ctx))
        if dev is None:
            raise UsbError("device not visible")
        handle = translate(dev.open)
        t = cls(ctx, handle)
        try:
            translate(functools.partial(_auto_detach, handle))
            for n in (IF_CONTROL, IF_MIC, IF_VIDEO):
                translate(functools.partial(handle.claimInterface, n))
            text = handshake(t, INFO) + " [libusb]"
        except BaseException:
            handle.close()
            raise
        return t, text

    def control_in(self, bm: int, req: int, value: int, index: int, length: int, timeout: int) -> bytes:
        return translate(lambda: bytes(self.handle.controlRead(bm, req, value, index, length, timeout=timeout)))

    def control_out(self, bm: int, req: int, value: int, index: int, data: bytes, timeout: int) -> int:
        return translate(lambda: self.handle.controlWrite(bm, req, value, index, data, timeout=timeout))

    def read_message(self, timeout_ms: int) -> Optional[bytes]:
        try:
            return translate(lambda: bytes(self.handle.interruptRead(EP_INT_IN, EP_INT_SIZE, timeout=timeout_ms)))
        except UsbTimeout:
            return None

    def set_video_alt(self, alt: int) -> None:
        translate(lambda: self.handle.setInterfaceAltSetting(IF_VIDEO, alt))

    def _handle_events_until(self, done: list[int], seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not done and time.monotonic() < deadline:
            self.ctx.handleEventsTimeout(EVENT_POLL_S)

    def send_video(self, data: bytes, lens: list[int]) -> int:
        done = []
        tr = self.handle.getTransfer(iso_packets=len(lens))
        tr.setIsochronous(EP_ISO_VIDEO, data, callback=lambda t: done.append(1), timeout=VIDEO_TIMEOUT_MS,
                          iso_transfer_length_list=lens)
        try:
            translate(tr.submit)
        except UsbError:
            tr.close()
            raise
        self._handle_events_until(done, VIDEO_WAIT_S)
        if not done:
            translate(tr.cancel)
            self._handle_events_until(done, STOP_WAIT_S)
            if done:
                tr.close()                              # libusb has let go of it; otherwise it must stay alive
            raise UsbTimeout("iso transfer timed out")
        bad = sum(1 for status, _ in tr.iterISO() if status != 0)
        failed = bad or tr.getStatus() != 0
        tr.close()
        return int(bool(failed))

    def start_mic(self, source: Any, rate: int, extended: bool) -> None:
        translate(lambda: self.handle.setInterfaceAltSetting(IF_MIC, 1))
        try:
            self.mic = MicStreamer(self, source, rate, extended)
        except UsbError:
            self._audio_off()                             # alt 1 is selected: do not leave it so
            raise

    def _audio_off(self) -> None:
        try:
            translate(lambda: self.handle.setInterfaceAltSetting(IF_MIC, 0))
        except UsbError as e:
            log.warning("Could not switch the audio interface off: %s" % e, event="mic_alt0_failed", error=e)

    def stop_mic(self) -> None:
        mic, self.mic = self.mic, None
        if mic is not None:
            try:
                mic.stop()
            finally:
                self._audio_off()

    def close(self) -> None:
        try:
            self.stop_mic()
        finally:
            try:
                self.handle.close()
            except usb1.USBError:
                pass
