# Finds the biggest video frame the PSP accepts (run: uv run python scripts/size_probe.py). Replaces the camera
# by frames of exactly known JPEG sizes that grow step by step; each picture shows its own size. Get the game /
# XMB camera to the VIDEO screen first, then plug the cable. Watch the PSP screen: the picture changes every
# step until a frame is too big and the PSP drops it; the number you see last is the biggest accepted size.
# Add --mirror if the text appears mirrored.
import struct
import sys
import time
from typing import Any, Callable

import cv2
import numpy as np

from psychea.protocol import PKT
from psychea.sources import orient

START, STOP, INCREMENT = 16384, 32768, 512      # JPEG bytes
STEPS = list(range(START, STOP + 1, INCREMENT))
HOLD_S = 1.5                                    # seconds on one size


def pad_jpeg(jpeg: bytes, total: int) -> bytes:
    # The same picture in exactly `total` bytes: a JPEG comment segment (ignored by decoders) after the SOI
    # marker.
    extra = total - len(jpeg)
    if extra < 4 or extra > 65537:
        raise ValueError("%d bytes cannot be reached from %d with one comment segment" % (total, len(jpeg)))
    comment = b"\xff\xfe" + struct.pack(">H", extra - 2) + b"\x55" * (extra - 4)
    return jpeg[:2] + comment + jpeg[2:]


def step_at(elapsed: float) -> int:
    return min(int(elapsed / HOLD_S), len(STEPS) - 1)


def label(size: int) -> str:
    return "%d B  %d packets" % (size, -(-size // PKT))


class ProbeSource:
    # Looks like a camera to the session: get() returns the frame of the current size step.

    def __init__(self, clock: Callable[[], float] = time.time, out: Any = None) -> None:
        self.clock, self.out = clock, out
        self.t0 = clock()
        self.next_send = 0.0

    def reset(self) -> None:
        self.t0 = self.clock()

    def close(self) -> None:
        return None

    def age(self) -> float:
        return 0.0

    def still(self, w: int, h: int, max_bytes: int) -> bytes:
        raise RuntimeError("the size probe only does video")

    def get(self) -> bytes:
        index = step_at(self.clock() - self.t0)
        size = STEPS[index]
        img = np.full((272, 480, 3), 30, np.uint8)
        cv2.putText(img, label(size), (20, 110), cv2.FONT_HERSHEY_SIMPLEX, 1.3, (255, 255, 255), 3)
        cv2.putText(img, "step %d of %d" % (index + 1, len(STEPS)), (20, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.0,
                    (0, 255, 255), 2)
        cv2.rectangle(img, (20, 200), (20 + int(440 * (index + 1) / len(STEPS)), 230), (0, 200, 0), -1)
        if self.out is not None:
            img = orient(img, self.out)                 # as the camera would: the game's mirror/flip, plus --mirror
        jpeg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 60])[1].tobytes()
        return pad_jpeg(jpeg, size)


def main() -> None:
    from psychea import cli, session
    session.Session.make_source = lambda self: ProbeSource(out=self.out)          # type: ignore[method-assign]
    print(__doc__)
    cli.main(["--mic", "off", "--logfile", "size_probe.log"] + sys.argv[1:])


if __name__ == "__main__":
    main()
