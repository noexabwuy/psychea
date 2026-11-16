from __future__ import annotations

import threading
import time
from typing import Any, Optional

from . import logs
from .audio_packets import MicPackets
from .protocol import EP_INT_IN, EP_INT_SIZE, EP_ISO_MIC, EP_ISO_VIDEO, IF_VIDEO, INFO, PID, VID, chunk_packets
from .transport import (
    STATS_PERIOD_S,
    DriverMissing,
    Transport,
    UsbError,
    UsbGone,
    UsbStall,
    UsbTimeout,
    audio_failure_text,
    audio_stats_text,
    handshake,
    is_unplug_follow_up,
    is_unplug_status,
    nt_status_text,
    report_failure,
    stream_is_dead,
)

log = logs.get("winusb")
STATUS_PENDING = 0x103
ERROR_IO_PENDING = 997
ERROR_TIMEOUT = (121, 995)          # ERROR_SEM_TIMEOUT, ERROR_OPERATION_ABORTED
ERROR_STALL = (31, 22)              # ERROR_GEN_FAILURE, ERROR_BAD_COMMAND
ERROR_GONE = (1167, 433, 23, 6, 1117, 2, 3)
PIPE_TRANSFER_TIMEOUT = 3
ULONG_BYTES = 4                       # size of the ULONG that WinUsb_SetPipePolicy reads (c_ulong on Windows)
NT_STATUS_MASK = 0xFFFFFFFF           # OVERLAPPED.Internal is a pointer-sized field; the status is its low 32 bits
REQ_RECIPIENT_MASK, REQ_TO_INTERFACE = 0x1F, 1    # bmRequestType bits 0..4: the recipient of the request
_timer_raised = False
GENERIC_READ_WRITE, SHARE_READ_WRITE, OPEN_EXISTING, FLAG_OVERLAPPED = 0xC0000000, 3, 3, 0x40000000


def _instance_guids(winreg: Any, base: str, inst: str) -> list[str]:
    try:
        params = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + inst + "\\Device Parameters")
        guids, _ = winreg.QueryValueEx(params, "DeviceInterfaceGUIDs")
    except OSError:
        return []
    return [guids] if isinstance(guids, str) else list(guids)


def _instances(winreg: Any, key: Any) -> list[str]:
    names: list[str] = []
    while True:
        try:
            names.append(winreg.EnumKey(key, len(names)))
        except OSError:
            return names


def device_paths(mi: str) -> list[str]:
    import winreg
    base = r"SYSTEM\CurrentControlSet\Enum\USB\VID_%04X&PID_%04X&%s" % (VID, PID, mi)
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
    except OSError:
        return []
    return ["\\\\?\\USB#VID_%04X&PID_%04X&%s#%s#%s" % (VID, PID, mi, inst, g)
            for inst in _instances(winreg, key) for g in _instance_guids(winreg, base, inst)]


class Api:
    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as wt
        self.ct, self.wt = ctypes, wt
        V, P, U8, U16, U32 = ctypes.c_void_p, ctypes.POINTER, ctypes.c_ubyte, ctypes.c_ushort, wt.ULONG

        class Setup(ctypes.Structure):
            _pack_ = 1
            _fields_ = [("RequestType", U8), ("Request", U8), ("Value", U16), ("Index", U16), ("Length", U16)]

        class Overlapped(ctypes.Structure):
            _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                        ("Offset", wt.DWORD), ("OffsetHigh", wt.DWORD), ("hEvent", V)]

        self.Setup, self.Overlapped = Setup, Overlapped
        k32 = self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        wu = self.wu = ctypes.WinDLL("winusb", use_last_error=True)
        k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, V, wt.DWORD, wt.DWORD, V]
        k32.CreateFileW.restype = V
        k32.CloseHandle.argtypes = [V]
        protos = {
            "Initialize": [V, P(V)], "Free": [V], "GetAssociatedInterface": [V, U8, P(V)],
            "ControlTransfer": [V, Setup, V, U32, P(U32), V], "SetPipePolicy": [V, U8, U32, U32, V],
            "ReadPipe": [V, U8, V, U32, P(U32), V], "SetCurrentAlternateSetting": [V, U8],
            "RegisterIsochBuffer": [V, U8, V, U32, P(V)], "UnregisterIsochBuffer": [V],
            "WriteIsochPipeAsap": [V, U32, U32, wt.BOOL, P(Overlapped)], "AbortPipe": [V, U8],
        }
        for name, argtypes in protos.items():
            fn = getattr(wu, "WinUsb_" + name)
            fn.argtypes, fn.restype = argtypes, wt.BOOL
        global _timer_raised
        if not _timer_raised:                             # once per process: connect() builds an Api per attempt
            _timer_raised = True
            try:
                ctypes.windll.winmm.timeBeginPeriod(1)    # 1 ms sleeps for the USB loops
            except (OSError, AttributeError):
                pass

    def last_error(self) -> int:
        return self.ct.get_last_error()

    def check(self, ok: Any, what: str) -> None:
        if ok:
            return
        e = self.last_error()
        if e in ERROR_TIMEOUT:
            raise UsbTimeout(what)
        if e in ERROR_STALL:
            raise UsbStall(what)
        if e in ERROR_GONE:
            raise UsbGone("%s: Windows error %d" % (what, e))
        raise UsbError("%s: Windows error %d" % (what, e))


class Interface:
    def __init__(self, api: Api, mi: str) -> None:
        self.api, self.mi = api, mi
        self.file: Any = None
        self.handle: Any = None
        self.assoc: Any = None
        self.opened_path: Optional[str] = None
        self.timeouts: dict[int, int] = {}

    def open(self) -> Interface:
        api, ct = self.api, self.api.ct
        paths = device_paths(self.mi)
        if not paths:
            raise DriverMissing("no registry entry for %s: the WinUSB driver is not installed "
                                "(run --install-driver once, as Administrator)" % self.mi)
        last = ""
        for path in paths:
            h = api.k32.CreateFileW(path, GENERIC_READ_WRITE, SHARE_READ_WRITE, None, OPEN_EXISTING,
                                    FLAG_OVERLAPPED, None)
            if h is None or h == ct.c_void_p(-1).value:
                last = "CreateFile error %d" % api.last_error()
                continue
            fh, ih = ct.c_void_p(h), ct.c_void_p()
            if not api.wu.WinUsb_Initialize(fh, ct.byref(ih)):
                last = "WinUsb_Initialize error %d" % api.last_error()
                api.k32.CloseHandle(fh)
                continue
            self.file, self.handle, self.opened_path = fh, ih, path
            return self
        raise UsbError("%s: %s" % (self.mi, last))

    def associated(self) -> None:
        ct = self.api.ct
        h = ct.c_void_p()
        self.api.check(self.api.wu.WinUsb_GetAssociatedInterface(self.handle, 0, ct.byref(h)), "GetAssociatedInterface")
        self.assoc = h

    def policy_timeout(self, pipe: int, ms: int) -> None:
        if self.timeouts.get(pipe) != ms:
            val = self.api.ct.c_ulong(ms)
            policy = self.api.wu.WinUsb_SetPipePolicy
            if policy(self.handle, pipe, PIPE_TRANSFER_TIMEOUT, ULONG_BYTES, self.api.ct.byref(val)):
                self.timeouts[pipe] = ms                  # only a timeout that is really set is remembered

    def control(self, bm: int, req: int, value: int, index: int, buf: Any, length: int, timeout: int) -> int:
        api, ct = self.api, self.api.ct
        self.policy_timeout(0, timeout)
        got = api.wt.ULONG(0)
        ok = api.wu.WinUsb_ControlTransfer(self.handle, api.Setup(bm, req, value, index, length),
                                           ct.cast(buf, ct.c_void_p), length, ct.byref(got), None)
        api.check(ok, "control %02X/%02X" % (bm, req))
        return got.value

    def read_pipe(self, pipe: int, length: int, timeout: int) -> bytes:
        api, ct = self.api, self.api.ct
        self.policy_timeout(pipe, timeout)
        buf, got = ct.create_string_buffer(length), api.wt.ULONG(0)
        api.check(api.wu.WinUsb_ReadPipe(self.handle, pipe, buf, length, ct.byref(got), None), "read EP 0x%02X" % pipe)
        return buf.raw[:got.value]

    def set_alt(self, alt: int, handle: Any = None) -> None:
        self.api.check(self.api.wu.WinUsb_SetCurrentAlternateSetting(handle or self.handle, alt),
                       "SET_INTERFACE alt %d" % alt)

    def off(self, handle: Any = None) -> None:
        try:
            self.set_alt(0, handle)
        except UsbError:
            pass

    def close(self) -> None:
        for h in (self.assoc, self.handle):
            if h is not None:
                self.api.wu.WinUsb_Free(h)
        if self.file is not None:
            self.api.k32.CloseHandle(self.file)
        self.file = self.handle = self.assoc = None


_PARKED: list[Any] = []       # objects the driver may still write to; each has settle() -> True when it is safe to drop


def release_parked() -> None:
    for obj in list(_PARKED):
        if obj.settle():
            _PARKED.remove(obj)


class IsochOut:
    def __init__(self, api: Api, handle: Any, pipe: int, slot_bytes: int, slots: int) -> None:
        self.api, self.pipe, self.slot_bytes, self.slots = api, pipe, slot_bytes, slots
        ct = api.ct
        self.buf = (ct.c_ubyte * (slot_bytes * slots))()
        self.ovs = [api.Overlapped() for _ in range(slots)]
        self.next = 0
        self.restarts = 0
        self.handle = None
        h = ct.c_void_p()
        api.check(api.wu.WinUsb_RegisterIsochBuffer(handle, pipe, ct.cast(self.buf, ct.c_void_p), len(self.buf),
                                                    ct.byref(h)), "RegisterIsochBuffer")
        self.handle = h

    def write(self, data: bytes, cont: bool = False) -> int:
        if len(data) > self.slot_bytes:
            raise ValueError("write larger than a slot")
        api, ct = self.api, self.api.ct
        if not self.done(self.next):                  # e.g. after a timeout: the driver may still read this slot
            raise UsbError("slot %d is still in use by the driver" % self.next)
        slot, self.next = self.next, (self.next + 1) % self.slots
        off = slot * self.slot_bytes
        ct.memmove(ct.addressof(self.buf) + off, data, len(data))
        ov = self.ovs[slot]
        for attempt_cont in ((True, False) if cont else (False,)):
            ov.Internal, ov.InternalHigh, ov.hEvent = STATUS_PENDING, 0, None
            ok = api.wu.WinUsb_WriteIsochPipeAsap(self.handle, off, len(data), attempt_cont, ct.byref(ov))
            if ok or api.last_error() == ERROR_IO_PENDING:
                return slot
            if attempt_cont:                  # stream ran dry: continuing would drop a frame -> new stream
                self.restarts += 1
        ov.Internal = 0                           # refused: no transfer exists, the slot is free again
        api.check(False, "WriteIsochPipeAsap")
        raise AssertionError("Api.check raises when it is given False")

    def done(self, slot: int) -> bool:
        return self.ovs[slot].Internal != STATUS_PENDING

    def status(self, slot: int) -> int:
        return self.ovs[slot].Internal & NT_STATUS_MASK

    def pending(self) -> bool:
        return any(not self.done(slot) for slot in range(self.slots))

    def settle(self) -> bool:
        return not self.pending()

    def close(self) -> None:
        if self.handle is not None:
            self.api.wu.WinUsb_UnregisterIsochBuffer(self.handle)
            self.handle = None
        if self.pending():
            _PARKED.append(self)                  # keep buf and ovs alive: the driver completes into them later


class MicStreamer:
    SLOTS, SLOT_BYTES, DEPTH = 64, 128, 24           # DEPTH ms queued ahead ride out scheduling hiccups
    STAT_PERIOD = STATS_PERIOD_S

    def __init__(self, iface: Interface, rate: int, extended: bool, source: Any) -> None:
        self.iface, self.rate, self.extended, self.source = iface, rate, extended, source
        self.iso = IsochOut(iface.api, iface.assoc, EP_ISO_MIC, self.SLOT_BYTES, self.SLOTS)
        self.packets = MicPackets(source, rate, extended)
        self.completed = self.errors = self.streak = 0
        self.unplugged = False                   # the current failure streak began with STATUS_CANCELLED (cable pulled)
        self.began_like_unplug = False           # the first failure of the streak was STATUS_CANCELLED
        self.seen_statuses: set[int] = set()     # every status of the current streak (for the debug line)
        self.dead = False                        # gave up: every write failed (the PSP is gone or the stream broke)
        self.inflight: list[int] = []
        self.new_stream = True                   # the next write starts a fresh stream (also after a failed transfer)
        self.active = True
        self.thread = threading.Thread(target=self._guarded, daemon=True)
        try:
            self.thread.start()
        except BaseException:
            self.iso.close()                      # nothing was queued: the buffer must not stay registered
            raise

    def packet(self) -> bytes:
        return self.packets.next()

    def _guarded(self) -> None:
        try:
            self._loop()
        except Exception as e:                    # a dead audio thread must not be silent
            log.error("The audio thread crashed: %r" % (e,), event="mic_thread_crashed", exc_info=True)

    def _reap(self) -> None:
        while self.inflight and self.iso.done(self.inflight[0]):
            slot = self.inflight.pop(0)
            status = self.iso.status(slot)
            if status != 0:
                self._failed(status)
            else:
                self._succeeded()
            self.completed += 1

    def _succeeded(self) -> None:
        if self.streak and self.unplugged:         # it was no unplug after all: the quiet failures deserve a line
            log.warning("Audio transfers failed %d times in a row and then went on (the PSP is still there)" % (
                self.streak,), event="mic_recovered", failures=self.streak)
        self.streak, self.unplugged = 0, False

    def _judge(self, status: int) -> None:
        if self.streak == 1:
            self.began_like_unplug = is_unplug_status(status)
            self.seen_statuses = set()
        self.seen_statuses.add(status)
        # judged by the start and the current status: a stray status in the middle (the log shows only the first
        # three and the 100th failure) must not turn the end of a real unplug loud
        self.unplugged = self.began_like_unplug and is_unplug_follow_up(status)

    def _failed(self, status: int) -> None:
        self.errors += 1
        self.streak += 1
        self.new_stream = True
        self._judge(status)
        warn, err = (log.debug, log.debug) if self.unplugged else (log.warning, log.error)
        if report_failure(self.errors) and not self.dead:
            warn(audio_failure_text(nt_status_text(status), self.errors),
                 event="mic_packet_failed", status="0x%08X" % status, errors=self.errors)
        if stream_is_dead(self.streak) and not self.dead:
            self.dead, self.active = True, False
            names = ", ".join(nt_status_text(x) for x in sorted(self.seen_statuses))
            log.debug("Statuses in the failed streak: %s" % names, event="mic_streak_statuses")
            err("Audio stopped: %d transfers failed in a row (last status %s)"
                % (self.streak, nt_status_text(status)),
                event="mic_gave_up", status="0x%08X" % status, errors=self.errors)

    def _log_stats(self) -> None:
        buffered = getattr(self.source, "buffered_ms", lambda: 0.0)()
        delay = ", delay %d ms (PC buffer %d + USB queue %d)" % (
            buffered + len(self.inflight), buffered, len(self.inflight))
        log.info(audio_stats_text(self.completed, self.errors, delay),
                 event="stats_mic", sent_ms=self.completed, failed=self.errors, restarts=self.iso.restarts,
                 queued_ms=len(self.inflight), pc_buffer_ms=round(buffered))

    def _fill(self) -> bool:
        while self.active and len(self.inflight) < self.DEPTH:       # stop() may come while a read blocks
            if self.new_stream:                   # capture went on while the stream was down: play the freshest audio
                getattr(self.source, "flush", lambda: None)()
            try:
                slot = self.iso.write(self.packet(), cont=bool(self.inflight) and not self.new_stream)
                self.new_stream = False
            except UsbError as e:
                log.error("Audio stopped: cannot queue audio for the PSP: %s" % e,
                          event="mic_submit_failed", error=e)
                self.active = False
                return False
            self.inflight.append(slot)
        return True

    def _loop(self) -> None:
        t_stat = time.monotonic()
        while self.active:
            self._reap()
            now = time.monotonic()
            if now - t_stat >= self.STAT_PERIOD:
                t_stat = now
                self._log_stats()
            if not self._fill():
                return
            time.sleep(0.001)

    def _wait_for_inflight(self) -> None:
        deadline = time.monotonic() + 1.0
        while self.inflight and time.monotonic() < deadline:
            self.inflight = [s for s in self.inflight if not self.iso.done(s)]
            time.sleep(0.005)

    def settle(self) -> bool:
        self.inflight = [s for s in self.inflight if not self.iso.done(s)]
        if self.inflight or self.thread.is_alive():
            return False
        self._release()
        return True

    def _release(self) -> None:
        self.iso.close()              # Windows says error 170 to SET_INTERFACE while the buffer is registered
        try:
            self.iface.set_alt(0, self.iface.assoc)
        except UsbError as e:
            gone = self.dead or (self.streak > 0 and self.unplugged)     # the main loop reports a disconnect itself
            level = log.debug if gone else log.warning
            level("Could not switch the audio interface off: %s" % e, event="mic_alt0_failed", error=e)
        self.iface.close()

    def stop(self) -> None:
        self.active = False
        self.thread.join(timeout=1.0)
        self.iso.api.wu.WinUsb_AbortPipe(self.iface.assoc, EP_ISO_MIC)
        self._wait_for_inflight()
        try:
            if self.thread.is_alive() or self.inflight:   # the driver or the thread may still use buffer and handles
                log.debug("Audio transfers still pending at shutdown; handles left open", event="mic_close_pending")
                _PARKED.append(self)
            else:
                self._release()
        finally:
            self.source.close()


class WinTransport(Transport):
    VIDEO_SLOT, VIDEO_SLOTS = 48 * 1024, 4
    VIDEO_TIMEOUT = 3.0

    def __init__(self) -> None:
        self.api = Api()
        self.if0: Any = None
        self.if3: Any = None
        self.video: Any = None
        self.mic: Any = None

    @classmethod
    def connect(cls) -> tuple[WinTransport, str]:
        t = cls()
        try:
            t.if0 = Interface(t.api, "MI_00").open()
            text = handshake(t, INFO) + " [winusb]"
        except BaseException:                    # also an unexpected error: the handle must not leak
            t.close()
            raise
        return t, text

    # --- control / messages
    def control_in(self, bm: int, req: int, value: int, index: int, length: int, timeout: int) -> bytes:
        buf = self.api.ct.create_string_buffer(max(length, 1))
        got = self._control(bm, req, value, index, buf, length, timeout)     # fills buf
        return buf.raw[:got]

    def control_out(self, bm: int, req: int, value: int, index: int, data: bytes, timeout: int) -> int:
        buf = self.api.ct.create_string_buffer(bytes(data), max(len(data), 1))
        return self._control(bm, req, value, index, buf, len(data), timeout)

    def _iface(self, n: int) -> Interface:
        if n == IF_VIDEO:
            if self.if3 is None:
                self.if3 = Interface(self.api, "MI_03").open()
            return self.if3
        return self.if0

    def _control(self, bm: int, req: int, value: int, index: int, buf: Any, length: int, timeout: int) -> int:
        # interface-recipient requests go through the handle of interface (wIndex & 0xFF); WinUSB writes
        # that interface number into wIndex itself
        to_interface = (bm & REQ_RECIPIENT_MASK) == REQ_TO_INTERFACE
        iface = self._iface(index & 0xFF) if to_interface else self.if0
        return iface.control(bm, req, value, index, buf, length, timeout)

    def read_message(self, timeout_ms: int) -> Optional[bytes]:
        try:
            return self.if0.read_pipe(EP_INT_IN, EP_INT_SIZE, timeout_ms)
        except UsbTimeout:
            return None

    # --- video
    def set_video_alt(self, alt: int) -> None:
        iface = self._iface(IF_VIDEO)
        if self.video is not None:
            self.video.close()
            self.video = None
        iface.set_alt(alt)
        if alt == 1:
            try:
                self.video = IsochOut(self.api, iface.handle, EP_ISO_VIDEO, self.VIDEO_SLOT, self.VIDEO_SLOTS)
            except UsbError:
                iface.off()                               # do not leave the PSP in streaming mode without a stream
                raise

    def send_video(self, data: bytes, lens: list[int]) -> int:
        if self.video is None:
            raise UsbError("the video interface is not switched on")
        chunks = chunk_packets(data, lens, self.VIDEO_SLOT)
        deadline = time.monotonic() + self.VIDEO_TIMEOUT + len(chunks) * 0.05
        inflight: list = []
        bad = 0

        def wait(slot: int) -> None:
            nonlocal bad
            while not self.video.done(slot):
                if time.monotonic() > deadline:
                    self.api.wu.WinUsb_AbortPipe(self.if3.handle, EP_ISO_VIDEO)
                    raise UsbTimeout("iso transfer timed out")
                time.sleep(0.0005)
            bad += 0 if self.video.status(slot) == 0 else 1

        for i, chunk in enumerate(chunks):
            if len(inflight) == self.VIDEO_SLOTS:          # round-robin slots: the oldest must be free again
                wait(inflight.pop(0))
            inflight.append(self.video.write(chunk, cont=i > 0))
        for slot in inflight:
            wait(slot)
        return bad

    # --- audio
    def start_mic(self, source: Any, rate: int, extended: bool) -> None:
        self.stop_mic()                                   # one streamer per interface (Session stops first anyway)
        release_parked()                                  # a streamer that was still busy at its stop may be done now
        iface = Interface(self.api, "MI_01").open()
        try:
            iface.associated()
            iface.set_alt(1, iface.assoc)                 # only after the PSP's StartMic message
        except UsbError:
            iface.close()
            raise
        try:
            self.mic = MicStreamer(iface, rate, extended, source)
        except BaseException:                             # alt 1 is selected: switch it off again before closing
            iface.off(iface.assoc)
            iface.close()
            raise
        log.debug("Audio interface opened", event="mic_interface", path=iface.opened_path)

    def stop_mic(self) -> None:
        mic, self.mic = self.mic, None                    # gone even when stopping it fails
        if mic is not None:
            mic.stop()

    def close(self) -> None:
        try:
            self.stop_mic()
        finally:                                          # whatever the microphone did, the handles must be closed
            if self.video is not None:
                self.video.close()
                self.video = None
            for iface in (self.if3, self.if0):
                if iface is not None:
                    iface.close()
            self.if0 = self.if3 = None
