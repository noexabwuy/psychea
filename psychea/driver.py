import os
import platform
import subprocess
import sys
import tempfile
from typing import Any, Callable, Optional

VID, PID = 0x054C, 0x0282
INTERFACES = (0, 1, 3)
WINUSB = 0                                          # wdi driver type 0 = WinUSB


def wdi_command(exe: str, iid: int, dest: str) -> list[str]:
    return [exe, "--vid", str(VID), "--pid", str(PID), "--iid", str(iid), "--type", str(WINUSB), "--dest", dest]


def install_interface(exe: str, iid: int, run: Callable[[list[str]], Any], say: Callable[[str], object]) -> bool:
    say("Installing WinUSB on interface %d ..." % iid)
    try:
        result = run(wdi_command(exe, iid, tempfile.mkdtemp()))
    except OSError as e:                                # blocked by a virus scanner, wrong bitness, no permission
        say("  could not run the installer: %s" % e)
        return False
    if result.returncode:
        say("  failed (exit code %d): %s" % (result.returncode, (result.stderr or result.stdout).decode(
            "utf-8", "replace").strip()))
    return not result.returncode


def install_driver(windows: bool, admin: bool, exe: Optional[str], run: Callable[[list[str]], Any],
                   say: Callable[[str], object]) -> int:
    if not windows:
        say("A driver is not needed on this system (Linux/macOS use libusb as it is).")
        return 0
    if not admin:
        say("Run this command in a terminal opened as Administrator (right click -> Run as administrator).")
        return 1
    if not exe:
        say("The driver installer is missing (uv sync, or install WinUSB by hand with Zadig: Options -> List all "
            "devices -> the PSP interfaces 0, 1 and 3 -> WinUSB -> Replace driver).")
        return 1
    failed = [iid for iid in INTERFACES if not install_interface(exe, iid, run, say)]
    if failed:
        say("Driver install failed on interface(s) %s. Try Zadig by hand (see the README)." % failed)
        return 1
    say("WinUSB is registered for the PSP camera interfaces. Windows uses it the next time the PSP shows up as the "
        "camera (the PSP stays on the camera screen only for a moment, so nothing needs to be plugged in now).")
    return 0


def find_wdi() -> Optional[str]:
    bits = platform.architecture()[0]
    if bits == "32bit" and platform.machine().upper() == "AMD64":
        bits = "64bit"
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "binaries", "wdi-%s.exe" % bits)
    return path if os.path.exists(path) else None


def is_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def run_install() -> int:
    return install_driver(sys.platform == "win32", is_admin(), find_wdi(),
                          lambda cmd: subprocess.run(cmd, capture_output=True), print)
