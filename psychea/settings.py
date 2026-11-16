import os
from dataclasses import dataclass
from typing import Optional, Union


@dataclass
class Settings:
    backend: str = "winusb" if os.name == "nt" else "libusb"
    webcam: Optional[int] = 0               # None = test pattern
    testpattern: bool = False               # --testpattern: wins over --webcam whatever the order
    mic: str = "pc"                         # pc | tone | off
    micdev: Optional[Union[int, str]] = None
    micgain: float = 1.0
    camres: Optional[tuple[int, int]] = None  # --camres WxH: capture size; None = 640x480
    mirror: bool = False                    # --mirror: flip left-right on top of what the game asks for
    verbose: bool = False                   # console shows DEBUG lines too
    logfile: Optional[str] = "psychea.log"   # the detailed log; None = no file (tests)
