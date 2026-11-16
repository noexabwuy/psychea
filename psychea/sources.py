import math
import os
import struct
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional, Sequence, Union

from . import logs
from .protocol import (
    BRIGHTNESS_NEUTRAL,
    CONTRAST_NEUTRAL,
    DEFAULT_MAX_JPEG,
    DEFAULT_SIZE,
    EV_NEUTRAL,
    SATURATION_NEUTRAL,
    ZOOM_MAX,
    ZOOM_NEUTRAL,
)
from .settings import Settings

log = logs.get("sources")

# Picture maths
GAMMA = 2.2                          # display gamma assumed when the exposure is applied in linear light
EV_STEP = 0.25                       # stops of exposure per code step
CONTRAST_STEP, CONTRAST_LIMIT = 2, 254   # contrast code -> formula value; the formula is defined for -255..255
MID_GREY = 128.0
REC709_BGR = (0.0722, 0.7152, 0.2126)    # luma weights, in the BGR order of the frames
SHARPEN_RADIUS, SHARPEN_PER_STEP = 1.0, 0.5   # Gaussian sigma of the blur; unsharp-mask amount per sharpness step
EFFECT_NEGATIVE, EFFECT_GREY, EFFECT_SEPIA = 1, 2, 3   # image effect codes; 0 = normal, 4..6 = blue, red, green tints
TINT_STRENGTH = 0.8                  # how much of the tint colour a coloured effect takes (the rest is plain grey)
DAYLIGHT_K = 6500                    # the white point the white balance gains aim at (D65)
# JPEG quality (1..100)
QUALITY_LOWEST, QUALITY_HIGHEST = 1, 100
QUALITY_FLOOR = 10                   # jpeg_within never goes below this (a photo that still does not fit goes oversize)
QUALITY_STEP = 10                    # jpeg_within lowers the quality by this much per try
QUALITY_VIDEO, QUALITY_PHOTO = 85, 92    # jpeg_within start for the test pattern / for photos
QUALITY_ADAPTIVE_START = 70          # the webcam's adaptive quality starts here
FIT_SPAN = 6                         # fit_quality first tries hint +- this
# Webcam timing
VGA = (640, 480)                     # the webcam size used when --camres is not given
CAMERA_FPS_ASKED = 30                # FPS requested from the camera
FIRST_PICTURE_TIMEOUT = 5.0          # seconds to wait for the first encoded picture
GAP_CAP, GAP_SMOOTHING = 0.2, 0.1    # a camera gap over 0.2 s (a stall) counts as 0.2 s; weight of a new gap
ENCODE_SMOOTHING = 0.2               # weight of a new encode time in the running average
# Microphone filter and limiter
INT16_MAX = 32767
BYTES_PER_SAMPLE = 2                 # int16 mono
LIMITER_KNEE = 0.7                   # fraction of full scale where the soft limiter starts
DC_BLOCK_HZ, LOWPASS_HZ, LOWPASS_MAX_OF_RATE = 100.0, 10000.0, 0.45
BUTTERWORTH_Q = 0.7071               # 2nd-order low-pass without resonance
TONE_HZ, TONE_AMPLITUDE = 1000.0, 8000


@dataclass
class Output:
    w: int = DEFAULT_SIZE[0]
    h: int = DEFAULT_SIZE[1]
    flip: int = 0
    mirror: int = 0
    mirror_extra: int = 0                   # --mirror: inverts the game's own request
    max_jpeg: int = DEFAULT_MAX_JPEG
    # picture settings from the camera app (neutral values: nothing is changed). Codes from the
    # sceUSBCam_Driver tables; the neutral points are the defaults the XMB app sends at start (0x80 contrast).
    effect: int = 0                         # 0 normal 1 negative 2 black&white 3 sepia 4 blue 5 red 6 green
    zoom: int = 0                           # 0 = off, else 10..80 = 1.0x..8.0x
    contrast: int = CONTRAST_NEUTRAL
    brightness: int = BRIGHTNESS_NEUTRAL
    wb: int = 0                             # white balance 0 auto 1 daylight 2 fluor. 3 incand. (StartVideo byte 5)
    sharpness: int = 0                      # 0..3 (SDK table); 0 = what the app starts with, no sharpening
    ev: int = EV_NEUTRAL                    # exposure code 4..16, 10 = 0: lower = brighter, 1/4 EV a step
    saturation: int = SATURATION_NEUTRAL    # byte = value/42, 0..6; 3 = the middle of the range


def orient(frame: Any, out: Output) -> Any:
    import cv2
    mirror = out.mirror ^ out.mirror_extra
    if mirror and out.flip:
        return cv2.flip(frame, -1)
    if mirror:
        return cv2.flip(frame, 1)
    if out.flip:
        return cv2.flip(frame, 0)
    return frame


_EFFECT_TINT = {4: (255, 0, 0), 5: (0, 0, 255), 6: (0, 255, 0)}                      # BGR multipliers
_SEPIA = ((0.131, 0.534, 0.272), (0.168, 0.686, 0.349), (0.189, 0.769, 0.393))      # BGR rows


_WB_KELVIN = {2: 4000, 3: 2800}                  # fluorescent, incandescent: the light the preset corrects for


def _kelvin_rgb(kelvin: float) -> tuple[float, float, float]:
    t = kelvin / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    return (min(255.0, max(1.0, r)), min(255.0, max(1.0, g)), min(255.0, max(1.0, b)))


def wb_gains(kelvin: float) -> tuple[float, float, float]:
    ref, lit = _kelvin_rgb(DAYLIGHT_K), _kelvin_rgb(kelvin)
    r, g, b = (ref[i] / lit[i] for i in range(3))
    return (b / g, 1.0, r / g)


@lru_cache(maxsize=32)
def _tone_lut_values(contrast: int, brightness: int, ev: int) -> Any:
    import numpy as np
    x = np.arange(256) / 255.0
    lin = x ** GAMMA * 2.0 ** ((EV_NEUTRAL - ev) * EV_STEP)
    v = np.clip(lin, 0, 1) ** (1 / GAMMA) * 255.0
    c = max(-CONTRAST_LIMIT, min(CONTRAST_LIMIT, (contrast - CONTRAST_NEUTRAL) * CONTRAST_STEP))
    factor = 259.0 * (c + 255) / (255.0 * (259 - c))
    v = factor * (v - MID_GREY) + MID_GREY + (brightness - BRIGHTNESS_NEUTRAL)
    return np.clip(np.rint(v), 0, 255).astype(np.uint8)


def _tone_lut(out: Output) -> Any:
    return _tone_lut_values(out.contrast, out.brightness, out.ev)


def crop_to_aspect(frame: Any, w: int, h: int) -> Any:
    fh, fw = frame.shape[:2]
    if fw * h > fh * w:
        nw = max(1, fh * w // h)
        x0 = (fw - nw) // 2
        return frame[:, x0:x0 + nw]
    nh = max(1, fw * h // w)
    y0 = (fh - nh) // 2
    return frame[y0:y0 + nh, :]


def _zoom(frame: Any, zoom: int) -> Any:
    import cv2
    h, w = frame.shape[:2]
    f = min(zoom, ZOOM_MAX) / ZOOM_NEUTRAL
    ch, cw = max(2, int(h / f)), max(2, int(w / f))
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    return cv2.resize(frame[y0:y0 + ch, x0:x0 + cw], (w, h), interpolation=cv2.INTER_LINEAR)


def _saturate(frame: Any, saturation: int) -> Any:
    import cv2
    import numpy as np
    k = saturation / SATURATION_NEUTRAL                              # luma blend, Rec.709 weights (W3C saturate)
    luma = np.array([REC709_BGR] * 3, np.float32)
    return cv2.transform(frame, luma * (1 - k) + np.eye(3, dtype=np.float32) * k)


def _sharpen(frame: Any, sharpness: int) -> Any:
    import cv2
    blur = cv2.GaussianBlur(frame, (0, 0), SHARPEN_RADIUS)
    amount = SHARPEN_PER_STEP * sharpness
    return cv2.addWeighted(frame, 1 + amount, blur, -amount, 0)


@lru_cache(maxsize=16)
def _wb_luts(gains: tuple[float, float, float]) -> tuple[Any, Any, Any]:
    import numpy as np
    g = np.array(gains, np.float32)
    return tuple(np.clip(np.arange(256, dtype=np.float32) * g[i], 0, 255).astype(np.uint8) for i in range(3))


def _white_balance(frame: Any, gains: Any) -> Any:
    import cv2
    luts = _wb_luts(tuple(gains))
    return cv2.merge([cv2.LUT(frame[:, :, i], luts[i]) for i in range(3)])


def _effect(frame: Any, e: int) -> Any:
    import cv2
    import numpy as np
    if e == EFFECT_NEGATIVE:
        return 255 - frame
    if e == EFFECT_GREY:
        grey = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)
    if e == EFFECT_SEPIA:
        return cv2.transform(frame, np.array(_SEPIA, np.float32))
    if e in _EFFECT_TINT:
        grey = cv2.cvtColor(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), cv2.COLOR_GRAY2BGR).astype(np.float32)
        tint = np.array(_EFFECT_TINT[e], np.float32) / 255.0 * TINT_STRENGTH + (1 - TINT_STRENGTH)
        return np.clip(grey * tint, 0, 255).astype(np.uint8)
    return frame


def adjust(frame: Any, out: Output) -> Any:
    import cv2
    if out.zoom > ZOOM_NEUTRAL:
        frame = _zoom(frame, out.zoom)
    if (out.contrast, out.brightness, out.ev) != (CONTRAST_NEUTRAL, BRIGHTNESS_NEUTRAL, EV_NEUTRAL):
        frame = cv2.LUT(frame, _tone_lut(out))
    if out.saturation != SATURATION_NEUTRAL:
        frame = _saturate(frame, out.saturation)
    if out.sharpness:
        frame = _sharpen(frame, out.sharpness)
    gains = wb_gains(_WB_KELVIN[out.wb]) if out.wb in _WB_KELVIN else None
    if gains:
        frame = _white_balance(frame, gains)
    return _effect(frame, out.effect)


def jpeg_within(frame: Any, max_bytes: int, quality: int = QUALITY_VIDEO) -> bytes:
    import cv2
    while True:
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality,
                                               cv2.IMWRITE_JPEG_PROGRESSIVE, 0])
        data = buf.tobytes()
        if len(data) <= max_bytes or quality <= QUALITY_FLOOR:
            return data
        quality = max(QUALITY_FLOOR, quality - QUALITY_STEP)


def _jpeg(frame: Any, quality: int) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality,
                                           cv2.IMWRITE_JPEG_PROGRESSIVE, 0])
    return bytes(buf.tobytes())


def fit_quality(frame: Any, max_bytes: int, hint: int, span: int = FIT_SPAN) -> tuple[bytes, int]:
    hint = min(QUALITY_HIGHEST, max(QUALITY_LOWEST, hint))
    below, above = max(QUALITY_LOWEST, hint - span), min(QUALITY_HIGHEST, hint + span)
    data = _jpeg(frame, below)
    if len(data) > max_bytes:
        # nothing in the bracket fits: search below it
        best = _jpeg(frame, QUALITY_LOWEST) if below > QUALITY_LOWEST else data
        bq, lo, hi = QUALITY_LOWEST, QUALITY_LOWEST + 1, below - 1
        if len(best) > max_bytes:
            return best, bq
    else:
        best, bq, lo, hi = data, below, below + 1, above
        top = _jpeg(frame, above) if above > below else None
        if top is not None and len(top) <= max_bytes:
            best, bq, lo, hi = top, above, above + 1, QUALITY_HIGHEST
    while lo <= hi:                                     # highest quality that fits in [lo, hi]
        mid = (lo + hi) // 2
        data = _jpeg(frame, mid)
        if len(data) <= max_bytes:
            best, bq, lo = data, mid, mid + 1
        else:
            hi = mid - 1
    return best, bq


class TestPattern:
    BARS = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0),
            (255, 0, 255), (0, 0, 255), (255, 0, 0), (0, 0, 0)]              # BGR
    BOX, BOX_TOP, BOX_STEP, BOX_RANGE = 70, 100, 8, 400      # moving grey box: size, top, px/frame

    def __init__(self, out: Output) -> None:
        self.n, self.out = 0, out
        self.next_send = 0.0                            # set by the session; this source ignores it

    def age(self) -> float:
        return 0.0

    def reset(self) -> None:
        pass                                            # every picture is made on request

    def close(self) -> None:
        pass

    def still(self, w: int, h: int, max_bytes: int) -> bytes:
        import cv2
        import numpy as np
        pw, ph = VGA
        img = np.zeros((ph, pw, 3), np.uint8)
        bar = pw // len(self.BARS)
        for i, colour in enumerate(self.BARS):
            img[:, i * bar:(i + 1) * bar] = colour
        cv2.putText(img, "TEST PHOTO", (pw * 9 // 32, ph // 2), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 0), 2)
        return still_picture(img, w, h, self.out, max_bytes)

    def get(self) -> Optional[bytes]:
        import cv2
        import numpy as np
        fw, fh = DEFAULT_SIZE
        frame = np.zeros((fh, fw, 3), np.uint8)
        bar = fw // len(self.BARS)
        for i, colour in enumerate(self.BARS):
            frame[:, i * bar:(i + 1) * bar] = colour
        x = (self.n * self.BOX_STEP) % self.BOX_RANGE
        cv2.rectangle(frame, (x, self.BOX_TOP), (x + self.BOX, self.BOX_TOP + self.BOX), (128, 128, 128), -1)
        cv2.putText(frame, "WEBCAM TEST %d" % self.n, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
        self.n += 1
        img = cv2.resize(frame, (self.out.w, self.out.h), interpolation=cv2.INTER_AREA)
        return jpeg_within(orient(adjust(img, self.out), self.out), self.out.max_jpeg)


def still_picture(frame: Any, w: int, h: int, out: Output, max_bytes: int) -> bytes:
    import cv2
    fw = frame.shape[1]
    frame = crop_to_aspect(frame, w, h)
    small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA if w <= fw else cv2.INTER_CUBIC)
    return jpeg_within(orient(adjust(small, out), out), max_bytes, quality=QUALITY_PHOTO)


def encode_now(now: float, next_send: float, cam_dt: float, enc_s: float) -> bool:
    return now + cam_dt + enc_s > next_send


MAX_ENCODE_FAILURES = 30                 # camera frames in a row that cannot be encoded before the camera is given up


def smoothed_dt(cam_dt: float, gap: float) -> float:
    return cam_dt + GAP_SMOOTHING * (min(gap, GAP_CAP) - cam_dt)


class Webcam:
    def __init__(self, index: int, out: Output, size: Optional[tuple[int, int]] = None) -> None:
        import cv2
        self.out = out
        self.size = size or VGA
        self.cv2 = cv2
        self.cap = self._open(index)
        self._configure()
        w, h, fps = (self.cap.get(p) for p in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT, cv2.CAP_PROP_FPS))
        log.info("Camera %d opened: %dx%d at %.0f fps" % (index, w, h, fps),
                 event="camera_open", index=index, w=int(w), h=int(h), fps=round(fps, 1))
        if size and (int(w), int(h)) != size:
            log.warning("The camera does not offer %dx%d and gives %dx%d, so --camres changes nothing"
                        % (size + (int(w), int(h))), event="camres_ignored", asked_w=size[0], asked_h=size[1])
        self.q = QUALITY_ADAPTIVE_START # adaptive JPEG quality: keeps frames just under self.out.max_jpeg
        self.next_send = 0.0            # when the session will send next (it sets this); 0 = encode every frame
        # measured: seconds between camera frames, seconds per encode
        self.cam_dt, self.enc_s = 1 / CAMERA_FPS_ASKED, 0.01
        self.captured_at = time.monotonic()
        self.jpeg: Optional[bytes] = None
        self.generation = 0             # bumped by reset(): an encode that started before it is dropped
        self.raw: Any = None
        self.err: Optional[str] = None
        self.stop = False
        self.lock = threading.Lock()
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.failures = 0               # frames in a row that could not be encoded
        self.th.start()
        try:
            self._wait_for_first_picture()
        except RuntimeError:
            self.close()                # the camera stays free for the next try
            raise

    def _open(self, index: int) -> Any:
        cv2 = self.cv2
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(index)
        if not cap.isOpened():
            cap = cv2.VideoCapture(index)
        if not cap.isOpened():
            raise RuntimeError("cannot open webcam %d" % index)
        return cap

    def _configure(self) -> None:
        cv2 = self.cv2
        try:
            self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if self.size != VGA:                      # bigger than VGA: webcams only offer it as MJPG
                self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter.fourcc(*"MJPG"))
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
            self.cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS_ASKED)    # encode only what is used
        except Exception:
            pass

    def _wait_for_first_picture(self) -> None:
        t0 = time.monotonic()
        while self.jpeg is None and self.err is None and time.monotonic() - t0 < FIRST_PICTURE_TIMEOUT:
            time.sleep(0.01)
        if self.err:
            raise RuntimeError(self.err)

    def _encode(self, small: Any) -> bytes:
        data, self.q = fit_quality(small, self.out.max_jpeg, self.q)
        return data

    def _loop(self) -> None:
        last_read = time.monotonic()
        while not self.stop and self.err is None:
            ok, frame = self.cap.read()
            if not ok or frame is None:
                self.err = "webcam read failed"
                return
            # newest full frame, for photos: read() returns a new array each time and this assigns the reference
            # at once, so a photo (main thread) never sees a half-written frame
            self.raw = frame
            now = time.monotonic()
            self.cam_dt = smoothed_dt(self.cam_dt, now - last_read)
            last_read = now
            if encode_now(now, self.next_send, self.cam_dt, self.enc_s):
                self._encode_safely(frame, now)
            # else: a fresher frame will be ready in time for the next send

    def _encode_safely(self, frame: Any, now: float) -> None:
        try:
            self._encode_frame(frame, now)
            self.failures = 0
        except Exception as e:
            self.failures += 1
            if self.failures == 1:
                log.warning("A camera frame could not be encoded: %s" % e, event="frame_encode_failed", error=e)
            if self.failures >= MAX_ENCODE_FAILURES:
                self.err = "webcam frames cannot be encoded: %s" % e

    def _encode_frame(self, frame: Any, now: float) -> None:
        out = self.out
        generation = self.generation
        frame = crop_to_aspect(frame, out.w, out.h)                # to the game's aspect ratio, then resize
        small = self.cv2.resize(frame, (out.w, out.h), interpolation=self.cv2.INTER_AREA)
        data = self._encode(orient(adjust(small, out), out))
        self.enc_s += ENCODE_SMOOTHING * ((time.monotonic() - now) - self.enc_s)
        with self.lock:
            if generation == self.generation:
                self.jpeg, self.captured_at = data, now           # together: age() describes this picture

    def close(self) -> None:
        self.stop = True
        if self.th.is_alive() and self.th is not threading.current_thread():
            self.th.join(timeout=1.0)
        self.cap.release()

    def reset(self) -> None:
        with self.lock:
            self.jpeg = None
            self.generation += 1

    def still(self, w: int, h: int, max_bytes: int) -> bytes:
        if self.err:
            raise RuntimeError(self.err)
        frame = self.raw
        if frame is None:
            raise RuntimeError("no camera frame yet")
        return still_picture(frame, w, h, self.out, max_bytes)

    def age(self) -> float:
        return time.monotonic() - self.captured_at

    def get(self) -> Optional[bytes]:
        if self.err:
            raise RuntimeError(self.err)
        with self.lock:
            return self.jpeg


# ------------------------------------------------------------------ audio sources (int16 LE mono)

class ToneSource:
    def __init__(self, rate: int) -> None:
        self.rate, self.k = rate, 0

    def read(self, n: int) -> bytes:
        self.k %= self.rate                                         # 1000 Hz repeats every `rate` samples: stay exact
        out = bytearray()
        for _ in range(n):
            v = int(TONE_AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * self.k / self.rate))
            out += struct.pack("<h", v)
            self.k += 1
        return bytes(out)

    def close(self) -> None:
        pass


class MicChain:
    KNEE = LIMITER_KNEE * INT16_MAX

    def __init__(self, rate: int, gain: float = 1.0, band: bool = True) -> None:
        self.gain, self.band = gain, band
        self.r = math.exp(-2 * math.pi * DC_BLOCK_HZ / rate)
        w0 = 2 * math.pi * min(LOWPASS_HZ, LOWPASS_MAX_OF_RATE * rate) / rate
        alpha, cos = math.sin(w0) / (2 * BUTTERWORTH_Q), math.cos(w0)
        a0 = 1 + alpha
        self.b0 = (1 - cos) / 2 / a0
        self.b1 = (1 - cos) / a0
        self.a1, self.a2 = -2 * cos / a0, (1 - alpha) / a0
        self.x1 = self.y1 = self.u1 = self.u2 = self.z1 = self.z2 = 0.0

    def process(self, samples: Sequence[int]) -> list[int]:
        out: list[int] = []
        limit, gain = self.limit, self.gain
        if not self.band:
            return [limit(float(x) * gain) for x in samples]
        r, b0, b1, a1, a2 = self.r, self.b0, self.b1, self.a1, self.a2
        x1, y1, u1, u2, z1, z2 = self.x1, self.y1, self.u1, self.u2, self.z1, self.z2       # state in locals: faster
        for x in samples:
            v = float(x)
            y = v - x1 + r * y1
            x1, y1 = v, y
            z = b0 * (y + u2) + b1 * u1 - a1 * z1 - a2 * z2
            u2, u1, z2, z1 = u1, y, z1, z
            out.append(limit(z * gain))
        self.x1, self.y1, self.u1, self.u2, self.z1, self.z2 = x1, y1, u1, u2, z1, z2
        return out

    def limit(self, v: float) -> int:
        mag = abs(v)
        if mag != mag:
            return 0                                                    # NaN (gain inf on silence): no sound
        if mag > self.KNEE:
            room = INT16_MAX - self.KNEE
            mag = self.KNEE + room * math.tanh((mag - self.KNEE) / room)
        return int(round(math.copysign(mag, v)))


def merge_last_pair(samples: tuple[int, ...]) -> tuple[int, ...]:
    return samples[:-2] + ((samples[-2] + samples[-1]) // 2,)


def wasapi_settings(sd: Any, device: Union[int, str, None]) -> Any:
    if device is None:
        return None
    try:
        api = sd.query_hostapis(sd.query_devices(device)["hostapi"])["name"]
        return sd.WasapiSettings(auto_convert=True) if "WASAPI" in api else None
    except Exception:
        return None


class PcMicSource:
    HIGH_MS, LOW_MS = 100, 20
    DRAIN_START, DRAIN_STOP, DRAIN_EVERY = 40, 25, 10      # ms, ms, reads (one read is 1 ms of audio)
    draining, reads = False, 0
    carry = b""                                         # a trailing odd byte, completed by the next callback

    def __init__(self, rate: int, device: Union[int, str, None] = None, gain: float = 1.0) -> None:
        import collections

        import sounddevice as sd
        self.chain, self.rate = MicChain(rate, gain), rate
        self.buf: collections.deque[bytes] = collections.deque()
        self.have = 0                                   # bytes buffered
        self.lock = threading.Lock()
        extra = wasapi_settings(sd, device)
        more = {"extra_settings": extra} if extra is not None else {}
        self.st = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=0, latency="low",
                                    device=device, callback=self._on_audio, **more)
        try:
            self.st.start()
        except Exception:
            self.close()                                # do not keep the sound card's handle after a failed start
            raise

    def _on_audio(self, indata: Any, frames: int, t: Any, status: Any) -> None:
        data = self.carry + bytes(indata)               # a copy: PortAudio reuses its buffer after the callback
        odd = len(data) % 2
        self.carry, data = data[len(data) - odd:], data[:len(data) - odd]       # whole int16 samples only
        if not data:
            return
        with self.lock:
            self.buf.append(data)
            self.have += len(data)
            if self.buffered_ms() > self.HIGH_MS:
                self._trim(self.LOW_MS)

    def buffered_ms(self) -> float:
        return self.have * (1000 / BYTES_PER_SAMPLE) / self.rate

    def _trim(self, keep_ms: float) -> None:
        drop = self.have - int(keep_ms * self.rate / (1000 / BYTES_PER_SAMPLE)) // BYTES_PER_SAMPLE * BYTES_PER_SAMPLE
        while drop > 0 and self.buf:
            chunk = self.buf.popleft()
            if len(chunk) > drop:
                self.buf.appendleft(chunk[drop:])
                self.have -= drop
                return
            drop -= len(chunk)
            self.have -= len(chunk)

    def flush(self) -> None:
        with self.lock:
            self._trim(self.LOW_MS)

    def _drain_now(self) -> bool:
        ms = self.buffered_ms()
        if ms > self.DRAIN_START:
            self.draining = True
        elif ms <= self.DRAIN_STOP:
            self.draining = False
        if self.draining:
            self.reads += 1
        return self.draining and self.reads % self.DRAIN_EVERY == 0

    def read(self, n: int) -> bytes:
        with self.lock:
            drain = n > 1 and self._drain_now()
            out = self._take(2 * (n + drain))
        samples = struct.unpack("<%dh" % (n + drain), out)
        if drain:
            samples = merge_last_pair(samples)
        return struct.pack("<%dh" % n, *self.chain.process(samples))

    def _take(self, need: int) -> bytearray:
        out = bytearray()
        while len(out) < need and self.buf:
            chunk = self.buf.popleft()
            take = need - len(out)
            out += chunk[:take]
            if len(chunk) > take:
                self.buf.appendleft(chunk[take:])
            self.have -= min(len(chunk), take)
        if len(out) < need:
            out += b"\x00" * (need - len(out))          # underrun: silence
        return out

    def close(self) -> None:
        try:
            self.st.stop()
            self.st.close()
        except Exception:
            pass


def make_source(args: Settings, rate: int) -> Any:
    if args.mic == "pc":
        try:
            return PcMicSource(rate, args.micdev, args.micgain)
        except Exception as e:
            log.warning("The PC microphone cannot be used (%s); sending a 1 kHz test tone instead. "
                        "Run with --list to see the microphones, or --mic off" % (e,),
                        event="mic_source_unavailable", error=e)
    return ToneSource(rate)
