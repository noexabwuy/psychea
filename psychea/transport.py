import time
from typing import Any, Optional

from .protocol import (
    CONTROL_TIMEOUT_MS,
    HANDSHAKE_IN_VALUE,
    IF_VIDEO,
    REQ_HANDSHAKE,
    REQ_REPLY,
    REQ_VENDOR_IN,
    REQ_VENDOR_OUT,
)


class UsbError(Exception):
    pass


class UsbTimeout(UsbError):
    pass


class DriverMissing(UsbError):
    pass


class UsbGone(UsbError):
    pass


class UsbStall(UsbError):
    pass


class Transport:
    def read_message(self, timeout_ms: int) -> Optional[bytes]:
        raise NotImplementedError

    def control_out(self, bm: int, req: int, value: int, index: int, data: bytes, timeout: int) -> int:
        raise NotImplementedError

    def reply(self, cmd: int, data: bytes) -> None:
        self.control_out(REQ_VENDOR_OUT, REQ_REPLY, cmd, IF_VIDEO, data, CONTROL_TIMEOUT_MS)

    def set_video_alt(self, alt: int) -> None:
        raise NotImplementedError

    def send_video(self, data: bytes, lens: list[int]) -> int:
        raise NotImplementedError

    def start_mic(self, source: Any, rate: int, extended: bool) -> None:
        raise NotImplementedError

    def stop_mic(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class HandshakeRejected(UsbError):
    pass


def handshake(dev: Any, info: bytes) -> str:
    t = time.monotonic()
    answer = bytes(dev.control_in(REQ_VENDOR_IN, REQ_HANDSHAKE, HANDSHAKE_IN_VALUE, 0x0000, 1, CONTROL_TIMEOUT_MS))
    written = dev.control_out(REQ_VENDOR_OUT, REQ_HANDSHAKE, 0, 0x0000, info, CONTROL_TIMEOUT_MS)
    ms = (time.monotonic() - t) * 1000
    text = "IN 0xC1/0x01 -> %s ; OUT 0x41/0x01 wrote %d (%.0f ms)" % (answer.hex(" "), written, ms)
    if answer != b"\x00":
        raise HandshakeRejected("PSP rejected: " + text)
    return text


# --- audio log lines shared by both backends, so a Linux log and a Windows log read the same
STATS_PERIOD_S = 10.0                # one "Audio: N s sent" (and one video) summary line per this many seconds
FAILURE_LOG_FIRST, FAILURE_LOG_EVERY = 3, 100      # failed audio transfers logged: the first few, then every 100th
def report_failure(count: int) -> bool:
    return count <= FAILURE_LOG_FIRST or count % FAILURE_LOG_EVERY == 0


NT_STATUS_NAMES = {0xC000000D: "STATUS_INVALID_PARAMETER", 0x80000011: "STATUS_DEVICE_BUSY",
                   0xC0000120: "STATUS_CANCELLED"}
STATUS_CANCELLED, STATUS_DEVICE_BUSY = 0xC0000120, 0x80000011
DEAD_STREAK = 100                    # this many failed 1-ms writes in a row: the stream (or the device) is gone


def nt_status_text(status: int) -> str:
    name = NT_STATUS_NAMES.get(status)
    return "0x%08X (%s)" % (status, name) if name else "0x%08X" % status


def is_unplug_status(status: int) -> bool:
    return status == STATUS_CANCELLED


def is_unplug_follow_up(status: int) -> bool:
    return status in (STATUS_CANCELLED, STATUS_DEVICE_BUSY)


def stream_is_dead(streak: int) -> bool:
    return streak >= DEAD_STREAK


def audio_failure_text(status: object, count: int) -> str:
    return "An audio transfer failed (status %s, failures so far: %d)" % (status, count)


def audio_stats_text(sent_ms: int, failed: int, extra: str = "") -> str:
    return "Audio: %.0f s sent | failed transfers %d%s" % (sent_ms / 1000, failed, extra)
