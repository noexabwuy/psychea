# Micro-benchmarks of the hot paths (run: uv run python scripts/bench.py). Numbers are per call.
import statistics
import time

import cv2
import numpy as np

from psychea import protocol, sources


def scene():
    # A photo-like 640x480 frame: gradients, shapes and a little sensor noise.
    rng = np.random.default_rng(1)
    y, x = np.mgrid[0:480, 0:640]
    img = np.stack([(x / 3) % 256, (y / 2) % 256, ((x + y) / 4) % 256], axis=-1).astype(np.uint8)
    cv2.circle(img, (320, 240), 120, (40, 200, 90), -1)
    cv2.putText(img, "BENCH", (200, 260), cv2.FONT_HERSHEY_SIMPLEX, 3, (255, 255, 255), 6)
    noise = rng.integers(-6, 6, img.shape, dtype=np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def timeit(fn, n=300):
    fn()
    t = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        t.append((time.perf_counter() - t0) * 1000)
    return statistics.median(t), max(t)


def report(name, fn, n=300):
    med, worst = timeit(fn, n)
    print("%-44s median %7.3f ms   worst %7.3f ms" % (name, med, worst))


def main():
    frame, out = scene(), sources.Output(w=480, h=272, max_jpeg=14884)
    print("--- video: webcam -> JPEG (frame 640x480)")
    report("crop 16:9 + resize INTER_AREA 480x272", lambda: cv2.resize(frame[44:436], (480, 272), interpolation=cv2.INTER_AREA))
    report("  same with INTER_LINEAR", lambda: cv2.resize(frame[44:436], (480, 272), interpolation=cv2.INTER_LINEAR))
    small = cv2.resize(frame[44:436], (480, 272), interpolation=cv2.INTER_AREA)
    report("orient (mirror)", lambda: sources.orient(small, sources.Output(mirror=1)))
    for q in (50, 70, 90):
        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, q])
        report("imencode quality %d (%d bytes)" % (q, len(buf)),
               lambda q=q: cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, q, cv2.IMWRITE_JPEG_OPTIMIZE, 0]))
    result = []
    report("fit_quality (as shipped, hint 40)", lambda: result.append(sources.fit_quality(small, out.max_jpeg, 40)))
    print("   result: quality %d, %d bytes of %d allowed" % (result[-1][1], len(result[-1][0]), out.max_jpeg))
    full = sources.Output(w=480, h=272, max_jpeg=14884, mirror=1)
    state = [70]

    def encode_path():
        # What the camera thread does per encoded frame (Webcam._encode_frame without the thread).
        img = cv2.resize(sources.crop_to_aspect(frame, full.w, full.h), (full.w, full.h), interpolation=cv2.INTER_AREA)
        data, state[0] = sources.fit_quality(sources.orient(sources.adjust(img, full), full), full.max_jpeg, state[0])
        return data
    report("whole encode path (crop, resize, adjust, fit)", encode_path)
    for name, setting in (("zoom 20", {"zoom": 20}), ("contrast+saturation", {"contrast": 150, "saturation": 5}),
                          ("white balance 3", {"wb": 3}), ("effect sepia", {"effect": 3})):
        o = sources.Output(**setting)
        report("  adjust: %s" % name, lambda o=o: sources.adjust(small, o))
    print("--- video: framing")
    jpeg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
    report("video_frame (%d bytes)" % len(jpeg), lambda: protocol.video_frame(jpeg))
    print("--- audio (per 1 ms packet)")
    tone = sources.ToneSource(22050)
    report("ToneSource.read(22)", lambda: tone.read(22), 3000)

    class Mic(sources.PcMicSource):
        def __init__(self, gain):
            import collections
            import threading
            self.chain, self.rate, self.have, self.lock = sources.MicChain(22050, gain), 22050, 0, threading.Lock()
            self.buf = collections.deque()

    for g in (1.0, 0.4):
        mic = Mic(g)

        def read(mic=mic):
            mic.buf.append(b"\x01\x00" * 22)
            mic.have = 44
            return mic.read(22)
        report("PcMicSource.read(22) gain %.1f" % g, read, 3000)


if __name__ == "__main__":
    main()
