import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_module_state():
    # winusb and libusb keep process-wide state on purpose (parked buffers, the timer resolution flag, the
    # shared libusb context); a test must neither see another test's leftovers nor leave its own behind.
    from psychea import libusb_backend, winusb
    parked, raised = list(winusb._PARKED), winusb._timer_raised
    keep, base = list(libusb_backend._KEEP), list(libusb_backend._BASE)
    yield
    winusb._PARKED[:] = parked
    winusb._timer_raised = raised
    libusb_backend._KEEP[:] = keep
    libusb_backend._BASE[:] = base
