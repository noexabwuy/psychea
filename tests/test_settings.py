# Defaults of the run-time settings.
import os

from psychea.settings import Settings


def test_defaults_are_the_documented_ones():
    s = Settings()
    assert s.backend == ("winusb" if os.name == "nt" else "libusb")
    assert (s.webcam, s.mic, s.micgain) == (0, "pc", 1.0)
    assert not s.testpattern and not s.verbose
    assert s.logfile == "psychea.log"
