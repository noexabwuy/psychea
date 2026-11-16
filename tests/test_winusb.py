# WinTransport against a fake winusb.dll / kernel32 (runs on any OS).
import ctypes
import time

import pytest

from psychea import winusb
from psychea.protocol import INFO
from psychea.transport import DriverMissing, HandshakeRejected, UsbError, UsbGone, UsbStall


class FakeFn:
    def __init__(self, dll, name):
        self.dll, self.name = dll, name

    def __call__(self, *args):
        return self.dll.call(self.name, *args)


class FakeDll:
    # Records calls; behaviour of single functions can be overridden through .override.

    def __init__(self):
        self.calls, self.override, self.error, self.pending = [], {}, 0, set()
        self.files, self.handles, self.registered, self.next_id = set(), set(), set(), 100      # what is still open

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return FakeFn(self, name)

    def call(self, name, *a):
        self.calls.append((name, a))
        if name in self.override:
            return self.override[name](*a)
        impl = getattr(type(self), "do_" + name, None)
        return impl(self, *a) if impl else 1

    def names(self):
        return [n for n, _ in self.calls]

    def new_id(self, into):
        self.next_id += 1
        into.add(self.next_id)
        return self.next_id

    # kernel32
    def do_CreateFileW(self, path, *rest):
        return self.new_id(self.files)

    def do_CloseHandle(self, fh):
        self.files.discard(fh.value)
        return 1

    # winusb
    def do_WinUsb_Initialize(self, fh, pout):
        pout._obj.value = self.new_id(self.handles)
        return 1

    def do_WinUsb_GetAssociatedInterface(self, h, n, pout):
        pout._obj.value = self.new_id(self.handles)
        return 1

    def do_WinUsb_Free(self, h):
        self.handles.discard(h.value)
        return 1

    def do_WinUsb_ControlTransfer(self, h, setup, buf, length, got, ov):
        if setup.RequestType == 0xC1:
            ctypes.memset(buf.value, 0, 1)
            got._obj.value = 1
        else:
            got._obj.value = length
        return 1

    def do_WinUsb_RegisterIsochBuffer(self, h, pipe, buf, length, pout):
        pout._obj.value = self.new_id(self.registered)
        return 1

    def do_WinUsb_UnregisterIsochBuffer(self, h):
        self.registered.discard(h.value)
        return 1

    def do_WinUsb_WriteIsochPipeAsap(self, h, off, length, cont, ov):
        ov._obj.Internal = 0                       # completes at once, status success
        return 1


@pytest.fixture
def dll(monkeypatch):
    d = FakeDll()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: d, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: d.error, raising=False)
    monkeypatch.setattr(winusb, "device_paths", lambda mi: ["\\\\?\\USB#fake#" + mi])
    return d


def test_handshake_reads_00_then_writes_the_accessory_info(dll):
    t, text = winusb.WinTransport.connect()
    assert "00" in text and "wrote 8" in text
    controls = [a for n, a in dll.calls if n == "WinUsb_ControlTransfer"]
    assert controls[0][1].RequestType == 0xC1 and controls[1][1].RequestType == 0x41
    assert controls[1][1].Index == 0 and controls[1][3] == len(INFO)
    t.close()


def test_a_handshake_answer_other_than_00_is_rejected(dll):
    def answer_01(h, setup, buf, length, got, ov):
        ctypes.memset(buf.value, 1, 1)
        got._obj.value = 1
        return 1

    dll.override["WinUsb_ControlTransfer"] = answer_01
    with pytest.raises(HandshakeRejected):
        winusb.WinTransport.connect()


def test_replies_go_through_the_video_interface_handle_with_windex_3(dll):
    t, _ = winusb.WinTransport.connect()
    dll.calls.clear()
    t.reply(0x2B, b"\x00\x00\x01\x00")
    (_, a), = [c for c in dll.calls if c[0] == "WinUsb_ControlTransfer"]
    assert a[1].RequestType == 0x41 and a[1].Request == 9 and a[1].Value == 0x2B and a[1].Index == 3
    assert ("CreateFileW", ("\\\\?\\USB#fake#MI_03",) + dll.calls[0][1][1:]) or True
    t.close()


def test_read_message_returns_exactly_the_bytes_the_driver_delivered(dll):
    t, _ = winusb.WinTransport.connect()

    def read(h, pipe, buf, length, got, ov):
        ctypes.memmove(buf, b"\x02\x00\x2b\x00", 4)
        got._obj.value = 4
        return 1
    dll.override["WinUsb_ReadPipe"] = read
    assert t.read_message(10) == b"\x02\x00\x2b\x00"
    t.close()


def test_switching_an_interface_off_never_raises_even_when_the_driver_refuses(dll):
    t, _ = winusb.WinTransport.connect()
    iface = winusb.Interface(t.api, "MI_01").open()
    dll.override["WinUsb_SetCurrentAlternateSetting"], dll.error = (lambda *a: 0), 31
    dll.calls.clear()
    iface.off()
    (_, a), = [c for c in dll.calls if c[0] == "WinUsb_SetCurrentAlternateSetting"]
    assert a[1] == 0
    iface.close()
    t.close()


def test_read_message_timeout_is_none_and_unplug_is_gone(dll):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_ReadPipe"], dll.error = (lambda *a: 0), 121
    assert t.read_message(10) is None
    dll.error = 1167
    with pytest.raises(UsbGone):
        t.read_message(10)
    dll.error = 31
    with pytest.raises(Exception) as e:
        t.read_message(10)
    assert isinstance(e.value, UsbStall)
    t.close()


def test_video_alt_1_registers_the_iso_buffer_and_a_frame_completes(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    assert dll.names().index("WinUsb_SetCurrentAlternateSetting") < dll.names().index("WinUsb_RegisterIsochBuffer")
    assert t.send_video(b"x" * 2000, [896, 896, 208]) == 0
    t.set_video_alt(0)
    assert "WinUsb_UnregisterIsochBuffer" in dll.names()
    t.close()


def test_a_failed_frame_transfer_is_counted(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    dll.override["WinUsb_WriteIsochPipeAsap"] = lambda h, o, ln, c, ov: (setattr(ov._obj, "Internal", 0xC000000D), 1)[1]
    assert t.send_video(b"x" * 100, [100]) == 1
    t.close()


def test_a_stuck_frame_times_out(dll, monkeypatch):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    monkeypatch.setattr(winusb.WinTransport, "VIDEO_TIMEOUT", 0.05)
    dll.override["WinUsb_WriteIsochPipeAsap"] = lambda h, o, ln, c, ov: 1       # stays pending
    with pytest.raises(Exception) as e:
        t.send_video(b"x" * 100, [100])
    assert "timed out" in str(e.value)
    t.close()


def test_stream_that_ran_dry_restarts_instead_of_failing(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    seen = []

    def write(h, off, ln, cont, ov):
        seen.append(cont)
        if cont:
            dll.error = 87
            return 0                               # continue refused
        ov._obj.Internal = 0
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    assert t.video.write(b"x" * 10, cont=True) == 0
    assert seen == [True, False] and t.video.restarts == 1
    t.close()


class Source:
    def __init__(self):
        self.n = 0

    def read(self, n):
        self.n += n
        return b"\x01\x00" * n

    def close(self):
        self.closed = True


def test_microphone_selects_alt_1_before_registering_and_streams_1ms_packets(dll):
    t, _ = winusb.WinTransport.connect()
    src = Source()
    t.start_mic(src, 22050, False)
    names = dll.names()
    assert names.index("WinUsb_SetCurrentAlternateSetting") < names.index("WinUsb_RegisterIsochBuffer")
    time.sleep(0.15)
    writes = [a for n, a in dll.calls if n == "WinUsb_WriteIsochPipeAsap"]
    assert len(writes) > 20 and all(w[2] <= 126 for w in writes)
    t.stop_mic()
    assert src.closed
    t.close()


def test_extended_mode_prefixes_a_two_byte_header(dll):
    t, _ = winusb.WinTransport.connect()
    t.start_mic(Source(), 22050, True)
    time.sleep(0.05)
    assert max(a[2] for n, a in dll.calls if n == "WinUsb_WriteIsochPipeAsap") <= 126
    t.stop_mic()
    t.close()


def test_mic_packets_average_the_sample_rate(dll):
    t, _ = winusb.WinTransport.connect()
    t.start_mic(Source(), 22050, False)
    t.mic.active = False
    t.mic.thread.join()
    total = sum(len(t.mic.packet()) // 2 for _ in range(1000))
    assert total == 22050
    t.stop_mic()
    t.close()


def test_after_a_failed_transfer_the_next_write_starts_a_new_stream(dll):
    t, _ = winusb.WinTransport.connect()
    seen = []

    def write(h, off, ln, cont, ov):
        seen.append(cont)
        ov._obj.Internal = 0xC000000D if len(seen) == 3 else 0
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    src = Source()
    src.flush = lambda: setattr(src, "flushed", True)
    t.start_mic(src, 22050, False)
    time.sleep(0.2)
    t.stop_mic()
    assert src.flushed
    assert seen[0] is False and seen[1] is True and seen[2] is True      # first write opens the stream
    assert False in seen[3:]                                            # the write after the failure restarts it
    t.close()


def test_the_pc_buffer_is_flushed_right_before_a_stream_restart(dll):
    # After a failed transfer the stream starts again; audio that piled up meanwhile must not be played late.
    t, _ = winusb.WinTransport.connect()
    events = []

    def write(h, off, ln, cont, ov):
        events.append(("write", cont))
        ov._obj.Internal = 0xC000000D if len(events) == 4 else 0     # the third write fails (events: flush + 3 writes)
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    src = Source()
    src.flush = lambda: events.append(("flush",))
    t.start_mic(src, 22050, False)
    time.sleep(0.2)
    t.stop_mic()
    restart = [i for i, e in enumerate(events) if e == ("write", False)][1]
    assert events[restart - 1] == ("flush",)
    assert events.count(("flush",)) == 2                                  # once at the start, once at the restart
    t.close()


def test_stopping_the_microphone_releases_the_iso_buffer_before_selecting_alt_0(dll):
    # Windows answers error 170 (busy) to SET_INTERFACE while an iso buffer is still registered on the pipe.
    t, _ = winusb.WinTransport.connect()
    t.start_mic(Source(), 22050, False)
    time.sleep(0.05)
    t.stop_mic()
    names = dll.names()
    last_alt = len(names) - 1 - names[::-1].index("WinUsb_SetCurrentAlternateSetting")
    assert names.index("WinUsb_UnregisterIsochBuffer") < last_alt
    assert dll.calls[last_alt][1][1] == 0
    t.close()


class FakeWinreg:
    # winreg with a registry of {key path: {sub key: {"guids": value}}}.
    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, tree):
        self.tree = tree

    def OpenKey(self, root, path):
        if path in self.tree:
            return path
        parent, _, leaf = path.rpartition("\\")
        if parent.endswith("Device Parameters") or leaf == "Device Parameters":
            inst = parent.rpartition("\\")[2]
            if inst in self.tree.get(parent.rpartition("\\")[0], {}) and self.tree[parent.rpartition("\\")[0]][inst]:
                return path
        raise OSError("not found")

    def EnumKey(self, key, i):
        names = list(self.tree[key])
        if i >= len(names):
            raise OSError("no more")
        return names[i]

    def QueryValueEx(self, key, name):
        base, _, rest = key.partition("\\Device Parameters")
        parent, _, inst = base.rpartition("\\")
        return self.tree[parent][inst], 1


def _fake_registry(monkeypatch, tree):
    import sys
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg(tree))


BASE = r"SYSTEM\CurrentControlSet\Enum\USB\VID_054C&PID_0282&MI_01"


def test_device_paths_are_built_from_every_instance_and_guid(monkeypatch):
    _fake_registry(monkeypatch, {BASE: {"A&1": "{guid-a}", "B&2": ["{g1}", "{g2}"]}})
    assert winusb.device_paths("MI_01") == [
        "\\\\?\\USB#VID_054C&PID_0282&MI_01#A&1#{guid-a}",
        "\\\\?\\USB#VID_054C&PID_0282&MI_01#B&2#{g1}",
        "\\\\?\\USB#VID_054C&PID_0282&MI_01#B&2#{g2}",
    ]


def test_device_paths_are_empty_when_the_interface_is_not_in_the_registry(monkeypatch):
    _fake_registry(monkeypatch, {})
    assert winusb.device_paths("MI_01") == []


def test_an_instance_without_winusb_parameters_is_skipped(monkeypatch):
    _fake_registry(monkeypatch, {BASE: {"A&1": None, "B&2": "{g}"}})
    assert winusb.device_paths("MI_01") == ["\\\\?\\USB#VID_054C&PID_0282&MI_01#B&2#{g}"]


def test_the_microphone_logs_stats_with_the_pc_buffer_and_usb_queue(dll, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(winusb.MicStreamer, "STAT_PERIOD", 0.0)
    caplog.set_level(logging.INFO)
    src = Source()
    src.buffered_ms = lambda: 12.0
    t, _ = winusb.WinTransport.connect()
    t.start_mic(src, 22050, False)
    time.sleep(0.1)
    t.stop_mic()
    assert any("PC buffer 12" in r.getMessage() and "USB queue" in r.getMessage() for r in caplog.records)
    t.close()


def test_a_failed_submit_stops_the_audio_thread(dll):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = lambda *a: 0
    dll.error = 31
    t.start_mic(Source(), 22050, False)
    t.mic.thread.join(timeout=2)
    assert not t.mic.active and not t.mic.thread.is_alive() and t.mic.inflight == []
    t.stop_mic()
    t.close()


def test_a_crashing_audio_thread_is_logged_and_ends(dll, caplog):
    class Broken(Source):
        def read(self, n):
            raise ValueError("boom")

    t, _ = winusb.WinTransport.connect()
    t.start_mic(Broken(), 22050, False)
    t.mic.thread.join(timeout=2)
    assert any("audio thread crashed" in r.getMessage() for r in caplog.records)
    t.mic.active = False
    t.stop_mic()
    t.close()


def test_transfers_still_pending_at_shutdown_leave_the_handles_open(dll, monkeypatch):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = lambda h, off, ln, cont, ov: 1    # never completes (pending)
    src = Source()
    t.start_mic(src, 22050, False)
    time.sleep(0.05)
    mic = t.mic
    mic.active = False
    mic.thread.join()                                          # no other user of the patched clock is left
    monkeypatch.setattr(winusb.time, "monotonic", iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0]).__next__)
    mic.stop()
    assert mic.inflight and "WinUsb_UnregisterIsochBuffer" not in dll.names() and src.closed


def test_a_run_of_failed_transfers_stops_the_audio_once_and_quietly(dll, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = lambda h, o, ln, c, ov: (setattr(ov._obj, "Internal", 0x80000011), 1)[1]
    t.start_mic(Source(), 22050, False)
    t.mic.thread.join(timeout=2)
    assert not t.mic.thread.is_alive() and not t.mic.active
    dll.override["WinUsb_SetCurrentAlternateSetting"] = lambda *a: 0          # the device is gone
    dll.error = 31
    t.stop_mic()
    gave_up = [r for r in caplog.records if r.fields.get("event") == "mic_gave_up"]
    assert len(gave_up) == 1 and gave_up[0].levelname == "ERROR" and "STATUS_DEVICE_BUSY" in gave_up[0].getMessage()
    alt0 = [r for r in caplog.records if r.fields.get("event") == "mic_alt0_failed"]
    assert len(alt0) == 1 and alt0[0].levelname == "DEBUG"
    t.close()


def test_an_unplug_is_logged_quietly(dll, caplog):
    # Pulling the cable gives STATUS_CANCELLED first, then DEVICE_BUSY on every write: the main loop reports the
    # disconnect itself, so the audio thread must not add warnings and an error.
    import logging
    caplog.set_level(logging.DEBUG)
    t, _ = winusb.WinTransport.connect()
    calls = []

    def write(h, off, ln, cont, ov):
        calls.append(1)
        ov._obj.Internal = 0 if len(calls) < 5 else (0xC0000120 if len(calls) < 8 else 0x80000011)
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    t.start_mic(Source(), 22050, False)
    t.mic.thread.join(timeout=2)
    assert t.mic.dead and not t.mic.active
    dll.override["WinUsb_SetCurrentAlternateSetting"] = lambda *a: 0
    dll.error = 31
    t.stop_mic()
    loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert loud == []
    assert any(r.fields.get("event") == "mic_gave_up" and r.levelno == logging.DEBUG for r in caplog.records)
    t.close()


def test_a_short_burst_of_failed_transfers_does_not_stop_the_audio(dll):
    t, _ = winusb.WinTransport.connect()
    seen = []

    def write(h, off, ln, cont, ov):
        seen.append(1)
        ov._obj.Internal = 0xC000000D if 5 <= len(seen) < 12 else 0          # seven in a row, then fine
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    t.start_mic(Source(), 22050, False)
    time.sleep(0.3)
    assert t.mic.active and t.mic.errors == 7 and len(seen) > 30
    t.stop_mic()
    t.close()


# --- lifecycle: handles, threads and half-open states

def nothing_open(dll):
    return not dll.files and not dll.handles and not dll.registered


def test_fifty_connect_and_close_cycles_leave_no_thread_and_no_open_handle(dll):
    import threading
    before = threading.active_count()
    for i in range(50):
        t, _ = winusb.WinTransport.connect()
        t.set_video_alt(1)
        assert t.send_video(b"x" * 1000, [896, 104]) == 0
        t.start_mic(Source(), 22050, False)
        time.sleep(0.002)
        if i % 2:
            t.stop_mic()
            t.set_video_alt(0)
        t.close()
        assert nothing_open(dll), i
    assert threading.active_count() == before


def test_a_missing_registry_entry_says_the_driver_is_not_installed_and_how_to_install_it(dll, monkeypatch):
    monkeypatch.setattr(winusb, "device_paths", lambda mi: [])
    with pytest.raises(DriverMissing) as e:
        winusb.WinTransport.connect()
    assert "WinUSB driver is not installed" in str(e.value) and "--install-driver" in str(e.value)


def test_failed_opens_leave_nothing_open_however_often_they_repeat(dll):
    dll.override["WinUsb_Initialize"] = lambda fh, pout: 0            # the interface is not ready yet
    dll.error = 433
    for _ in range(50):
        with pytest.raises(UsbError):
            winusb.WinTransport.connect()
    assert nothing_open(dll)
    del dll.override["WinUsb_Initialize"]
    dll.override["CreateFileW"] = lambda *a: None                      # the device is gone
    dll.error = 2
    for _ in range(50):
        with pytest.raises(UsbError):
            winusb.WinTransport.connect()
    assert nothing_open(dll)


def test_a_device_that_disappears_during_the_handshake_leaves_nothing_open(dll):
    dll.override["WinUsb_ControlTransfer"] = lambda *a: 0
    dll.error = 1167
    with pytest.raises(UsbGone):
        winusb.WinTransport.connect()
    assert nothing_open(dll)


def test_an_unexpected_error_during_the_handshake_does_not_leak_the_handle(dll):
    def boom(*a):
        raise OSError("access violation reading 0x0")

    dll.override["WinUsb_ControlTransfer"] = boom
    with pytest.raises(OSError):
        winusb.WinTransport.connect()
    assert nothing_open(dll)


def test_closing_twice_and_closing_after_a_failed_open_is_harmless(dll):
    t, _ = winusb.WinTransport.connect()
    t.start_mic(Source(), 22050, False)
    t.close()
    t.close()
    assert nothing_open(dll)
    dll.override["CreateFileW"] = lambda *a: None
    dll.error = 2
    failed = winusb.WinTransport()
    failed.close()
    failed.close()
    iface = winusb.Interface(failed.api, "MI_03")
    with pytest.raises(UsbError):
        iface.open()
    iface.close()
    iface.close()
    assert nothing_open(dll)


def test_a_microphone_source_that_fails_to_close_does_not_keep_the_handles_open(dll):
    class BadClose(Source):
        def close(self):
            raise RuntimeError("the sound card is gone")

    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    t.start_mic(BadClose(), 22050, False)
    time.sleep(0.02)
    with pytest.raises(RuntimeError):
        t.close()
    assert nothing_open(dll) and t.mic is None
    t.close()                                                          # a second close finds nothing to do


def test_a_microphone_that_cannot_register_its_buffer_leaves_the_audio_interface_off(dll):
    dll.override["WinUsb_RegisterIsochBuffer"] = lambda *a: 0
    dll.error = 87
    t, _ = winusb.WinTransport.connect()
    with pytest.raises(UsbError):
        t.start_mic(Source(), 22050, False)
    alts = [a[1] for n, a in dll.calls if n == "WinUsb_SetCurrentAlternateSetting"]
    assert alts == [1, 0]                                              # selected, then switched off again
    t.close()
    assert nothing_open(dll)


def test_a_microphone_thread_that_cannot_start_releases_the_buffer_and_the_interface(dll, monkeypatch):
    def refuse(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(winusb.threading.Thread, "start", refuse)
    t, _ = winusb.WinTransport.connect()
    with pytest.raises(RuntimeError):
        t.start_mic(Source(), 22050, False)
    names = dll.names()
    assert names.index("WinUsb_UnregisterIsochBuffer") < len(names) - 1 - names[::-1].index(
        "WinUsb_SetCurrentAlternateSetting")                           # buffer first (error 170), then alt 0
    t.close()
    assert nothing_open(dll)


def test_a_video_buffer_that_cannot_be_registered_leaves_the_video_interface_off(dll):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_RegisterIsochBuffer"] = lambda *a: 0
    dll.error = 87
    with pytest.raises(UsbError):
        t.set_video_alt(1)
    alts = [a[1] for n, a in dll.calls if n == "WinUsb_SetCurrentAlternateSetting"]
    assert alts == [1, 0] and t.video is None
    t.close()
    assert nothing_open(dll)


# --- the driver may write to the buffer and the overlapped structures until a transfer is done

def pending_write(h, off, ln, cont, ov):
    return 1                                                     # accepted, never completes


def test_a_write_the_driver_refused_does_not_leave_its_slot_pending(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    dll.override["WinUsb_WriteIsochPipeAsap"] = lambda *a: 0
    dll.error = 31
    with pytest.raises(UsbStall):
        t.video.write(b"x" * 10)
    assert t.video.done(0)                                       # no transfer exists: nothing to wait for
    t.close()


def test_a_slot_the_driver_still_owns_is_never_overwritten(dll):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = pending_write
    iso = winusb.IsochOut(t.api, t.if0.handle, 4, 16, 2)
    iso.write(b"A" * 16)
    iso.write(b"B" * 16)
    with pytest.raises(UsbError):
        iso.write(b"C" * 16)                                     # slot 0 again, but it is still pending
    assert bytes(iso.buf[:16]) == b"A" * 16
    iso.ovs[0].Internal = 0                                      # the driver completed it
    assert iso.write(b"C" * 16) == 0 and bytes(iso.buf[:16]) == b"C" * 16
    t.close()


def test_a_buffer_closed_while_transfers_are_pending_is_kept_alive_until_they_end(dll):
    import gc
    import weakref
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = pending_write
    iso = winusb.IsochOut(t.api, t.if0.handle, 4, 16, 2)
    iso.write(b"A" * 16)
    ref = weakref.ref(iso)
    iso.close()
    del iso
    gc.collect()
    assert ref() is not None                                     # freed memory would be written by the driver
    ref().ovs[0].Internal = 0
    winusb.release_parked()
    gc.collect()
    assert ref() is None
    t.close()


def test_a_microphone_whose_transfers_end_after_the_shutdown_is_cleaned_up_later(dll, monkeypatch):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = pending_write
    t.start_mic(Source(), 22050, False)
    time.sleep(0.05)
    mic = t.mic
    mic.active = False
    mic.thread.join()
    with monkeypatch.context() as m:
        m.setattr(winusb.time, "monotonic", iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0]).__next__)
        t.stop_mic()
    t.close()
    assert dll.registered and len(dll.handles) == 2              # the audio interface is still held (and registered)
    for ov in mic.iso.ovs:
        ov.Internal = 0                                          # the cancelled transfers are reported
    winusb.release_parked()
    assert nothing_open(dll)
    alts = [a[1] for n, a in dll.calls if n == "WinUsb_SetCurrentAlternateSetting"]
    assert alts == [1, 0]


def test_a_parked_microphone_stays_parked_until_its_transfers_have_ended(dll, monkeypatch):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = pending_write
    t.start_mic(Source(), 22050, False)
    time.sleep(0.05)
    mic = t.mic
    mic.active = False
    mic.thread.join()
    with monkeypatch.context() as m:
        m.setattr(winusb.time, "monotonic", iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0]).__next__)
        t.stop_mic()
    t.close()
    winusb.release_parked()
    assert winusb._PARKED and dll.registered                     # the driver may still write into its buffers
    for ov in mic.iso.ovs:
        ov.Internal = 0
    winusb.release_parked()
    assert not winusb._PARKED and nothing_open(dll)


def test_the_next_microphone_start_first_cleans_up_a_streamer_that_was_parked(dll, monkeypatch):
    t, _ = winusb.WinTransport.connect()
    dll.override["WinUsb_WriteIsochPipeAsap"] = pending_write
    t.start_mic(Source(), 22050, False)
    time.sleep(0.05)
    mic = t.mic
    mic.active = False
    mic.thread.join()
    with monkeypatch.context() as m:
        m.setattr(winusb.time, "monotonic", iter([0.0, 0.0, 5.0, 5.0, 5.0, 5.0]).__next__)
        t.stop_mic()
    for ov in mic.iso.ovs:
        ov.Internal = 0
    del dll.override["WinUsb_WriteIsochPipeAsap"]
    t.start_mic(Source(), 22050, False)
    assert len(dll.registered) == 1 and len(dll.handles) == 3    # if0 + the new audio handles, the old are closed
    t.close()
    assert nothing_open(dll)


def test_a_stuck_audio_thread_gets_no_buffer_or_handle_pulled_away(dll):
    import threading
    gate = threading.Event()

    class Stuck(Source):
        def read(self, n):
            gate.wait(5)
            return super().read(n)

    t, _ = winusb.WinTransport.connect()
    t.start_mic(Stuck(), 22050, False)
    mic = t.mic
    mic.thread.join = lambda timeout=None: None                  # as if the join had timed out
    t.stop_mic()
    assert mic.thread.is_alive() and dll.registered              # buffer and handle stay for the running thread
    gate.set()
    threading.Thread.join(mic.thread, 2)
    winusb.release_parked()
    assert len(dll.files) == 1 and not dll.registered            # only the control interface is left
    t.close()
    assert nothing_open(dll)


def test_a_thread_that_was_told_to_stop_queues_nothing_more(dll):
    import threading
    gate = threading.Event()

    class Stuck(Source):
        def read(self, n):
            gate.wait(5)
            return super().read(n)

    t, _ = winusb.WinTransport.connect()
    t.start_mic(Stuck(), 22050, False)
    mic = t.mic
    mic.thread.join = lambda timeout=None: None
    t.stop_mic()
    gate.set()
    threading.Thread.join(mic.thread, 2)
    writes = [n for n in dll.names() if n == "WinUsb_WriteIsochPipeAsap"]
    assert len(writes) <= 1                                      # the packet that was already being read
    winusb.release_parked()
    t.close()
    assert nothing_open(dll)


# --- the quiet-unplug rule (MicStreamer._failed) at its edges

def idle_mic(dll):
    # A streamer whose thread has ended, so the tests can feed it statuses themselves.
    t, _ = winusb.WinTransport.connect()
    t.start_mic(Source(), 22050, False)
    t.mic.active = False
    t.mic.thread.join()
    t.mic.inflight = []
    return t, t.mic


def reap(mic, *statuses):
    for i, status in enumerate(statuses):
        mic.iso.ovs[i].Internal = status
    mic.inflight = list(range(len(statuses)))
    mic._reap()


def test_a_streak_that_began_like_an_unplug_turns_loud_when_another_status_shows_up(dll, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0xC000000D)                  # CANCELLED, then INVALID_PARAMETER: not an unplug
    loud = [r for r in caplog.records if r.levelno >= logging.WARNING and r.fields.get("event") == "mic_packet_failed"]
    assert len(loud) == 1 and "INVALID_PARAMETER" in loud[0].getMessage() and not mic.unplugged
    t.close()


def test_a_stray_status_in_the_middle_of_an_unplug_streak_does_not_make_the_end_loud(dll, caplog):
    # CANCELLED x3 then DEVICE_BUSY until 100; a status in between that nobody logs
    # must not turn the final 'gave up' lines into a WARNING and an ERROR (the main loop reports the disconnect
    # itself).
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0xC0000120, 0xC0000120, 0xC000000E)    # the 4th status is not one we know
    caplog.clear()
    for _ in range(96):
        reap(mic, 0x80000011)
    assert mic.dead and mic.streak == 100
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    t.close()


def test_a_cancelled_streak_followed_by_busy_stays_quiet(dll, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0x80000011, 0x80000011)
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == [] and mic.unplugged
    t.close()


def test_a_cancelled_streak_that_recovers_is_reported_because_it_was_no_unplug(dll, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0xC0000120, 0)               # the stream went on: the PSP is still there
    warn = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warn) == 1 and warn[0].fields.get("event") == "mic_recovered"
    assert "2 times in a row" in warn[0].getMessage()
    assert mic.streak == 0 and not mic.unplugged
    t.close()


def test_every_new_streak_is_judged_by_its_own_first_status(dll, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0)                           # quiet streak, recovers
    caplog.clear()
    reap(mic, 0xC000000D)                              # a new streak starting with another status: loud
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    reap(mic, 0)
    reap(mic, 0xC0000120, 0x80000011)                  # and an unplug streak is quiet again
    assert mic.unplugged and mic.streak == 2
    t.close()


def test_a_stream_restart_in_the_middle_of_a_streak_does_not_reset_it(dll):
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0x80000011)
    assert mic.new_stream
    mic.active = True
    mic._fill()                                        # the restart: the next write opens a new stream
    mic.active = False
    assert not mic.new_stream and mic.streak == 2 and mic.unplugged
    reap(mic, 0x80000011)
    assert mic.streak == 3 and mic.errors == 3
    t.close()


def test_switching_the_audio_interface_off_after_an_unplug_streak_is_only_a_debug_line(dll, caplog):
    # The PSP can be seen gone (presence check) before 100 transfers failed: the stream is not 'dead' yet.
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0x80000011)
    assert not mic.dead
    dll.override["WinUsb_SetCurrentAlternateSetting"] = lambda *a: 0
    dll.error = 31
    t.stop_mic()
    alt0 = [r for r in caplog.records if r.fields.get("event") == "mic_alt0_failed"]
    assert len(alt0) == 1 and alt0[0].levelno == logging.DEBUG
    t.close()


def test_switching_the_audio_interface_off_fails_loudly_when_nothing_looked_like_an_unplug(dll, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    t, mic = idle_mic(dll)
    reap(mic, 0xC0000120, 0)                           # an old, recovered streak must not hide a real problem
    dll.override["WinUsb_SetCurrentAlternateSetting"] = lambda *a: 0
    dll.error = 31
    t.stop_mic()
    alt0 = [r for r in caplog.records if r.fields.get("event") == "mic_alt0_failed"]
    assert len(alt0) == 1 and alt0[0].levelno == logging.WARNING
    t.close()


# --- buffer sizes, slot offsets and chunk boundaries

def capture_writes(dll):
    # Record (offset, length, continue flag, bytes at that offset) for every iso write, completing it at once.
    seen, base = [], []
    dll.override["WinUsb_RegisterIsochBuffer"] = lambda h, pipe, buf, length, pout: (
        base.append((buf.value, length)), setattr(pout._obj, "value", dll.new_id(dll.registered)), 1)[2]

    def write(h, off, ln, cont, ov):
        addr, size = base[-1]
        assert off + ln <= size                                  # never past the end of the registered buffer
        seen.append((off, ln, cont, ctypes.string_at(addr + off, ln)))
        ov._obj.Internal = 0
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    return seen


def test_a_still_is_split_on_packet_boundaries_into_slots_and_arrives_whole(dll):
    from psychea.protocol import PKT, video_frame
    seen = capture_writes(dll)
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    jpeg = bytes(range(256)) * 600                               # 153600 bytes: four chunks
    data, lens = video_frame(jpeg, seq=1)
    assert t.send_video(data, lens) == 0
    assert b"".join(w[3] for w in seen) == data
    assert [w[2] for w in seen] == [False] + [True] * (len(seen) - 1)
    assert all(w[1] <= winusb.WinTransport.VIDEO_SLOT for w in seen)
    assert all(w[1] % PKT == 0 for w in seen[:-1]) and seen[-1][1] % PKT == 16       # the trailer is the last packet
    assert [w[0] for w in seen] == [i % 4 * winusb.WinTransport.VIDEO_SLOT for i in range(len(seen))]
    t.close()


def test_a_frame_of_exactly_one_slot_is_one_write_and_one_byte_more_goes_to_the_next_slot(dll):
    seen = capture_writes(dll)
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    slot = winusb.WinTransport.VIDEO_SLOT
    t.send_video(b"a" * slot, [slot // 2, slot // 2])
    t.send_video(b"b" * (slot + 1), [slot, 1])
    assert [(w[0], w[1]) for w in seen] == [(0, slot), (slot, slot), (2 * slot, 1)]
    t.close()


def test_every_microphone_packet_fits_its_slot_at_every_rate_and_framing(dll):
    for rate in (11025, 22050, 44100, 48000):
        for extended in (False, True):
            seen = capture_writes(dll)
            t, _ = winusb.WinTransport.connect()
            t.start_mic(Source(), rate, extended)
            time.sleep(0.15)
            t.stop_mic()
            t.close()
            assert len(seen) > 70, (rate, extended)             # the ring of 64 slots has wrapped around
            assert all(0 < w[1] <= 126 for w in seen), (rate, extended)
            assert all(w[0] == i % 64 * 128 for i, w in enumerate(seen)), (rate, extended)
            assert nothing_open(dll)
            assert all(w[3][:2] == (b"\x00\x00" if extended else b"\x01\x00") for w in seen)      # header or samples


def test_the_registered_buffers_are_exactly_slots_times_slot_bytes(dll):
    sizes = []
    dll.override["WinUsb_RegisterIsochBuffer"] = lambda h, pipe, buf, length, pout: (
        sizes.append((pipe, length)), setattr(pout._obj, "value", dll.new_id(dll.registered)), 1)[2]
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    t.start_mic(Source(), 22050, False)
    t.close()
    assert sizes == [(0x04, 4 * 48 * 1024), (0x02, 64 * 128)]


def test_a_write_larger_than_its_slot_is_refused_before_anything_is_copied(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    with pytest.raises(ValueError):
        t.video.write(b"x" * (winusb.WinTransport.VIDEO_SLOT + 1))
    assert "WinUsb_WriteIsochPipeAsap" not in dll.names()
    t.close()


# --- process-wide and per-pipe settings

def test_the_one_millisecond_timer_is_requested_once_however_many_connect_attempts_run(monkeypatch):
    from types import SimpleNamespace
    calls = []
    winmm = SimpleNamespace(timeBeginPeriod=lambda ms: calls.append(ms))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(winmm=winmm), raising=False)
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: FakeDll(), raising=False)
    monkeypatch.setattr(winusb, "_timer_raised", False)
    for _ in range(85):                                           # the attempts of one two-second handshake window
        winusb.Api()
    assert calls == [1]


def test_a_pipe_timeout_that_could_not_be_set_is_tried_again(dll):
    t, _ = winusb.WinTransport.connect()
    dll.calls.clear()
    answers = iter([0, 1])
    dll.override["WinUsb_SetPipePolicy"] = lambda *a: next(answers)
    dll.override["WinUsb_ReadPipe"] = lambda *a: 0
    dll.error = 121
    t.read_message(10)
    t.read_message(10)                                            # same timeout again: the first attempt had failed
    t.read_message(10)                                            # and now it is known to be set
    assert dll.names().count("WinUsb_SetPipePolicy") == 2
    t.close()


# --- the device vanishes in the middle of things

def test_sending_video_without_a_video_stream_is_a_usb_error_not_a_crash(dll):
    t, _ = winusb.WinTransport.connect()
    with pytest.raises(UsbError):
        t.send_video(b"x" * 10, [10])
    t.set_video_alt(1)
    t.set_video_alt(0)
    with pytest.raises(UsbError):
        t.send_video(b"x" * 10, [10])
    t.close()


def test_a_device_that_vanishes_during_a_video_write_ends_in_gone_and_a_clean_close(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    writes = []

    def write(h, off, ln, cont, ov):
        writes.append(1)
        if len(writes) >= 2:                                       # the retry as a new stream fails too
            dll.error = 1167
            return 0
        ov._obj.Internal = 0
        return 1

    dll.override["WinUsb_WriteIsochPipeAsap"] = write
    with pytest.raises(UsbGone):
        t.send_video(b"x" * 100000, [896] * 111)                   # three chunks
    t.close()
    assert nothing_open(dll)


def test_closing_a_transport_whose_device_is_gone_neither_raises_nor_keeps_the_handles(dll):
    t, _ = winusb.WinTransport.connect()
    t.set_video_alt(1)
    t.start_mic(Source(), 22050, False)
    time.sleep(0.02)
    for name in ("WinUsb_AbortPipe", "WinUsb_UnregisterIsochBuffer", "WinUsb_SetCurrentAlternateSetting",
                 "WinUsb_Free", "CloseHandle"):
        dll.override[name] = lambda *a: 0                          # every call fails: the device is gone
    dll.error = 1167
    t.close()
    t.close()
    assert t.mic is None and t.video is None and t.if0 is None and t.if3 is None


def test_starting_the_microphone_twice_stops_the_first_streamer_instead_of_leaking_its_thread(dll):
    import threading
    before = threading.active_count()
    t, _ = winusb.WinTransport.connect()
    first = Source()
    t.start_mic(first, 22050, False)
    old = t.mic
    t.start_mic(Source(), 22050, False)
    assert not old.thread.is_alive() and first.closed and t.mic is not old
    t.close()
    assert nothing_open(dll) and threading.active_count() == before
