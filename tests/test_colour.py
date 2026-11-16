# Colour maths pinned to published values and hand-computed pixels (found by mutation testing).
import numpy as np
import pytest

from psychea import sources
from psychea.sources import Output


def px(b, g, r):
    return np.full((6, 6, 3), (b, g, r), np.uint8)


def centre(img):
    return tuple(int(v) for v in img[3, 3])


# Tanner Helland's black-body fit; 6600 K is the white point, 40000 K = 151, 185, 255 in the reference.
@pytest.mark.parametrize("kelvin, rgb", [
    (1000, (255.0, 67.92, 1.0)), (1900, (255.0, 131.77, 1.0)), (2000, (255.0, 136.87, 13.9)),
    (2800, (255.0, 170.34, 95.32)), (4000, (255.0, 205.82, 166.08)), (6500, (255.0, 254.11, 250.04)),
    (6600, (255.0, 255.0, 255.0)), (6700, (254.42, 248.75, 255.0)), (8000, (221.21, 229.79, 255.0)),
    (40000, (151.67, 185.53, 255.0))])
def test_black_body_colour(kelvin, rgb):
    assert sources._kelvin_rgb(kelvin) == pytest.approx(rgb, abs=0.01)


def test_white_balance_gains_neutralise_the_light_and_leave_daylight_alone():
    assert sources.wb_gains(6500) == pytest.approx((1.0, 1.0, 1.0))
    assert sources.wb_gains(2800) == pytest.approx((1.7583, 1.0, 0.6703), abs=1e-4)      # BGR: warm light -> more blue
    assert sources.wb_gains(4000) == pytest.approx((1.2194, 1.0, 0.8099), abs=1e-4)


def test_white_balance_scales_the_channels_and_clips():
    out = sources._white_balance(px(100, 100, 100), (2.0, 1.0, 0.5))
    assert centre(out) == (200, 100, 50) and out.dtype == np.uint8
    assert centre(sources._white_balance(px(200, 100, 100), (2.0, 1.0, 0.5))) == (255, 100, 50)


@pytest.mark.parametrize("kw, expected", [
    ({}, {0: 0, 64: 64, 128: 128, 192: 192, 255: 255}),
    ({"ev": 6}, {0: 0, 64: 88, 128: 175, 255: 255}), ({"ev": 8}, {64: 75, 128: 150}),
    ({"ev": 12}, {64: 55, 128: 109, 255: 218}), ({"ev": 14}, {64: 47, 128: 93, 255: 186}),
    ({"contrast": 0}, {0: 128, 64: 128, 255: 128}), ({"contrast": 64}, {0: 85, 64: 107, 128: 128, 192: 149, 255: 170}),
    ({"contrast": 192}, {0: 0, 64: 0, 128: 128, 192: 255}), ({"contrast": 255}, {64: 0, 128: 128, 192: 255}),
    ({"brightness": 0}, {0: 0, 64: 0, 255: 127}), ({"brightness": 100}, {64: 36, 128: 100, 255: 227}),
    ({"brightness": 160}, {0: 32, 64: 96, 128: 160, 255: 255}), ({"brightness": 255}, {0: 127, 64: 191, 128: 255})])
def test_tone_curve_values(kw, expected):
    lut = sources._tone_lut(Output(**kw))
    assert {i: int(lut[i]) for i in expected} == expected and lut.dtype == np.uint8 and len(lut) == 256


def test_lower_exposure_codes_are_brighter():
    mid = [int(sources._tone_lut(Output(ev=ev))[100]) for ev in range(4, 17)]
    assert mid == sorted(mid, reverse=True) and len(set(mid)) == len(mid)


def test_zoom_keeps_the_size_and_enlarges_the_centre():
    img = np.zeros((40, 80, 3), np.uint8)
    img[15:25, 35:45] = 200                                       # a patch in the middle
    for zoom in (20, 40, 80, 200):                                # 200 is limited to 8x
        out = sources._zoom(img, zoom)
        assert out.shape == img.shape
    assert int(sources._zoom(img, 20)[20, 40, 0]) == 200 and int(sources._zoom(img, 20)[0, 0, 0]) == 0
    big = sources._zoom(img, 20)
    assert (big[:, :, 0] == 200).sum() > (img[:, :, 0] == 200).sum() * 3        # 2x per side: about 4x the area
    assert (sources._zoom(img, 80)[:, :, 0] == 200).sum() >= (sources._zoom(img, 20)[:, :, 0] == 200).sum()


def test_zoom_crops_at_least_two_pixels():
    img = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)
    assert sources._zoom(img, 80).shape == img.shape


def test_saturation_blends_with_the_luma_grey():
    red = px(0, 0, 255)
    grey = int(round(0.2126 * 255))
    assert centre(sources._saturate(red, 0)) == (grey, grey, grey)
    assert centre(sources._saturate(px(0, 0, 128), 6)) == (0, 0, 229)         # k = 2: pushed away from grey, clipped
    assert centre(sources._saturate(px(100, 100, 100), 6)) == (100, 100, 100)  # grey stays grey at any saturation
    assert centre(sources._saturate(red, 3)) == (0, 0, 255)


def test_sharpening_overshoots_at_edges_and_leaves_flat_areas_alone():
    img = np.full((9, 18, 3), 100, np.uint8)
    img[:, 9:] = 200
    flat = sources._sharpen(img, 2)
    assert int(flat[4, 0, 0]) == 100 and int(flat[4, 17, 0]) == 200
    assert int(flat[4, 8, 0]) < 100 and int(flat[4, 9, 0]) > 200               # undershoot / overshoot at the step
    mild, strong = sources._sharpen(img, 1), sources._sharpen(img, 3)
    assert int(mild[4, 9, 0]) < int(flat[4, 9, 0]) < int(strong[4, 9, 0])


@pytest.mark.parametrize("effect, expected", [
    (0, (10, 20, 30)), (1, (245, 235, 225)), (7, (10, 20, 30)), (99, (10, 20, 30))])
def test_simple_effects(effect, expected):
    assert centre(sources._effect(px(10, 20, 30), effect)) == expected


def test_grey_sepia_and_tint_effects():
    assert centre(sources._effect(px(0, 0, 255), 2)) == (76, 76, 76)
    assert centre(sources._effect(px(100, 100, 100), 3)) == (94, 120, 135)
    assert centre(sources._effect(px(100, 100, 100), 4)) == (100, 20, 20)      # blue
    assert centre(sources._effect(px(100, 100, 100), 5)) == (20, 20, 100)      # red
    assert centre(sources._effect(px(100, 100, 100), 6)) == (20, 100, 20)      # green


def test_adjust_leaves_the_frame_alone_with_default_settings():
    img = np.random.default_rng(1).integers(0, 255, (20, 30, 3), dtype=np.uint8)
    assert (sources.adjust(img, Output()) == img).all()
    assert (sources.adjust(img, Output(zoom=10)) == img).all()               # zoom 10 = 1.0x, off
    assert (sources.adjust(img, Output(wb=1)) == img).all()                  # daylight needs no gains


@pytest.mark.parametrize("kw", [{"zoom": 11}, {"ev": 9}, {"contrast": 129}, {"brightness": 127}, {"saturation": 2},
                                {"sharpness": 1}, {"wb": 2}, {"wb": 3}, {"effect": 1}])
def test_every_setting_changes_the_picture(kw):
    img = np.random.default_rng(2).integers(0, 255, (20, 30, 3), dtype=np.uint8)
    assert (sources.adjust(img, Output(**kw)) != img).any()


def test_the_effect_is_applied_last_and_white_balance_before_it():
    img = px(100, 100, 100)
    wb = centre(sources.adjust(img, Output(wb=3)))
    assert wb == tuple(int(v) for v in np.clip(np.array([100, 100, 100]) * np.array(sources.wb_gains(2800)), 0, 255))
    assert centre(sources.adjust(img, Output(wb=3, effect=1))) == tuple(255 - v for v in wb)


def test_the_limiter_gives_silence_for_nan_and_the_limit_for_infinity():
    chain = sources.MicChain(22050, band=False)
    inf = float("inf")
    assert chain.limit(float("nan")) == 0 and chain.limit(inf) == 32767 and chain.limit(-inf) == -32767
    assert sources.MicChain(22050, float("inf"), band=False).process([0, 1, -1]) == [0, 32767, -32767]


# ---- MicChain: the filter coefficients and the frequency response (found by mutation testing)
@pytest.mark.parametrize("rate, r, b0, a1, a2", [
    (22050, 0.971907, 0.800591, 1.561015, 0.641349), (11025, 0.944603, 0.800591, 1.561015, 0.641349),
    (44100, 0.985853, 0.251379, -0.171241, 0.176757)])
def test_mic_chain_coefficients(rate, r, b0, a1, a2):
    chain = sources.MicChain(rate)
    assert (chain.r, chain.b0, chain.a1, chain.a2) == pytest.approx((r, b0, a1, a2), abs=1e-6)
    assert chain.b1 == pytest.approx(2 * chain.b0)


def tone_gain(freq, rate):
    import math
    chain = sources.MicChain(rate)
    out = chain.process([int(10000 * math.sin(2 * math.pi * freq * k / rate)) for k in range(6000)])
    return max(abs(v) for v in out[3000:]) / 10000


def test_both_corners_of_the_band_are_3_db_down():
    assert tone_gain(100, 44100) == pytest.approx(0.707, abs=0.015)        # DC blocker corner
    assert tone_gain(10000, 44100) == pytest.approx(0.707, abs=0.015)      # low-pass corner
    assert tone_gain(1000, 44100) == pytest.approx(1.0, abs=0.01)
    assert tone_gain(50, 44100) == pytest.approx(0.45, abs=0.02)           # 6 dB per octave below the corner
    assert tone_gain(4960, 11025) == pytest.approx(0.73, abs=0.02)         # corner at 0.45 x rate: below Nyquist


def test_a_new_chain_adds_nothing_to_silence_and_keeps_the_gain_before_the_limiter():
    assert sources.MicChain(22050).process([0] * 50) == [0] * 50
    assert sources.MicChain(22050, 3.0, band=False).process([1000, -1000]) == [3000, -3000]


def test_the_limiter_knee_is_continuous():
    chain = sources.MicChain(22050, band=False)
    assert chain.limit(chain.KNEE - 1) == round(chain.KNEE - 1) and chain.limit(chain.KNEE + 1) == round(chain.KNEE + 1)


# ---- picture helpers pinned to hand-checked values
def test_sharpening_values_at_a_step_edge():
    img = np.full((9, 18, 3), 100, np.uint8)
    img[:, 9:] = 200
    # sigma 1 blur of the step is about 131 / 169 beside it, so k=2 gives 3 * 100 - 2 * 131 = ~70 and ~230
    assert [int(sources._sharpen(img, 1)[4, i, 0]) for i in range(6, 12)] == [100, 97, 85, 215, 203, 200]
    assert [int(sources._sharpen(img, 2)[4, i, 0]) for i in range(6, 12)] == [100, 94, 70, 230, 206, 200]


def test_effects_and_white_balance_keep_black_dark_and_stay_uint8():
    black = px(0, 0, 0)
    assert centre(sources._white_balance(black, (2.0, 1.0, 0.5))) == (0, 0, 0)
    for effect in range(0, 8):
        out = sources._effect(px(30, 60, 90), effect)
        assert out.dtype == np.uint8 and out.shape == (6, 6, 3), effect
        assert centre(sources._effect(black, effect)) == ((255,) * 3 if effect == 1 else (0, 0, 0)), effect
    for fn in (lambda f: sources._saturate(f, 5), lambda f: sources._sharpen(f, 2), lambda f: sources._zoom(f, 30)):
        assert fn(px(30, 60, 90)).dtype == np.uint8


def test_a_very_bright_exposure_is_clipped_before_the_contrast_pivots_it():
    lut = sources._tone_lut(Output(ev=4, contrast=64))
    assert [int(lut[i]) for i in (0, 128, 255)] == [85, 154, 170]            # 255 -> 170 = 128 + (255 - 128) / 3


def test_zoom_cuts_the_centre_from_at_least_two_pixels_and_stops_at_8x():
    img = np.arange(4 * 4 * 3, dtype=np.uint8).reshape(4, 4, 3)
    import cv2
    assert np.array_equal(sources._zoom(img, 80), cv2.resize(img[1:3, 1:3], (4, 4), interpolation=cv2.INTER_LINEAR))
    big = np.random.default_rng(5).integers(0, 255, (80, 120, 3), dtype=np.uint8)
    assert np.array_equal(sources._zoom(big, 80), sources._zoom(big, 255))
    assert not np.array_equal(sources._zoom(big, 80), sources._zoom(big, 70))


@pytest.mark.parametrize("kw", [{"brightness": 129}, {"ev": 11}, {"ev": 9}, {"contrast": 127}, {"saturation": 4},
                                {"zoom": 80}, {"zoom": 11}])
def test_the_smallest_step_of_every_setting_is_applied(kw):
    img = np.random.default_rng(2).integers(0, 255, (20, 30, 3), dtype=np.uint8)
    assert (sources.adjust(img, Output(**kw)) != img).any()


@pytest.mark.parametrize("zoom", [0, 1, 5, 9, 10])
def test_zoom_codes_up_to_10_mean_no_zoom(zoom):
    img = np.random.default_rng(2).integers(0, 255, (20, 30, 3), dtype=np.uint8)
    assert (sources.adjust(img, Output(zoom=zoom)) == img).all()
