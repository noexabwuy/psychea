from __future__ import annotations

from typing import Any

MAX_SAMPLES = 63            # one isochronous packet is 126 bytes: 63 int16 samples
EXTENDED_HEADER = b"\x00\x00"


class MicPackets:
    def __init__(self, source: Any, rate: int, extended: bool) -> None:
        self.source, self.rate, self.extended = source, rate, extended
        self.acc = 0

    def next(self) -> bytes:
        self.acc += self.rate
        n, self.acc = divmod(self.acc, 1000)
        n = min(n, MAX_SAMPLES - 1 if self.extended else MAX_SAMPLES)
        data = self.source.read(n)
        return EXTENDED_HEADER + data if self.extended else data
