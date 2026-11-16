import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

from . import logs
from .libusb_backend import present
from .session import Session
from .settings import Settings
from .transport import DriverMissing, Transport, UsbError

log = logs.get("cli")
ATTEMPT_WINDOW = 2.0        # the PSP checks the handshake for only a moment after the cable is plugged in
ATTEMPT_PAUSE_S = 0.02
APPEAR_POLL_S, DISAPPEAR_POLL_S = 0.01, 0.2   # how often the bus is looked at while waiting for the PSP to come / go

USAGE = """Usage: uv run psychea [options]

Picture
  --webcam N         PC camera to use (default 0; numbers from --list)
  --camres WxH       capture size, e.g. 1280x720: sharper after scaling down (default 640x480)
  --mirror           flip the picture left-right (if the webcam shows a mirror image)
  --testpattern      colour bars instead of a camera (checks the PSP link without a webcam)

Sound
  --mic pc|tone|off  PC microphone (default; needs sounddevice), 1 kHz test tone, or no audio
  --micdev N         microphone to use (numbers from --list)
  --micgain G        volume factor, e.g. 0.4 (default 1.0)

Diagnostics
  --list             show the cameras and microphones of this PC, then exit
  --verbose          also show protocol detail on the console (the log file always has it)
  --logfile PATH     where to write the detailed log (default psychea.log; send it if something goes wrong)

Windows
  --install-driver   once, as Administrator: register WinUSB for the PSP camera (the PSP can stay unplugged)

Options with a value also accept --name=value.
Run it, get the game to the camera screen, THEN plug the cable.
"""


def number(name: str, lo: float, whole: bool = False) -> Callable[[str], float]:
    def parse(text: str) -> float:
        try:
            v = int(text) if whole else float(text)
        except ValueError:
            raise ValueError("%s: %r is not a %s" % (name, text, "whole number" if whole else "number")) from None
        if not math.isfinite(v) or v < lo:
            raise ValueError("%s: %r is out of range (must be at least %g)" % (name, text, lo))
        return v
    return parse


CAMRES_MIN, CAMRES_MAX = (160, 120), (4096, 4096)


def resolution(text: str) -> tuple[int, int]:
    w, sep, h = text.lower().partition("x")
    in_range = w.isdigit() and h.isdigit() and (
        CAMRES_MIN[0] <= int(w) <= CAMRES_MAX[0] and CAMRES_MIN[1] <= int(h) <= CAMRES_MAX[1])
    if not sep or not in_range:
        raise ValueError("--camres: %r is not WIDTHxHEIGHT (%dx%d .. %dx%d), e.g. 1280x720" % (
            (text,) + CAMRES_MIN + CAMRES_MAX))
    return int(w), int(h)


FLAGS = {"--testpattern": ("testpattern", True),
         "--mirror": ("mirror", True),
         "--verbose": ("verbose", True)}
OPTIONS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "--webcam": ("webcam", number("--webcam", 0, whole=True)),
    "--micgain": ("micgain", number("--micgain", 0.01)), "--camres": ("camres", resolution),
    "--micdev": ("micdev", lambda v: int(v) if v.isdigit() else v),
    "--mic": ("mic", str.lower), "--logfile": ("logfile", str)}


def next_value(argv: list[str], i: int, name: str) -> tuple[str, int]:
    if i + 1 >= len(argv):
        raise ValueError("%s needs a value" % name)
    return argv[i + 1], i + 1


def apply_arguments(s: Settings, argv: list[str]) -> None:
    i = 0
    while i < len(argv):
        x, eq, inline = argv[i].partition("=")         # --name value  or  --name=value
        if x in FLAGS and not eq:
            setattr(s, FLAGS[x][0], FLAGS[x][1])
        elif x in OPTIONS:
            value, i = next_value(argv, i, x) if not eq else (inline, i)
            setattr(s, OPTIONS[x][0], OPTIONS[x][1](value))
        else:
            raise ValueError("unknown option %s" % argv[i])
        i += 1
    if s.testpattern:                               # --testpattern wins over --webcam
        s.webcam = None


def check_consistency(s: Settings) -> None:
    if s.mic not in ("pc", "tone", "off"):
        raise ValueError("--mic: %r is not pc, tone or off" % s.mic)


def parse_args(argv: list[str]) -> Settings:
    if "-h" in argv or "--help" in argv:
        print(USAGE)
        raise SystemExit(0)
    if "--list" in argv:
        from .devices import describe_devices
        print(describe_devices())
        raise SystemExit(0)
    if "--install-driver" in argv:
        from .driver import run_install
        raise SystemExit(run_install())
    s = Settings()
    try:
        apply_arguments(s, argv)
        check_consistency(s)
    except (ValueError, IndexError) as e:
        print("bad argument: %s\n" % (e,) + USAGE)
        raise SystemExit(2) from e
    return s


def connector(settings: Settings) -> Callable[[], tuple[Transport, str]]:
    if settings.backend == "winusb":
        from .winusb import WinTransport
        return WinTransport.connect
    from .libusb_backend import LibusbTransport
    return LibusbTransport.connect


def wait_until(condition: Callable[[], bool], step: float) -> None:
    while not condition():
        time.sleep(step)


def connect_within_window(connect: Callable[[], tuple[Transport, str]]) -> tuple[Optional[Transport], int]:
    t_seen, last = time.monotonic(), ""
    attempts = 0
    while time.monotonic() - t_seen < ATTEMPT_WINDOW:
        attempts += 1
        ms = (time.monotonic() - t_seen) * 1000
        try:
            transport, text = connect()
        except Exception as e:                  # a UsbError, or the OS failing in some other way: try again
            text = "%s: %s" % (type(e).__name__, e)
            if text != last:
                (log.debug if type(e) is not DriverMissing and isinstance(e, UsbError) else log.warning)(
                    "Handshake attempt %d failed: %s" % (attempts, text), event="handshake_attempt",
                    attempt=attempts, ms=round(ms), error=text)
                last = text
            time.sleep(ATTEMPT_PAUSE_S)
            continue
        try:
            log.info("Connected to the PSP (handshake OK, %d ms after it appeared)" % ms, event="handshake_ok",
                     attempts=attempts, ms=round(ms), detail=text)
        except BaseException:                   # Ctrl+C here: nobody else holds the transport yet
            close_transport(transport)
            raise
        return transport, attempts
    return None, attempts


def close_transport(transport: Transport) -> None:
    try:
        transport.close()
    except Exception as e:                      # the cable is probably out already: the loop must go on
        log.warning("Could not close the USB connection cleanly: %r" % (e,), event="close_failed", error=e)


def run_session(transport: Transport, settings: Settings) -> None:
    crashed = False
    try:
        Session(transport, settings, present).run()
    except Exception as e:                      # keep the loop alive, show why
        crashed = True
        log.error("The session crashed: %r" % (e,), event="session_crashed", exc_info=True)
    finally:
        close_transport(transport)              # also on Ctrl+C: stops the microphone stream, frees the handles
    if crashed or present():                    # a session that ended normally usually means the PSP left already
        log.info("Unplug the cable to start again.", event="waiting_unplug")


def serve_once(settings: Settings, connect: Callable[[], tuple[Transport, str]]) -> None:
    wait_until(present, APPEAR_POLL_S)
    log.info("PSP detected on USB", event="device_seen")
    transport, attempts = connect_within_window(connect)
    if transport is None:
        log.error("Could not connect to the PSP (%d attempts). Unplug and re-plug the cable; the game must already be "
                  "on its camera screen." % attempts, event="handshake_failed", attempts=attempts)
    else:
        run_session(transport, settings)
    wait_until(lambda: not present(), DISAPPEAR_POLL_S)


def serve_forever(settings: Settings, connect: Callable[[], tuple[Transport, str]]) -> None:
    last = ""
    while True:
        try:
            serve_once(settings, connect)
        except Exception as e:
            text = repr(e)
            log.error("The connect loop failed: %s. Trying again in 1 s" % text, event="loop_failed",
                      exc_info=text != last)         # the traceback once per kind of error, not every second
            last = text
            time.sleep(1.0)


def announce_ready(path: Optional[Path]) -> None:
    log.info("Ready. 1) get the game to its camera screen  2) plug the PSP in with the USB cable.  Ctrl+C quits.",
             event="ready")
    if path:
        log.info("Detailed log: %s (send this file if something goes wrong)" % path.resolve(), event="logfile",
                 path=str(path.resolve()))


def main(argv: Optional[list[str]] = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    settings = parse_args(argv)
    path = logs.setup(settings.verbose, settings.logfile)
    logs.describe_environment(settings, argv)
    connect = connector(settings)
    announce_ready(path)
    try:
        serve_forever(settings, connect)
    except KeyboardInterrupt:
        log.info("Stopped by the user (Ctrl+C)", event="stopped")
    sys.stdout.flush()
    os._exit(0)
