import statistics
from typing import Any, Optional

from .transport import STATS_PERIOD_S


class VideoStats:
    PERIOD = STATS_PERIOD_S

    def __init__(self, now: float) -> None:
        self.restart(now)

    def restart(self, now: float) -> None:
        self.start = now
        self.sizes: list[int] = []
        self.send_ms: list[float] = []
        self.age_ms: list[float] = []

    def frame(self, nbytes: int, send_ms: float, age_ms: float) -> None:
        self.sizes.append(nbytes)
        self.send_ms.append(send_ms)
        self.age_ms.append(age_ms)

    def due(self, now: float) -> bool:
        return now - self.start >= self.PERIOD and bool(self.sizes)

    def summary(self, now: float, quality: Optional[int] = None,
                limit: Optional[float] = None) -> tuple[str, dict[str, Any]]:
        seconds = max(now - self.start, 1e-9)
        fps, kb = len(self.sizes) / seconds, statistics.fmean(self.sizes) / 1024
        send, age = statistics.fmean(self.send_ms), statistics.fmean(self.age_ms)
        fields = {"fps": round(fps, 1), "kb_per_frame": round(kb, 1), "limit_kb": limit and round(limit / 1024, 1),
                  "send_ms": round(send, 1), "send_ms_max": round(max(self.send_ms), 1),
                  "age_ms": round(age), "age_ms_max": round(max(self.age_ms)), "jpeg_quality": quality}
        text = "Video: %.1f fps | %.1f KB/frame%s | picture delay %d ms (max %d)%s" % (
            fps, kb, "" if limit is None else " of %.1f" % (limit / 1024), age, max(self.age_ms),
            "" if quality is None else " | JPEG quality %d" % quality)
        self.restart(now)
        return text, fields
