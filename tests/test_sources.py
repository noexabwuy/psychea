import collections
import struct
import threading
import time

import pytest

from psychea import sources
from psychea.settings import Settings


def tone(n):
    return struct.unpack("<%dh" % n, sources.ToneSource(22050).read(n))


def test_tone_is_a_1khz_sine_of_amplitude_8000():
    v = tone(2205)                                            # 0.1 s
    assert max(v) == pytest.approx(8000, abs=100)
    assert sum(1 for a, b in zip(v, v[1:]) if a < 0 <= b) in (99, 100)


class Mic(sources.PcMicSource):
    def __init__(self, gain, samples):
        self.chain, self.rate, self.lock = sources.MicChain(22050, gain, band=False), 22050, threading.Lock()
        self.have = 2 * len(samples)
        self.buf = collections.deque([struct.pack("<%dh" % len(samples), *samples)])


def test_gain_scales_and_stays_inside_int16():
    assert struct.unpack("<4h", Mic(2.0, [20000, -20000, 100, -100]).read(4))[2:] == (200, -200)
    assert all(abs(v) <= 32767 for v in struct.unpack("<4h", Mic(2.0, [20000, -20000, 100, -100]).read(4)))
    assert struct.unpack("<4h", Mic(0.5, [20000, -20000, 100, -100]).read(4)) == (10000, -10000, 50, -50)


def test_underrun_is_filled_with_silence():
    assert struct.unpack("<3h", Mic(1.0, [5]).read(3)) == (5, 0, 0)


def test_missing_sounddevice_falls_back_to_the_tone(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", None)        # "import sounddevice" fails
    assert isinstance(sources.make_source(Settings(mic="pc"), 22050), sources.ToneSource)


def test_an_unusable_microphone_is_reported_with_the_way_out(monkeypatch, caplog):
    monkeypatch.setitem(__import__("sys").modules, "sounddevice", None)
    sources.make_source(Settings(mic="pc"), 22050)
    warning = [r for r in caplog.records if getattr(r, "fields", {}).get("event") == "mic_source_unavailable"]
    assert len(warning) == 1 and warning[0].levelname == "WARNING"
    text = warning[0].getMessage()
    assert text.startswith("The PC microphone cannot be used (") and "1 kHz test tone" in text
    assert text.endswith("Run with --list to see the microphones, or --mic off")


def test_tone_setting_gives_the_tone():
    assert isinstance(sources.make_source(Settings(mic="tone"), 22050), sources.ToneSource)


def test_test_pattern_fits_the_jpeg_budget():
    out = sources.Output(max_jpeg=14884)
    assert len(sources.TestPattern(out).get()) <= 14884


def test_mirror_and_flip_change_the_picture():
    plain, mirrored = sources.Output(), sources.Output(mirror=1)
    assert sources.TestPattern(plain).get() != sources.TestPattern(mirrored).get()


def test_jpeg_within_lowers_quality_until_it_fits():
    import numpy as np
    noise = np.random.default_rng(1).integers(0, 255, (272, 480, 3), dtype=np.uint8)
    big, small = sources.jpeg_within(noise, 10 ** 9), sources.jpeg_within(noise, 20000)
    assert len(big) > 20000 >= len(small) or len(small) < len(big)


def test_orient_flips_the_requested_axes():
    import numpy as np
    img = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    assert (sources.orient(img, sources.Output()) == img).all()
    assert (sources.orient(img, sources.Output(mirror=1)) == img[:, ::-1]).all()
    assert (sources.orient(img, sources.Output(flip=1)) == img[::-1]).all()
    assert (sources.orient(img, sources.Output(mirror=1, flip=1)) == img[::-1, ::-1]).all()


def test_orient_extra_mirror_inverts_the_games_request():
    import numpy as np
    img = np.arange(2 * 3 * 3, dtype=np.uint8).reshape(2, 3, 3)
    assert (sources.orient(img, sources.Output(mirror_extra=1)) == img[:, ::-1]).all()
    assert (sources.orient(img, sources.Output(mirror=1, mirror_extra=1)) == img).all()
    assert (sources.orient(img, sources.Output(mirror_extra=1, flip=1)) == img[::-1, ::-1]).all()


class FakeMic(sources.PcMicSource):
    # A PcMicSource without a sound card: feed() plays the role of the PortAudio callback.

    def __init__(self, rate=22050):
        import collections
        self.chain, self.rate, self.have, self.lock = sources.MicChain(rate, 1.0, band=False), rate, 0, threading.Lock()
        self.buf = collections.deque()

    def feed(self, ms):
        self._on_audio(b"\x01\x00" * int(self.rate * ms / 1000), 0, 0, None)


def test_buffered_time_is_reported_in_milliseconds():
    mic = FakeMic()
    mic.feed(50)
    assert mic.buffered_ms() == pytest.approx(50, abs=0.1)
    mic.read(22050 // 100)                                  # 10 ms
    assert mic.buffered_ms() == pytest.approx(40, abs=0.1)


def test_a_pile_up_beyond_the_hard_limit_is_trimmed_to_20_ms():
    mic = FakeMic()
    for _ in range(mic.HIGH_MS // 10):
        mic.feed(10)
    assert mic.buffered_ms() == pytest.approx(mic.HIGH_MS, abs=0.5)
    mic.feed(10)
    assert mic.buffered_ms() <= mic.LOW_MS


def run_ms(mic, ms):
    # Capture and USB read at the same pace: 1 ms in, 1 ms out. Returns the samples taken from the buffer.
    taken = 0
    for _ in range(ms):
        mic.feed(1)
        before = mic.have
        mic.read(22)
        taken += (before - mic.have) // 2
    return taken


def test_a_moderate_backlog_drains_slowly_without_cutting_audio():
    mic = FakeMic()
    mic.feed(50)                                            # above DRAIN_START, below the hard limit
    run_ms(mic, 10_000)
    assert mic.LOW_MS < mic.buffered_ms() <= mic.DRAIN_STOP + 2       # got shorter, was not wiped out


def test_draining_changes_the_playback_speed_by_less_than_one_percent():
    mic = FakeMic()
    mic.feed(50)
    taken = run_ms(mic, 2000)
    assert taken <= 1.01 * 22 * 2000 + 44


def test_no_draining_while_the_buffer_stays_in_the_normal_range():
    mic = FakeMic()
    mic.feed(mic.DRAIN_START - 5)
    start = mic.buffered_ms()
    run_ms(mic, 3000)
    assert mic.buffered_ms() == pytest.approx(start, abs=1.0)


def test_draining_stops_at_the_lower_threshold_and_does_not_restart_in_between():
    mic = FakeMic()
    mic.feed(mic.DRAIN_START + 5)
    run_ms(mic, 10_000)
    assert not mic.draining
    level = mic.buffered_ms()
    run_ms(mic, 3000)
    assert mic.buffered_ms() == pytest.approx(level, abs=1.0)


def test_two_neighbouring_samples_are_merged_into_their_mean():
    assert sources.merge_last_pair((1, 2, 3, 10, 20)) == (1, 2, 3, 15)
    assert sources.merge_last_pair((-5, 7)) == (1,)


def test_flush_keeps_only_the_newest_audio():
    mic = FakeMic()
    mic.feed(50)
    mic.flush()
    assert mic.buffered_ms() <= mic.LOW_MS


def test_encode_only_the_last_frame_that_can_still_be_ready_for_the_next_send():
    # camera frames every 33 ms, an encode takes 10 ms, the PSP is sent a frame at t = 1.000
    assert sources.encode_now(0.900, 1.000, 0.033, 0.010) is False      # the next camera frame (0.933) is early enough
    assert sources.encode_now(0.960, 1.000, 0.033, 0.010) is True       # the next one (0.993) would be too late
    assert sources.encode_now(2.000, 1.000, 0.033, 0.010) is True       # we are behind: take every frame


def test_the_webcam_encodes_on_the_senders_schedule(monkeypatch):
    import cv2

    from tests.test_webcam import FakeCapture
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    cam = sources.Webcam(0, sources.Output())
    cam.next_send = time.monotonic() + 10.0                      # nothing is due for a long time
    time.sleep(0.05)                                        # let an encode that was already running finish
    first = cam.get()
    time.sleep(0.1)
    assert cam.get() is first and cam.age() >= 0.0
    cam.next_send = 0.0                                     # the sender is waiting: encode again
    time.sleep(0.1)
    assert cam.get() is not first
    cam.close()


# ---- picture settings the camera app sends (zoom, effect, contrast, brightness, saturation)
import numpy as np  # noqa: E402

from psychea.sources import Output as _Out  # noqa: E402
from psychea.sources import adjust  # noqa: E402


def _img():
    img = np.zeros((40, 60, 3), np.uint8)
    img[:, :30] = (200, 50, 50)           # blue-ish left half (BGR)
    img[:, 30:] = (40, 120, 220)          # orange-ish right half
    return img


def test_neutral_settings_leave_the_picture_untouched():
    img = _img()
    assert np.array_equal(adjust(img.copy(), _Out()), img)


def test_black_and_white_effect_makes_all_channels_equal():
    out = adjust(_img(), _Out(effect=2))
    assert np.array_equal(out[..., 0], out[..., 1]) and np.array_equal(out[..., 1], out[..., 2])


def test_negative_effect_inverts():
    img = _img()
    assert np.array_equal(adjust(img.copy(), _Out(effect=1)), 255 - img)


def test_zoom_keeps_the_centre_and_the_size():
    img = _img()
    out = adjust(img.copy(), _Out(zoom=20))           # 2.0x
    assert out.shape == img.shape
    assert np.all(out[:, :28] == img[0, 0]) and np.all(out[:, 32:] == img[0, -1])   # centre line between halves stays


def test_contrast_above_neutral_spreads_values_and_below_flattens_them():
    img = _img()
    hi, lo = adjust(img.copy(), _Out(contrast=192)), adjust(img.copy(), _Out(contrast=64))
    assert int(hi.max()) - int(hi.min()) > int(img.max()) - int(img.min()) > int(lo.max()) - int(lo.min())


def test_brightness_shifts_values():
    img = _img()
    assert adjust(img.copy(), _Out(brightness=168)).mean() > img.mean() > adjust(img.copy(), _Out(brightness=88)).mean()


def test_zero_saturation_gives_grey():
    out = adjust(_img(), _Out(saturation=0))
    assert np.abs(out[..., 0].astype(int) - out[..., 2].astype(int)).max() <= 1


def test_exposure_compensation_brightens_and_darkens_in_quarter_stops():
    img = _img()
    assert adjust(img.copy(), _Out(ev=4)).mean() > img.mean() > adjust(img.copy(), _Out(ev=16)).mean()
    assert np.array_equal(adjust(img.copy(), _Out(ev=10)), img)


def test_adjust_white_balance_incandescent_cools_image():
    import numpy as np

    from psychea.sources import Output, adjust
    frame = np.full((4, 4, 3), 100, np.uint8)
    out = adjust(frame, Output(wb=3))
    assert out[0, 0, 0] > 100 > out[0, 0, 2]
    assert (adjust(frame, Output(wb=1)) == frame).all()


def _px(value):
    return np.full((2, 2, 3), value, np.uint8)


def test_contrast_pivots_around_mid_grey():
    assert adjust(_px(128), _Out(contrast=192))[0, 0, 0] == 128
    assert adjust(_px(200), _Out(contrast=192))[0, 0, 0] > 200
    assert adjust(_px(56), _Out(contrast=192))[0, 0, 0] < 56


def test_brightness_adds_the_offset():
    assert adjust(_px(100), _Out(brightness=138))[0, 0, 0] == 110


def test_exposure_plus_one_stop_doubles_linear_light():
    expected = 255 * (2 * (128 / 255) ** 2.2) ** (1 / 2.2)
    assert abs(int(adjust(_px(128), _Out(ev=6))[0, 0, 0]) - expected) <= 1


def test_zero_saturation_uses_rec709_luma():
    red = np.zeros((2, 2, 3), np.uint8)
    red[..., 2] = 255
    out = adjust(red, _Out(saturation=0))
    assert abs(int(out[0, 0, 0]) - round(0.2126 * 255)) <= 2


def test_white_balance_gains_follow_the_blackbody_colour():
    from psychea.sources import wb_gains
    b, g, r = wb_gains(2800)
    assert g == 1.0 and 1.6 < b < 1.9 and 0.6 < r < 0.75
    assert all(abs(x - 1) < 0.05 for x in wb_gains(6500))


def test_sharpness_levels_add_edge_contrast_and_zero_changes_nothing():
    img = _img()
    assert np.array_equal(adjust(img.copy(), _Out(sharpness=0)), img)
    soft = adjust(img.copy(), _Out(sharpness=1))
    hard = adjust(img.copy(), _Out(sharpness=3))
    edge = lambda im: int(abs(im[20, 31].astype(int) - im[20, 28].astype(int)).sum())  # noqa: E731
    assert edge(img) < edge(soft) < edge(hard)


def sine(freq, rate, n, amp=8000):
    import math
    return [int(amp * math.sin(2 * math.pi * freq * k / rate)) for k in range(n)]


def peak_after(chain, samples, skip):
    return max(abs(v) for v in chain.process(samples)[skip:])


def test_dc_offset_is_removed():
    out = sources.MicChain(22050).process([5000] * 4000)
    assert abs(out[-1]) < 50


def test_speech_band_passes_and_rumble_and_hiss_do_not():
    rate = 44100
    assert peak_after(sources.MicChain(rate), sine(1000, rate, 4000), 2000) > 7600
    assert peak_after(sources.MicChain(rate), sine(30, rate, 8000), 4000) < 3000        # below 100 Hz
    assert peak_after(sources.MicChain(rate), sine(18000, rate, 4000), 2000) < 1500     # above 10 kHz


def test_band_limit_stays_below_nyquist_at_low_rates():
    assert peak_after(sources.MicChain(11025), sine(1000, 11025, 4000), 2000) > 7600


def test_filter_can_be_switched_off():
    assert sources.MicChain(22050, band=False).process([5000] * 10) == [5000] * 10


def test_gain_is_linear_below_the_knee_and_soft_limited_above_it():
    chain = sources.MicChain(22050, gain=2.0, band=False)
    assert chain.process([100, -100]) == [200, -200]
    loud = chain.process([20000, -20000, 32767, 60000])
    assert all(-32767 <= v <= 32767 for v in loud)
    assert loud[0] > 22900 and loud[1] < -22900                  # compressed, but not flattened to the limit
    assert loud[2] < loud[3] <= 32767                            # monotonic: louder in -> louder out


def test_a_reset_webcam_has_no_picture_until_a_new_one_is_encoded(monkeypatch):
    import cv2

    from tests.test_webcam import FakeCapture
    monkeypatch.setattr(cv2, "VideoCapture", FakeCapture)
    cam = sources.Webcam(0, sources.Output())
    cam.next_send = time.monotonic() + 10.0                      # no new encode on its own
    time.sleep(0.05)
    assert cam.get() is not None
    cam.reset()
    assert cam.get() is None
    cam.next_send = 0.0
    time.sleep(0.1)
    assert cam.get() is not None
    cam.close()


# ---- crop, photo and effect helpers
def test_crop_to_aspect_centres_a_wide_frame_horizontally():
    frame = np.zeros((40, 100, 3), np.uint8)
    frame[:, 30:70] = 9
    out = sources.crop_to_aspect(frame, 1, 1)
    assert out.shape == (40, 40, 3) and np.all(out == 9)


def test_crop_to_aspect_centres_a_tall_frame_vertically():
    frame = np.zeros((100, 40, 3), np.uint8)
    frame[30:70] = 9
    out = sources.crop_to_aspect(frame, 1, 1)
    assert out.shape == (40, 40, 3) and np.all(out == 9)


def test_crop_to_aspect_keeps_a_frame_that_already_fits():
    assert sources.crop_to_aspect(np.zeros((272, 480, 3), np.uint8), 480, 272).shape == (272, 480, 3)


def test_still_picture_is_cropped_to_the_requested_size_and_fits_the_budget():
    import cv2
    frame = np.zeros((480, 640, 3), np.uint8)
    frame[:, :320] = (0, 0, 255)
    jpeg = sources.still_picture(frame, 100, 100, sources.Output(), 20000)
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (100, 100) and len(jpeg) <= 20000
    assert img[50, 5, 2] > 150 > img[50, 95, 2]                                # red on the left only


def test_the_tint_effects_colour_a_grey_picture_in_their_channel():
    grey = np.full((4, 4, 3), 128, np.uint8)
    for effect, channel in ((4, 0), (5, 2), (6, 1)):
        out = adjust(grey.copy(), _Out(effect=effect))
        assert out[0, 0, channel] == 128 and out[0, 0, channel] > out[0, 0, (channel + 1) % 3]


def test_the_sepia_effect_makes_a_warm_picture():
    out = adjust(np.full((4, 4, 3), 128, np.uint8), _Out(effect=3))
    assert out[0, 0, 2] > out[0, 0, 1] > out[0, 0, 0]


def test_an_unknown_effect_changes_nothing():
    img = _img()
    assert np.array_equal(adjust(img.copy(), _Out(effect=99)), img)


def _noisy(seed=3):
    import numpy as np
    base = np.linspace(0, 255, 480, dtype=np.float32)[None, :, None].repeat(272, 0).repeat(3, 2)
    return np.clip(base + np.random.default_rng(seed).normal(0, 12, base.shape), 0, 255).astype(np.uint8)


def _size_at(frame, q):
    import cv2
    return len(cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, q])[1])


def test_fit_quality_finds_the_highest_quality_that_fits():
    frame = _noisy()
    budget = _size_at(frame, 40) + 10
    for hint in (1, 15, 40, 90):                          # whatever the starting guess
        data, q = sources.fit_quality(frame, budget, hint)
        assert len(data) <= budget and q == 40
        assert _size_at(frame, q + 1) > budget


def test_fit_quality_gives_the_smallest_picture_when_nothing_fits():
    data, q = sources.fit_quality(_noisy(), 100, 50)
    assert q == 1 and len(data) == _size_at(_noisy(), 1)


def test_fit_quality_uses_few_encodes_near_the_hint(monkeypatch):
    import cv2
    frame, calls = _noisy(), []
    real = cv2.imencode
    monkeypatch.setattr(cv2, "imencode", lambda *a, **k: calls.append(1) or real(*a, **k))
    sources.fit_quality(frame, _size_at(frame, 40) + 10, 38)
    assert len(calls) <= 7


def test_crop_to_aspect_uses_exact_integer_arithmetic():
    # Float division cut a pixel too many when the exact crop height is a whole number (27 x 33 is 11:9).
    assert sources.crop_to_aspect(np.zeros((30, 33, 3), np.uint8), 176, 144).shape == (27, 33, 3)
    assert sources.crop_to_aspect(np.zeros((480, 640, 3), np.uint8), 480, 272).shape == (362, 640, 3)    # the camera
    assert sources.crop_to_aspect(np.zeros((720, 1280, 3), np.uint8), 640, 480).shape == (720, 960, 3)


def test_crop_to_aspect_of_a_one_pixel_frame_is_not_empty():
    assert sources.crop_to_aspect(np.zeros((1, 1, 3), np.uint8), 480, 272).shape == (1, 1, 3)
    assert sources.crop_to_aspect(np.zeros((1, 9, 3), np.uint8), 272, 480).shape == (1, 1, 3)


@pytest.mark.parametrize("hint", [-5, 0, 101, 300])
def test_fit_quality_returns_a_quality_from_1_to_100_whatever_the_hint(hint):
    data, q = sources.fit_quality(_noisy(), 10 ** 9, hint)
    assert q == 100 and data[:2] == b"\xff\xd8"


def test_a_stalled_callback_that_delivers_one_big_chunk_keeps_the_newest_20_ms():
    # Trimming dropped whole chunks: one 150 ms chunk after a stall left nothing (a 20 ms hole in the sound).
    mic = FakeMic(11025)
    mic._on_audio(b"".join(struct.pack("<h", i) for i in range(1654)), 0, 0, None)         # 150 ms, 3308 bytes
    assert mic.have == 440 and mic.buffered_ms() == pytest.approx(19.95, abs=0.1)
    assert struct.unpack("<220h", mic.read(220)) == tuple(range(1654 - 220, 1654))            # the newest samples


def test_a_chunk_with_an_odd_number_of_bytes_keeps_the_samples_aligned():
    mic = FakeMic()
    data = struct.pack("<6h", 1, 2, 3, 4, 5, 6)
    for a, b in ((0, 3), (3, 4), (4, 4), (4, 9), (9, 12)):                # odd, odd, empty, odd, odd cuts
        mic._on_audio(data[a:b], 0, 0, None)
    assert mic.have == 12 and struct.unpack("<6h", mic.read(6)) == (1, 2, 3, 4, 5, 6)


def test_a_dangling_odd_byte_waits_for_its_other_half():
    mic = FakeMic()
    mic._on_audio(b"\x07", 0, 0, None)
    assert mic.have == 0 and mic.carry == b"\x07"
    mic._on_audio(b"\x00", 0, 0, None)
    assert mic.have == 2 and mic.carry == b"" and struct.unpack("<h", mic.read(1)) == (7,)


# ---- test pattern and photo, jpeg size control, buffer details (found by mutation testing)
def _decode(jpeg):
    import cv2
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


BAR_COLOURS = [(255, 255, 255), (0, 255, 255), (255, 255, 0), (0, 255, 0), (255, 0, 255), (0, 0, 255), (255, 0, 0),
               (0, 0, 0)]                                                      # BGR, left to right


def _close(pixel, colour, tolerance=12):
    return all(abs(int(a) - b) <= tolerance for a, b in zip(pixel, colour))


def test_the_test_pattern_has_eight_colour_bars_a_moving_box_and_a_counter():
    pattern = sources.TestPattern(sources.Output())
    first, second = _decode(pattern.get()), _decode(pattern.get())
    assert first.shape == (272, 480, 3)
    assert all(_close(first[250, i * 60 + 30], c) for i, c in enumerate(BAR_COLOURS))
    assert _close(first[135, 35], (128,) * 3) and not _close(first[135, 300], (128,) * 3)               # box, a bar
    assert _close(second[135, 75], (128,) * 3) and not _close(first[135, 75], (128,) * 3)               # box moved 8 px
    assert int((first[10:30, 10:170].max(axis=2) < 70).sum()) > 100                                     # the lettering
    assert int((first[10:30, 100:150] != second[10:30, 100:150]).sum()) > 100                          # counter changed
    assert pattern.n == 2


def test_the_test_pattern_follows_the_requested_size():
    jpeg = sources.TestPattern(sources.Output(w=320, h=240, max_jpeg=9000)).get()
    assert _decode(jpeg).shape == (240, 320, 3) and len(jpeg) <= 9000


def test_the_test_photo_has_the_bars_and_the_lettering_at_the_requested_size():
    photo = _decode(sources.TestPattern(sources.Output()).still(640, 480, 60000))
    assert photo.shape == (480, 640, 3)
    assert all(_close(photo[400, i * 80 + 40], c) for i, c in enumerate(BAR_COLOURS))
    assert int((photo[220:260, 180:460].max(axis=2) < 70).sum()) > 500                                  # TEST PHOTO
    small = _decode(sources.TestPattern(sources.Output()).still(160, 120, 60000))
    assert small.shape == (120, 160, 3)


def test_jpeg_within_stops_at_exactly_the_budget_and_walks_down_in_steps_of_ten(monkeypatch):
    import cv2
    img = np.random.default_rng(4).integers(0, 255, (60, 80, 3), dtype=np.uint8)
    exact = len(sources.jpeg_within(img, 10 ** 9, 85))
    assert len(sources.jpeg_within(img, exact, 85)) == exact                      # equal to the budget is still a fit
    qualities, real = [], cv2.imencode
    monkeypatch.setattr(cv2, "imencode", lambda ext, im, params: qualities.append(params[1]) or real(ext, im, params))
    sources.jpeg_within(img, 1, 92)
    assert qualities == [92, 82, 72, 62, 52, 42, 32, 22, 12, 10]               # never below 10, then it gives up


def test_still_picture_encodes_at_quality_92_and_picks_the_resize_by_the_width_asked(monkeypatch):
    import cv2
    asked, real = [], cv2.resize
    monkeypatch.setattr(cv2, "resize", lambda im, size, interpolation=None: (
        asked.append(interpolation), real(im, size, interpolation=interpolation))[1])
    qualities, real_encode = [], cv2.imencode
    monkeypatch.setattr(cv2, "imencode", lambda ext, im, params: (
        qualities.append(params[1]), real_encode(ext, im, params))[1])
    frame = np.zeros((480, 640, 3), np.uint8)
    for w, h, expected in ((480, 272, cv2.INTER_AREA), (640, 480, cv2.INTER_AREA), (1280, 960, cv2.INTER_CUBIC)):
        asked.clear()
        qualities.clear()
        assert sources.still_picture(frame, w, h, sources.Output(), 10 ** 9)[:2] == b"\xff\xd8"
        assert asked == [expected] and qualities == [92], (w, h)


def test_encode_now_is_false_when_the_next_camera_frame_lands_exactly_on_the_send_time():
    assert sources.encode_now(0.5, 1.0, 0.25, 0.25) is False and sources.encode_now(0.5, 1.0, 0.25, 0.26) is True


@pytest.mark.parametrize("q", [1, 2, 20, 33, 35, 40, 41, 46, 47, 88, 99, 100])
@pytest.mark.parametrize("hint", [1, 40, 95])
def test_fit_quality_is_the_highest_quality_whose_size_equals_or_is_below_the_budget(q, hint):
    frame = _noisy()
    budget = _size_at(frame, q)                                                  # exactly the size at quality q
    data, found = sources.fit_quality(frame, budget, hint)
    assert len(data) <= budget and found >= q and (found == 100 or _size_at(frame, found + 1) > budget)


def test_merge_last_pair_gives_whole_samples():
    assert all(type(v) is int for v in sources.merge_last_pair((3, 4, 5, 6)))
    assert sources.merge_last_pair((3, 4, 5, 6)) == (3, 4, 5)


def test_the_tone_is_a_1khz_sine_of_amplitude_8000_at_any_rate():
    import math
    for rate in (11025, 22050, 44100):
        expected = tuple(int(8000 * math.sin(2 * math.pi * k / (rate / 1000.0))) for k in range(30))
        assert tone_at(rate, 30) == expected, rate


def tone_at(rate, n):
    return struct.unpack("<%dh" % n, sources.ToneSource(rate).read(n))


# a mic with a tiny sample rate, so that the millisecond limits are whole numbers of samples
def small_mic(samples, rate=2000):
    mic = FakeMic(rate)
    mic.chain.gain = 1.0
    mic._on_audio(struct.pack("<%dh" % samples, *range(1, samples + 1)), 0, 0, None)
    return mic


def test_a_buffer_of_exactly_the_hard_limit_is_kept_and_one_sample_more_is_trimmed():
    assert small_mic(200).have == 400                                  # 100 ms at 2000 Hz: not above HIGH_MS
    mic = small_mic(201)
    assert mic.have == 80 and mic.buffered_ms() == pytest.approx(20)   # trimmed to LOW_MS = 20 ms = 40 samples


def test_trimming_across_several_chunks_drops_whole_chunks_and_cuts_the_next():
    mic = FakeMic(2000)
    for first in (1, 11, 21, 31):                                      # four chunks of 10 samples
        mic.buf.append(struct.pack("<10h", *range(first, first + 10)))
    mic.have = 80
    mic._trim(15)                                                      # keep 15 ms = 30 samples = 60 bytes
    assert mic.have == 60 and sum(len(c) for c in mic.buf) == 60
    assert struct.unpack("<30h", b"".join(mic.buf)) == tuple(range(11, 41))
    mic._trim(15)
    assert mic.have == 60                                              # nothing left to drop
    mic._trim(0)
    assert mic.have == 0 and not mic.buf


def test_the_drain_has_hysteresis_with_exact_thresholds():
    mic = FakeMic(2000)                                                # 1 sample = 0.5 ms
    for samples, expect in ((80, False), (81, True), (60, True), (51, True), (50, False), (60, False), (81, True)):
        mic.have = 2 * samples
        mic._drain_now()
        assert mic.draining is expect, samples


def test_every_tenth_read_in_the_drain_takes_one_extra_sample():
    mic = FakeMic(2000)
    mic.have = 2 * 90                                                  # 45 ms: draining
    mic.buf.append(struct.pack("<2000h", *([1] * 2000)))
    mic.have = 4000
    taken = []
    for _ in range(40):
        before = mic.have
        mic.read(5)
        taken.append((before - mic.have) // 2)
        mic.buf.appendleft(struct.pack("<%dh" % (taken[-1]), *([1] * taken[-1])))          # keep the level constant
        mic.have += 2 * taken[-1]
    assert [i + 1 for i, n in enumerate(taken) if n == 6] == [10, 20, 30, 40] and set(taken) == {5, 6}


def test_a_single_sample_read_never_drains_and_a_two_sample_read_may():
    mic = FakeMic(2000)
    mic.draining, mic.reads = True, 9
    mic.buf.append(struct.pack("<100h", *range(100)))
    mic.have = 200                                                     # 50 ms: above the drain thresholds
    mic.read(1)
    assert mic.have == 198                                             # read 10 would drain, but one sample cannot
    mic.reads = 9
    mic.read(2)
    assert mic.have == 198 - 6                                         # 2 samples + 1 extra


def test_closing_the_tone_changes_nothing():
    tone = sources.ToneSource(22050)
    tone.close()
    assert len(tone.read(22)) == 44


def test_the_callback_thread_and_the_reader_never_corrupt_the_buffer():
    # The PortAudio callback appends and trims while the USB thread takes 1 ms reads: the byte count stays
    # exact, every read is 22 whole samples and no sample is a torn mix of two bytes (the signal counts up to
    # 19999).
    mic = FakeMic()
    mic.chain.gain = 1.0
    stop, errors = threading.Event(), []

    def callback():
        n = 0
        try:
            while not stop.is_set():
                mic._on_audio(struct.pack("<10h", *((n + i) % 20000 for i in range(10))), 10, 0, None)
                n += 10
        except Exception as e:                                                      # pragma: no cover
            errors.append(e)

    thread = threading.Thread(target=callback)
    thread.start()
    try:
        last, reads = None, 0
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            samples = struct.unpack("<22h", mic.read(22))
            reads += 1
            assert all(0 <= s < 20000 for s in samples)
            with mic.lock:
                assert mic.have == sum(len(c) for c in mic.buf) >= 0 and mic.have % 2 == 0
            last = samples
    finally:
        stop.set()
        thread.join()
    assert not errors and reads > 10 and last is not None
