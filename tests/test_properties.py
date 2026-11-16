# Property tests (hypothesis) for the pure picture and sound functions: what must hold for every input.
import struct
import threading
from collections import deque

import cv2
import numpy as np
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from psychea import sources
from psychea.audio_packets import MAX_SAMPLES, MicPackets
from psychea.protocol import RES_CODES

FAST = settings(max_examples=200, deadline=None, database=None, suppress_health_check=list(HealthCheck))
RATES = st.sampled_from([11025, 22050, 44100, 48000])
SAMPLES = st.lists(st.integers(-32768, 32767), max_size=300)
SIZES = st.sampled_from(sorted(set(RES_CODES.values())))


def frame_of(h, w, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


# ---------------------------------------------------------------- crop / zoom

@FAST
@given(fh=st.integers(1, 120), fw=st.integers(1, 120), size=SIZES)
def test_crop_to_aspect_is_never_empty_and_stays_inside_the_frame(fh, fw, size):
    out = sources.crop_to_aspect(np.zeros((fh, fw, 3), np.uint8), *size)
    assert 1 <= out.shape[0] <= fh and 1 <= out.shape[1] <= fw and out.shape[2] == 3


@FAST
@given(fh=st.integers(2, 400), fw=st.integers(2, 400), size=SIZES)
def test_crop_to_aspect_keeps_the_aspect_exactly_and_touches_one_edge_pair(fh, fw, size):
    w, h = size
    ch, cw = sources.crop_to_aspect(np.zeros((fh, fw, 3), np.uint8), w, h).shape[:2]
    assert (ch, cw) == ((fw * h // w, fw) if fw * h <= fh * w else (fh, fh * w // h)) or min(ch, cw) == 1
    assert fh - ch < 2 or fw - cw < 2 or min(ch, cw) == 1                    # only one dimension is cut


@FAST
@given(fh=st.integers(1, 40), fw=st.integers(1, 40), zoom=st.integers(0, 255))
def test_adjust_zoom_keeps_shape_and_dtype_for_any_frame_and_zoom(fh, fw, zoom):
    out = sources.adjust(frame_of(fh, fw), sources.Output(zoom=zoom))
    assert out.shape == (fh, fw, 3) and out.dtype == np.uint8


# ---------------------------------------------------------------- colour

@FAST
@given(contrast=st.integers(0, 255), brightness=st.integers(0, 255), ev=st.integers(0, 255))
def test_the_tone_curve_is_a_256_entry_uint8_table_for_any_wire_value(contrast, brightness, ev):
    lut = sources._tone_lut(sources.Output(contrast=contrast, brightness=brightness, ev=ev))
    assert lut.shape == (256,) and lut.dtype == np.uint8


SMALL_OR_ANY = st.one_of(st.integers(0, 8), st.integers(0, 255))                 # codes in use, or any wire byte


@FAST
@given(effect=SMALL_OR_ANY, saturation=SMALL_OR_ANY, sharpness=SMALL_OR_ANY, wb=SMALL_OR_ANY,
       contrast=st.integers(0, 255), brightness=st.integers(0, 255),
       ev=st.one_of(st.integers(4, 16), st.integers(0, 255)), zoom=st.one_of(st.integers(10, 80), st.integers(0, 255)),
       mirror=st.integers(0, 1), flip=st.integers(0, 1))
def test_adjust_and_orient_keep_shape_and_dtype_for_any_setting_byte(effect, saturation, sharpness, wb, contrast,
                                                                    brightness, ev, zoom, mirror, flip):
    out = sources.Output(effect=effect, saturation=saturation, sharpness=sharpness, wb=wb, contrast=contrast,
                         brightness=brightness, ev=ev, zoom=zoom, mirror=mirror, flip=flip)
    img = sources.orient(sources.adjust(frame_of(12, 16, 1), out), out)
    assert img.shape == (12, 16, 3) and img.dtype == np.uint8


@FAST
@given(mirror=st.integers(0, 1), flip=st.integers(0, 1), extra=st.integers(0, 1))
def test_orient_twice_is_the_identity(mirror, flip, extra):
    out, img = sources.Output(mirror=mirror, flip=flip, mirror_extra=extra), frame_of(5, 7, 2)
    assert np.array_equal(sources.orient(sources.orient(img, out), out), img)


# ---------------------------------------------------------------- JPEG size control

NOISE = frame_of(48, 64, 3)
SMOOTH = np.tile(np.linspace(0, 255, 64, dtype=np.uint8)[None, :, None], (48, 1, 3))


@FAST
@given(budget=st.integers(-100, 20000), hint=st.integers(-50, 300),
       img=st.sampled_from([NOISE, SMOOTH, SMOOTH[:, :, 0]]))
def test_fit_quality_fits_the_budget_or_gives_the_smallest_possible(budget, hint, img):
    data, q = sources.fit_quality(img, budget, hint)
    assert 1 <= q <= 100 and data[:2] == b"\xff\xd8"
    assert len(data) <= budget or q == 1


@FAST
@given(budget=st.integers(-100, 20000), img=st.sampled_from([NOISE, SMOOTH, SMOOTH[:, :, 0]]),
       quality=st.integers(1, 100))
def test_jpeg_within_fits_or_stops_at_quality_10(budget, img, quality):
    data = sources.jpeg_within(img, budget, quality)
    floor = len(cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 10, cv2.IMWRITE_JPEG_PROGRESSIVE, 0])[1])
    assert data[:2] == b"\xff\xd8" and (len(data) <= budget or len(data) == floor or quality < 10)


@FAST
@given(fh=st.integers(1, 60), fw=st.integers(1, 60), size=SIZES, budget=st.integers(0, 30000))
def test_still_picture_has_the_asked_size_for_any_camera_frame(fh, fw, size, budget):
    w, h = size
    jpeg = sources.still_picture(frame_of(fh, fw), min(w, 160), min(h, 160), sources.Output(), budget)
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (min(h, 160), min(w, 160))


# ---------------------------------------------------------------- MicChain

@FAST
@given(rate=RATES, gain=st.floats(0.01, 1e6), samples=SAMPLES, band=st.booleans())
def test_the_chain_gives_one_int16_per_sample_for_any_gain(rate, gain, samples, band):
    out = sources.MicChain(rate, gain, band).process(samples)
    assert len(out) == len(samples) and all(isinstance(v, int) and -32767 <= v <= 32767 for v in out)


@FAST
@given(gain=st.sampled_from([float("nan"), float("inf"), -float("inf"), 1e308, -1.0, 0.0]), samples=SAMPLES)
def test_the_chain_survives_gains_that_are_not_sensible(gain, samples):
    out = sources.MicChain(22050, gain, band=False).process(samples)
    assert len(out) == len(samples) and all(-32767 <= v <= 32767 for v in out)


@FAST
@given(rate=RATES, samples=SAMPLES, cut=st.integers(0, 300))
def test_the_chain_keeps_its_state_between_calls(rate, samples, cut):
    cut = min(cut, len(samples))
    whole = sources.MicChain(rate, 3.0).process(samples)
    chain = sources.MicChain(rate, 3.0)
    assert chain.process(samples[:cut]) + chain.process(samples[cut:]) == whole


@FAST
@given(v=st.floats(-1e12, 1e12))
def test_the_limiter_is_monotonic_odd_and_bounded(v):
    chain = sources.MicChain(22050, band=False)
    assert abs(chain.limit(v)) <= 32767 and chain.limit(-v) == -chain.limit(v)
    assert chain.limit(v) <= chain.limit(v + 1.0)


# ---------------------------------------------------------------- PcMicSource buffer

class Mic(sources.PcMicSource):
    # PcMicSource without a sound card; deliver() plays the PortAudio callback.

    def __init__(self, rate=22050):
        self.chain, self.rate, self.have, self.lock = sources.MicChain(rate, 1.0, band=False), rate, 0, threading.Lock()
        self.buf = deque()

    def deliver(self, data):
        self._on_audio(data, len(data) // 2, 0, None)


OPS = st.lists(st.one_of(st.tuples(st.just("feed"), st.binary(max_size=900)),
                         st.tuples(st.just("read"), st.integers(0, 70)),
                         st.tuples(st.just("flush"), st.just(0))), max_size=60)


@FAST
@given(rate=st.sampled_from([11025, 22050]), ops=OPS)
def test_the_buffer_count_is_exact_never_negative_and_a_read_is_always_n_samples(rate, ops):
    mic = Mic(rate)
    for op, arg in ops:
        if op == "feed":
            mic.deliver(arg)
        elif op == "read":
            assert len(mic.read(arg)) == 2 * arg
        else:
            mic.flush()
        assert mic.have == sum(len(c) for c in mic.buf) >= 0
        assert mic.have % 2 == 0


@FAST
@given(rate=st.sampled_from([11025, 22050]), n=st.integers(1, 3000))
def test_one_huge_chunk_is_trimmed_to_the_low_mark_not_wiped(rate, n):
    mic = Mic(rate)
    mic.deliver(b"\x01\x00" * (4 * n))
    keep = int(mic.LOW_MS * rate / 500) // 2 * 2                    # whole samples
    assert mic.have == (8 * n if 8 * n * 500 / rate <= mic.HIGH_MS else keep)


@FAST
@given(samples=st.lists(st.integers(-20000, 20000), min_size=1, max_size=300), cuts=st.lists(st.integers(0, 600)))
def test_chunks_cut_at_odd_bytes_do_not_shift_the_sample_boundaries(samples, cuts):
    data = struct.pack("<%dh" % len(samples), *samples)
    points = sorted({0, len(data), *(min(c, len(data)) for c in cuts)})
    mic = Mic()
    for a, b in zip(points, points[1:]):
        mic.deliver(data[a:b])
    assert mic.have == len(data) and struct.unpack("<%dh" % len(samples), mic.read(len(samples))) == tuple(samples)


@FAST
@given(rate=st.sampled_from([11025, 22050, 44100, 48000]), extended=st.booleans(), packets=st.integers(1, 3000))
def test_mic_packets_never_exceed_126_bytes_and_keep_the_average_rate(rate, extended, packets):
    class Src:
        def read(self, n):
            return b"\x00\x00" * n
    p = MicPackets(Src(), rate, extended)
    sizes = [len(p.next()) - (2 if extended else 0) for _ in range(packets)]
    assert all(0 <= s <= 2 * MAX_SAMPLES for s in sizes) and 0 <= p.acc < 1000
    if rate <= 60000:
        assert abs(sum(sizes) // 2 - rate * packets / 1000) < 1.0


# ---------------------------------------------------------------- ToneSource

@FAST
@given(rate=RATES, start=st.integers(0, 10 ** 9), sizes=st.lists(st.integers(0, 80), max_size=8))
def test_the_tone_is_continuous_across_reads(rate, start, sizes):
    a, b = sources.ToneSource(rate), sources.ToneSource(rate)
    a.k = b.k = start
    pieces = b"".join(a.read(n) for n in sizes)
    assert pieces == b.read(sum(sizes)) and len(pieces) == 2 * sum(sizes)


def test_the_tone_phase_does_not_grow_without_end():
    t = sources.ToneSource(22050)
    t.k = 22050 * 3600 * 24 * 30
    first = t.read(5)
    assert t.k < 22050 * 2
    assert first == sources.ToneSource(22050).read(5)


def reference_process(chain, samples):
    # The straightforward per-sample filter (what MicChain.process computed before its loop was tightened).
    out = []
    for x in samples:
        v = float(x)
        if chain.band:
            y = v - chain.x1 + chain.r * chain.y1
            chain.x1, chain.y1 = v, y
            z = chain.b0 * (y + chain.u2) + chain.b1 * chain.u1 - chain.a1 * chain.z1 - chain.a2 * chain.z2
            chain.u2, chain.u1, chain.z2, chain.z1 = chain.u1, y, chain.z1, z
            v = z
        out.append(chain.limit(v * chain.gain))
    return out


@FAST
@given(rate=RATES, gain=st.floats(0.01, 50), samples=SAMPLES, band=st.booleans(), cut=st.integers(0, 300))
def test_the_chain_gives_bit_identical_samples_to_the_reference_filter(rate, gain, samples, band, cut):
    cut = min(cut, len(samples))
    fast, slow = sources.MicChain(rate, gain, band), sources.MicChain(rate, gain, band)
    assert fast.process(samples[:cut]) + fast.process(samples[cut:]) == reference_process(slow, samples)
    names = ("x1", "y1", "u1", "u2", "z1", "z2")
    assert [getattr(fast, n) for n in names] == [getattr(slow, n) for n in names]
